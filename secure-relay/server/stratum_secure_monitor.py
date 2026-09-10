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


def check_once(now=None):
    now = int(now or time.time())
    config = read_json(CONFIG_FILE, {})
    clients = config.get("clients") or []
    if not clients and config.get("token"):
        clients = [{"id": "default", "name": "默认矿场", "enabled": True}]
    sites = read_json(STATE_FILE, {}).get("sites", {})
    monitor = read_json(MONITOR_FILE, {"started_at": now, "clients": {}})
    statuses = monitor.setdefault("clients", {})
    offline_after = int(config.get("offline_after_seconds", 180))
    events = []
    for client in clients:
        if not client.get("enabled", True) or not client.get("alert_enabled", True):
            continue
        client_id = str(client.get("id", "default"))
        name = str(client.get("name", client_id))
        last_seen = int(sites.get(client_id, {}).get("last_seen", 0))
        offline = (last_seen and now - last_seen > offline_after) or (not last_seen and now - int(monitor["started_at"]) > offline_after)
        previous = statuses.get(client_id, "waiting")
        current = "offline" if offline else ("online" if last_seen else "waiting")
        if current == "offline" and previous != "offline":
            when = datetime.fromtimestamp(last_seen, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S") if last_seen else "从未连接"
            events.append(f"【木林森中转离线】\n矿场：{name}\n最后连接：{when}\n请检查值守电脑、流量卡和VPS线路。")
        elif current == "online" and previous == "offline":
            events.append(f"【木林森中转恢复】\n矿场：{name}\n客户端心跳已经恢复。")
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
