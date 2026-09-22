#!/usr/bin/env python3
"""Conservative one-shot watchdog for an unattended Stratum VPS.

systemd already restarts crashed processes.  This helper covers the harder
case where a process still exists but its local health endpoint or state
writer has stopped responding.  A service is restarted only after three
consecutive failed checks and no more than once per five minutes.
"""

import json
import os
import socket
import ssl
import subprocess
import tempfile
import time
from pathlib import Path

from endpoint_monitor import Notifier


STATE_FILE = Path(os.getenv("VPS_WATCHDOG_STATE", "/var/lib/stratum-monitor/vps-watchdog.json"))
RELAY_CONFIG = Path(os.getenv("SECURE_RELAY_CONFIG", "/etc/stratum-secure-relay.json"))
INSPECTOR_STATE = Path(os.getenv("INSPECTOR_STATE_FILE", "/var/lib/stratum-inspector/state.json"))
V3_CONFIG = Path(os.getenv("V3_CONFIG_FILE", "/etc/stratum-v3.json"))
EVENT_FILE = Path(os.getenv("ENDPOINT_EVENT_FILE", "/var/lib/stratum-monitor/endpoint-events.jsonl"))
FAILURE_THRESHOLD = 3
RESTART_COOLDOWN = 300


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def service_active(name):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", name], timeout=10, check=False
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def relay_health(timeout=5):
    config = read_json(RELAY_CONFIG, {})
    clients = config.get("clients") or []
    if not clients and config.get("token"):
        clients = [{"token": config["token"], "enabled": True}]
    token = next((str(item.get("token", "")) for item in clients if item.get("enabled", True)), "")
    port = int(config.get("listen_port", 0) or 0)
    empty_clients = config.get("clients") == [] and not config.get("token")
    if (not token and not empty_clients) or not port:
        return False, "加密中转配置缺少端口或可用密钥"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname="localhost") as connection:
                connection.settimeout(timeout)
                if empty_clients:
                    return True, "尚未配置矿场，TLS 监听检查通过，跳过密钥认证检查"
                request = (
                    "CONNECT /relay/v2/health HTTP/1.1\r\n"
                    "Host: localhost\r\n"
                    f"Authorization: Bearer {token}\r\n"
                    "X-Health-Origin: vps-watchdog\r\n"
                    "\r\n"
                ).encode("ascii")
                connection.sendall(request)
                response = b""
                while b"\r\n\r\n" not in response and len(response) <= 8192:
                    block = connection.recv(4096)
                    if not block:
                        break
                    response += block
        return response.startswith(b"HTTP/1.1 200 "), "加密入口未返回健康响应"
    except (OSError, ssl.SSLError, ValueError) as exc:
        return False, f"加密入口无响应：{str(exc)[:160]}"


def inspector_health(now=None, maximum_age=120):
    now = now or time.time()
    try:
        age = max(0, now - INSPECTOR_STATE.stat().st_mtime)
        if age <= maximum_age:
            return True, ""
        return False, f"矿机状态已 {int(age)} 秒没有更新"
    except OSError as exc:
        return False, f"矿机状态文件不可用：{str(exc)[:160]}"


def admin_health(timeout=5):
    try:
        with socket.create_connection(("127.0.0.1", 8789), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(b"GET /login HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            response = connection.recv(64)
        return response.startswith(b"HTTP/"), "管理面板未返回 HTTP 响应"
    except OSError as exc:
        return False, f"管理面板无响应：{str(exc)[:160]}"


def http_health(port, path="/healthz", timeout=5):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode("ascii"))
            response = connection.recv(64)
        return response.startswith(b"HTTP/"), f"本机端口 {port} 未返回 HTTP 响应"
    except OSError as exc:
        return False, f"本机端口 {port} 无响应：{str(exc)[:160]}"


def listening_ports():
    result = set()
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in Path(name).read_text(encoding="ascii").splitlines()[1:]:
                fields = line.split()
                if len(fields) > 3 and fields[3] == "0A":
                    result.add(int(fields[1].rsplit(":", 1)[1], 16))
        except (OSError, ValueError, IndexError):
            continue
    return result


def listener_health(ports, label, observed=None):
    observed = listening_ports() if observed is None else observed
    missing = sorted(set(map(int, ports)) - observed)
    return (not missing, "" if not missing else f"{label}缺少监听端口：" + "、".join(map(str, missing)))


def expected_route_ports(config):
    ports = []
    for group in config.get("port_groups", []):
        for port in group.get("ports", []):
            try:
                if int(port) > 0:
                    ports.append(int(port))
            except (TypeError, ValueError):
                continue
    for item in config.get("fixed_routes", []):
        try:
            if int(item.get("port", 0)) > 0:
                ports.append(int(item["port"]))
        except (TypeError, ValueError, KeyError):
            continue
    return ports


def port_owner_summary(ports):
    details = []
    for port in sorted(set(ports)):
        try:
            result = subprocess.run(["ss", "-H", "-ltnp", f"sport = :{port}"], capture_output=True,
                text=True, timeout=5, check=False)
            value = " ".join((result.stdout or "").split())[:180]
            if value:
                details.append(f"{port}={value}")
        except (OSError, subprocess.TimeoutExpired):
            pass
    return "；".join(details)


def collect_checks(now=None):
    config = read_json(V3_CONFIG, {})
    observed = listening_ports()
    routes = expected_route_ports(config)
    inspector_ports = [20000 + index for index, item in enumerate(config.get("endpoints", []))
        if item.get("enabled", True)]
    def inspector_probe():
        ports_ok = listener_health(inspector_ports, "内部检查器", observed)
        return inspector_health(now) if ports_ok[0] else ports_ok
    probes = {
        "stratum-inspector-v3.service": inspector_probe,
        "stratum-admin.service": admin_health,
        "stratum-public-status.service": lambda: http_health(8790),
        "stratum-endpoint-monitor.service": None,
        "stratum-route-switch-monitor.service": None,
        "stratum-security-monitor.service": None,
        "haproxy.service": lambda: listener_health(routes, "矿机转发", observed),
    }
    expected = {"stratum-admin.service": [8789], "stratum-public-status.service": [8790],
        "stratum-inspector-v3.service": inspector_ports, "haproxy.service": routes}
    if RELAY_CONFIG.exists():
        probes["stratum-secure-relay.service"] = relay_health
        probes["stratum-secure-monitor.service"] = None
        relay_port = int(read_json(RELAY_CONFIG, {}).get("listen_port", 0) or 0)
        expected["stratum-secure-relay.service"] = [relay_port] if relay_port else []
    checks = {}
    for service, probe in probes.items():
        if not service_active(service):
            occupied = sorted(set(expected.get(service, [])) & observed)
            owner = port_owner_summary(occupied) if occupied else ""
            message = "systemd 显示服务未运行"
            if owner:
                message += "；预留端口已被其他进程占用：" + owner
            checks[service] = (False, message)
        elif probe:
            checks[service] = probe()
        else:
            checks[service] = (True, "")
    return checks


def evaluate(checks, state, now, restart):
    services = state.setdefault("services", {})
    events = state.setdefault("events", [])
    for service, (healthy, message) in checks.items():
        entry = services.setdefault(service, {"failures": 0, "last_restart": 0, "last_error": ""})
        if healthy:
            if entry.get("failures", 0):
                events.append({"time": int(now), "service": service, "type": "recovered",
                    "message": "本机健康检查已经恢复"})
            entry["failures"] = 0
            entry["last_error"] = ""
            entry["last_ok"] = int(now)
            continue
        entry["failures"] = int(entry.get("failures", 0)) + 1
        entry["last_error"] = message
        entry["last_failure"] = int(now)
        if (entry["failures"] >= FAILURE_THRESHOLD
                and now - int(entry.get("last_restart", 0)) >= RESTART_COOLDOWN):
            restarted = restart(service)
            entry["last_restart"] = int(now)
            events.append({"time": int(now), "service": service,
                "type": "restarted" if restarted else "restart_failed", "message": message})
    state["events"] = events[-200:]
    state["updated_at"] = int(now)
    return state


def restart_service(service):
    try:
        result = subprocess.run(["systemctl", "restart", service], timeout=30, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def main():
    now = time.time()
    state = read_json(STATE_FILE, {"version": 1, "services": {}, "events": []})
    evaluate(collect_checks(now), state, now, restart_service)
    atomic_json(STATE_FILE, state)
    current = [event for event in state.get("events", []) if event.get("time") == int(now)
        and event.get("type") in {"restarted", "restart_failed"}]
    if current:
        failed = [event for event in current if event["type"] == "restart_failed"]
        message = f"健康检查触发 {len(current)} 个服务恢复：" + "、".join(
            event["service"] for event in current)
        if failed:
            message += f"；其中 {len(failed)} 个重启失败，需要人工处理"
        Notifier(settings=os.environ, event_path=EVENT_FILE)({"time": int(now),
            "type": "vps_auto_recovery", "endpoint": "VPS 服务恢复", "message": message})


if __name__ == "__main__":
    main()
