#!/usr/bin/env python3
"""Send actionable WeChat alerts when a known mine-site client goes offline."""

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


CONFIG_FILE = Path(os.getenv("SECURE_RELAY_CONFIG", "/etc/stratum-secure-relay.json"))
STATE_FILE = Path(os.getenv("SECURE_RELAY_STATE", "/var/lib/stratum-secure-relay/sites.json"))
MONITOR_FILE = Path(os.getenv("SECURE_RELAY_MONITOR_STATE", "/var/lib/stratum-secure-relay/monitor.json"))
EVENT_FILE = Path(os.getenv("SECURE_RELAY_EVENT_FILE", "/var/lib/stratum-secure-relay/events.jsonl"))
EVENT_MAX_BYTES = int(os.getenv("SECURE_RELAY_EVENT_MAX_BYTES", str(8 * 1024 * 1024)))
EVENT_KEEP_LINES = int(os.getenv("SECURE_RELAY_EVENT_KEEP_LINES", "5000"))
ENV_FILE = Path("/etc/stratum-v3.env")


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def webhook():
    value = os.getenv("WECHAT_WEBHOOK", "")
    if value:
        return value
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("WECHAT_WEBHOOK="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def notify(url, content):
    if not url:
        return
    payload = json.dumps({"msgtype": "text", "text": {"content": content}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def save_monitor(value):
    MONITOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = MONITOR_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o640)
    os.replace(temporary, MONITOR_FILE)


def append_event(kind, client_id, name, message, now, site=None):
    site = site or {}
    EVENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EVENT_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": int(now), "type": kind, "client_id": client_id,
            "site": name, "message": message, "miner_count": int(site.get("last_miner_count", site.get("miner_count", 0)) or 0),
            "connections": int(site.get("active", 0) or 0)}, ensure_ascii=False) + "\n")
    if EVENT_FILE.stat().st_size > EVENT_MAX_BYTES:
        with EVENT_FILE.open("rb") as handle:
            handle.seek(max(0, EVENT_FILE.stat().st_size - EVENT_MAX_BYTES))
            tail = handle.read().splitlines()[-EVENT_KEEP_LINES:]
        temporary = EVENT_FILE.with_suffix(".tmp")
        temporary.write_bytes(b"\n".join(tail) + b"\n")
        os.chmod(temporary, 0o640)
        os.replace(temporary, EVENT_FILE)


def recently_emitted(kind, client_id, now, window):
    if window <= 0:
        return False
    try:
        lines = EVENT_FILE.read_text(encoding="utf-8").splitlines()[-500:]
    except OSError:
        return False
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        event_time = int(event.get("time", 0) or 0)
        if event_time and now - event_time > window:
            break
        if event.get("type") == kind and str(event.get("client_id", "")) == client_id:
            return True
    return False


def duplicate_notification(notifications, kind, client_id, now, window):
    item = notifications.get(client_id, {})
    if window > 0 and item.get("kind") == kind and now - int(item.get("time", 0) or 0) <= window:
        return True
    return recently_emitted(kind, client_id, now, window)


def check_once(now=None):
    now = int(now or time.time())
    config = read_json(CONFIG_FILE, {})
    clients = config.get("clients") or []
    if not clients and config.get("token"):
        clients = [{"id": "default", "name": "默认矿场", "enabled": True}]
    sites = read_json(STATE_FILE, {}).get("sites", {})
    monitor = read_json(MONITOR_FILE, {"started_at": now, "clients": {}})
    statuses = monitor.setdefault("clients", {})
    pending = monitor.setdefault("pending", {})
    notifications = monitor.setdefault("notifications", {})
    offline_after = int(config.get("offline_after_seconds", 180))
    offline_checks = max(1, min(10, int(config.get("offline_confirm_checks", 2))))
    recovery_checks = max(1, min(10, int(config.get("recovery_confirm_checks", 2))))
    dedup_seconds = max(0, int(config.get("notification_dedup_seconds", 600)))
    events = []
    for client in clients:
        if not client.get("enabled", True) or not client.get("alert_enabled", True):
            continue
        client_id = str(client.get("id", "default"))
        name = str(client.get("name", client_id))
        site = sites.get(client_id, {})
        last_seen = int(site.get("last_seen", 0))
        offline = (last_seen and now - last_seen > offline_after) or (not last_seen and now - int(monitor["started_at"]) > offline_after)
        previous = statuses.get(client_id, "waiting")
        observed = "offline" if offline else ("online" if last_seen else "waiting")
        current = observed
        transition = ((previous != "offline" and observed == "offline") or
            (previous == "offline" and observed == "online"))
        if transition:
            item = pending.get(client_id, {})
            count = int(item.get("count", 0)) + 1 if item.get("target") == observed else 1
            pending[client_id] = {"target": observed, "count": count, "since": int(item.get("since", now)) if item.get("target") == observed else now}
            required = offline_checks if observed == "offline" else recovery_checks
            if count < required:
                current = previous
            else:
                pending.pop(client_id, None)
        else:
            pending.pop(client_id, None)
        if current == "offline" and previous != "offline":
            when = datetime.fromtimestamp(last_seen, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S") if last_seen else "从未连接"
            message = f"【木林森中转离线】\n矿场：{name}\n最后连接：{when}\n请检查值守电脑、流量卡和VPS线路。"
            if not duplicate_notification(notifications, "site_offline", client_id, now, dedup_seconds):
                events.append(message)
                append_event("site_offline", client_id, name, message, now, site)
                notifications[client_id] = {"kind": "site_offline", "time": now}
        elif current == "online" and previous == "offline":
            message = f"【木林森中转恢复】\n矿场：{name}\n客户端心跳已经恢复。"
            if not duplicate_notification(notifications, "site_recovered", client_id, now, dedup_seconds):
                events.append(message)
                append_event("site_recovered", client_id, name, message, now, site)
                notifications[client_id] = {"kind": "site_recovered", "time": now}
        statuses[client_id] = current
    save_monitor(monitor)
    return events


def main():
    while True:
        try:
            for event in check_once():
                notify(webhook(), event)
        except Exception as exc:
            print(f"monitor error: {exc}", flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
