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


STATE_FILE = Path(os.getenv("VPS_WATCHDOG_STATE", "/var/lib/stratum-monitor/vps-watchdog.json"))
RELAY_CONFIG = Path(os.getenv("SECURE_RELAY_CONFIG", "/etc/stratum-secure-relay.json"))
INSPECTOR_STATE = Path(os.getenv("INSPECTOR_STATE_FILE", "/var/lib/stratum-inspector/state.json"))
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
    if not token or not port:
        return False, "加密中转配置缺少端口或可用密钥"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname="localhost") as connection:
                connection.settimeout(timeout)
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


def collect_checks(now=None):
    probes = {
        "stratum-inspector-v3.service": lambda: inspector_health(now),
        "stratum-admin.service": admin_health,
        "stratum-endpoint-monitor.service": None,
        "stratum-route-switch-monitor.service": None,
        "stratum-security-monitor.service": None,
        "haproxy.service": None,
    }
    if RELAY_CONFIG.exists():
        probes["stratum-secure-relay.service"] = relay_health
        probes["stratum-secure-monitor.service"] = None
    checks = {}
    for service, probe in probes.items():
        if not service_active(service):
            checks[service] = (False, "systemd 显示服务未运行")
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


if __name__ == "__main__":
    main()
