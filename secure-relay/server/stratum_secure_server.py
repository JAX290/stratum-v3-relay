#!/usr/bin/env python3
"""TLS ingress for the Stratum V3 relay.

The client opens a TLS connection and sends an HTTP CONNECT-like request.  The
request is inside TLS.  After authentication both sides switch to a raw byte
stream, preserving the original Stratum protocol for the existing inspector.
"""

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import signal
import ssl
import tempfile
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows unit tests do not provide POSIX file locks.
    fcntl = None

try:
    from version_info import load_versions
except ModuleNotFoundError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "monitor-panel"))
    from version_info import load_versions


CONFIG_FILE = Path(os.getenv("SECURE_RELAY_CONFIG", "/etc/stratum-secure-relay.json"))
V3_CONFIG_FILE = Path(os.getenv("V3_CONFIG_FILE", "/etc/stratum-v3.json"))
MAX_HEADER = 8192
DRAIN_TIMEOUT = 120
INTERNAL_START = 20000
DEFAULT_STATE_FILE = Path("/var/lib/stratum-secure-relay/sites.json")
SERVER_VERSION = load_versions()["secure_relay"]
CONTROL_FILE = Path(os.getenv("SECURE_RELAY_CONTROL", "/var/lib/stratum-secure-relay/control.json"))
CLIENT_ACTION_FILE = Path(os.getenv("SECURE_RELAY_CLIENT_ACTIONS", "/var/lib/stratum-secure-relay/client-actions.json"))
_v3_cache_key = None
_v3_cache_value = None


def load_config():
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    required = ("listen_host", "listen_port", "certificate", "private_key")
    missing = [name for name in required if not data.get(name)]
    if missing:
        raise ValueError("missing secure relay settings: " + ", ".join(missing))
    clients = data.get("clients") or []
    if not clients and data.get("token"):
        clients = [{"id": "default", "name": "默认矿场", "token": data["token"], "enabled": True}]
    if (not clients and "clients" not in data) or any(len(str(item.get("token", ""))) < 32 for item in clients):
        raise ValueError("at least one secure relay client token is required")
    data["clients"] = clients
    return data


def route_map(miner_ip=None):
    global _v3_cache_key, _v3_cache_value
    stat = V3_CONFIG_FILE.stat()
    cache_key = (str(V3_CONFIG_FILE), getattr(stat, "st_ino", 0), stat.st_mtime_ns, stat.st_size)
    if cache_key != _v3_cache_key:
        _v3_cache_value = json.loads(V3_CONFIG_FILE.read_text(encoding="utf-8"))
        _v3_cache_key = cache_key
    config = _v3_cache_value
    internal = {}
    for offset, endpoint in enumerate(config.get("endpoints", [])):
        if endpoint.get("enabled", True):
            internal[endpoint["id"]] = INTERNAL_START + offset
    routes = {}
    for group in config.get("port_groups", []):
        for public_port, endpoint_id in zip(group.get("ports", []), group.get("endpoint_ids", [])):
            if endpoint_id in internal:
                routes[int(public_port)] = internal[endpoint_id]
    for route in config.get("fixed_routes", []):
        endpoint_id = route.get("endpoint_id")
        if endpoint_id in internal:
            routes[int(route["port"])] = internal[endpoint_id]
    for canary in config.get("canary_routes", []):
        endpoint_id = canary.get("endpoint_id")
        if miner_ip and canary.get("source_ip") == miner_ip and endpoint_id in internal:
            routes[int(canary["port"])] = internal[endpoint_id]
    return routes


async def close_writer(writer):
    if writer is None:
        return
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=5)
    except (ConnectionError, OSError, ssl.SSLError, asyncio.TimeoutError):
        pass


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await asyncio.wait_for(writer.drain(), timeout=DRAIN_TIMEOUT)
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass


def parse_request(raw):
    text = raw.decode("iso-8859-1")
    lines = text.split("\r\n")
    request = lines[0].split()
    if len(request) != 3 or request[0] != "CONNECT" or request[2] != "HTTP/1.1":
        raise ValueError("invalid request line")
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            raise ValueError("invalid header")
        headers[name.strip().lower()] = value.strip()
    prefix = "/relay/v1/"
    if request[1] == "/relay/v2/health":
        port = None
    elif request[1].startswith(prefix):
        port = int(request[1][len(prefix):])
    else:
        raise ValueError("invalid path")
    authorization = headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise PermissionError("missing token")
    return port, authorization[7:], headers


def authenticate(config, supplied_token):
    for client in config.get("clients", []):
        if client.get("enabled", True) and hmac.compare_digest(str(client.get("token", "")), supplied_token):
            return {"id": str(client.get("id", "default")), "name": str(client.get("name", client.get("id", "默认矿场")))}
    raise PermissionError("invalid token")


class SiteState:
    def __init__(self, path):
        self.path = Path(path)
        self.sites = {}
        self.miner_counts = {}
        self.last_write = 0.0
        self.last_write_error_log = 0.0
        self.dirty = False
        try:
            self.sites = json.loads(self.path.read_text(encoding="utf-8")).get("sites", {})
        except (OSError, ValueError):
            pass
        # A previous process may have been killed before decrementing its counters.
        # Every connection in a new process starts from zero.
        for site in self.sites.values():
            site["active"] = 0
            site["miners"] = []
            site["miner_count"] = 0
        if self.sites:
            self.write()

    def update(self, client, peer, active_delta=0, force=False, miner_ip="", client_version="",
               reported_miner_count=None, reported_connections=None, current_vps="", last_share=None,
               reconnect_count=None, action_id="", action_status=""):
        now = time.time()
        site = self.sites.setdefault(client["id"], {"id": client["id"], "name": client["name"], "active": 0})
        site["name"] = client["name"]
        site["last_seen"] = int(now)
        site["last_ip"] = str(peer[0])
        site["active"] = max(0, int(site.get("active", 0)) + active_delta)
        if client_version:
            site["client_version"] = str(client_version)[:32]
        if reported_miner_count is not None:
            site["reported_miner_count"] = max(0, min(100000, int(reported_miner_count)))
            site["last_miner_count"] = max(int(site.get("last_miner_count", 0) or 0), site["reported_miner_count"])
        if reported_connections is not None:
            site["reported_connections"] = max(0, min(1000000, int(reported_connections)))
        if current_vps:
            site["current_vps"] = str(current_vps)[:64]
        if last_share is not None:
            site["last_share"] = max(0, int(last_share))
        if reconnect_count is not None:
            site["reconnect_count"] = max(0, min(1000000000, int(reconnect_count)))
        if action_id and action_status:
            site["last_action_id"] = str(action_id)[:32]
            site["last_action_status"] = str(action_status)[:32]
            site["last_action_time"] = int(now)
        if miner_ip and active_delta:
            counts = self.miner_counts.setdefault(client["id"], {})
            counts[miner_ip] = max(0, int(counts.get(miner_ip, 0)) + active_delta)
            if counts[miner_ip] == 0:
                counts.pop(miner_ip, None)
            site["miners"] = sorted(counts)
            site["miner_count"] = len(counts)
            if active_delta > 0:
                site["last_miner_count"] = max(int(site.get("last_miner_count", 0) or 0), len(counts))
        self.dirty = True
        if force or now - self.last_write >= 5:
            self.write()

    def write(self):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=str(self.path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"updated_at": int(time.time()), "server_version": SERVER_VERSION, "sites": self.sites}, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.chmod(temporary, 0o640)
            os.replace(temporary, self.path)
            self.last_write = time.time()
            self.dirty = False
            return True
        except OSError as exc:
            # Status persistence is auxiliary. A missing systemd write permission
            # must never take the mining relay itself offline during an upgrade.
            now = time.time()
            if now - self.last_write_error_log >= 60:
                logging.error("cannot persist relay site state path=%s reason=%s", self.path, exc)
                self.last_write_error_log = now
            return False
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


def source_address(headers, peer):
    candidate = headers.get("x-miner-ip", "")
    try:
        address = ipaddress.ip_address(candidate)
        if address.version != 4:
            raise ValueError("IPv6 client is not supported by this build")
        source_ip = str(address)
    except ValueError:
        source_ip = str(peer[0])
        ipaddress.ip_address(source_ip)
    return source_ip


def optional_count(headers, name):
    try:
        value = int(headers.get(name, ""))
        return value if value >= 0 else None
    except (TypeError, ValueError):
        return None


def optional_timestamp(headers, name):
    value = optional_count(headers, name)
    return value if value is not None and value <= int(time.time()) + 300 else None


def _write_action_store(data):
    CLIENT_ACTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=CLIENT_ACTION_FILE.name + ".", dir=str(CLIENT_ACTION_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o660)
        os.replace(temporary, CLIENT_ACTION_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def take_client_action(client_id, now=None):
    now = int(now or time.time())
    lock_path = CLIENT_ACTION_FILE.with_suffix(CLIENT_ACTION_FILE.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="ascii") as lock:
        if fcntl:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            data = json.loads(CLIENT_ACTION_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"clients": {}, "results": {}}
        clients = data.get("clients", {}) if isinstance(data.get("clients", {}), dict) else {}
        item = clients.pop(str(client_id), None)
        data["clients"] = clients
        if not isinstance(item, dict) or item.get("action") not in {"diagnose", "reconnect", "upgrade"} or int(item.get("expires", 0) or 0) <= now:
            if item is not None:
                _write_action_store(data)
            return None
        results = data.setdefault("results", {})
        results[str(client_id)] = {"id": str(item.get("id", "")), "action": item["action"], "status": "delivered", "time": now}
        _write_action_store(data)
        return {"id": str(item.get("id", "")), "action": item["action"]}


def is_local_watchdog(port, headers, peer):
    try:
        return (port is None and headers.get("x-health-origin") == "vps-watchdog"
            and ipaddress.ip_address(str(peer[0])).is_loopback)
    except ValueError:
        return False


def proxy_header(headers, peer, destination_port):
    source_ip = source_address(headers, peer)
    try:
        source_port = int(headers.get("x-miner-port", peer[1]))
    except (TypeError, ValueError):
        source_port = int(peer[1])
    if not 1 <= source_port <= 65535:
        source_port = 1
    return f"PROXY TCP4 {source_ip} 127.0.0.1 {source_port} {destination_port}\r\n".encode("ascii")


class SecureRelay:
    def __init__(self, config):
        self.config = config
        self.semaphore = asyncio.Semaphore(int(config.get("max_connections", 1000)))
        self.state = SiteState(config.get("state_file", str(DEFAULT_STATE_FILE)))
        self.connections = {}
        self.last_control_id = str(load_control().get("id", ""))
        self.config_mtime = CONFIG_FILE.stat().st_mtime_ns if CONFIG_FILE.exists() else 0

    def current_config(self):
        try:
            current_mtime = CONFIG_FILE.stat().st_mtime_ns
            if current_mtime != self.config_mtime:
                self.config = load_config()
                self.config_mtime = current_mtime
            return self.config
        except (OSError, ValueError):
            return self.config

    async def state_loop(self):
        """Coalesce reconnect storms into at most one small state write per second."""
        while True:
            await asyncio.sleep(1)
            if self.state.dirty and time.time() - self.state.last_write >= 1:
                self.state.write()

    def disconnect_matching(self, port, miner_ip=""):
        matched = 0
        for connection in list(self.connections.values()):
            if int(connection["port"]) == int(port) and (not miner_ip or connection["miner_ip"] == miner_ip):
                connection["writer"].close()
                matched += 1
        return matched

    async def control_loop(self):
        while True:
            await asyncio.sleep(1)
            command = load_control()
            command_id = str(command.get("id", ""))
            if not command_id or command_id == self.last_control_id:
                continue
            self.last_control_id = command_id
            if command.get("action") == "reconnect":
                matched = self.disconnect_matching(command.get("port", 0), str(command.get("source_ip", "")))
                logging.info("relay reconnect requested port=%s miner=%s matched=%s", command.get("port"), command.get("source_ip") or "all", matched)

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername") or ("0.0.0.0", 1)
        upstream_writer = None
        client = None
        active_counted = False
        # Do not let excess or abusive connections wait inside Python while
        # retaining TLS stream buffers. Existing authenticated miners keep
        # their slots; callers above the configured ceiling reconnect later.
        if self.semaphore.locked():
            logging.warning("relay connection limit reached source=%s", peer[0])
            await close_writer(writer)
            return
        async with self.semaphore:
            try:
                raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
                if len(raw) > MAX_HEADER:
                    raise ValueError("header too large")
                port, supplied_token, headers = parse_request(raw)
                client = authenticate(self.current_config(), supplied_token)
                client_version = headers.get("x-client-version", "")
                local_watchdog = is_local_watchdog(port, headers, peer)
                if not local_watchdog:
                    self.state.update(client, peer, client_version=client_version,
                        reported_miner_count=optional_count(headers, "x-miner-count"),
                        reported_connections=optional_count(headers, "x-active-connections"),
                        current_vps=headers.get("x-current-vps", ""),
                        last_share=optional_timestamp(headers, "x-last-share"),
                        reconnect_count=optional_count(headers, "x-reconnect-count"),
                        action_id=headers.get("x-last-action-id", ""),
                        action_status=headers.get("x-last-action-status", ""))
                if port is None:
                    action = None if local_watchdog else take_client_action(client["id"])
                    response = "HTTP/1.1 200 OK\r\nContent-Length: 0\r\n"
                    if action:
                        response += "X-Client-Action: " + action["action"] + "\r\nX-Action-Id: " + action["id"] + "\r\n"
                    writer.write((response + "\r\n").encode("ascii"))
                    await writer.drain()
                    return
                miner_ip = source_address(headers, peer)
                internal_port = route_map(miner_ip).get(port)
                if not internal_port:
                    raise ValueError("route is not enabled")
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", internal_port), timeout=10
                )
                upstream_writer.write(proxy_header(headers, peer, port))
                await upstream_writer.drain()
                writer.write(b"HTTP/1.1 200 Connection Established\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                self.state.update(client, peer, active_delta=1, miner_ip=miner_ip, client_version=client_version)
                active_counted = True
                connection_key = id(writer)
                self.connections[connection_key] = {"writer": writer, "port": port, "miner_ip": miner_ip}
                logging.info("relay connected client=%s source=%s miner=%s port=%s", client["id"], peer[0], headers.get("x-miner-ip", "?"), port)
                tasks = (asyncio.create_task(pipe(reader, upstream_writer)),
                         asyncio.create_task(pipe(upstream_reader, writer)))
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*done, *pending, return_exceptions=True)
            except PermissionError:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                logging.warning("rejected unauthenticated connection source=%s", peer[0])
            except (ValueError, OSError, ssl.SSLError, asyncio.TimeoutError,
                    asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
                logging.info("connection closed source=%s reason=%s", peer[0], exc)
            finally:
                self.connections.pop(id(writer), None)
                if active_counted and client:
                    self.state.update(client, peer, active_delta=-1, miner_ip=miner_ip)
                await close_writer(upstream_writer)
                await close_writer(writer)


def load_control():
    try:
        return json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


async def main():
    config = load_config()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config["certificate"], config["private_key"])
    relay = SecureRelay(config)
    server = await asyncio.start_server(
        relay.handle,
        config["listen_host"],
        int(config["listen_port"]),
        ssl=context,
        backlog=256,
        ssl_handshake_timeout=10,
        limit=MAX_HEADER,
    )
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    logging.info("secure relay listening on %s", addresses)
    control_task = asyncio.create_task(relay.control_loop())
    state_task = asyncio.create_task(relay.state_loop())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            loop.add_signal_handler(getattr(signal, name), stop.set)
    stop_task = asyncio.create_task(stop.wait())
    async with server:
        done, _ = await asyncio.wait(
            (stop_task, control_task, state_task), return_when=asyncio.FIRST_COMPLETED
        )
    failure = None
    for task in (control_task, state_task):
        if task in done and not task.cancelled():
            failure = task.exception() or RuntimeError("critical relay background task stopped")
            break
    for task in (stop_task, control_task, state_task):
        task.cancel()
    await asyncio.gather(stop_task, control_task, state_task, return_exceptions=True)
    if relay.state.dirty:
        relay.state.write()
    if failure:
        raise failure


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
