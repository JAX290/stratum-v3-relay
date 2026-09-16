#!/usr/bin/env python3
"""Password-protected, read-only operator status panel."""

import json
import os
import re
import secrets
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from admin_auth import (LOGIN_FAILURES, LOGIN_FAILURES_LOCK, RequestAwareSessionInterface,
                        login_retry_after, record_login_failure)


CONFIG_FILE = Path(os.getenv("V3_CONFIG_FILE", "/etc/stratum-v3.json"))
INSPECTOR_STATE_FILE = Path(os.getenv("INSPECTOR_STATE_FILE", "/var/lib/stratum-inspector/state.json"))
ENDPOINT_EVENT_FILE = Path(os.getenv("ENDPOINT_EVENT_FILE", "/var/lib/stratum-monitor/endpoint-events.jsonl"))
SECURE_RELAY_EVENT_FILE = Path(os.getenv("SECURE_RELAY_EVENT_FILE", "/var/lib/stratum-secure-relay/events.jsonl"))
SECURE_RELAY_STATE_FILE = Path(os.getenv("SECURE_RELAY_STATE_FILE", "/var/lib/stratum-secure-relay/sites.json"))
SECURE_RELAY_MONITOR_FILE = Path(os.getenv("SECURE_RELAY_MONITOR_FILE", "/var/lib/stratum-secure-relay/monitor.json"))

SERVICES = {
    "haproxy": "流量转发",
    "stratum-inspector-v3": "矿机状态采集",
    "stratum-endpoint-monitor": "矿池线路监控",
    "stratum-route-switch-monitor": "线路切换",
    "stratum-security-monitor": "安全监控",
    "stratum-secure-relay": "加密接入",
    "stratum-secure-monitor": "矿场在线监控",
    "stratum-public-status": "只读值守面板",
    "stratum-vps-watchdog.timer": "自动恢复",
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
app.secret_key = os.getenv("PUBLIC_STATUS_SECRET_KEY", "public-status-not-configured")
app.session_interface = RequestAwareSessionInterface()
app.config.update(MAX_CONTENT_LENGTH=4096, SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=timedelta(hours=12))


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


def redact_message(value):
    """Remove credentials while retaining operator-useful hosts, ports and IPs."""
    text = str(value or "")[:1000]
    patterns = (
        (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[已隐藏]"),
        (r"(?i)((?:token|password|passwd|secret|shared[_ -]?key)\s*[:=]\s*)[^\s,;]+", r"\1[已隐藏]"),
        (r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[^\s]+", "[企业微信地址已隐藏]"),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def operator_events(limit=20):
    rows = []
    for path in (ENDPOINT_EVENT_FILE, SECURE_RELAY_EVENT_FILE):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = str(event.get("type", ""))
            if kind not in EVENT_LABELS:
                continue
            label, level = EVENT_LABELS[kind]
            rows.append({"time": int(event.get("time", 0) or 0), "label": label, "level": level,
                         "message": redact_message(event.get("message") or event.get("detail") or "")})
    rows.sort(key=lambda item: item["time"], reverse=True)
    return [{**row, "display_time": beijing_time(row["time"])} for row in rows[:limit]]


def assigned_ports(config):
    result = {}
    for group in config.get("port_groups", []):
        for port, endpoint_id in zip(group.get("ports", []), group.get("endpoint_ids", [])):
            result.setdefault(str(endpoint_id), []).append(int(port))
    for route in config.get("fixed_routes", []):
        result.setdefault(str(route.get("endpoint_id", "")), []).append(int(route.get("port", 0) or 0))
    return result


def site_rows(now=None):
    now = int(now or time.time())
    state = load_json(SECURE_RELAY_STATE_FILE, {"sites": {}})
    monitor = load_json(SECURE_RELAY_MONITOR_FILE, {"clients": {}}).get("clients", {})
    sites = state.get("sites", {}) if isinstance(state.get("sites", {}), dict) else {}
    rows = []
    for site_id, value in sites.items():
        if not isinstance(value, dict):
            continue
        last_seen = int(value.get("last_seen", 0) or 0)
        monitor_status = str(monitor.get(site_id, ""))
        if monitor_status == "offline" or (last_seen and now - last_seen > 180):
            status, status_text = "bad", "离线"
        elif last_seen:
            status, status_text = "good", "在线"
        else:
            status, status_text = "unknown", "等待连接"
        rows.append({"id": str(site_id), "name": str(value.get("name") or site_id),
                     "status": status, "status_text": status_text,
                     "last_seen": beijing_time(last_seen) if last_seen else "尚未连接",
                     "last_ip": str(value.get("last_ip") or "-"),
                     "miners": int(value.get("reported_miner_count", value.get("miner_count", 0)) or 0),
                     "connections": int(value.get("reported_connections", value.get("active", 0)) or 0),
                     "client_version": str(value.get("client_version") or "未上报")})
    return sorted(rows, key=lambda item: (item["status"] != "bad", item["name"]))


def worker_row(worker):
    submitted = int(worker.get("submitted", 0) or 0)
    rejected = int(worker.get("rejected", 0) or 0)
    sources = [str(value) for value in worker.get("sources", []) if value]
    details = worker.get("details", []) if isinstance(worker.get("details", []), list) else []
    if not sources:
        sources = sorted({str(item.get("source_ip")) for item in details if item.get("source_ip")})
    return {
        "name": str(worker.get("name") or "等待识别"),
        "agent": str(worker.get("agent") or "未知"),
        "sources": sources,
        "status": str(worker.get("status_text") or worker.get("status") or "未知"),
        "status_code": str(worker.get("status") or "unknown"),
        "active": int(worker.get("active", 0) or 0),
        "submitted": submitted,
        "accepted": int(worker.get("accepted", 0) or 0),
        "rejected": rejected,
        "reject_percent": round(rejected * 100 / submitted, 2) if submitted else 0,
        "latency_ms": int(worker.get("latency_ms", 0) or 0),
        "hashrate": str(worker.get("hashrate") or "0 H/s"),
        "last_share": str(worker.get("last_share") or "尚未提交"),
        "last_error": redact_message(worker.get("last_error")),
    }


def status_snapshot():
    config = load_json(CONFIG_FILE, {"endpoints": []})
    inspector = load_json(INSPECTOR_STATE_FILE, {"pools": []})
    states = {str(item.get("id", "")): item for item in inspector.get("pools", [])}
    ports = assigned_ports(config)
    pools = {}
    totals = {key: 0 for key in ("connections", "workers", "submitted", "accepted", "rejected")}
    worker_names = set()

    for endpoint in config.get("endpoints", []):
        endpoint_id = str(endpoint.get("id", ""))
        state = states.get(endpoint_id, {})
        summary = state.get("summary", {})
        routes = state.get("routes", [])
        route_state = routes[0] if routes else {}
        pool_name = str(endpoint.get("pool") or "未分类")
        pool = pools.setdefault(pool_name, {"name": pool_name, "routes": [], "workers": [],
                                            "summary": {key: 0 for key in totals}})
        route_workers = [worker_row(worker) for worker in state.get("workers", [])]
        pool["workers"].extend(route_workers)
        pool["routes"].append({
            "region": str(endpoint.get("region") or "默认"),
            "target": f"{endpoint.get('host', '未配置')}:{endpoint.get('port', '-')}",
            "public_ports": ", ".join(f":{port}" for port in ports.get(endpoint_id, [])) or "未分配",
            "ok": bool(route_state.get("upstream_ok")),
            "latency": route_state.get("upstream_latency_ms"),
            "connections": int(summary.get("connections", 0) or 0),
        })
        for key in ("connections", "submitted", "accepted", "rejected"):
            value = int(summary.get(key, 0) or 0)
            pool["summary"][key] += value
            totals[key] += value
        for worker in route_workers:
            if worker["status_code"] in {"online", "recent"}:
                worker_names.add(worker["name"])

    totals["workers"] = len(worker_names)
    totals["reject_percent"] = round(totals["rejected"] * 100 / totals["submitted"], 2) if totals["submitted"] else 0
    public_pools = []
    for pool in sorted(pools.values(), key=lambda item: item["name"].lower()):
        pool["workers"].sort(key=lambda item: (item["status_code"] not in {"online", "recent"}, item["name"]))
        pool["summary"]["workers"] = sum(1 for item in pool["workers"] if item["status_code"] in {"online", "recent"})
        pool["summary"]["reject_percent"] = (round(pool["summary"]["rejected"] * 100 / pool["summary"]["submitted"], 2)
                                                   if pool["summary"]["submitted"] else 0)
        pool["status"] = "good" if pool["routes"] and any(route["ok"] for route in pool["routes"]) else "bad"
        public_pools.append(pool)

    services = [{"label": label, "state": service_state(unit)} for unit, label in SERVICES.items()]
    bad_services = [item for item in services if item["state"] not in {"active", "unavailable"}]
    bad_pools = [item for item in public_pools if item["status"] == "bad"]
    try:
        state_age = max(0, int(time.time() - INSPECTOR_STATE_FILE.stat().st_mtime))
    except OSError:
        state_age = None
    if state_age is not None and state_age > 180:
        level, headline = "bad", "状态数据已停止更新，请联系管理员"
    elif bad_services or bad_pools or totals["reject_percent"] > 1:
        level, headline = "bad", "当前有异常，需要检查"
    elif not public_pools:
        level, headline = "unknown", "正在等待状态数据"
    else:
        level, headline = "good", "当前运行正常"
    return {"level": level, "headline": headline, "totals": totals, "pools": public_pools,
            "services": services, "events": operator_events(), "updated": beijing_time(time.time()),
            "state_age": state_age, "sites": site_rows()}


def auth_configured():
    return bool(os.getenv("PUBLIC_STATUS_USERNAME", "").strip()
                and os.getenv("PUBLIC_STATUS_PASSWORD_HASH", "").strip()
                and len(os.getenv("PUBLIC_STATUS_SECRET_KEY", "")) >= 32)


def authorized():
    return auth_configured() and session.get("public_status_authenticated") is True


def operator_login_key():
    if request.remote_addr in {"127.0.0.1", "::1"}:
        forwarded = request.headers.get("X-Operator-IP", "").strip()
        if forwarded:
            return forwarded[:128]
    return request.remote_addr or "unknown"


@app.after_request
def security_headers(response):
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "no-store, max-age=0"
    if request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    if authorized():
        return redirect(url_for("index"))
    if not auth_configured():
        return render_template("public_login.html", setup_required=True, error=""), 503
    error = ""
    if request.method == "POST":
        key = operator_login_key()
        retry_after = login_retry_after(key)
        if retry_after:
            return render_template("public_login.html", setup_required=False,
                                   error=f"尝试次数过多，请在 {max(1, (retry_after + 59) // 60)} 分钟后重试"), \
                429, {"Retry-After": str(retry_after)}
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        valid_user = secrets.compare_digest(username, os.getenv("PUBLIC_STATUS_USERNAME", ""))
        valid_password = check_password_hash(os.getenv("PUBLIC_STATUS_PASSWORD_HASH", ""), password)
        if valid_user and valid_password:
            with LOGIN_FAILURES_LOCK:
                LOGIN_FAILURES.pop(key, None)
            session.clear()
            session["public_status_authenticated"] = True
            session.permanent = True
            return redirect(url_for("index"))
        record_login_failure(key)
        error = "账号或密码不正确"
    return render_template("public_login.html", setup_required=False, error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
def index():
    if not authorized():
        return redirect(url_for("login"))
    return render_template("public_status.html", status=status_snapshot(),
                           username=os.getenv("PUBLIC_STATUS_USERNAME", "值守人员"))


@app.get("/healthz")
def healthz():
    snapshot = status_snapshot()
    return jsonify({"ok": snapshot["level"] == "good", "updated": snapshot["updated"]}), \
        (200 if snapshot["level"] == "good" else 503)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8790)
