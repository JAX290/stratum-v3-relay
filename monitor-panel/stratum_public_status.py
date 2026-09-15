#!/usr/bin/env python3
"""Public, read-only and deliberately redacted Stratum status page."""

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template


CONFIG_FILE = Path(os.getenv("V3_CONFIG_FILE", "/etc/stratum-v3.json"))
INSPECTOR_STATE_FILE = Path(os.getenv("INSPECTOR_STATE_FILE", "/var/lib/stratum-inspector/state.json"))
ENDPOINT_EVENT_FILE = Path(os.getenv("ENDPOINT_EVENT_FILE", "/var/lib/stratum-monitor/endpoint-events.jsonl"))
SECURE_RELAY_EVENT_FILE = Path(os.getenv("SECURE_RELAY_EVENT_FILE", "/var/lib/stratum-secure-relay/events.jsonl"))

SERVICES = {
    "haproxy": "流量转发",
    "stratum-inspector-v3": "矿机状态采集",
    "stratum-endpoint-monitor": "矿池线路监控",
    "stratum-secure-relay": "加密接入",
}

EVENT_LABELS = {
    "site_offline": ("矿场连接异常", "bad"),
    "site_recovered": ("矿场连接已恢复", "good"),
    "canary_started": ("线路测试已开始", "normal"),
    "canary_passed": ("线路测试通过", "good"),
    "canary_failed": ("线路测试未通过", "bad"),
    "canary_stopped": ("线路测试已停止", "normal"),
    "verified_route_applied": ("已切换到验证线路", "normal"),
    "route_restored": ("线路已恢复", "good"),
    "route_sync_queued": ("线路配置等待同步", "normal"),
    "route_sync_ok": ("线路配置同步完成", "good"),
    "route_sync_failed": ("线路配置同步失败", "bad"),
    "integrity_changed": ("系统文件检查异常", "bad"),
    "integrity_recovery": ("系统文件检查已恢复", "good"),
    "expiry_warning": ("资源即将到期", "bad"),
    "expiry_recovery": ("到期提醒已解除", "good"),
}

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=1024)


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def service_state(name):
    try:
        result = subprocess.run(["systemctl", "is-active", name], capture_output=True,
                                text=True, timeout=3, check=False)
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def beijing_time(value):
    try:
        moment = datetime.fromtimestamp(float(value), timezone.utc).astimezone(timezone(timedelta(hours=8)))
    except (TypeError, ValueError, OSError):
        return "时间未知"
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def public_events(limit=8):
    """Return only event category and time; never expose stored message or identifiers."""
    rows = []
    for path in (ENDPOINT_EVENT_FILE, SECURE_RELAY_EVENT_FILE):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = str(event.get("type", ""))
            if kind in EVENT_LABELS:
                label, level = EVENT_LABELS[kind]
                rows.append({"time": int(event.get("time", 0) or 0), "label": label, "level": level})
    rows.sort(key=lambda item: item["time"], reverse=True)
    return [{"display_time": beijing_time(row["time"]), "label": row["label"], "level": row["level"]}
            for row in rows[:limit]]


def status_snapshot():
    config = load_json(CONFIG_FILE, {"endpoints": []})
    inspector = load_json(INSPECTOR_STATE_FILE, {"pools": []})
    endpoint_to_pool = {str(item.get("id", "")): str(item.get("pool", "未分类"))
                        for item in config.get("endpoints", [])}
    pools = {}
    totals = {key: 0 for key in ("connections", "workers", "submitted", "accepted", "rejected")}
    for state in inspector.get("pools", []):
        name = endpoint_to_pool.get(str(state.get("id", "")), "未分类")
        row = pools.setdefault(name, {"name": name, "connections": 0, "workers": set(),
                                     "submitted": 0, "accepted": 0, "rejected": 0,
                                     "routes": 0, "healthy_routes": 0})
        summary = state.get("summary", {})
        row["connections"] += int(summary.get("connections", 0) or 0)
        row["submitted"] += int(summary.get("submitted", 0) or 0)
        row["accepted"] += int(summary.get("accepted", 0) or 0)
        row["rejected"] += int(summary.get("rejected", 0) or 0)
        for worker in state.get("workers", []):
            if worker.get("status") in {"online", "recent"}:
                # The name is used only as an in-memory deduplication key and is never returned.
                row["workers"].add(str(worker.get("name", "")))
        routes = state.get("routes", [])
        row["routes"] += len(routes)
        row["healthy_routes"] += sum(1 for route in routes if route.get("upstream_ok"))

    public_pools = []
    for row in sorted(pools.values(), key=lambda item: item["name"].lower()):
        item = {**row, "workers": len(row["workers"])}
        item["reject_percent"] = round(item["rejected"] * 100 / item["submitted"], 2) if item["submitted"] else 0
        item["status"] = "good" if item["routes"] and item["healthy_routes"] else "bad"
        public_pools.append(item)
        for key in totals:
            totals[key] += item[key]
    totals["reject_percent"] = round(totals["rejected"] * 100 / totals["submitted"], 2) if totals["submitted"] else 0

    services = [{"label": label, "state": service_state(unit)} for unit, label in SERVICES.items()]
    bad_services = [item for item in services if item["state"] not in {"active", "unavailable"}]
    bad_pools = [item for item in public_pools if item["status"] == "bad"]
    try:
        state_age = max(0, int(time.time() - INSPECTOR_STATE_FILE.stat().st_mtime))
    except OSError:
        state_age = None
    if state_age is not None and state_age > 180:
        level, headline = "bad", "状态数据已停止更新，请管理员检查"
    elif bad_services or bad_pools or totals["reject_percent"] > 1:
        level, headline = "bad", "当前有异常，请管理员检查"
    elif not public_pools:
        level, headline = "unknown", "正在等待状态数据"
    else:
        level, headline = "good", "当前运行正常"
    return {"level": level, "headline": headline, "totals": totals, "pools": public_pools,
            "services": services, "events": public_events(), "updated": beijing_time(time.time())}


@app.after_request
def security_headers(response):
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "public, max-age=15"
    return response


@app.get("/")
def index():
    return render_template("public_status.html", status=status_snapshot())


@app.get("/healthz")
def healthz():
    snapshot = status_snapshot()
    return jsonify({"ok": snapshot["level"] == "good", "updated": snapshot["updated"]}), \
        (200 if snapshot["level"] == "good" else 503)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8790)
