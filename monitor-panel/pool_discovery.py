#!/usr/bin/env python3
"""Discover usable mining pools for a coin and check the current relay's pool coverage.

Why this exists
---------------
When a new coin appears, the operator has to answer two questions before touching the
relay: *which pools actually serve this coin*, and *which of them are reachable and
speaking Stratum from this VPS*. This tool answers both and prints something that can be
pasted into ``v3-config.json``.

How it finds candidates
-----------------------
It deliberately does **not** brute-force DNS names. Pool hostnames follow no consistent
scheme (``ltc.f2pool.com``, ``ltc-doge-asia.longpool.org``, ``us2.litecoinpool.org``), so
guessing produces almost nothing but timeouts. Candidates come from four places instead,
in order of trust:

1. ``--config`` — the relay's own config, i.e. pools already known to work here.
2. ``--catalog`` / the bundled ``pool-catalog.json`` — operator-maintained seed list.
3. ``--candidates`` — newline-based ``host:port`` list, one per line, ``#`` comments.
4. ``--registry`` — optional online lookup (MiningPoolStats). Best effort: it is skipped
   silently when unreachable, because it is frequently down and must never be a
   prerequisite for the probes.

Every candidate is then probed for real: TCP connect, then a Stratum ``mining.subscribe``
handshake. ``--authorize`` additionally checks that the pool accepts a login for the coin.

The script is read-only: it never writes the relay config. It prints a report and
optionally writes a JSON file for review.
"""

import argparse
import concurrent.futures
import json
import math
import socket
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from v3_manager import ConfigError, validate_config
except ImportError:  # Running outside monitor-panel, e.g. from a bare checkout
    ConfigError = ValueError
    validate_config = None


HERE = Path(__file__).resolve().parent
DEFAULT_CATALOG = HERE / "pool-catalog.json"
DEFAULT_CONFIG = HERE / "v3-config.json"
REGISTRY_URL = "https://api.miningpoolstats.stream/v1/coin/{coin}"
MAX_RESPONSE_BYTES = 256 * 1024
# Ports a pool could plausibly serve Stratum on; used only when a catalog entry gives a
# host without a port, never to guess hostnames.
COMMON_STRATUM_PORTS = (3333, 3334, 3335, 3336, 8080, 8443, 9999, 443, 8888, 5200)


# --------------------------------------------------------------------------------------
# Candidate model
# --------------------------------------------------------------------------------------

class Candidate:
    """One probe target: a pool endpoint plus why we believe it serves the coin."""

    __slots__ = ("pool", "region", "host", "port", "algorithm", "coins", "source")

    def __init__(self, pool, region, host, port, algorithm="", coins="", source=""):
        self.pool = str(pool).strip()
        self.region = str(region).strip()
        self.host = str(host).strip().lower().rstrip(".")
        self.port = int(port)
        self.algorithm = str(algorithm).strip().lower()
        self.coins = str(coins).strip().upper()
        self.source = source

    @property
    def key(self):
        return (self.host, self.port)

    def as_config_endpoint(self, index):
        slug = "".join(ch if ch.isalnum() else "-" for ch in f"{self.pool}-{self.region}".lower())
        slug = "-".join(part for part in slug.split("-") if part)[:40] or f"pool-{index}"
        return {
            "id": slug,
            "pool": self.pool,
            "region": self.region,
            "host": self.host,
            "port": self.port,
            "algorithm": self.algorithm or "scrypt",
            "coins": self.coins or "",
            "transport": "tcp",
            "source": self.source or "pool-discovery",
            "enabled": True,
        }

    def __repr__(self):
        return f"<Candidate {self.host}:{self.port} ({self.pool}/{self.region})>"


def split_host_port(value, default_port):
    """Parse ``host``, ``host:port`` or ``[v6]:port``. Returns (host, port) or raises."""
    value = value.strip()
    if not value:
        raise ValueError("empty target")
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        port = int(rest.lstrip(":") or default_port)
        return host.strip().lower(), port
    if value.count(":") == 1:
        host, _, port_text = value.partition(":")
        return host.strip().lower(), int(port_text)
    if ":" in value:  # bare IPv6 literal, no port
        return value.lower(), int(default_port)
    return value.lower(), int(default_port)


def parse_candidate_lines(text, source="file"):
    """Parse a ``host[:port][ pool=..][ region=..]`` list. Pure; used by tests."""
    candidates = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        try:
            host, port = split_host_port(fields[0], 3333)
        except (ValueError, IndexError) as exc:
            raise ValueError(f"line {number}: cannot parse {raw!r}: {exc}") from None
        extra = {}
        for token in fields[1:]:
            if "=" not in token:
                raise ValueError(f"line {number}: expected key=value, got {token!r}")
            key, _, value = token.partition("=")
            extra[key.strip().lower()] = value.strip()
        candidates.append(Candidate(
            pool=extra.get("pool", host),
            region=extra.get("region", ""),
            host=host,
            port=port,
            algorithm=extra.get("algorithm", ""),
            coins=extra.get("coins", ""),
            source=source,
        ))
    return candidates


def load_catalog(path):
    """Read the seed catalog. Missing file is not an error; it just means no seeds."""
    path = Path(path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"catalog {path} is not valid JSON: {exc}") from None
    candidates = []
    for pool in data.get("pools", []):
        for entry in pool.get("endpoints", []):
            candidates.append(Candidate(
                pool=pool.get("name", entry.get("host", "")),
                region=entry.get("region", ""),
                host=entry.get("host", ""),
                port=entry.get("port", 3333),
                algorithm=pool.get("algorithm", ""),
                coins=",".join(pool.get("coins", [])),
                source=str(path.name),
            ))
    return candidates


def candidates_from_config(path):
    """Endpoints already used by the relay, tagged with the coins the config claims."""
    path = Path(path)
    if not path.is_file():
        return [], None
    config = json.loads(path.read_text(encoding="utf-8"))
    candidates = []
    for endpoint in config.get("endpoints", []):
        if not endpoint.get("enabled", True):
            continue
        candidates.append(Candidate(
            pool=endpoint.get("pool", ""),
            region=endpoint.get("region", ""),
            host=endpoint.get("host", ""),
            port=endpoint.get("port", 0),
            algorithm=endpoint.get("algorithm", ""),
            coins=endpoint.get("coins", ""),
            source="relay-config",
        ))
    return candidates, config


# --------------------------------------------------------------------------------------
# Filtering and ranking
# --------------------------------------------------------------------------------------

def coin_tokens(value):
    """Split ``LTC/DOGE``, ``LTC,DOGE`` or ``LTC`` into an upper-case token set."""
    return {token.strip().upper() for token in str(value).replace("/", ",").split(",") if token.strip()}


def matches_coin(candidate, coin):
    """A candidate matches when its declared coin list contains the symbol.

    Entries that declare no coins at all are *unknown*, not matching: silently treating
    them as matches is what produces pools that turn out to mine something else.
    """
    return coin.upper() in coin_tokens(candidate.coins)


def deduplicate(candidates):
    """Keep the first occurrence of each host:port, preserving input order."""
    seen = set()
    unique = []
    for candidate in candidates:
        if candidate.key not in seen:
            seen.add(candidate.key)
            unique.append(candidate)
    return unique


def rank_key(result):
    """Best first: reachable, then Stratum-confirmed, then fast and steady."""
    return (
        0 if result["stratum_ok"] else 1 if result["tcp_ok"] else 2,
        result["stratum_p95_ms"] if result["stratum_p95_ms"] is not None else float("inf"),
        result["jitter_ms"] if result["jitter_ms"] is not None else float("inf"),
        result["tcp_p95_ms"] if result["tcp_p95_ms"] is not None else float("inf"),
    )


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def summarize(samples, attempts):
    """Pure: turn per-candidate samples into ranked result rows."""
    rows = []
    for candidate, measurements in samples.items():
        tcp = [item[0] for item in measurements]
        stratum = [item[1] for item in measurements]
        attempts_for = attempts.get(candidate.key, 0)
        rows.append({
            "pool": candidate.pool,
            "region": candidate.region,
            "host": candidate.host,
            "port": candidate.port,
            "algorithm": candidate.algorithm,
            "coins": candidate.coins,
            "source": candidate.source,
            "attempts": attempts_for,
            "tcp_ok": bool(tcp),
            "stratum_ok": bool(stratum),
            "success_percent": round(len(stratum) * 100 / attempts_for, 1) if attempts_for else 0.0,
            "tcp_p95_ms": round(percentile(tcp, 0.95), 2) if tcp else None,
            "stratum_median_ms": round(statistics.median(stratum), 2) if stratum else None,
            "stratum_p95_ms": round(percentile(stratum, 0.95), 2) if stratum else None,
            "jitter_ms": round(statistics.pstdev(stratum), 2) if len(stratum) > 1 else 0.0,
        })
    return sorted(rows, key=rank_key)


def pick_diverse(rows, limit=3):
    """Choose up to ``limit`` rows, preferring a different pool for every slot.

    Two ports on one host are not redundancy, and two regional hosts of one pool still
    share that pool's fate — the same mistake that put every dynamic port group on a
    single pool. So fill one slot per distinct pool first, then top up with further
    hosts only if fewer pools than slots are available.
    """
    reachable = [row for row in rows if row["stratum_ok"]]
    chosen, hosts, pools = [], set(), set()
    for row in reachable:
        if row["host"] in hosts or row["pool"] in pools:
            continue
        chosen.append(row)
        hosts.add(row["host"])
        pools.add(row["pool"])
        if len(chosen) == limit:
            return chosen
    for row in reachable:
        if row["host"] in hosts:
            continue
        chosen.append(row)
        hosts.add(row["host"])
        if len(chosen) == limit:
            break
    return chosen


# --------------------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------------------

def resolve_host(host, port):
    """Resolve and reject anything non-global, matching what the relay itself enforces."""
    from ipaddress import ip_address
    values = {row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    for value in values:
        if not ip_address(value.split("%")[0]).is_global:
            raise OSError(f"unsafe non-public address: {value}")
    return sorted(values)


def _read_response(connection, request_id, deadline):
    buffer = b""
    while len(buffer) < MAX_RESPONSE_BYTES and time.monotonic() < deadline:
        connection.settimeout(max(0.1, deadline - time.monotonic()))
        try:
            chunk = connection.recv(65536)
        except socket.timeout:
            return None
        if not chunk:
            return None
        buffer += chunk
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            try:
                message = json.loads(raw.strip())
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
    return None


def probe_once(candidate, timeout=3.0, username="", password="x", user_agent="pool-discovery/1.0",
               resolver=None):
    """One TCP + Stratum subscribe attempt. Returns (tcp_ms, stratum_ms) or raises OSError.

    ``resolver`` is injectable so tests can point at a loopback fixture: the production
    path rejects non-public addresses, which is deliberate and must stay that way.
    """
    (resolver or resolve_host)(candidate.host, candidate.port)
    started = time.perf_counter()
    with socket.create_connection((candidate.host, candidate.port), timeout=timeout) as connection:
        tcp_ms = (time.perf_counter() - started) * 1000
        connection.settimeout(timeout)
        request = {"id": 9001, "method": "mining.subscribe", "params": [user_agent]}
        connection.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
        reply = _read_response(connection, 9001, time.monotonic() + timeout)
        if not reply or ("result" not in reply and "error" not in reply):
            raise OSError("no valid mining.subscribe response")
        if username:
            auth = {"id": 9002, "method": "mining.authorize", "params": [username, password]}
            connection.sendall((json.dumps(auth, separators=(",", ":")) + "\n").encode())
            authorized = _read_response(connection, 9002, time.monotonic() + timeout)
            if not authorized or not authorized.get("result"):
                error = (authorized or {}).get("error")
                raise OSError(f"authorize rejected: {error if error else 'no result'}")
        return tcp_ms, (time.perf_counter() - started) * 1000


def probe_round(candidates, rounds, timeout, username, password, workers, quiet, resolver=None):
    samples = {candidate: [] for candidate in candidates}
    attempts = {candidate.key: 0 for candidate in candidates}
    for round_number in range(1, rounds + 1):
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(probe_once, candidate, timeout, username, password,
                                       "pool-discovery/1.0", resolver): candidate
                       for candidate in candidates}
            for future, candidate in futures.items():
                attempts[candidate.key] += 1
                try:
                    tcp_ms, stratum_ms = future.result()
                    samples[candidate].append((tcp_ms, stratum_ms))
                except Exception:  # noqa: BLE001 - any failure is just a failed sample
                    continue
        if not quiet:
            reachable = sum(1 for values in samples.values() if values)
            print(f"  round {round_number}/{rounds}: {reachable}/{len(candidates)} endpoints responded",
                  file=sys.stderr)
        if round_number != rounds:
            time.sleep(0.4)
    return samples, attempts


def registry_candidates(coin, timeout=10):
    """Best-effort online lookup. Returns (candidates, note). Never raises."""
    url = REGISTRY_URL.format(coin=coin.lower())
    request = urllib.request.Request(url, headers={"User-Agent": "pool-discovery/1.0",
                                                  "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return [], f"registry unreachable ({type(exc).__name__}); skipped"
    candidates = []
    for pool in payload.get("pools", []) if isinstance(payload, dict) else []:
        host = str(pool.get("host", "")).strip()
        if not host:
            continue
        port = pool.get("port") or pool.get("ports") or 3333
        if isinstance(port, list):
            port = port[0] if port else 3333
        try:
            candidates.append(Candidate(pool=pool.get("name", host), region=pool.get("region", ""),
                                        host=host, port=int(port), algorithm=pool.get("algorithm", ""),
                                        coins=coin, source="registry"))
        except (TypeError, ValueError):
            continue
    return candidates, f"registry returned {len(candidates)} candidate(s)"


# --------------------------------------------------------------------------------------
# Relay coverage report
# --------------------------------------------------------------------------------------

def coverage_report(config):
    """Describe which pool each configured port serves, and flag concentration.

    Only *dynamic port groups* count towards a concentration warning. Fixed routes are
    single-purpose by construction (``11001-11003`` exist to pin LitecoinPool), so a pool
    owning both a fixed route and a group is a deliberate arrangement, not a hazard. What
    matters is whether one pool backs several of the interchangeable port groups: that is
    the case where a single pool outage takes every line down at once.
    """
    if not config:
        return [], {}
    pool_of = {item["id"]: item.get("pool", "") for item in config.get("endpoints", [])}
    rows, holders = [], {}
    for group in config.get("port_groups", []):
        pools = sorted({pool_of.get(endpoint_id, endpoint_id) for endpoint_id in group.get("endpoint_ids", [])})
        rows.append({"id": group.get("id", ""), "name": group.get("name", ""),
                     "ports": group.get("ports", []), "pools": pools})
        for pool in pools:
            holders.setdefault(pool, []).append(group.get("id", ""))
    for route in config.get("fixed_routes", []):
        endpoint_id = route.get("endpoint_id", "")
        rows.append({"id": f":{route.get('port')}", "name": "固定端口", "ports": [route.get("port")],
                     "pools": [pool_of.get(endpoint_id, endpoint_id)]})
    shared = {pool: names for pool, names in holders.items() if len(names) > 1}
    return rows, shared


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------

def print_coverage(coverage, shared):
    if coverage:
        print()
        print("当前中转配置的矿池分布：")
        for row in coverage:
            shown = "/".join(str(port) for port in row["ports"])
            print(f"  {row['id']:<10} {shown:<26} -> {', '.join(row['pools'])}")
    if shared:
        print()
        print("⚠ 以下矿池被多个动态端口组共用，该矿池整体故障会同时影响这些端口：")
        for pool, holders in sorted(shared.items()):
            print(f"    {pool}: {', '.join(holders)}")
        print("  建议每组端口指向一个不同矿池。")


def print_report(coin, results, chosen, skipped, coverage, shared, notes):
    print()
    print("=" * 78)
    print(f"  矿池发现报告  coin={coin.upper()}")
    print("=" * 78)
    for note in notes:
        print(f"  · {note}")

    if results:
        print()
        if skipped:
            print(f"候选过滤：{len(skipped)} 个端点未声明 {coin.upper()}，已跳过（用 --all-coins 可全部探测）")
            print()
        print(f"{'状态':<6}{'成功':>7}{'TCP95':>9}{'验证中位':>10}{'验证95':>9}{'抖动':>8}  端点")
        print("-" * 78)
        for row in results:
            status = "可用" if row["stratum_ok"] else "仅TCP" if row["tcp_ok"] else "失败"
            print(f"{status:<6}{row['success_percent']:>6}%"
                  f"{str(row['tcp_p95_ms']):>9}{str(row['stratum_median_ms']):>10}"
                  f"{str(row['stratum_p95_ms']):>9}{str(row['jitter_ms']):>8}"
                  f"  {row['host']}:{row['port']} ({row['pool']} {row['region']})".rstrip())
        print()
        if chosen:
            print(f"建议线路（每个矿池只取一条，共 {len(chosen)} 条）：")
            for row in chosen:
                print(f"  {row['host']}:{row['port']}   {row['pool']} · {row['region']}"
                      f"   验证95={row['stratum_p95_ms']}ms 抖动={row['jitter_ms']}ms")
        else:
            print("没有任何端点通过 Stratum 验证，建议线路为空。")

    print_coverage(coverage, shared)


def write_output(path, coin, results, chosen):
    payload = {
        "coin": coin.upper(),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "results": results,
        "recommended": chosen,
        "endpoints": [row for row in chosen],
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n结果已写入 {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="探测某个币种当前可用的矿池，并检查中转配置的矿池分布",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python3 pool_discovery.py --coin LTC\n"
               "  python3 pool_discovery.py --coin DOGE --rounds 5 --authorize me.rig1\n"
               "  python3 pool_discovery.py --coin XYZ --candidates newpools.txt\n")
    parser.add_argument("--coin", required=True, help="币种符号，必填，例如 LTC、DOGE、XYZ")
    parser.add_argument("--pool", help="只探测名称包含该字符串的矿池")
    parser.add_argument("--candidates", action="append", default=[],
                        help="额外候选文件，每行 host[:port] [pool=..] [region=..]；可重复")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="种子矿池目录 JSON")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="中转配置，用于候选与分布检查")
    parser.add_argument("--rounds", type=int, default=3, help="每个端点的探测轮数（默认 3）")
    parser.add_argument("--timeout", type=float, default=3.0, help="单次连接/响应超时秒数")
    parser.add_argument("--workers", type=int, default=16, help="并发数（默认 16）")
    parser.add_argument("--authorize", metavar="USER",
                        help="额外验证 mining.authorize，通常填 账号.矿工名")
    parser.add_argument("--password", default="x", help="配合 --authorize 的密码")
    parser.add_argument("--all-coins", action="store_true",
                        help="不按币种过滤，探测全部候选（会明显变慢）")
    parser.add_argument("--registry", action="store_true",
                        help="额外尝试在线矿池目录（不稳定，失败会自动跳过）")
    parser.add_argument("--output", help="把结果写入 JSON 文件")
    parser.add_argument("--quiet", action="store_true", help="不显示每轮进度")
    parser.add_argument("--coverage-only", action="store_true",
                        help="只检查当前中转配置的矿池分布，不做网络探测")
    args = parser.parse_args(argv)

    coin = args.coin.strip().upper()
    if not coin:
        parser.error("--coin 不能为空")
    if not 1 <= args.rounds <= 50:
        parser.error("--rounds 必须在 1-50 之间")
    if not 1 <= args.workers <= 128:
        parser.error("--workers 必须在 1-128 之间")

    notes = []
    config_candidates, config = candidates_from_config(args.config)
    if config is None:
        notes.append(f"未找到中转配置 {args.config}，跳过矿池分布检查")
    else:
        if validate_config is not None:
            try:
                validate_config(config)
                notes.append(f"中转配置校验通过（{len(config.get('endpoints', []))} 个地址）")
            except (ConfigError, ValueError) as exc:
                notes.append(f"⚠ 中转配置校验未通过：{exc}")

    coverage, shared = coverage_report(config)

    if args.coverage_only:
        print()
        print("=" * 78)
        print(f"  中转配置矿池分布检查  coin={coin.upper()}")
        print("=" * 78)
        for note in notes:
            print(f"  · {note}")
        print_coverage(coverage, shared)
        return 1 if shared else 0

    candidates = list(config_candidates)
    try:
        candidates.extend(load_catalog(args.catalog))
    except ValueError as exc:
        notes.append(f"⚠ {exc}")
    for extra in args.candidates:
        try:
            candidates.extend(parse_candidate_lines(Path(extra).read_text(encoding="utf-8"), source=Path(extra).name))
        except (OSError, ValueError) as exc:
            notes.append(f"⚠ 候选文件 {extra} 读取失败：{exc}")
    if args.registry:
        found, note = registry_candidates(coin)
        candidates.extend(found)
        notes.append(note)

    if args.pool:
        needle = args.pool.lower()
        candidates = [item for item in candidates if needle in item.pool.lower()]

    candidates = deduplicate(candidates)
    if not candidates:
        notes.append("没有可用候选：请检查 --catalog，或用 --candidates 提供 host:port 列表")
        print_report(coin, [], [], [], coverage, shared, notes)
        return 2

    if args.all_coins:
        selected, skipped = candidates, []
    else:
        declared = [(item, matches_coin(item, coin)) for item in candidates]
        selected = [item for item, matched in declared if matched]
        skipped = [item for item, matched in declared if not matched]
        # Endpoints the relay already trusts are probed even without a declared coin, so
        # the operator can see whether an existing line is still alive.
        selected.extend(item for item in skipped if item.source == "relay-config")
        skipped = [item for item in skipped if item.source != "relay-config"]

    if not selected:
        notes.append(f"没有任何候选声明支持 {coin}。请用 --candidates 指定，或加 --all-coins 强制探测。")
        print_report(coin, [], [], skipped, coverage, shared, notes)
        return 2

    if not args.quiet:
        print(f"探测 {len(selected)} 个端点，每端点 {args.rounds} 轮，超时 {args.timeout}s …", file=sys.stderr)
    samples, attempts = probe_round(selected, args.rounds, args.timeout,
                                    args.authorize or "", args.password, args.workers, args.quiet)
    results = summarize(samples, attempts)
    chosen = pick_diverse(results)

    print_report(coin, results, chosen, skipped, coverage, shared, notes)
    if args.output:
        write_output(args.output, coin, results, chosen)
    return 0 if chosen else 1


if __name__ == "__main__":
    sys.exit(main())
