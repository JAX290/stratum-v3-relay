#!/usr/bin/env python3
import argparse
import concurrent.futures
import json
import math
import socket
import statistics
import time
from pathlib import Path


CANDIDATES = {
    "Hash-Hut": [
        ("EU", "eu.pool.hash-hut.net", 9999),
        ("RU", "ru.pool.hash-hut.net", 9999),
        ("BY", "by.pool.hash-hut.net", 9999),
    ],
    "LitecoinPool.org": [
        ("US West", "us2.litecoinpool.org", 3333),
        ("US West alt", "us2.litecoinpool.org", 8080),
        ("US East", "us.litecoinpool.org", 3333),
        ("US East alt", "us.litecoinpool.org", 8080),
        ("Europe", "eu.litecoinpool.org", 3333),
        ("Europe alt", "eu.litecoinpool.org", 8080),
    ],
    "ViaBTC": [
        ("Global IO 3333", "ltc.viabtc.io", 3333),
        ("Global IO 443", "ltc.viabtc.io", 443),
        ("Global CC 3333", "ltc.viabtc.cc", 3333),
        ("Global CC 443", "ltc.viabtc.cc", 443),
        ("Global TOP 3333", "ltc.viabtc.top", 3333),
        ("Global TOP 443", "ltc.viabtc.top", 443),
        ("Europe 3333", "ltc.powhashing.com", 3333),
        ("Europe 443", "ltc.powhashing.com", 443),
    ],
    "F2Pool": [
        ("Asia 3335", "ltc-asia.f2pool.com", 3335),
        ("Asia 5200", "ltc-asia.f2pool.com", 5200),
        ("Asia 8888", "ltc-asia.f2pool.com", 8888),
        ("Global 3335", "ltc.f2pool.com", 3335),
        ("Global 5200", "ltc.f2pool.com", 5200),
        ("Global 8888", "ltc.f2pool.com", 8888),
        ("North America 3335", "ltc-na.f2pool.com", 3335),
        ("North America 5200", "ltc-na.f2pool.com", 5200),
        ("North America 8888", "ltc-na.f2pool.com", 8888),
        ("Europe 3335", "ltc-euro.f2pool.com", 3335),
        ("Europe 5200", "ltc-euro.f2pool.com", 5200),
        ("Europe 8888", "ltc-euro.f2pool.com", 8888),
    ],
    "LongPool": [
        ("Asia 8443", "ltc-doge-asia.longpool.org", 8443),
        ("Asia 8080", "ltc-doge-asia.longpool.org", 8080),
        ("Asia 8880", "ltc-doge-asia.longpool.org", 8880),
        ("US West 8443", "ltc-doge-usw-01.longpool.org", 8443),
        ("US West 8080", "ltc-doge-usw-01.longpool.org", 8080),
        ("US West 8880", "ltc-doge-usw-01.longpool.org", 8880),
    ],
}

PORT_PLAN = {
    "LitecoinPool.org": [11001, 11002, 11003],
    "ViaBTC": [11101, 11102, 11103],
    "F2Pool": [11201, 11202, 11203],
    "LongPool": [11301, 11302, 11303],
}

HASHHUT_FIXED_PORTS = {
    "eu.pool.hash-hut.net": 9999,
    "ru.pool.hash-hut.net": 10001,
    "by.pool.hash-hut.net": 10002,
}


def probe(candidate, timeout):
    label, host, port = candidate
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            tcp_latency = (time.perf_counter() - started) * 1000
            connection.settimeout(timeout)
            request = {"id": 1, "method": "mining.subscribe", "params": ["hk-relay-benchmark/1.0"]}
            connection.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
            buffer = b""
            valid_response = False
            while len(buffer) < 1024 * 1024:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    try:
                        message = json.loads(raw.strip())
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if isinstance(message, dict) and message.get("id") == 1 and (
                        "result" in message or "error" in message
                    ):
                        valid_response = True
                        break
                if valid_response:
                    break
            if not valid_response:
                raise OSError("no valid Stratum subscribe response")
            stratum_latency = (time.perf_counter() - started) * 1000
        return label, host, port, round(tcp_latency, 2), round(stratum_latency, 2), None
    except OSError as exc:
        return label, host, port, None, None, type(exc).__name__


def percentile(values, percentile_value):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile_value) - 1)
    return ordered[index]


def summarize(samples, rounds):
    rows = []
    for pool, candidates in CANDIDATES.items():
        for label, host, port in candidates:
            key = (pool, label, host, port)
            measurements = samples.get(key, [])
            tcp_latencies = [item[0] for item in measurements]
            stratum_latencies = [item[1] for item in measurements]
            success = len(measurements)
            rows.append({
                "pool": pool,
                "label": label,
                "host": host,
                "port": port,
                "success": success,
                "rounds": rounds,
                "success_percent": round(success * 100 / rounds, 1),
                "tcp_median_ms": round(statistics.median(tcp_latencies), 2) if tcp_latencies else None,
                "tcp_p95_ms": round(percentile(tcp_latencies, 0.95), 2) if tcp_latencies else None,
                "stratum_median_ms": round(statistics.median(stratum_latencies), 2) if stratum_latencies else None,
                "stratum_p95_ms": round(percentile(stratum_latencies, 0.95), 2) if stratum_latencies else None,
                "jitter_ms": round(statistics.pstdev(stratum_latencies), 2) if len(stratum_latencies) > 1 else 0,
            })
    return rows


def rank_key(row):
    return (
        -row["success_percent"],
        row["stratum_p95_ms"] if row["stratum_p95_ms"] is not None else float("inf"),
        row["tcp_p95_ms"] if row["tcp_p95_ms"] is not None else float("inf"),
        row["jitter_ms"] if row["jitter_ms"] is not None else float("inf"),
        row["stratum_median_ms"] if row["stratum_median_ms"] is not None else float("inf"),
    )


def recommend(pool_rows):
    eligible = sorted((row for row in pool_rows if row["success_percent"] >= 80), key=rank_key)
    selected = []
    hosts = set()
    for row in eligible:
        if row["host"] not in hosts:
            selected.append(row)
            hosts.add(row["host"])
        if len(selected) == 3:
            return selected
    for row in eligible:
        if row not in selected:
            selected.append(row)
        if len(selected) == 3:
            break
    return selected


def main():
    parser = argparse.ArgumentParser(description="Benchmark Scrypt pool endpoints from this server")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--output", default="/root/pool-benchmark-results.json")
    args = parser.parse_args()
    if not 5 <= args.rounds <= 100:
        parser.error("--rounds must be between 5 and 100")

    flat = [(pool, candidate) for pool, values in CANDIDATES.items() for candidate in values]
    samples = {}
    print(f"Testing {len(flat)} endpoints for {args.rounds} rounds...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(flat)) as executor:
        for round_number in range(1, args.rounds + 1):
            futures = {executor.submit(probe, candidate, args.timeout): (pool, candidate) for pool, candidate in flat}
            for future, (pool, candidate) in futures.items():
                label, host, port, tcp_latency, stratum_latency, _ = future.result()
                if stratum_latency is not None:
                    samples.setdefault((pool, label, host, port), []).append((tcp_latency, stratum_latency))
            print(f"Round {round_number}/{args.rounds} complete", flush=True)
            if round_number != args.rounds:
                time.sleep(args.interval)

    rows = summarize(samples, args.rounds)
    output = {"tested_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "rounds": args.rounds, "results": rows, "recommendations": {}}
    for pool in CANDIDATES:
        pool_rows = [row for row in rows if row["pool"] == pool]
        chosen = recommend(pool_rows)
        assigned = []
        if pool == "Hash-Hut":
            for row in chosen:
                assigned.append({"local_port": HASHHUT_FIXED_PORTS[row["host"]], **row})
        else:
            for local_port, row in zip(PORT_PLAN[pool], chosen):
                assigned.append({"local_port": local_port, **row})
        output["recommendations"][pool] = assigned

        print(f"\n[{pool}]")
        print("success  tcp50  tcp95  stratum50  stratum95  jitter  endpoint")
        for row in sorted(pool_rows, key=rank_key):
            print(
                f"{row['success_percent']:6.1f}%  {str(row['tcp_median_ms']):>5}  "
                f"{str(row['tcp_p95_ms']):>5}  {str(row['stratum_median_ms']):>9}  "
                f"{str(row['stratum_p95_ms']):>9}  {str(row['jitter_ms']):>6}  "
                f"{row['host']}:{row['port']} ({row['label']})"
            )
        print("Recommended:")
        for item in assigned:
            print(f"  VPS :{item['local_port']} -> {item['host']}:{item['port']} ({item['label']})")
        if len(assigned) < 3:
            print("  WARNING: fewer than three endpoints reached 80% success")

    Path(args.output).write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nFull results saved to {args.output}")


if __name__ == "__main__":
    main()
