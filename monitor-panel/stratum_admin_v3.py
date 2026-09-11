#!/usr/bin/env python3
"""Authenticated V3 administration panel for Stratum relay configuration."""

import json
import hashlib
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from ipaddress import ip_address
from urllib.parse import urlsplit
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template, render_template_string, request, session, url_for
from werkzeug.security import check_password_hash

from endpoint_monitor import Notifier, beijing_time, probe_stratum
from security_monitor import atomic_write as write_integrity, load as load_integrity, snapshot
from v3_manager import ConfigError, ConfigStore, render_haproxy_config, render_inspector_config, route_map, validate_config


CONFIG_FILE = Path(os.getenv("V3_CONFIG_FILE", "/etc/stratum-v3.json"))
STATE_FILE = Path(os.getenv("ENDPOINT_STATE_FILE", "/var/lib/stratum-monitor/endpoints.json"))
INSPECTOR_STATE_FILE = Path(os.getenv("INSPECTOR_STATE_FILE", "/var/lib/stratum-inspector/state.json"))
HISTORY_DIR = Path(os.getenv("V3_HISTORY_DIR", "/var/lib/stratum-monitor/history"))
AUDIT_FILE = Path(os.getenv("V3_AUDIT_FILE", "/var/log/stratum-audit.jsonl"))
INSPECTOR_CONFIG = Path(os.getenv("INSPECTOR_CONFIG_FILE", "/etc/stratum-inspector.json"))
HAPROXY_CONFIG = Path(os.getenv("HAPROXY_V3_CONFIG", "/etc/haproxy/stratum-v3.cfg"))
INTEGRITY_BASELINE = Path(os.getenv("INTEGRITY_BASELINE_FILE", "/var/lib/stratum-monitor/integrity.json"))
ENV_FILE = Path(os.getenv("MONITOR_ENV_FILE", "/etc/stratum-monitor.env"))
CRON_FILE = Path(os.getenv("MONITOR_CRON_FILE", "/etc/cron.d/stratum-monitor"))
ENDPOINT_EVENT_FILE = Path(os.getenv("ENDPOINT_EVENT_FILE", "/var/lib/stratum-monitor/endpoint-events.jsonl"))
SECURITY_STATE_FILE = Path(os.getenv("INTEGRITY_STATE_FILE", "/var/lib/stratum-monitor/security-state.json"))
PUBLIC_IP_ALERT_STATE_FILE = Path(os.getenv("PUBLIC_IP_ALERT_STATE_FILE", "/var/lib/stratum-monitor/public-ip-alert.json"))
RELAY_CONTROL_FILE = Path(os.getenv("SECURE_RELAY_CONTROL", "/var/lib/stratum-secure-relay/control.json"))
PEER_SYNC_FILE = Path(os.getenv("V3_PEER_SYNC_FILE", "/etc/stratum-v3-peer.json"))
PEER_OUTBOX_FILE = Path(os.getenv("V3_PEER_OUTBOX_FILE", "/var/lib/stratum-monitor/peer-sync-outbox.json"))
PEER_STATE_FILE = Path(os.getenv("V3_PEER_STATE_FILE", "/var/lib/stratum-monitor/peer-sync-state.json"))

app = Flask(__name__)
app.secret_key = os.environ.get("PANEL_SECRET_KEY", secrets.token_hex(32))
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", MAX_CONTENT_LENGTH=65536)
store = ConfigStore(CONFIG_FILE, HISTORY_DIR, AUDIT_FILE)


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def read_env():
    values = {}
    if not ENV_FILE.exists():
        return values
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


def write_env(updates):
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    output, seen = [], set()
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in updates.items() if key not in seen)
    ConfigStore._atomic_write(ENV_FILE, "\n".join(output) + "\n", mode=0o600)


def clamp_int(value, minimum, maximum):
    number = int(value)
    if not (minimum <= number <= maximum):
        raise ValueError
    return number


def monitor_enabled():
    try:
        return any("stratum-monitor.sh" in line and not line.lstrip().startswith("#") for line in CRON_FILE.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def set_monitor_enabled(enabled):
    content = "SHELL=/bin/bash\nPATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
    content += "* * * * * root /root/stratum-monitor.sh\n" if enabled else "# Monitoring paused from V3 panel\n"
    ConfigStore._atomic_write(CRON_FILE, content, mode=0o644)


def server_metrics():
    result = {"load": "-", "memory": "-", "disk": "-", "uptime": "-"}
    try:
        result["load"] = " / ".join(f"{value:.2f}" for value in os.getloadavg())
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0])
        total, available = values["MemTotal"], values["MemAvailable"]
        result["memory"] = f"{(total-available)*100/total:.1f}%"
        disk = shutil.disk_usage("/")
        result["disk"] = f"{disk.used*100/disk.total:.1f}%"
        seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
        result["uptime"] = f"{seconds//86400}天 {(seconds%86400)//3600}小时"
    except (AttributeError, OSError, KeyError, ValueError, ZeroDivisionError):
        pass
    return result


def recent_logs():
    command = ["journalctl", "-u", "haproxy", "-u", "stratum-inspector-v3", "-u", "stratum-endpoint-monitor", "-u", "stratum-security-monitor", "-n", "100", "--no-pager"]
    try:
        journal = subprocess.run(command, capture_output=True, text=True, timeout=6, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        journal = "日志暂不可用"
    events = []
    if ENDPOINT_EVENT_FILE.exists():
        events = ENDPOINT_EVENT_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
    return {"journal": journal or "暂无服务日志", "events": "\n".join(events) or "暂无报警事件"}


def parse_custom_target(value):
    value = value.strip()
    if not value:
        raise ConfigError("自定义地址不能为空")
    parsed = urlsplit(value if "://" in value else "//" + value)
    if parsed.scheme and parsed.scheme not in {"stratum+tcp", "tcp"}:
        raise ConfigError("自定义地址仅支持 stratum+tcp")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ConfigError("自定义地址只能包含域名和端口")
    if not parsed.hostname or not parsed.port:
        raise ConfigError("请按 host:port 或 stratum+tcp://host:port 输入")
    return parsed.hostname.lower(), int(parsed.port)


def ensure_custom_endpoint(config, target, label):
    host, port = parse_custom_target(target)
    for endpoint in config["endpoints"]:
        if endpoint["host"].lower() == host and int(endpoint["port"]) == port:
            return endpoint["id"]
    endpoint_id = "custom-" + hashlib.sha256(f"{host}:{port}".encode()).hexdigest()[:12]
    endpoint = {"id": endpoint_id, "pool": "自定义", "region": label, "host": host, "port": port,
        "transport": "tcp", "source": "panel", "enabled": True}
    config["endpoints"].append(endpoint)
    validate_config(config, resolve=True)
    result = probe_stratum(endpoint)
    if not result.get("ok"):
        raise ConfigError(f"自定义地址 TCP 可达，但未通过 Stratum 验证：{result.get('error', '未收到有效响应')}")
    return endpoint_id


def endpoint_algorithm(config, endpoint):
    declared = str(endpoint.get("algorithm", "")).strip().lower()
    if declared:
        return declared
    found = {
        str(template.get("algorithm", "unknown")).lower()
        for template in config.get("templates", [])
        if endpoint.get("id") in template.get("endpoint_ids", [])
    }
    return found.pop() if len(found) == 1 else "unknown"


def algorithm_text(value):
    return {"scrypt": "Scrypt", "sha256d": "SHA-256", "other": "其他", "unknown": "尚未确认"}.get(value, value)


def locate_route(config, port):
    for group in config.get("port_groups", []):
        if port in group.get("ports", []):
            return "group", group, group["ports"].index(port)
    for route in config.get("fixed_routes", []):
        if int(route.get("port", 0)) == int(port):
            return "fixed", route, None
    raise ConfigError(f"端口 {port} 不在当前转发配置中")


def route_endpoint_id(config, port):
    kind, item, position = locate_route(config, port)
    return item["endpoint_ids"][position] if kind == "group" else item["endpoint_id"]


def set_route_endpoint(config, port, endpoint_id):
    kind, item, position = locate_route(config, port)
    previous = item["endpoint_ids"][position] if kind == "group" else item["endpoint_id"]
    if kind == "group":
        item["endpoint_ids"][position] = endpoint_id
        item["template_id"] = ""
    else:
        item["endpoint_id"] = endpoint_id
    return previous


def request_reconnect(port, source_ip=""):
    command = {"id": secrets.token_hex(12), "action": "reconnect", "port": int(port),
        "source_ip": str(source_ip), "created_at": int(time.time())}
    ConfigStore._atomic_write(RELAY_CONTROL_FILE, json.dumps(command, ensure_ascii=False) + "\n", mode=0o640)


def endpoint_miner_totals(inspector, endpoint_id, source_ip, public_port):
    total = {"submitted": 0, "accepted": 0, "rejected": 0, "active": 0}
    for pool in inspector.get("pools", []):
        if pool.get("id") != endpoint_id:
            continue
        for worker in pool.get("workers", []):
            for detail in worker.get("details", []):
                if detail.get("source_ip") != source_ip:
                    continue
                observed_port = detail.get("public_port")
                if observed_port is not None and int(observed_port) != int(public_port):
                    continue
                for key in ("submitted", "accepted", "rejected"):
                    total[key] += int(detail.get(key, 0) or 0)
                if detail.get("status") == "在线":
                    total["active"] += 1
    return total


def forwarding_rows(config, endpoint_state, inspector):
    endpoint_map = {item["id"]: item for item in config.get("endpoints", [])}
    canary_map = {int(item["port"]): item for item in config.get("canary_routes", [])}
    change_map = {}
    for item in route_change_history(config):
        change_map.setdefault(int(item["port"]), item)
    rows = []
    for public_port, endpoint_id, _, group_name in route_map(config):
        endpoint = endpoint_map[endpoint_id]
        health = endpoint_state.get("endpoints", {}).get(endpoint_id, {})
        result = health.get("last_result", {})
        connections = 0
        miner_ips = set()
        for pool in inspector.get("pools", []):
            if pool.get("id") != endpoint_id:
                continue
            for connection in pool.get("connections", []):
                observed_port = connection.get("public_port")
                if observed_port is None or int(observed_port) == int(public_port):
                    connections += 1
                    if connection.get("source_ip"):
                        miner_ips.add(str(connection["source_ip"]))
        row = {"port": public_port, "group": group_name, "endpoint_id": endpoint_id, "endpoint": endpoint,
            "algorithm": endpoint_algorithm(config, endpoint), "health_ok": bool(result.get("ok")),
            "latency": result.get("stratum_ms"), "connections": connections,
            "miner_ips": sorted(miner_ips, key=lambda value: tuple(int(part) for part in value.split("."))),
            "canary": None, "last_change": None}
        change = change_map.get(int(public_port))
        if change and change.get("endpoint_id") == endpoint_id and change.get("previous_endpoint_id") in endpoint_map:
            row["last_change"] = {**change, "previous_endpoint": endpoint_map[change["previous_endpoint_id"]]}
        canary = canary_map.get(int(public_port))
        if canary:
            target = endpoint_map.get(canary.get("endpoint_id"), {})
            current = endpoint_miner_totals(inspector, canary.get("endpoint_id"), canary.get("source_ip"), public_port)
            baseline = canary.get("baseline", {})
            delta = {key: max(0, int(current.get(key, 0)) - int(baseline.get(key, 0))) for key in ("submitted", "accepted", "rejected")}
            row["canary"] = {**canary, "endpoint": target, "delta": delta,
                "elapsed_minutes": max(0, int((time.time() - int(canary.get("started_at", time.time()))) / 60)),
                "ready": delta["accepted"] > 0, "active": current["active"]}
        rows.append(row)
    return rows


def online_miner_ips(inspector):
    values = set()
    for pool in inspector.get("pools", []):
        for connection in pool.get("connections", []):
            value = str(connection.get("source_ip", ""))
            try:
                if ip_address(value).version == 4:
                    values.add(value)
            except ValueError:
                pass
    return sorted(values, key=lambda value: tuple(int(part) for part in value.split(".")))


def authorized():
    return session.get("authenticated") is True


def csrf_ok():
    return secrets.compare_digest(session.get("csrf", ""), request.form.get("csrf", ""))


def actor():
    return request.remote_addr or "panel"


def service_state(name):
    try:
        result = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=3, check=False)
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def public_ipv4(value):
    try:
        address = ip_address(str(value).strip())
    except ValueError:
        return ""
    if address.version == 4 and address.is_global:
        return str(address)
    return ""


def detect_relay_public_ip(config):
    configured = str(config.get("settings", {}).get("relay_public_host", "")).strip()
    if public_ipv4(configured):
        return {"ok": True, "host": configured, "source": "config", "message": ""}
    commands = [
        (["ip", "-4", "route", "get", "1.1.1.1"], "default-route"),
        (["hostname", "-I"], "hostname"),
    ]
    errors = []
    for command, source in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=2, check=False)
        except (OSError, subprocess.SubprocessError):
            errors.append(" ".join(command))
            continue
        tokens = result.stdout.replace("\n", " ").split()
        if "src" in tokens:
            index = tokens.index("src")
            if index + 1 < len(tokens):
                detected = public_ipv4(tokens[index + 1])
                if detected:
                    return {"ok": True, "host": detected, "source": source, "message": ""}
        for token in tokens:
            detected = public_ipv4(token)
            if detected:
                return {"ok": True, "host": detected, "source": source, "message": ""}
        if result.stderr.strip():
            errors.append(result.stderr.strip()[:160])
    message = "无法从默认出公网路由或主机地址中识别 VPS 公网 IPv4"
    if errors:
        message += "；" + "；".join(errors[:2])
    return {"ok": False, "host": "", "source": "", "message": message}


def notify_public_ip_missing(status, now=None):
    now = int(now or time.time())
    state = load_json(PUBLIC_IP_ALERT_STATE_FILE, {})
    if now - int(state.get("last_alert", 0) or 0) < 3600:
        return False
    event = {
        "time": now,
        "type": "public_ip_missing",
        "endpoint": "VPS公网IP",
        "message": status.get("message") or "当前 VPS 公网 IP 无法获取",
    }
    ENDPOINT_EVENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ENDPOINT_EVENT_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    webhook = read_env().get("WECHAT_WEBHOOK", "")
    if webhook.startswith("https://"):
        content = (
            "【严重】VPS 公网 IP 无法获取\n"
            f"时间（北京时间）：{beijing_time(now)}\n"
            "影响说明：管理面板无法确认当前 VPS 公网入口，因此不会生成矿机可复制的 stratum 转发地址，避免把 Tailscale 或管理面板访问域名错误复制到矿机。\n"
            f"详细信息：{event['message']}\n"
            "建议检查：服务器公网网卡、默认路由、云厂商弹性公网 IP 绑定状态，以及 `ip -4 route get 1.1.1.1` 的输出。"
        )
        payload = json.dumps({"msgtype": "text", "text": {"content": content}}, ensure_ascii=False).encode()
        try:
            req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=8).close()
        except OSError:
            pass
    ConfigStore._atomic_write(PUBLIC_IP_ALERT_STATE_FILE, json.dumps({"last_alert": now}, ensure_ascii=False) + "\n", mode=0o600)
    return True


def port_listening(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.15):
            return True
    except OSError:
        return False


def overview_summary(pools):
    workers = [worker for pool in pools for worker in pool.get("workers", [])]
    summary = {
        "connections": sum(int(pool.get("summary", {}).get("connections", 0) or 0) for pool in pools),
        "online_workers": sum(1 for worker in workers if worker.get("status") == "online"),
        "recent_workers": sum(1 for worker in workers if worker.get("status") == "recent"),
        "offline_workers": sum(1 for worker in workers if worker.get("status") == "offline"),
        "invalid_workers": sum(1 for worker in workers if worker.get("status") == "invalid"),
        "submitted": sum(int(pool.get("summary", {}).get("submitted", 0) or 0) for pool in pools),
        "accepted": sum(int(pool.get("summary", {}).get("accepted", 0) or 0) for pool in pools),
        "rejected": sum(int(pool.get("summary", {}).get("rejected", 0) or 0) for pool in pools),
    }
    total = summary["submitted"]
    summary["reject_percent"] = round(summary["rejected"] * 100 / total, 2) if total else 0
    return summary


def audit_rows(limit=30):
    if not AUDIT_FILE.exists():
        return []
    rows = []
    for line in AUDIT_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            item = json.loads(line)
            item["display_time"] = beijing_time(item.get("time", 0))
            rows.append(item)
        except ValueError:
            continue
    return list(reversed(rows))


def stratum_pool_rows(config, inspector):
    states = {pool.get("id"): pool for pool in inspector.get("pools", [])}
    public_by_endpoint = {}
    for public_port, endpoint_id, _, group_name in route_map(config):
        public_by_endpoint.setdefault(endpoint_id, []).append({"port": public_port, "group": group_name})
    pools = {}
    for endpoint in config["endpoints"]:
        state = states.get(endpoint["id"], {})
        route = (state.get("routes") or [{}])[0]
        pool = pools.setdefault(endpoint["pool"], {"name": endpoint["pool"], "routes": [], "workers": [], "anomalies": [],
            "summary": {"connections": 0, "workers": 0, "submitted": 0, "accepted": 0, "rejected": 0}})
        summary = state.get("summary", {})
        pool["routes"].append({"region": endpoint.get("region", "默认"), "target": f"{endpoint['host']}:{endpoint['port']}",
            "public_ports": ", ".join(f":{item['port']}" for item in public_by_endpoint.get(endpoint["id"], [])) or "未分配",
            "ok": bool(route.get("upstream_ok")), "latency": route.get("upstream_latency_ms"),
            "connections": summary.get("connections", 0)})
        pool["workers"].extend(state.get("workers", []))
        pool["anomalies"].extend(state.get("anomalies", []))
        for key in pool["summary"]:
            pool["summary"][key] += int(summary.get(key, 0) or 0)
    status_order = {"online": 0, "recent": 1, "offline": 2, "invalid": 3}
    for pool in pools.values():
        merged = {}
        for worker in pool["workers"]:
            row = merged.setdefault(worker["name"], {
                "name": worker["name"], "agents": set(), "sources": set(), "active": 0,
                "submitted": 0, "accepted": 0, "rejected": 0, "latency_total": 0,
                "latency_weight": 0, "hashrate_value": 0.0, "last_share": "尚未提交",
                "last_seen": "", "inactive_seconds": 0, "status": "invalid",
                "status_text": "失效", "details": [], "last_error": "", "_initialized": False,
            })
            if worker.get("agent"):
                row["agents"].add(worker["agent"])
            row["sources"].update(worker.get("sources", []))
            row["active"] += int(worker.get("active", 0))
            for key in ("submitted", "accepted", "rejected"):
                row[key] += int(worker.get(key, 0))
            samples = int(worker.get("submitted", 0))
            row["latency_total"] += float(worker.get("latency_ms", 0)) * samples
            row["latency_weight"] += samples
            row["hashrate_value"] += float(worker.get("hashrate_value", 0))
            if worker.get("last_share") and worker["last_share"] != "尚未提交":
                row["last_share"] = max(row["last_share"], worker["last_share"])
            row["last_seen"] = max(row["last_seen"], worker.get("last_seen", ""))
            row["last_error"] = worker.get("last_error") or row["last_error"]
            if not row["_initialized"] or status_order.get(worker.get("status"), 4) < status_order.get(row["status"], 4):
                row["status"] = worker["status"]
                row["status_text"] = worker.get("status_text", worker["status"])
                row["inactive_seconds"] = int(worker.get("inactive_seconds", 0))
            elif worker.get("status") == row["status"]:
                row["inactive_seconds"] = min(row["inactive_seconds"], int(worker.get("inactive_seconds", 0)))
            for detail in worker.get("details", []):
                row["details"].append({
                    **detail,
                    "route": worker.get("route", "默认"),
                    "listen_port": worker.get("listen_port", ""),
                })
            row["_initialized"] = True
        pool["workers"] = []
        for row in merged.values():
            submitted = row["submitted"]
            rejected = row["rejected"]
            row["agent"] = " / ".join(sorted(row.pop("agents"))) or "未知"
            row["sources"] = sorted(row["sources"])
            row["reject_percent"] = round(rejected * 100 / submitted, 2) if submitted else 0
            latency_total = row.pop("latency_total")
            latency_weight = row.pop("latency_weight")
            row["latency_ms"] = round(latency_total / latency_weight) if latency_weight else 0
            row["hashrate"] = format_hashrate_value(row.pop("hashrate_value"))
            row.pop("_initialized", None)
            row["details"].sort(key=lambda item: (item["status"] != "在线", item["route"], item["source_ip"], item["source_port"]))
            pool["workers"].append(row)
        pool["workers"].sort(key=lambda item: (status_order.get(item["status"], 4), item["name"]))
        pool["summary"]["workers"] = sum(1 for item in pool["workers"] if item["status"] == "online")
        pool["worker_groups"] = {
            "online": [item for item in pool["workers"] if item["status"] in {"online", "recent"}],
            "offline": [item for item in pool["workers"] if item["status"] == "offline"],
            "invalid": [item for item in pool["workers"] if item["status"] == "invalid"],
        }
    return list(pools.values())


def format_hashrate_value(value):
    value = max(0.0, float(value))
    for unit in ("H/s", "KH/s", "MH/s", "GH/s", "TH/s", "PH/s"):
        if value < 1000 or unit == "PH/s":
            return f"{value:.2f} {unit}"
        value /= 1000


def read_actual_route_ids():
    try:
        content = HAPROXY_CONFIG.read_text(encoding="utf-8")
    except OSError:
        return {}
    result = {}
    pattern = re.compile(r"^backend stratum_v3_(\d+)_.*?\n\s*# .*? -> ([^\s]+)", re.MULTILINE)
    for match in pattern.finditer(content):
        result[int(match.group(1))] = match.group(2)
    return result


def route_page_groups(config, endpoint_state, relay_status=None):
    endpoint_map = {item["id"]: item for item in config["endpoints"]}
    actual_ids = read_actual_route_ids()
    relay_status = relay_status or detect_relay_public_ip(config)
    host = relay_status.get("host", "")
    groups = []
    for group in config["port_groups"]:
        rows = []
        for port, endpoint_id in zip(group["ports"], group["endpoint_ids"]):
            endpoint = endpoint_map[endpoint_id]
            actual_id = actual_ids.get(port)
            actual = endpoint_map.get(actual_id)
            health = endpoint_state.get("endpoints", {}).get(endpoint_id, {})
            last = health.get("last_result", {})
            rows.append({"port": port, "endpoint_id": endpoint_id, "endpoint": endpoint,
                "actual_id": actual_id, "actual": actual, "synced": actual_id == endpoint_id,
                "health_ok": bool(last.get("ok")), "latency": last.get("stratum_ms"),
                "listening": port_listening(port), "relay_url": f"stratum+tcp://{host}:{port}" if host else "",
                "last_check": beijing_time(health["last_check"]) if health.get("last_check") else "等待检测"})
        groups.append({**group, "rows": rows})
    return groups


def save_and_reload(config, action, actor_value=None):
    validate_config(config)
    previous = store.load()
    previous_inspector = render_inspector_config(previous)
    next_inspector = render_inspector_config(config)
    rendered_haproxy = render_haproxy_config(config)
    if os.getenv("V3_RELOAD_SERVICES", "0") == "1":
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".cfg", delete=False) as handle:
            handle.write(rendered_haproxy)
            candidate = handle.name
        try:
            result = subprocess.run(["haproxy", "-c", "-f", candidate], capture_output=True, text=True, timeout=15, check=False)
            if result.returncode:
                raise ConfigError((result.stderr or result.stdout or "HAProxy 配置检查失败")[-500:])
        finally:
            os.unlink(candidate)
    store.save(config, actor=actor_value or actor(), action=action)
    ConfigStore._atomic_write(INSPECTOR_CONFIG, json.dumps(next_inspector, ensure_ascii=False, indent=2) + "\n")
    ConfigStore._atomic_write(HAPROXY_CONFIG, rendered_haproxy, mode=0o644)
    if os.getenv("V3_RELOAD_SERVICES", "0") == "1":
        previous_ids = {item["id"] for item in previous_inspector["relays"]}
        new_ports = [item["listen_port"] for item in next_inspector["relays"] if item["id"] not in previous_ids]
        deadline = time.monotonic() + 10
        for port in new_ports:
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        store.save(previous, actor="automatic", action=f"auto-rollback:{action}")
                        ConfigStore._atomic_write(INSPECTOR_CONFIG, json.dumps(previous_inspector, ensure_ascii=False, indent=2) + "\n")
                        ConfigStore._atomic_write(HAPROXY_CONFIG, render_haproxy_config(previous), mode=0o644)
                        raise ConfigError(f"新的内部检查器端口 {port} 未能启动，配置未切换")
                    time.sleep(0.25)
        result = subprocess.run(["systemctl", "reload", "haproxy"], capture_output=True, text=True, timeout=15, check=False)
        if result.returncode:
            store.save(previous, actor="automatic", action=f"auto-rollback:{action}")
            ConfigStore._atomic_write(INSPECTOR_CONFIG, json.dumps(render_inspector_config(previous), ensure_ascii=False, indent=2) + "\n")
            ConfigStore._atomic_write(HAPROXY_CONFIG, render_haproxy_config(previous), mode=0o644)
            subprocess.run(["systemctl", "reload", "haproxy"], capture_output=True, timeout=15, check=False)
            raise ConfigError((result.stderr or "HAProxy 平滑重载失败，已自动回滚")[-500:])
    if INTEGRITY_BASELINE.exists():
        baseline = load_integrity(INTEGRITY_BASELINE, {})
        baseline.update(snapshot([str(CONFIG_FILE), str(INSPECTOR_CONFIG), str(HAPROXY_CONFIG)]))
        write_integrity(INTEGRITY_BASELINE, baseline)


def approve_integrity(paths):
    if INTEGRITY_BASELINE.exists():
        baseline = load_integrity(INTEGRITY_BASELINE, {})
        baseline.update(snapshot([str(path) for path in paths]))
        write_integrity(INTEGRITY_BASELINE, baseline)


LOGIN = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>中转管理登录</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#edf1f3;color:#17232b;font:15px system-ui}.box{width:min(380px,calc(100% - 30px));background:#fff;border:1px solid #d8e0e4;border-radius:8px;padding:26px}h1{font-size:20px;margin:0 0 18px}label{display:block;color:#68757d;margin-bottom:6px}input,button{width:100%;box-sizing:border-box;padding:11px;border-radius:5px;font:inherit}input{border:1px solid #b9c4ca}button{border:0;background:#1268d8;color:#fff;font-weight:700;margin-top:14px}.err{color:#b52f2f;margin-bottom:12px}</style></head>
<body><form class="box" method="post"><h1>Stratum V3 管理</h1>{% if error %}<div class="err">{{error}}</div>{% endif %}<label>管理密码</label><input name="password" type="password" required autofocus><button>登录</button></form></body></html>
"""


PAGE = """
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Stratum V3 中转管理</title>
<style>
:root{--bg:#f2f5f6;--panel:#fff;--ink:#17232b;--muted:#65737c;--line:#dbe2e6;--blue:#1268d8;--green:#148454;--red:#bd3434;--amber:#a16410}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}.top{background:#16242d;color:#fff}.wrap{width:min(1240px,calc(100% - 28px));margin:auto}.top .wrap{min-height:62px;display:flex;align-items:center;justify-content:space-between}.top h1{font-size:18px;margin:0}.top a{color:#fff;text-decoration:none;margin-left:15px}.tabs{display:flex;gap:18px;overflow:auto;border-bottom:1px solid var(--line);padding-top:18px}.tabs a{padding:9px 1px;color:var(--muted);text-decoration:none;white-space:nowrap}.tabs a:first-child{color:var(--ink);border-bottom:2px solid var(--blue)}main{padding-bottom:42px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:14px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:17px}.wide{grid-column:1/-1}h2{font-size:16px;margin:0 0 13px}h3{font-size:14px;margin:0}.muted{color:var(--muted)}.small{font-size:12px}.status-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.status{border-left:3px solid var(--green);padding:8px 10px;background:#f6f8f9}.status.bad{border-color:var(--red)}.groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.group{border:1px solid var(--line);border-radius:6px;padding:14px}.row{display:grid;grid-template-columns:82px 1fr;gap:9px;align-items:center;margin-top:9px}.row code{font-weight:700}select,input{width:100%;padding:9px;border:1px solid #bac5cb;border-radius:5px;background:#fff;font:inherit}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}button{border:0;border-radius:5px;padding:9px 13px;background:var(--blue);color:#fff;font-weight:650;cursor:pointer}.secondary{background:#53636d}.danger{background:var(--red)}table{width:100%;border-collapse:collapse;min-width:820px}th,td{text-align:left;padding:9px 8px;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--muted);font-size:12px}.table{overflow:auto}.good{color:var(--green);font-weight:700}.bad-text{color:var(--red);font-weight:700}.warn{color:var(--amber);font-weight:700}.flash{margin-top:14px;padding:10px 12px;border-radius:5px;background:#e5f3eb;color:#12673f}.metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:1px;background:var(--line)}.metric{background:#fff;padding:12px}.metric b{display:block;font-size:18px}.inline{display:flex;gap:8px;align-items:end}.inline>div{flex:1}.badge{display:inline-block;padding:2px 7px;border-radius:10px;background:#e9eef1;font-size:11px}
pre{margin:8px 0 0;background:#101a20;color:#dce8ee;border-radius:6px;padding:13px;overflow:auto;max-height:380px;font:12px/1.55 ui-monospace,monospace;white-space:pre-wrap}.panel form{margin:0}
@media(max-width:780px){.grid,.groups{grid-template-columns:1fr}.wide{grid-column:auto}.status-grid,.metrics{grid-template-columns:repeat(2,1fr)}.panel{padding:14px}.row{grid-template-columns:68px 1fr}.inline{display:grid;grid-template-columns:1fr}.top .wrap{align-items:flex-start;padding:14px 0}.top h1{font-size:16px}.top a{font-size:12px}}
</style></head><body><header class="top"><div class="wrap"><h1>Stratum V3 中转管理</h1><div><a href="{{url_for('index')}}">刷新</a><a href="{{url_for('logout')}}">退出</a></div></div></header><main class="wrap">
<nav class="tabs"><a href="#overview">总览</a><a href="#groups">端口组</a><a href="#stratum">Stratum</a><a href="#endpoints">地址检测</a><a href="#templates">模板</a><a href="#alerts">报警设置</a><a href="#logs">日志</a><a href="#history">审计与回滚</a></nav>
{% for message in get_flashed_messages() %}<div class="flash">{{message}}</div>{% endfor %}
<div class="grid"><section class="panel wide" id="overview"><h2>运行状态</h2><div class="status-grid">{% for name,value in services.items() %}<div class="status {{'bad' if value not in ['active','unavailable'] else ''}}"><b>{{name}}</b><div class="small muted">{{value}}</div></div>{% endfor %}</div><div class="metrics" style="margin-top:12px"><div class="metric"><span class="muted">地址总数</span><b>{{config.endpoints|length}}</b></div><div class="metric"><span class="muted">在线</span><b>{{online}}</b></div><div class="metric"><span class="muted">报警中</span><b>{{alerting}}</b></div><div class="metric"><span class="muted">活跃探测</span><b>{{(config.settings.active_probe_seconds // 60 ~ ' 分钟') if config.settings.get('endpoint_probe_enabled', True) else '已关闭'}}</b></div><div class="metric"><span class="muted">未用模板探测</span><b>{{(config.settings.template_probe_seconds // 60 ~ ' 分钟') if config.settings.get('endpoint_probe_enabled', True) else '已关闭'}}</b></div></div><div class="metrics" style="margin-top:1px"><div class="metric"><span class="muted">负载 1/5/15</span><b>{{server.load}}</b></div><div class="metric"><span class="muted">内存</span><b>{{server.memory}}</b></div><div class="metric"><span class="muted">磁盘</span><b>{{server.disk}}</b></div><div class="metric"><span class="muted">运行时间</span><b>{{server.uptime}}</b></div><div class="metric"><span class="muted">旧监控</span><b>{{'运行中' if legacy_enabled else '已暂停'}}</b></div></div></section>

<section class="panel wide" id="groups"><h2>动态端口组</h2><div class="groups">{% for group in config.port_groups %}<form class="group" method="post" action="{{url_for('save_group',group_id=group.id)}}"><input type="hidden" name="csrf" value="{{csrf}}"><h3>{{group.name}}</h3><div class="small muted">可选择地址库，也可为任一端口输入自定义 host:port；自定义地址会先验证再保存。</div><div class="row"><span>整组模板</span><select name="template_id">{% for template in config.templates %}<option value="{{template.id}}" {{'selected' if template.id==group.template_id}}>{{template.name}}</option>{% endfor %}</select></div>{% for port in group.ports %}<div class="row"><code>:{{port}}</code><div><select name="endpoint_{{loop.index0}}">{% for endpoint in config.endpoints %}<option value="{{endpoint.id}}" {{'selected' if endpoint.id==group.endpoint_ids[loop.index0]}}>{{endpoint.pool}} · {{endpoint.region}} · {{endpoint.host}}:{{endpoint.port}}</option>{% endfor %}<option value="__custom__">自定义输入…</option></select><input name="custom_{{loop.index0}}" style="margin-top:6px" placeholder="例如 pool.example.com:3333"></div></div>{% endfor %}<div class="actions"><button name="mode" value="ports">验证并保存端口</button><button class="secondary" name="mode" value="template">应用模板</button></div></form>{% endfor %}</div></section>

<section class="panel wide" id="stratum"><h2>Stratum 连接、Worker 与 Share</h2>{% for pool in pools %}<div class="group" style="margin-top:12px"><h3>{{pool.name}}</h3><div class="metrics" style="margin-top:10px"><div class="metric"><span class="muted">连接</span><b>{{pool.summary.connections}}</b></div><div class="metric"><span class="muted">Worker</span><b>{{pool.summary.workers}}</b></div><div class="metric"><span class="muted">提交</span><b>{{pool.summary.submitted}}</b></div><div class="metric"><span class="muted">接受</span><b>{{pool.summary.accepted}}</b></div><div class="metric"><span class="muted">拒绝</span><b>{{pool.summary.rejected}}</b></div></div><div class="table"><table><thead><tr><th>区域</th><th>公网端口</th><th>上游</th><th>状态</th><th>延迟</th><th>连接</th></tr></thead><tbody>{% for route in pool.routes %}<tr><td>{{route.region}}</td><td>{{route.public_ports}}</td><td>{{route.target}}</td><td class="{{'good' if route.ok else 'bad-text'}}">{{'正常' if route.ok else '不可达'}}</td><td>{{route.latency ~ ' ms' if route.latency is not none else '-'}}</td><td>{{route.connections}}</td></tr>{% endfor %}</tbody></table></div>{% if pool.workers %}<div class="table"><table><thead><tr><th>Worker</th><th>矿机软件</th><th>来源IP</th><th>状态</th><th>提交/接受/拒绝</th><th>拒绝率</th><th>估算算力</th><th>最近Share</th></tr></thead><tbody>{% for worker in pool.workers %}<tr><td><b>{{worker.name}}</b></td><td>{{worker.agent}}</td><td>{{worker.sources|join(', ')}}</td><td>{{worker.active}}</td><td>{{worker.submitted}} / {{worker.accepted}} / {{worker.rejected}}</td><td>{{worker.reject_percent}}%</td><td>{{worker.hashrate}}</td><td>{{worker.last_share}}</td></tr>{% endfor %}</tbody></table></div>{% endif %}{% if pool.anomalies %}<div class="bad-text small" style="margin-top:10px">最近协议异常：{% for item in pool.anomalies[-5:] %}<div>{{item.time}} · {{item.type}} · {{item.detail}}</div>{% endfor %}</div>{% endif %}</div>{% endfor %}</section>

<section class="panel wide" id="endpoints"><h2>地址稳定性</h2><div class="small muted" style="margin-bottom:10px">正在使用每30秒探活；仅保存在模板中每10分钟探活；每小时10轮采样，每日20轮长测。连续3次失败报警。</div><div class="table"><table><thead><tr><th>矿池/区域</th><th>地址</th><th>用途</th><th>状态</th><th>最近检测</th><th>24h成功率</th><th>P95</th><th>抖动</th><th>操作</th></tr></thead><tbody>{% for endpoint in endpoint_rows %}<tr><td><b>{{endpoint.pool}}</b><br><span class="small muted">{{endpoint.region}}</span></td><td>{{endpoint.host}}:{{endpoint.port}}</td><td><span class="badge">{{endpoint.usage}}</span></td><td class="{{'good' if endpoint.ok else 'bad-text'}}">{{'正常' if endpoint.ok else ('连续失败 '+endpoint.failures|string+' 次')}}</td><td>{{endpoint.last_check}}</td><td>{{endpoint.success}}</td><td>{{endpoint.p95}}</td><td>{{endpoint.jitter}}</td><td><form method="post" action="{{url_for('test_endpoint',endpoint_id=endpoint.id)}}"><input type="hidden" name="csrf" value="{{csrf}}"><button class="secondary">立即检测</button></form></td></tr>{% endfor %}</tbody></table></div></section>

<section class="panel wide" id="templates"><h2>挖矿模板</h2><div class="groups">{% for template in config.templates %}<form class="group" method="post" action="{{url_for('edit_template',template_id=template.id)}}"><input type="hidden" name="csrf" value="{{csrf}}"><div class="inline"><div><label class="small muted">模板名称</label><input name="name" value="{{template.name}}"></div><div><label class="small muted">结算模式</label><input name="settlement" value="{{template.settlement}}"></div></div>{% for number in range(3) %}<div class="row"><span>{{['主地址','备用1','备用2'][number]}}</span><select name="endpoint_{{number}}">{% for endpoint in config.endpoints %}<option value="{{endpoint.id}}" {{'selected' if endpoint.id==template.endpoint_ids[number]}}>{{endpoint.pool}} · {{endpoint.region}}</option>{% endfor %}</select></div>{% endfor %}<div class="actions"><button name="action" value="save">保存</button><button class="secondary" name="action" value="copy">复制</button><button class="danger" name="action" value="delete">删除</button></div></form>{% endfor %}</div><form method="post" action="{{url_for('create_template')}}" style="margin-top:14px"><input type="hidden" name="csrf" value="{{csrf}}"><div class="inline"><div><label class="small muted">模板名称</label><input name="name" required></div>{% for number in range(3) %}<div><label class="small muted">{{['主地址','备用1','备用2'][number]}}</label><select name="endpoint_{{number}}">{% for endpoint in config.endpoints %}<option value="{{endpoint.id}}">{{endpoint.pool}} · {{endpoint.region}}</option>{% endfor %}</select></div>{% endfor %}</div><div class="actions"><button>创建模板</button></div></form></section>

<section class="panel" id="alerts"><h2>报警设置</h2><form method="post" action="{{url_for('save_alerts')}}"><input type="hidden" name="csrf" value="{{csrf}}"><div class="row"><span>TCP失败次数</span><input name="failure_count" type="number" min="1" max="20" value="{{config.settings.failure_alert_count}}"></div><div class="row"><span>协议无响应次数</span><input name="protocol_failure_count" type="number" min="3" max="30" value="{{config.settings.get('protocol_failure_alert_count',6)}}"></div><div class="row"><span>重复间隔</span><input name="repeat_minutes" type="number" min="1" max="1440" value="{{config.settings.alert_repeat_seconds//60}}"></div><div class="row"><span>最低连接</span><input name="min_connections" type="number" min="0" max="100000" value="{{alert_settings.MIN_CONNECTIONS}}"></div><div class="row"><span>内存阈值%</span><input name="mem_threshold" type="number" min="1" max="99" value="{{alert_settings.MEM_THRESHOLD}}"></div><div class="row"><span>磁盘阈值%</span><input name="disk_threshold" type="number" min="1" max="99" value="{{alert_settings.DISK_THRESHOLD}}"></div><div class="actions"><button>保存报警设置</button></div></form></section>
<section class="panel"><h2>通知与监控控制</h2><p class="muted">企业微信：{{'已配置' if wechat_configured else '未配置'}}<br>旧版每分钟资源/连接监控：{{'运行中' if legacy_enabled else '已暂停'}}</p><div class="actions"><form method="post" action="{{url_for('test_wechat')}}"><input type="hidden" name="csrf" value="{{csrf}}"><button class="secondary">发送测试通知</button></form><form method="post" action="{{url_for('toggle_monitor')}}"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="enabled" value="{{0 if legacy_enabled else 1}}"><button class="{{'danger' if legacy_enabled else ''}}">{{'暂停旧监控' if legacy_enabled else '恢复旧监控'}}</button></form></div></section>

<section class="panel wide" id="logs"><h2>日志浏览</h2><h3>服务日志</h3><pre>{{logs.journal}}</pre><h3 style="margin-top:14px">报警事件</h3><pre>{{logs.events}}</pre></section>

<section class="panel" id="history"><h2>配置历史</h2>{% if history %}{% for name in history[:10] %}<form class="inline" method="post" action="{{url_for('rollback')}}" style="margin-top:7px"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="name" value="{{name}}"><code style="flex:1">{{name}}</code><button class="secondary">回滚</button></form>{% endfor %}{% else %}<p class="muted">暂无历史版本</p>{% endif %}</section>
<section class="panel"><h2>最近审计</h2>{% for row in audit %}<div style="padding:7px 0;border-bottom:1px solid var(--line)"><b>{{row.action}}</b><div class="small muted">{{row.display_time}} · {{row.actor}}</div></div>{% else %}<p class="muted">暂无审计记录</p>{% endfor %}</section>
</div></main></body></html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        expected = os.environ.get("PANEL_PASSWORD_HASH", "")
        if expected and check_password_hash(expected, request.form.get("password", "")):
            session.clear()
            session["authenticated"] = True
            session["csrf"] = secrets.token_urlsafe(24)
            return redirect(url_for("index"))
        error = "密码不正确"
    return render_template_string(LOGIN, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def build_page_context(page):
    config = store.load()
    state = load_json(STATE_FILE, {"endpoints": {}})
    inspector = load_json(INSPECTOR_STATE_FILE, {"pools": []})
    alert_settings = {"MIN_CONNECTIONS": "1", "MEM_THRESHOLD": "85", "DISK_THRESHOLD": "85", **read_env()}
    endpoint_map = {item["id"]: item for item in config["endpoints"]}
    active = {item["endpoint_id"] for item in config.get("fixed_routes", [])}
    for group in config["port_groups"]:
        active.update(group["endpoint_ids"])
    template_ids = {value for template in config["templates"] for value in template["endpoint_ids"]}
    rows = []
    for endpoint in config["endpoints"]:
        health = state.get("endpoints", {}).get(endpoint["id"], {})
        result = health.get("last_result", {})
        stats = health.get("stats_24h", {})
        checked = health.get("last_check", 0)
        rows.append({**endpoint, "usage": "正在使用" if endpoint["id"] in active else "仅模板" if endpoint["id"] in template_ids else "地址库",
            "ok": bool(result.get("ok")), "alerting": bool(health.get("alerting")), "failures": health.get("consecutive_failures", 0),
            "last_check": beijing_time(checked) if checked else "等待检测",
            "success": f"{stats['success_percent']}%" if stats.get("success_percent") is not None else "-",
            "p95": f"{stats['p95_ms']} ms" if stats.get("p95_ms") is not None else "-",
            "jitter": f"{stats['jitter_ms']} ms" if stats.get("jitter_ms") is not None else "-"})
    security = load_json(SECURITY_STATE_FILE, {"events": [], "active": {}})
    pools = stratum_pool_rows(config, inspector)
    relay_status = detect_relay_public_ip(config)
    route_groups = route_page_groups(config, state, relay_status)
    overview_routes = [row for group in route_groups for row in group["rows"]]
    active_forwarding = forwarding_rows(config, state, inspector)
    visible_forwarding = [row for row in active_forwarding if row["connections"] or row.get("canary")]
    if not visible_forwarding:
        visible_forwarding = active_forwarding
    peer_settings = load_peer_settings()
    peer_outbox = load_json(PEER_OUTBOX_FILE, {"items": []})
    return dict(page=page, config=config, endpoint_map=endpoint_map, endpoint_rows=rows,
        online=sum(1 for row in rows if row["ok"]), alerting=sum(1 for value in state.get("endpoints", {}).values() if value.get("alerting")),
        services={"HAProxy": service_state("haproxy"), "协议检查器": service_state("stratum-inspector-v3"),
            "稳定性监控": service_state("stratum-endpoint-monitor"),
            "自动切换": service_state("stratum-route-switch-monitor"),
            "安全监控": service_state("stratum-security-monitor")},
        server=server_metrics(), pools=pools, overview=overview_summary(pools), logs=recent_logs(),
        alert_settings=alert_settings, wechat_configured=read_env().get("WECHAT_WEBHOOK", "").startswith("https://"),
        legacy_enabled=monitor_enabled(), history=store.history(), audit=audit_rows(),
        route_groups=route_groups, overview_routes=overview_routes, relay_status=relay_status,
        active_forwarding=active_forwarding, visible_forwarding=visible_forwarding, miner_ips=online_miner_ips(inspector),
        endpoint_options=[{**endpoint, "algorithm_value": endpoint_algorithm(config, endpoint)} for endpoint in config["endpoints"]],
        verified_endpoints=[{**endpoint, "algorithm_value": endpoint_algorithm(config, endpoint)} for endpoint in config["endpoints"] if endpoint.get("verified")],
        route_history=route_history_rows(config), route_events=route_event_rows(),
        peer_settings=peer_settings, peer_pending=len(peer_outbox.get("items", [])),
        route_event_labels={"canary_started": "测试已开始", "canary_passed": "测试通过并切换",
            "canary_failed": "测试失败并退回", "canary_stopped": "测试已提前停止",
            "verified_route_applied": "已验证地址切换", "route_restored": "历史线路已恢复",
            "route_sync_queued": "全部线路已加入同步队列",
            "route_sync_ok": "备用VPS同步成功", "route_sync_failed": "备用VPS同步失败",
            "route_sync_received": "已接收VPS同步"},
        algorithms={"scrypt": "Scrypt", "sha256d": "SHA-256", "other": "其他/自定义", "unknown": "算法待确认"},
        security=security, csrf=session["csrf"])


@app.route("/")
def index():
    if not authorized():
        return redirect(url_for("login"))
    return redirect(url_for("dashboard_page", page="overview"))


@app.route("/<page>")
def dashboard_page(page):
    if not authorized():
        return redirect(url_for("login"))
    if page not in {"overview", "miners", "routes", "alerts", "settings", "logs"}:
        return "Not found", 404
    return render_template("v3_dashboard.html", **build_page_context(page))


@app.route("/group/<group_id>", methods=["POST"])
def save_group(group_id):
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    before_routes = {port: endpoint_id for port, endpoint_id, _, _ in route_map(config)}
    group = next((item for item in config["port_groups"] if item["id"] == group_id), None)
    if not group:
        return "Not found", 404
    try:
        template_id = request.form["template_id"]
        group["template_id"] = template_id
        if request.form.get("mode") == "template":
            template = next(item for item in config["templates"] if item["id"] == template_id)
            group["endpoint_ids"] = list(template["endpoint_ids"])
        else:
            endpoint_ids = []
            for number, port in enumerate(group["ports"]):
                selected = request.form[f"endpoint_{number}"]
                if selected == "__custom__":
                    selected = ensure_custom_endpoint(config, request.form.get(f"custom_{number}", ""), f"{group['name']} :{port}")
                endpoint_ids.append(selected)
            group["endpoint_ids"] = endpoint_ids
        save_and_reload(config, f"update-group:{group_id}")
        for changed_port, endpoint_id, _, _ in route_map(config):
            if before_routes.get(changed_port) != endpoint_id:
                queue_route_sync(config, changed_port, f"update-group:{group_id}")
        flash(f"{group['name']} 已保存；新连接使用新配置，已有连接继续保持。")
    except (KeyError, StopIteration, ConfigError, OSError) as exc:
        flash(f"保存失败：{exc}")
    return redirect(url_for("dashboard_page", page="routes"))


def locate_dynamic_route(config, port):
    for group in config["port_groups"]:
        if port in group["ports"]:
            return group, group["ports"].index(port)
    raise ConfigError(f"端口 {port} 不是可配置动态端口")


def endpoint_from_form(config, port):
    selected = request.form.get("endpoint_id", "")
    if selected == "__custom__":
        selected = ensure_custom_endpoint(config, request.form.get("custom_target", ""), f"端口 :{port}")
        endpoint = next(item for item in config["endpoints"] if item["id"] == selected)
        endpoint["pool"] = request.form.get("custom_pool", "自定义矿池").strip()[:80] or "自定义矿池"
        endpoint["region"] = request.form.get("custom_region", f"端口 :{port}").strip()[:80] or f"端口 :{port}"
        endpoint["algorithm"] = request.form.get("algorithm", "unknown").strip().lower()
        endpoint["coins"] = request.form.get("coins", "").strip()[:80]
        return selected
    if selected not in {item["id"] for item in config["endpoints"]}:
        raise ConfigError("请选择有效的地址库节点")
    return selected


def remember_route_change(config, port, previous_endpoint_id, endpoint_id):
    changes = list(reversed(route_change_history(config)))
    changes.append({"id": secrets.token_hex(8), "port": int(port),
        "previous_endpoint_id": previous_endpoint_id, "endpoint_id": endpoint_id,
        "changed_at": int(time.time())})
    config["route_change_history"] = changes[-10:]
    config.pop("last_route_changes", None)


def route_change_history(config):
    changes = config.get("route_change_history")
    if changes is None:
        changes = config.get("last_route_changes", [])
    return list(reversed(changes))


def route_history_rows(config):
    endpoint_map = {item["id"]: item for item in config.get("endpoints", [])}
    rows = []
    for change in route_change_history(config)[:10]:
        previous = endpoint_map.get(change.get("previous_endpoint_id"))
        endpoint = endpoint_map.get(change.get("endpoint_id"))
        if not previous or not endpoint:
            continue
        rows.append({**change, "id": change.get("id", ""), "previous_endpoint": previous,
            "endpoint": endpoint, "display_time": beijing_time(change.get("changed_at", 0))})
    return rows


def route_event_rows(limit=10):
    rows = []
    try:
        lines = ENDPOINT_EVENT_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rows
    kinds = {"canary_started", "canary_passed", "canary_failed", "canary_stopped",
        "verified_route_applied", "route_restored", "route_sync_queued", "route_sync_ok", "route_sync_failed", "route_sync_received"}
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") not in kinds:
            continue
        rows.append({**event, "display_time": beijing_time(event.get("time", 0))})
        if len(rows) >= limit:
            break
    return rows


def load_peer_settings():
    value = load_json(PEER_SYNC_FILE, {})
    return {"enabled": bool(value.get("enabled")), "peers": list(value.get("peers", []))[:4],
        "token": str(value.get("token", ""))}


def validate_peer_url(value):
    parsed = urlsplit(str(value).strip().rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ConfigError("同步地址必须是Tailscale提供的HTTPS地址")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigError("同步地址只填写VPS首页地址，不要附加路径或参数")
    try:
        address = ip_address(parsed.hostname)
    except ValueError:
        if not parsed.hostname.lower().endswith(".ts.net"):
            raise ConfigError("同步域名必须是Tailscale的 .ts.net 地址")
    else:
        if address.is_global:
            raise ConfigError("同步地址只能使用Tailscale或内网地址")
    return f"https://{parsed.netloc}"


def queue_route_sync(config, port, action):
    settings = load_peer_settings()
    if not settings["enabled"] or len(settings["token"]) < 32 or not settings["peers"]:
        return 0
    endpoint_id = route_endpoint_id(config, port)
    endpoint = next(item for item in config["endpoints"] if item["id"] == endpoint_id)
    allowed = ("id", "pool", "region", "host", "port", "algorithm", "coins", "transport",
        "source", "enabled", "verified", "verified_at", "verified_test")
    revision = time.time_ns()
    source = socket.gethostname()
    payload = {"event_id": secrets.token_hex(16), "created_at": int(time.time()), "revision": revision,
        "source": source, "port": int(port), "action": str(action)[:80],
        "endpoint": {key: endpoint[key] for key in allowed if key in endpoint}}
    outbox = load_json(PEER_OUTBOX_FILE, {"items": []})
    items = list(outbox.get("items", []))
    for peer in settings["peers"]:
        items.append({"id": secrets.token_hex(12), "peer": validate_peer_url(peer),
            "payload": payload, "attempts": 0, "next_attempt": 0})
    ConfigStore._atomic_write(PEER_OUTBOX_FILE,
        json.dumps({"items": items[-100:]}, ensure_ascii=False, indent=2) + "\n", mode=0o600)
    state = load_json(PEER_STATE_FILE, {"received": []})
    state.setdefault("route_versions", {})[str(int(port))] = {"revision": revision, "source": source}
    ConfigStore._atomic_write(PEER_STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2) + "\n", mode=0o600)
    return len(settings["peers"])


def apply_peer_payload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("endpoint"), dict):
        raise ConfigError("同步数据格式不正确")
    event_id = str(payload.get("event_id", ""))
    if not re.fullmatch(r"[a-f0-9]{32}", event_id):
        raise ConfigError("同步事件编号不合法")
    state = load_json(PEER_STATE_FILE, {"received": []})
    if event_id in state.get("received", []):
        return {"duplicate": True, "changed": False}
    port = int(payload.get("port", 0))
    source = str(payload.get("source", "vps"))[:80]
    revision = int(payload.get("revision", payload.get("created_at", 0)))
    current_version = state.get("route_versions", {}).get(str(port), {})
    incoming_order = (revision, source)
    current_order = (int(current_version.get("revision", -1)), str(current_version.get("source", "")))
    if incoming_order <= current_order:
        state["received"] = (list(state.get("received", [])) + [event_id])[-200:]
        ConfigStore._atomic_write(PEER_STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2) + "\n", mode=0o600)
        return {"duplicate": False, "stale": True, "changed": False}
    config = store.load()
    locate_route(config, port)
    supplied = payload["endpoint"]
    allowed = ("id", "pool", "region", "host", "port", "algorithm", "coins", "transport",
        "source", "enabled", "verified", "verified_at", "verified_test")
    endpoint = {key: supplied[key] for key in allowed if key in supplied}
    endpoint_id = str(endpoint.get("id", ""))
    if not endpoint_id or len(endpoint_id) > 100:
        raise ConfigError("同步矿池地址ID不合法")
    existing = next((item for item in config["endpoints"] if item["id"] == endpoint_id), None)
    if existing:
        existing.update(endpoint)
        endpoint = existing
    else:
        config["endpoints"].append(endpoint)
    current_id = route_endpoint_id(config, port)
    changed = current_id != endpoint_id
    if changed:
        set_route_endpoint(config, port, endpoint_id)
        remember_route_change(config, port, current_id, endpoint_id)
    config["canary_routes"] = [item for item in config.get("canary_routes", []) if int(item.get("port", 0)) != port]
    save_and_reload(config, f"peer-sync:{port}:{current_id}->{endpoint_id}", actor_value=f"peer:{source}")
    if changed:
        request_reconnect(port)
    state["received"] = (list(state.get("received", [])) + [event_id])[-200:]
    state.setdefault("route_versions", {})[str(port)] = {"revision": revision, "source": source}
    ConfigStore._atomic_write(PEER_STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2) + "\n", mode=0o600)
    send_route_event("route_sync_received", port, endpoint,
        f"已接收对端VPS的线路设置并{'完成切换' if changed else '确认一致'}。")
    return {"duplicate": False, "changed": changed}


def send_route_event(kind, port, endpoint, message, **extra):
    event = {"time": int(time.time()), "type": kind, "port": int(port),
        "endpoint_id": endpoint.get("id", ""), "endpoint": f"{endpoint.get('host')}:{endpoint.get('port')}",
        "pool": endpoint.get("pool", "未知矿池"), "region": endpoint.get("region", "未知区域"),
        "message": message, **extra}
    Notifier(read_env().get("WECHAT_WEBHOOK", ""), ENDPOINT_EVENT_FILE)(event)


@app.route("/route/<int:port>/test", methods=["POST"])
def test_route(port):
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    try:
        locate_route(config, port)
        selected = request.form.get("endpoint_id", "")
        if selected == "__custom__":
            host, upstream_port = parse_custom_target(request.form.get("custom_target", ""))
            endpoint = {"id": "test", "pool": "自定义", "region": f":{port}", "host": host,
                "port": upstream_port, "algorithm": request.form.get("algorithm", "unknown"),
                "transport": "tcp", "source": "panel", "enabled": True}
            candidate = json.loads(json.dumps(config))
            candidate["endpoints"].append(endpoint)
            validate_config(candidate, resolve=True)
        else:
            endpoint = next(item for item in config["endpoints"] if item["id"] == selected)
        result = probe_stratum(endpoint, username=request.form.get("test_username", "").strip(),
            password=request.form.get("test_password", ""))
        if result.get("ok"):
            auth = "；测试账号认证成功" if result.get("authorized") is True else (
                f"；测试账号认证失败：{result.get('authorization_error')}" if result.get("authorized") is False else "；未填写测试账号")
            flash(f"端口 {port} 候选上游测试成功：{endpoint['host']}:{endpoint['port']}，Stratum响应 {result['stratum_ms']} ms，算法资料为 {algorithm_text(endpoint_algorithm(config, endpoint))}{auth}。")
        else:
            flash(f"端口 {port} 候选上游测试未通过：{result.get('error', '未收到有效响应')}。配置未修改。")
    except (KeyError, StopIteration, ConfigError, OSError) as exc:
        flash(f"端口 {port} 测试失败：{exc}。配置未修改。")
    return redirect(url_for("dashboard_page", page=request.form.get("return_page", "routes")))


@app.route("/route/<int:port>/apply", methods=["POST"])
def apply_route(port):
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    original_config = json.loads(json.dumps(config))
    try:
        endpoint_id = endpoint_from_form(config, port)
        previous = set_route_endpoint(config, port, endpoint_id)
        remember_route_change(config, port, previous, endpoint_id)
        save_and_reload(config, f"update-port:{port}:{previous}->{endpoint_id}")
        actual = read_actual_route_ids().get(port)
        if actual != endpoint_id:
            save_and_reload(original_config, f"auto-rollback-port:{port}")
            raise ConfigError(f"保存后核验失败：配置目标为 {endpoint_id}，实际HAProxy目标为 {actual or '未找到'}")
        endpoint = next(item for item in config["endpoints"] if item["id"] == endpoint_id)
        queue_route_sync(config, port, f"update-port:{port}")
        flash(f"端口 {port} 已应用到 {endpoint['host']}:{endpoint['port']}；现有连接保持原目标，新连接使用新目标。")
    except (KeyError, StopIteration, ConfigError, OSError) as exc:
        flash(f"端口 {port} 应用失败：{exc}")
    return redirect(url_for("dashboard_page", page="routes"))


@app.route("/route/<int:port>/canary/start", methods=["POST"])
def start_canary(port):
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    try:
        current_id = route_endpoint_id(config, port)
        target_id = endpoint_from_form(config, port)
        if target_id == current_id:
            raise ConfigError("目标矿池与当前线路相同，不需要试切")
        source_ip = str(ip_address(request.form.get("source_ip", "").strip()))
        if ip_address(source_ip).version != 4 or ip_address(source_ip).is_global:
            raise ConfigError("请选择矿场局域网 IPv4")
        inspector = load_json(INSPECTOR_STATE_FILE, {"pools": []})
        eligible = next(row["miner_ips"] for row in forwarding_rows(config, {}, inspector) if row["port"] == port)
        if source_ip not in eligible:
            raise ConfigError(f"矿机 {source_ip} 当前没有连接端口 {port}，请刷新页面后重新选择")
        endpoint_map = {item["id"]: item for item in config["endpoints"]}
        current_algorithm = endpoint_algorithm(config, endpoint_map[current_id])
        target_algorithm = endpoint_algorithm(config, endpoint_map[target_id])
        if current_algorithm in {"unknown", "other"} or target_algorithm in {"unknown", "other"}:
            raise ConfigError("当前线路或目标矿池的算法尚未确认，不能开始安全试切")
        if current_algorithm != target_algorithm:
            raise ConfigError(f"算法不匹配：当前为 {algorithm_text(current_algorithm)}，目标为 {algorithm_text(target_algorithm)}")
        username = request.form.get("test_username", "").strip()
        result = probe_stratum(endpoint_map[target_id], username=username, password=request.form.get("test_password", ""))
        if not result.get("ok"):
            raise ConfigError(f"目标矿池未通过 Stratum 检测：{result.get('error', '未知错误')}")
        if username and result.get("authorized") is not True:
            raise ConfigError(f"测试账号认证失败：{result.get('authorization_error', '矿池拒绝登录')}")
        baseline = endpoint_miner_totals(inspector, target_id, source_ip, port)
        duration = 10
        canary = {"port": port, "source_ip": source_ip, "endpoint_id": target_id,
            "original_endpoint_id": current_id, "algorithm": target_algorithm, "started_at": int(time.time()),
            "review_after": int(time.time()) + duration * 60, "duration_minutes": duration,
            "auto_switch": True, "baseline": baseline}
        config["canary_routes"] = [item for item in config.get("canary_routes", []) if int(item.get("port", 0)) != port]
        config["canary_routes"].append(canary)
        save_and_reload(config, f"start-canary:{port}:{source_ip}:{current_id}->{target_id}")
        request_reconnect(port, source_ip)
        send_route_event("canary_started", port, endpoint_map[target_id],
            f"矿机 {source_ip} 已开始10分钟自动测试。", source_ip=source_ip)
        flash(f"已让矿机 {source_ip} 在端口 {port} 试运行 {endpoint_map[target_id]['pool']}；10分钟后系统会自动判定、切换或退回，并发送企业微信通知。")
    except (KeyError, StopIteration, ValueError, ConfigError, OSError) as exc:
        flash(f"无法开始单机试切：{exc}")
    return redirect(url_for("dashboard_page", page="overview"))


@app.route("/route/<int:port>/canary/stop", methods=["POST"])
def stop_canary(port):
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    canary = next((item for item in config.get("canary_routes", []) if int(item.get("port", 0)) == port), None)
    if canary:
        config["canary_routes"] = [item for item in config.get("canary_routes", []) if item is not canary]
        save_and_reload(config, f"stop-canary:{port}:{canary.get('source_ip')}")
        request_reconnect(port, canary.get("source_ip", ""))
        endpoint = next((item for item in config["endpoints"] if item["id"] == canary.get("endpoint_id")), {})
        send_route_event("canary_stopped", port, endpoint,
            f"矿机 {canary.get('source_ip', '')} 的测试已人工提前停止并返回原线路。",
            source_ip=canary.get("source_ip", ""))
        flash(f"端口 {port} 的单机试切已停止，测试矿机正在返回原线路。")
    return redirect(url_for("dashboard_page", page="overview"))


@app.route("/route/<int:port>/canary/promote", methods=["POST"])
def promote_canary(port):
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    try:
        canary = next(item for item in config.get("canary_routes", []) if int(item.get("port", 0)) == port)
        current = endpoint_miner_totals(load_json(INSPECTOR_STATE_FILE, {"pools": []}),
            canary["endpoint_id"], canary["source_ip"], port)
        accepted = max(0, current["accepted"] - int(canary.get("baseline", {}).get("accepted", 0)))
        if accepted < 1:
            raise ConfigError("测试矿机还没有收到已接受的 Share，暂不能全量切换")
        previous = set_route_endpoint(config, port, canary["endpoint_id"])
        remember_route_change(config, port, previous, canary["endpoint_id"])
        config["canary_routes"] = [item for item in config.get("canary_routes", []) if int(item.get("port", 0)) != port]
        save_and_reload(config, f"promote-canary:{port}:{previous}->{canary['endpoint_id']}")
        request_reconnect(port)
        endpoint = next(item for item in config["endpoints"] if item["id"] == canary["endpoint_id"])
        queue_route_sync(config, port, f"promote-canary:{port}")
        flash(f"端口 {port} 已全量切换到 {endpoint['pool']}；该端口现有矿机正在自动重连，其他端口不受影响。")
    except (KeyError, StopIteration, ValueError, ConfigError, OSError) as exc:
        flash(f"暂不能全量切换：{exc}")
    return redirect(url_for("dashboard_page", page="overview"))


@app.route("/route/<int:port>/restore", methods=["POST"])
def restore_route(port):
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    try:
        change_id = request.form.get("change_id", "")
        change = next(item for item in route_change_history(config)
            if int(item.get("port", 0)) == port and (not change_id or item.get("id", "") == change_id))
        previous_id = change["previous_endpoint_id"]
        endpoint = next(item for item in config["endpoints"] if item["id"] == previous_id)
        current_id = route_endpoint_id(config, port)
        if current_id == previous_id:
            raise ConfigError("该端口当前已经使用这条线路")
        set_route_endpoint(config, port, previous_id)
        remember_route_change(config, port, current_id, previous_id)
        config["canary_routes"] = [item for item in config.get("canary_routes", []) if int(item.get("port", 0)) != port]
        save_and_reload(config, f"restore-port:{port}:{current_id}->{previous_id}")
        request_reconnect(port)
        send_route_event("route_restored", port, endpoint,
            f"端口 {port} 已恢复到历史线路，矿机正在自动重连。")
        queue_route_sync(config, port, f"restore-port:{port}")
        flash(f"端口 {port} 已恢复到 {endpoint['pool']}；该端口矿机正在自动重连。")
    except (KeyError, StopIteration, ValueError, ConfigError, OSError) as exc:
        flash(f"恢复失败：{exc}")
    return redirect(url_for("dashboard_page", page="overview"))


@app.route("/route/<int:port>/verified/apply", methods=["POST"])
def apply_verified_route(port):
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    try:
        endpoint_id = request.form.get("endpoint_id", "")
        endpoint_map = {item["id"]: item for item in config["endpoints"]}
        endpoint = endpoint_map[endpoint_id]
        if not endpoint.get("verified"):
            raise ConfigError("该地址尚未通过10分钟单机测试，不能免验证直接切换")
        current_id = route_endpoint_id(config, port)
        if current_id == endpoint_id:
            raise ConfigError("所选地址已经是当前线路")
        current_algorithm = endpoint_algorithm(config, endpoint_map[current_id])
        target_algorithm = endpoint_algorithm(config, endpoint)
        if current_algorithm in {"unknown", "other"} or current_algorithm != target_algorithm:
            raise ConfigError(f"算法不匹配或尚未确认：当前为 {algorithm_text(current_algorithm)}，目标为 {algorithm_text(target_algorithm)}")
        previous = set_route_endpoint(config, port, endpoint_id)
        remember_route_change(config, port, previous, endpoint_id)
        save_and_reload(config, f"apply-verified:{port}:{previous}->{endpoint_id}")
        request_reconnect(port)
        send_route_event("verified_route_applied", port, endpoint,
            f"端口 {port} 已从已验证地址库直接切换，矿机正在自动重连。")
        queue_route_sync(config, port, f"apply-verified:{port}")
        flash(f"端口 {port} 已直接切换到已验证地址 {endpoint['pool']}；该端口矿机正在自动重连。")
    except (KeyError, StopIteration, ValueError, ConfigError, OSError) as exc:
        flash(f"直接切换失败：{exc}")
    return redirect(url_for("dashboard_page", page="overview"))


@app.route("/peer-settings", methods=["POST"])
def save_peer_settings():
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    try:
        enabled = request.form.get("enabled") == "1"
        peers = []
        for line in request.form.get("peers", "").splitlines():
            value = line.strip()
            if value:
                normalized = validate_peer_url(value)
                if normalized not in peers:
                    peers.append(normalized)
        if len(peers) > 4:
            raise ConfigError("最多配置4台对等VPS")
        token = request.form.get("token", "").strip()
        if enabled and not token:
            token = secrets.token_hex(32)
        if enabled and (len(token) < 32 or not peers):
            raise ConfigError("启用同步时必须填写对端地址和至少32位的共享同步密钥")
        ConfigStore._atomic_write(PEER_SYNC_FILE,
            json.dumps({"enabled": enabled, "peers": peers, "token": token}, ensure_ascii=False, indent=2) + "\n",
            mode=0o600)
        approve_integrity([PEER_SYNC_FILE])
        flash("VPS双向同步设置已保存。请确保另一台VPS填写相同同步密钥，并把本机地址填为其对端。")
    except (ValueError, ConfigError, OSError) as exc:
        flash(f"VPS同步设置保存失败：{exc}")
    return redirect(url_for("dashboard_page", page="settings"))


@app.route("/peer-sync-all", methods=["POST"])
def sync_all_routes():
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    try:
        config = store.load()
        routes = route_map(config)
        queued = sum(queue_route_sync(config, port, "sync-all") for port, _, _, _ in routes)
        if not queued:
            raise ConfigError("请先开启VPS双向同步，并填写对端地址和共享同步密钥")
        send_route_event("route_sync_queued", 0, {},
            f"本机全部 {len(routes)} 条线路已加入同步队列，共生成 {queued} 个同步任务。")
        flash(f"本机全部线路已加入同步队列（{queued}个任务），后台会自动发送并重试。")
    except (KeyError, StopIteration, ValueError, ConfigError, OSError) as exc:
        flash(f"全部线路同步失败：{exc}")
    return redirect(url_for("dashboard_page", page="settings"))


@app.route("/api/v3/route-sync", methods=["POST"])
def receive_route_sync():
    settings = load_peer_settings()
    authorization = request.headers.get("Authorization", "")
    supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not settings["enabled"] or len(settings["token"]) < 32 or not secrets.compare_digest(settings["token"], supplied):
        return "Not found", 404
    try:
        result = apply_peer_payload(request.get_json(silent=False))
        return jsonify({"ok": True, **result})
    except (KeyError, StopIteration, TypeError, ValueError, ConfigError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)[:300]}), 400


@app.route("/template/create", methods=["POST"])
def create_template():
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    try:
        name = request.form["name"].strip()[:80]
        template_id = "custom-" + secrets.token_hex(4)
        config["templates"].append({"id": template_id, "name": name, "algorithm": "scrypt", "settlement": "自定义",
            "endpoint_ids": [request.form[f"endpoint_{number}"] for number in range(3)],
            "worker_pattern": "{account}.{worker}", "password_pattern": "{password}", "enabled": True})
        save_and_reload(config, f"create-template:{template_id}")
        flash("模板已创建，保存的三个地址已纳入周期稳定性检测。")
    except (KeyError, ConfigError, OSError) as exc:
        flash(f"创建失败：{exc}")
    return redirect(url_for("dashboard_page", page="settings"))


@app.route("/template/<template_id>", methods=["POST"])
def edit_template(template_id):
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    template = next((item for item in config["templates"] if item["id"] == template_id), None)
    if not template:
        return "Not found", 404
    action = request.form.get("action", "save")
    try:
        in_use = any(group.get("template_id") == template_id for group in config["port_groups"])
        if action == "delete":
            if in_use:
                raise ConfigError("模板正在被端口组使用，不能删除")
            config["templates"] = [item for item in config["templates"] if item["id"] != template_id]
        else:
            updated = {**template, "name": request.form["name"].strip()[:80],
                "settlement": request.form["settlement"].strip()[:40],
                "endpoint_ids": [request.form[f"endpoint_{number}"] for number in range(3)]}
            if action == "copy":
                updated["id"] = "custom-" + secrets.token_hex(4)
                updated["name"] += " 副本"
                config["templates"].append(updated)
            else:
                template.update(updated)
        save_and_reload(config, f"{action}-template:{template_id}")
        flash("模板操作已完成，相关地址检测范围已同步更新。")
    except (KeyError, ConfigError, OSError) as exc:
        flash(f"模板操作失败：{exc}")
    return redirect(url_for("dashboard_page", page="settings"))


@app.route("/endpoint/<endpoint_id>/test", methods=["POST"])
def test_endpoint(endpoint_id):
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    endpoint = next((item for item in store.load()["endpoints"] if item["id"] == endpoint_id), None)
    if not endpoint:
        return "Not found", 404
    try:
        result = probe_stratum(endpoint)
        flash(f"检测成功：TCP {result['tcp_ms']} ms，Stratum {result['stratum_ms']} ms。")
    except OSError as exc:
        flash(f"检测失败：{exc}")
    return redirect(url_for("dashboard_page", page="settings"))


@app.route("/alerts", methods=["POST"])
def save_alerts():
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    try:
        failures = int(request.form["failure_count"])
        repeat_minutes = int(request.form["repeat_minutes"])
        minimum = int(request.form["min_connections"])
        memory = int(request.form["mem_threshold"])
        disk = int(request.form["disk_threshold"])
        if not (1 <= failures <= 20 and 1 <= repeat_minutes <= 1440 and 0 <= minimum <= 100000 and 1 <= memory <= 99 and 1 <= disk <= 99):
            raise ValueError
        config["settings"]["failure_alert_count"] = failures
        config["settings"]["alert_repeat_seconds"] = repeat_minutes * 60
        store.save(config, actor=actor(), action="update-alert-settings")
        write_env({"ALERT_INTERVAL": str(repeat_minutes * 60), "MIN_CONNECTIONS": str(minimum),
            "MEM_THRESHOLD": str(memory), "DISK_THRESHOLD": str(disk)})
        approve_integrity([CONFIG_FILE])
        flash("报警设置已保存，稳定性监控会自动重新载入。")
    except (KeyError, ValueError, ConfigError, OSError):
        flash("报警参数不合法，设置未保存。")
    return redirect(url_for("dashboard_page", page="alerts"))


@app.route("/probe-settings", methods=["POST"])
def save_probe_settings():
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    config = store.load()
    try:
        enabled = request.form.get("endpoint_probe_enabled") == "1"
        active_minutes = clamp_int(request.form.get("active_probe_minutes", "240"), 1, 10080)
        template_minutes = clamp_int(request.form.get("template_probe_minutes", "360"), 1, 10080)
        hourly_samples = clamp_int(request.form.get("hourly_samples", "0"), 0, 200)
        daily_samples = clamp_int(request.form.get("daily_samples", "0"), 0, 500)
        batch_enabled = request.form.get("endpoint_batch_probe_enabled") == "1"
        config.setdefault("settings", {})
        config["settings"]["endpoint_probe_enabled"] = enabled
        config["settings"]["active_probe_seconds"] = active_minutes * 60
        config["settings"]["template_probe_seconds"] = template_minutes * 60
        config["settings"]["endpoint_batch_probe_enabled"] = batch_enabled if enabled else False
        config["settings"]["hourly_samples"] = hourly_samples
        config["settings"]["daily_samples"] = daily_samples
        store.save(config, actor=actor(), action="update-endpoint-probe-settings")
        approve_integrity([CONFIG_FILE])
        if enabled:
            flash(f"地址自动探测已保存：正在使用地址每 {active_minutes} 分钟探测一次，模板地址每 {template_minutes} 分钟探测一次。")
        else:
            flash("地址自动探测已关闭；后台不会再持续 mining.subscribe 探测矿池，手动“立即检测”仍可使用。")
    except (KeyError, ValueError, ConfigError, OSError):
        flash("探测参数不合法，设置未保存。")
    return redirect(url_for("dashboard_page", page="settings"))


@app.route("/test-wechat", methods=["POST"])
def test_wechat():
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    webhook = read_env().get("WECHAT_WEBHOOK", "")
    if not webhook.startswith("https://"):
        flash("尚未配置有效的企业微信 Webhook。")
        return redirect(url_for("dashboard_page", page="alerts"))
    payload = json.dumps({"msgtype": "text", "text": {"content": f"Stratum V3 测试通知\n时间（北京时间）：{beijing_time(time.time())}"}}, ensure_ascii=False).encode()
    try:
        req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as response:
            result = json.loads(response.read())
        flash("测试通知发送成功。" if result.get("errcode") == 0 else f"企业微信返回错误：{result}")
    except (OSError, ValueError) as exc:
        flash(f"测试通知发送失败：{exc}")
    return redirect(url_for("dashboard_page", page="alerts"))


@app.route("/toggle-monitor", methods=["POST"])
def toggle_monitor():
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    enabled = request.form.get("enabled") == "1"
    try:
        set_monitor_enabled(enabled)
        approve_integrity([CRON_FILE])
        flash("旧版每分钟监控已恢复。" if enabled else "旧版每分钟监控已暂停。")
    except OSError as exc:
        flash(f"监控状态修改失败：{exc}")
    return redirect(url_for("dashboard_page", page="alerts"))


@app.route("/rollback", methods=["POST"])
def rollback():
    if not authorized() or not csrf_ok():
        return "Forbidden", 403
    try:
        name = request.form["name"]
        config = store.read_history(name)
        save_and_reload(config, f"rollback:{name}")
        flash("已恢复历史配置。")
    except (KeyError, OSError, ValueError) as exc:
        flash(f"回滚失败：{exc}")
    return redirect(url_for("dashboard_page", page="logs"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8789)
