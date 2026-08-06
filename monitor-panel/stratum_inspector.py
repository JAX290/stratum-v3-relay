#!/usr/bin/env python3
import asyncio
import json
import os
import signal
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


CONFIG_FILE = Path(os.getenv("CONFIG_FILE", "/etc/stratum-inspector.json"))
STATE_FILE = Path(os.getenv("STATE_FILE", "/var/lib/stratum-inspector/state.json"))
MAX_CONNECTIONS = int(os.getenv("MAX_CONNECTIONS", "500"))
HASHRATE_WINDOW = int(os.getenv("HASHRATE_WINDOW", "1800"))
WORKER_OFFLINE_AFTER = int(os.getenv("WORKER_OFFLINE_AFTER", "900"))
WORKER_INVALID_AFTER = int(os.getenv("WORKER_INVALID_AFTER", "86400"))
WORKER_RETENTION = int(os.getenv("WORKER_RETENTION", "604800"))
BEIJING = ZoneInfo("Asia/Shanghai")

DEFAULT_RELAYS = [
    {
        "id": "hashhut-eu-test",
        "name": "Hash-Hut",
        "region": "欧洲灰度",
        "listen_port": 11001,
        "upstream_host": "eu.pool.hash-hut.net",
        "upstream_port": 9999,
    }
]


def load_relays():
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        relays = data.get("relays", [])
    except (OSError, ValueError):
        relays = DEFAULT_RELAYS
    ports = set()
    result = []
    for relay in relays:
        port = int(relay["listen_port"])
        if port in ports or not (1024 <= port <= 65535):
            raise ValueError(f"invalid or duplicate listen port: {port}")
        ports.add(port)
        result.append({
            "id": str(relay["id"]),
            "pool_id": str(relay.get("pool_id", relay["id"])),
            "name": str(relay["name"]),
            "region": str(relay.get("region", "默认")),
            "role": str(relay.get("role", "线路")),
            "priority": int(relay.get("priority", 1)),
            "external": bool(relay.get("external", False)),
            "proxy_protocol": bool(relay.get("proxy_protocol", False)),
            "listen_host": str(relay.get("listen_host", "0.0.0.0")),
            "listen_port": port,
            "upstream_host": str(relay["upstream_host"]),
            "upstream_port": int(relay["upstream_port"]),
        })
    return result


def now_text(timestamp=None):
    return datetime.fromtimestamp(timestamp or time.time(), BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def format_hashrate(value):
    value = max(0.0, float(value))
    for unit in ("H/s", "KH/s", "MH/s", "GH/s", "TH/s", "PH/s"):
        if value < 1000 or unit == "PH/s":
            return f"{value:.2f} {unit}"
        value /= 1000


def error_message(value):
    if isinstance(value, list) and len(value) > 1:
        return str(value[1])[:160]
    if isinstance(value, dict):
        return str(value.get("message") or value)[:160]
    return str(value or "unknown rejection")[:160]


class Inspector:
    def __init__(self, relay=None):
        self.relay = relay or load_relays()[0]
        self.connections = {}
        self.workers = {}
        self.accepted_events = defaultdict(deque)
        self.connection_counter = 0
        self.semaphore = asyncio.Semaphore(MAX_CONNECTIONS)
        self.upstream_ok = False
        self.upstream_latency_ms = None
        self.anomalies = deque(maxlen=200)

    def worker(self, name):
        if name not in self.workers:
            self.workers[name] = {
                "name": name,
                "agent": "未知",
                "sources": set(),
                "active_connections": set(),
                "submitted": 0,
                "accepted": 0,
                "rejected": 0,
                "last_share": None,
                "last_error": "",
                "latency_total_ms": 0.0,
                "latency_samples": 0,
                "difficulty": 0.0,
                "connections": {},
                "last_seen": time.time(),
            }
        return self.workers[name]

    async def handle(self, client_reader, client_writer):
        async with self.semaphore:
            self.connection_counter += 1
            connection_id = str(self.connection_counter)
            peer = client_writer.get_extra_info("peername") or ("unknown", 0)
            if self.relay.get("proxy_protocol"):
                try:
                    header = await asyncio.wait_for(client_reader.readline(), timeout=3)
                    fields = header.decode("ascii", errors="strict").strip().split()
                    if len(fields) != 6 or fields[0] != "PROXY" or fields[1] not in {"TCP4", "TCP6"}:
                        client_writer.close()
                        await client_writer.wait_closed()
                        return
                    peer = (fields[2], int(fields[4]))
                except (OSError, ValueError, UnicodeError, asyncio.TimeoutError):
                    client_writer.close()
                    await client_writer.wait_closed()
                    return
            connection = {
                "id": connection_id,
                "source_ip": str(peer[0]),
                "source_port": int(peer[1]),
                "connected_at": time.time(),
                "worker": "",
                "agent": "未知",
                "difficulty": 0.0,
                "pending": {},
                "authorized_worker": "",
                "jobs": set(),
                "disconnected_at": None,
                "submitted": 0,
                "accepted": 0,
                "rejected": 0,
                "last_share": None,
                "last_error": "",
                "latency_total_ms": 0.0,
                "latency_samples": 0,
            }
            self.connections[connection_id] = connection
            upstream_writer = None
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        self.relay["upstream_host"], self.relay["upstream_port"]
                    ),
                    timeout=10,
                )
                client_to_pool = asyncio.create_task(
                    self.pipe(client_reader, upstream_writer, connection, True)
                )
                pool_to_client = asyncio.create_task(
                    self.pipe(upstream_reader, client_writer, connection, False)
                )
                done, pending = await asyncio.wait(
                    (client_to_pool, pool_to_client), return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await asyncio.gather(*done, return_exceptions=True)
            except (OSError, asyncio.TimeoutError):
                pass
            finally:
                worker_name = connection.get("worker")
                if worker_name and worker_name in self.workers:
                    worker = self.workers[worker_name]
                    worker["active_connections"].discard(connection_id)
                    connection["disconnected_at"] = time.time()
                    worker["last_seen"] = connection["disconnected_at"]
                self.connections.pop(connection_id, None)
                if upstream_writer:
                    upstream_writer.close()
                client_writer.close()
                if upstream_writer:
                    await upstream_writer.wait_closed()
                await client_writer.wait_closed()

    async def pipe(self, reader, writer, connection, from_miner):
        buffer = b""
        while True:
            data = await reader.read(65536)
            if not data:
                break
            # Observe complete protocol messages before forwarding them. This
            # prevents a fast miner from submitting a freshly received Job
            # before the corresponding mining.notify is registered locally.
            buffer += data
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                if len(raw) > 1024 * 1024:
                    continue
                try:
                    message = json.loads(raw.strip())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(message, dict):
                    if from_miner:
                        self.from_miner(connection, message)
                    else:
                        self.from_pool(connection, message)
            if len(buffer) > 1024 * 1024:
                buffer = b""
            writer.write(data)
            await writer.drain()

    def attach_worker(self, connection, name):
        name = str(name)[:200]
        for key, default in (
            ("disconnected_at", None), ("submitted", 0), ("accepted", 0),
            ("rejected", 0), ("last_share", None), ("last_error", ""),
            ("latency_total_ms", 0.0), ("latency_samples", 0),
        ):
            connection.setdefault(key, default)
        old_name = connection.get("worker")
        if old_name and old_name != name and old_name in self.workers:
            old_worker = self.workers[old_name]
            old_worker["active_connections"].discard(connection["id"])
            old_worker["connections"].pop(connection["id"], None)
        connection["worker"] = name
        worker = self.worker(name)
        worker["sources"].add(connection["source_ip"])
        worker["active_connections"].add(connection["id"])
        worker["connections"][connection["id"]] = connection
        worker["last_seen"] = time.time()
        worker["agent"] = connection["agent"]
        worker["difficulty"] = connection["difficulty"]
        return worker

    def from_miner(self, connection, message):
        method = message.get("method")
        params = message.get("params") or []
        if method == "mining.subscribe" and params:
            connection["agent"] = str(params[0])[:160]
            if connection.get("worker"):
                self.worker(connection["worker"])["agent"] = connection["agent"]
        elif method == "mining.authorize" and params:
            proposed = str(params[0])[:200]
            previous = connection.get("authorized_worker")
            if previous and previous != proposed:
                self.add_anomaly(connection, "worker_changed", f"同一矿机连接先授权为 {previous}，随后改为 {proposed}")
            connection["authorized_worker"] = proposed
            self.attach_worker(connection, params[0])
        elif method == "mining.submit" and params:
            submitted_worker = str(params[0])[:200]
            authorized_worker = connection.get("authorized_worker")
            if authorized_worker and submitted_worker != authorized_worker:
                self.add_anomaly(connection, "worker_mismatch", f"连接授权 Worker 为 {authorized_worker}，但 Share 提交使用 {submitted_worker}")
            worker = self.attach_worker(connection, params[0])
            worker["submitted"] += 1
            worker["last_share"] = now_text()
            worker["last_seen"] = time.time()
            connection["submitted"] += 1
            connection["last_share"] = now_text()
            request_id = json.dumps(message.get("id"), separators=(",", ":"))
            connection["pending"][request_id] = {
                "started": time.monotonic(),
                "worker": worker["name"],
                "difficulty": connection["difficulty"],
            }

    def from_pool(self, connection, message):
        if message.get("method") == "mining.notify":
            params = message.get("params") or []
            if params:
                connection["jobs"].add(str(params[0])[:200])
                if len(connection["jobs"]) > 100:
                    connection["jobs"] = set(list(connection["jobs"])[-50:])
            return
        if message.get("method") == "mining.set_difficulty":
            params = message.get("params") or []
            if params:
                try:
                    connection["difficulty"] = float(params[0])
                except (TypeError, ValueError):
                    return
                if connection.get("worker"):
                    self.worker(connection["worker"])["difficulty"] = connection["difficulty"]
            return

        request_id = json.dumps(message.get("id"), separators=(",", ":"))
        pending = connection["pending"].pop(request_id, None)
        if not pending:
            return
        worker = self.worker(pending["worker"])
        latency = (time.monotonic() - pending["started"]) * 1000
        worker["latency_total_ms"] += latency
        worker["latency_samples"] += 1
        connection["latency_total_ms"] += latency
        connection["latency_samples"] += 1
        accepted = message.get("result") is True and not message.get("error")
        if accepted:
            worker["accepted"] += 1
            connection["accepted"] += 1
            self.accepted_events[worker["name"]].append((time.time(), pending["difficulty"]))
        else:
            worker["rejected"] += 1
            worker["last_error"] = error_message(message.get("error"))
            connection["rejected"] += 1
            connection["last_error"] = worker["last_error"]

    def add_anomaly(self, connection, kind, detail):
        self.anomalies.append({
            "time": now_text(), "type": kind, "detail": detail[:300],
            "source_ip": connection.get("source_ip", "unknown"),
            "worker": connection.get("worker", ""),
            "route": self.relay["id"],
        })

    def state(self):
        now = time.time()
        worker_rows = []
        expired_workers = []
        for name, worker in list(self.workers.items()):
            expired_connections = [
                connection_id for connection_id, connection in worker["connections"].items()
                if connection.get("disconnected_at") and now - connection["disconnected_at"] > WORKER_RETENTION
            ]
            for connection_id in expired_connections:
                worker["connections"].pop(connection_id, None)
            if not worker["connections"] and not worker["active_connections"] and now - worker.get("last_seen", now) > WORKER_RETENTION:
                expired_workers.append(name)
                continue
            events = self.accepted_events[name]
            while events and events[0][0] < now - HASHRATE_WINDOW:
                events.popleft()
            if events:
                observed = min(HASHRATE_WINDOW, max(60, now - events[0][0]))
                estimated = sum(event[1] for event in events) * (2 ** 32) / observed
            else:
                estimated = 0
            submitted = worker["submitted"]
            rejected = worker["rejected"]
            active_count = len(worker["active_connections"])
            last_seen = max(
                [worker.get("last_seen", 0)]
                + [connection.get("disconnected_at") or now for connection in worker["connections"].values()]
            )
            inactive_seconds = 0 if active_count else max(0, int(now - last_seen))
            if active_count:
                status = "online"
                status_text = "在线"
            elif inactive_seconds > WORKER_INVALID_AFTER:
                status = "invalid"
                status_text = "失效"
            elif inactive_seconds > WORKER_OFFLINE_AFTER:
                status = "offline"
                status_text = "离线"
            else:
                status = "recent"
                status_text = "最近断开"
            details = []
            for connection in worker["connections"].values():
                detail_active = connection["id"] in worker["active_connections"]
                detail_last_seen = connection.get("disconnected_at") or now
                detail_inactive = 0 if detail_active else max(0, int(now - detail_last_seen))
                detail_status = (
                    "在线" if detail_active else
                    "失效" if detail_inactive > WORKER_INVALID_AFTER else
                    "离线" if detail_inactive > WORKER_OFFLINE_AFTER else
                    "最近断开"
                )
                detail_submitted = connection.get("submitted", 0)
                detail_rejected = connection.get("rejected", 0)
                details.append({
                    "id": connection["id"],
                    "source_ip": connection["source_ip"],
                    "source_port": connection["source_port"],
                    "agent": connection.get("agent", "未知"),
                    "status": detail_status,
                    "connected_at": now_text(connection["connected_at"]),
                    "disconnected_at": now_text(connection["disconnected_at"]) if connection.get("disconnected_at") else "",
                    "submitted": detail_submitted,
                    "accepted": connection.get("accepted", 0),
                    "rejected": detail_rejected,
                    "reject_percent": round(detail_rejected * 100 / detail_submitted, 2) if detail_submitted else 0,
                    "latency_ms": round(connection["latency_total_ms"] / connection["latency_samples"]) if connection.get("latency_samples") else 0,
                    "last_share": connection.get("last_share") or "尚未提交",
                    "last_error": connection.get("last_error", ""),
                })
            worker_rows.append({
                "name": name,
                "agent": worker["agent"],
                "sources": sorted(worker["sources"]),
                "active": active_count,
                "status": status,
                "status_text": status_text,
                "inactive_seconds": inactive_seconds,
                "last_seen": now_text(last_seen),
                "difficulty": worker["difficulty"],
                "submitted": submitted,
                "accepted": worker["accepted"],
                "rejected": rejected,
                "reject_percent": round(rejected * 100 / submitted, 2) if submitted else 0,
                "last_share": worker["last_share"] or "尚未提交",
                "last_error": worker["last_error"],
                "latency_ms": round(worker["latency_total_ms"] / worker["latency_samples"]) if worker["latency_samples"] else 0,
                "hashrate": format_hashrate(estimated),
                "hashrate_value": estimated,
                "details": sorted(details, key=lambda item: (item["status"] != "在线", item["source_ip"], item["source_port"])),
            })
        for name in expired_workers:
            self.workers.pop(name, None)
            self.accepted_events.pop(name, None)
        connection_rows = []
        for connection in self.connections.values():
            connection_rows.append({
                "source_ip": connection["source_ip"],
                "worker": connection["worker"] or "等待授权",
                "agent": connection["agent"],
                "connected_at": now_text(connection["connected_at"]),
                "duration_seconds": int(now - connection["connected_at"]),
            })
        return {
            "id": self.relay["id"],
            "pool_id": self.relay["pool_id"],
            "name": self.relay["name"],
            "region": self.relay["region"],
            "role": self.relay["role"],
            "priority": self.relay["priority"],
            "external": self.relay["external"],
            "listen_port": self.relay["listen_port"],
            "upstream_host": self.relay["upstream_host"],
            "upstream_port": self.relay["upstream_port"],
            "endpoint": (
                f"0.0.0.0:{self.relay['listen_port']} -> "
                f"{self.relay['upstream_host']}:{self.relay['upstream_port']}"
            ),
            "upstream_ok": self.upstream_ok,
            "upstream_latency_ms": self.upstream_latency_ms,
            "summary": {
                "connections": len(connection_rows),
                "workers": sum(1 for item in worker_rows if item["active"]),
                "submitted": sum(item["submitted"] for item in worker_rows),
                "accepted": sum(item["accepted"] for item in worker_rows),
                "rejected": sum(item["rejected"] for item in worker_rows),
            },
            "workers": sorted(worker_rows, key=lambda item: (
                {"online": 0, "recent": 1, "offline": 2, "invalid": 3}.get(item["status"], 4),
                item["name"],
            )),
            "connections": connection_rows,
            "anomalies": list(self.anomalies),
        }

    async def refresh_health(self):
        started = time.monotonic()
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.relay["upstream_host"], self.relay["upstream_port"]
                ),
                timeout=3,
            )
            request_id = 991337
            request = {
                "id": request_id,
                "method": "mining.subscribe",
                "params": ["hk-relay-health/1.0"],
            }
            writer.write((json.dumps(request, separators=(",", ":")) + "\n").encode())
            await writer.drain()
            valid_response = False
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                line = await asyncio.wait_for(
                    reader.readline(), timeout=max(0.1, deadline - time.monotonic())
                )
                if not line:
                    break
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(message, dict) and message.get("id") == request_id and (
                    "result" in message or "error" in message
                ):
                    valid_response = True
                    break
            self.upstream_ok = valid_response
            self.upstream_latency_ms = (
                round((time.monotonic() - started) * 1000) if valid_response else None
            )
        except (OSError, asyncio.TimeoutError, ValueError):
            self.upstream_ok = False
            self.upstream_latency_ms = None
        finally:
            if writer:
                writer.close()
                await writer.wait_closed()

async def write_state(inspectors):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    last_health_check = 0.0
    while True:
        if time.monotonic() - last_health_check >= 300:
            await asyncio.gather(*(item.refresh_health() for item in inspectors))
            last_health_check = time.monotonic()
        groups = {}
        for inspector in inspectors:
            route = inspector.state()
            group = groups.setdefault(route["pool_id"], {
                "id": route["pool_id"],
                "name": route["name"],
                "external": route["external"],
                "summary": {"connections": 0, "workers": 0, "submitted": 0, "accepted": 0, "rejected": 0},
                "routes": [],
                "workers": [],
            })
            group["external"] = group["external"] and route["external"]
            group["routes"].append({
                "id": route["id"],
                "region": route["region"],
                "role": route["role"],
                "priority": route["priority"],
                "external": route["external"],
                "listen_port": route["listen_port"],
                "upstream_host": route["upstream_host"],
                "upstream_port": route["upstream_port"],
                "upstream_ok": route["upstream_ok"],
                "upstream_latency_ms": route["upstream_latency_ms"],
                "connections": route["summary"]["connections"],
            })
            for key in group["summary"]:
                group["summary"][key] += route["summary"][key]
            for worker in route["workers"]:
                group["workers"].append({**worker, "route": route["region"], "listen_port": route["listen_port"]})
            group.setdefault("anomalies", []).extend(route.get("anomalies", []))
        for group in groups.values():
            group["routes"].sort(key=lambda item: item["priority"])
            group["workers"].sort(key=lambda item: (not item["active"], item["name"], item["listen_port"]))
        payload = {"updated": now_text(), "pools": list(groups.values())}
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, STATE_FILE)
        await asyncio.sleep(5)


async def watch_config(inspectors, servers):
    """Add newly approved endpoint listeners without dropping existing miners."""
    known = {item.relay["id"] for item in inspectors}
    modified = CONFIG_FILE.stat().st_mtime_ns if CONFIG_FILE.exists() else 0
    while True:
        await asyncio.sleep(2)
        current = CONFIG_FILE.stat().st_mtime_ns if CONFIG_FILE.exists() else 0
        if current == modified:
            continue
        try:
            for relay in load_relays():
                if relay["id"] in known:
                    continue
                inspector = Inspector(relay)
                if not relay["external"]:
                    server = await asyncio.start_server(inspector.handle, relay["listen_host"], relay["listen_port"])
                    await server.start_serving()
                    servers.append(server)
                inspectors.append(inspector)
                known.add(relay["id"])
            modified = current
        except (OSError, ValueError):
            await asyncio.sleep(2)


async def main():
    inspectors = [Inspector(relay) for relay in load_relays()]
    servers = []
    for inspector in inspectors:
        if inspector.relay["external"]:
            continue
        server = await asyncio.start_server(
            inspector.handle,
            inspector.relay["listen_host"],
            inspector.relay["listen_port"],
        )
        servers.append(server)
    state_task = asyncio.create_task(write_state(inspectors))
    config_task = asyncio.create_task(watch_config(inspectors, servers))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    for server in servers:
        await server.start_serving()
    await stop.wait()
    for server in servers:
        server.close()
    await asyncio.gather(*(server.wait_closed() for server in servers))
    state_task.cancel()
    config_task.cancel()
    await asyncio.gather(state_task, config_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
