#!/usr/bin/env python3
"""Independent file-integrity monitor for the Stratum relay stack."""

import argparse
import hashlib
import json
import os
import tempfile
import time
import ssl
from datetime import datetime, timezone
from pathlib import Path

from endpoint_monitor import Notifier


BASELINE_FILE = Path(os.getenv("INTEGRITY_BASELINE_FILE", "/var/lib/stratum-monitor/integrity.json"))
STATE_FILE = Path(os.getenv("INTEGRITY_STATE_FILE", "/var/lib/stratum-monitor/security-state.json"))
INSPECTOR_STATE_FILE = Path(os.getenv("INSPECTOR_STATE_FILE", "/var/lib/stratum-inspector/state.json"))
V3_CONFIG_FILE = Path(os.getenv("V3_CONFIG_FILE", "/etc/stratum-v3.json"))
SECURE_RELAY_CONFIG = Path(os.getenv("SECURE_RELAY_CONFIG", "/etc/stratum-secure-relay.json"))
DEFAULT_PATHS = [
    "/etc/stratum-v3.json",
    "/etc/stratum-v3-peer.json",
    "/etc/stratum-inspector.json",
    "/etc/haproxy/stratum-v3.cfg",
    "/etc/systemd/system/stratum-inspector-v3.service",
    "/etc/systemd/system/stratum-endpoint-monitor.service",
    "/etc/systemd/system/stratum-security-monitor.service",
    "/etc/systemd/system/stratum-route-switch-monitor.service",
    "/etc/systemd/system/stratum-admin.service",
    "/etc/systemd/system/stratum-vps-watchdog.service",
    "/etc/systemd/system/stratum-vps-watchdog.timer",
    "/etc/cron.d/stratum-monitor",
    "/opt/stratum-admin/stratum_inspector.py",
    "/opt/stratum-admin/endpoint_monitor.py",
    "/opt/stratum-admin/security_monitor.py",
    "/opt/stratum-admin/stratum_admin_v3.py",
    "/opt/stratum-admin/route_switch_monitor.py",
    "/opt/stratum-admin/vps_watchdog.py",
    "/opt/stratum-admin/reset-panel-password.sh",
    "/opt/stratum-admin/templates/v3_dashboard.html",
    "/opt/stratum-admin/static/v3.css",
]


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def snapshot(paths):
    result = {}
    for value in paths:
        path = Path(value)
        result[str(path)] = {"exists": path.exists(), "sha256": digest(path) if path.is_file() else None}
    return result


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


class SecurityMonitor:
    def __init__(self, paths, baseline, state=None, notify=None, repeat_seconds=900,
                 inspector_state_path=None, baseline_path=None, config_path=None, secure_relay_config_path=None):
        self.paths = paths
        self.baseline = baseline
        self.baseline_path = Path(baseline_path) if baseline_path else None
        self.state = state or {"active": {}, "events": []}
        self.notify = notify
        self.repeat_seconds = repeat_seconds
        self.inspector_state_path = Path(inspector_state_path) if inspector_state_path else None
        self.config_path = Path(config_path) if config_path else None
        self.secure_relay_config_path = Path(secure_relay_config_path) if secure_relay_config_path else None

    def refresh_baseline(self):
        if not self.baseline_path:
            return
        updated = load(self.baseline_path, None)
        if isinstance(updated, dict) and updated:
            self.baseline = updated

    def check(self, now=None):
        now = int(now or time.time())
        # The administration panel updates the approved baseline after an
        # authorized configuration change. Reload it without requiring a
        # service restart, otherwise the old hash is reported every interval.
        self.refresh_baseline()
        current = snapshot(self.paths)
        active = self.state.setdefault("active", {})
        for path in self.paths:
            expected = self.baseline.get(path, {"exists": False, "sha256": None})
            observed = current[path]
            changed = expected != observed
            previous = active.get(path)
            if changed and (not previous or now - previous.get("last_alert", 0) >= self.repeat_seconds):
                event = {"time": now, "type": "integrity_changed", "endpoint": path,
                    "message": "受保护文件的存在状态或 SHA-256 哈希与批准基线不一致", "expected": expected, "observed": observed}
                self.state.setdefault("events", []).append(event)
                active[path] = {"first_seen": previous.get("first_seen", now) if previous else now, "last_alert": now}
                if self.notify:
                    self.notify(event)
            elif not changed and previous:
                event = {"time": now, "type": "integrity_recovery", "endpoint": path,
                    "message": "受保护文件的 SHA-256 哈希已恢复为批准基线"}
                self.state.setdefault("events", []).append(event)
                active.pop(path, None)
                if self.notify:
                    self.notify(event)
        self.state["events"] = self.state.get("events", [])[-200:]
        self.check_protocol_anomalies(now)
        self.check_expiry_reminders(now)
        self.state["updated_at"] = now
        return self.state

    def check_expiry_reminders(self, now):
        if not self.config_path:
            return
        settings = load(self.config_path, {}).get("settings", {})
        threshold = max(1, min(365, int(settings.get("expiry_reminder_days", 30) or 30)))
        today = datetime.fromtimestamp(now, timezone.utc).date()
        candidates = []
        for key, label, date_key in (("vps", str(settings.get("vps_name", "本机VPS") or "本机VPS"), "vps_expiry"),
                ("domain", str(settings.get("domain_name", "中转域名") or "中转域名"), "domain_expiry")):
            try:
                target = datetime.strptime(str(settings.get(date_key, "")), "%Y-%m-%d").date()
                candidates.append((key, label, (target - today).days, target.isoformat()))
            except ValueError:
                pass
        if self.secure_relay_config_path:
            relay = load(self.secure_relay_config_path, {})
            try:
                decoded = ssl._ssl._test_decode_cert(str(relay.get("certificate", "")))
                expiry = datetime.strptime(decoded["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                candidates.append(("certificate", "TLS证书", int((expiry - datetime.fromtimestamp(now, timezone.utc)).total_seconds() // 86400), expiry.date().isoformat()))
            except (AttributeError, KeyError, OSError, ValueError, ssl.SSLError):
                pass
        active = self.state.setdefault("expiry_alerts", {})
        present = set()
        for key, label, days, date_text in candidates:
            present.add(key)
            previous = active.get(key)
            due = days <= threshold
            if due and (not previous or now - int(previous.get("last_alert", 0)) >= 86400):
                message = f"{label}已到期，请立即处理" if days < 0 else f"{label}将在 {days} 天后到期（{date_text}），请提前续费或更新"
                event = {"time": now, "type": "expiry_warning", "endpoint": label, "message": message, "days_left": days}
                self.state.setdefault("events", []).append(event)
                active[key] = {"last_alert": now, "days_left": days}
                if self.notify:
                    self.notify(event)
            elif not due and previous:
                event = {"time": now, "type": "expiry_recovery", "endpoint": label, "message": f"{label}的到期提醒已解除"}
                self.state.setdefault("events", []).append(event)
                active.pop(key, None)
                if self.notify:
                    self.notify(event)
        for key in list(active):
            if key not in present:
                active.pop(key, None)

    def check_protocol_anomalies(self, now):
        if not self.inspector_state_path:
            return
        inspector = load(self.inspector_state_path, {})
        seen = set(self.state.get("seen_protocol_anomalies", []))
        alert_state = self.state.setdefault("protocol_alerts", {})
        for pool in inspector.get("pools", []):
            for anomaly in pool.get("anomalies", []):
                fingerprint = hashlib.sha256(json.dumps(anomaly, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                # Some pools legitimately submit jobs that cannot be matched
                # to a locally observed mining.notify. This signal produced
                # sustained false positives, so it is retained only in old
                # state history and never sent as an alert.
                if anomaly.get("type") == "unknown_job":
                    self.state["unknown_job_suppressed"] = self.state.get("unknown_job_suppressed", 0) + 1
                    continue
                key = "|".join([str(anomaly.get("type", "protocol_anomaly")), str(anomaly.get("route", "unknown")), str(anomaly.get("worker", "unknown"))])
                aggregate = alert_state.setdefault(key, {"last_alert": 0, "pending": 0, "first_seen": now})
                aggregate["pending"] += 1
                if aggregate["last_alert"] and now - aggregate["last_alert"] < self.repeat_seconds:
                    continue
                event = {"time": now, "type": anomaly.get("type", "protocol_anomaly"),
                    "endpoint": anomaly.get("route", pool.get("name", "unknown")),
                    "message": anomaly.get("detail", "检测到 Stratum 授权、任务或 Share 归属不一致"),
                    "worker": anomaly.get("worker", ""), "source_ip": anomaly.get("source_ip", ""),
                    "occurrences": aggregate["pending"], "first_seen": aggregate["first_seen"]}
                self.state.setdefault("events", []).append(event)
                if self.notify:
                    self.notify(event)
                aggregate["last_alert"] = now
                aggregate["pending"] = 0
                aggregate["first_seen"] = now
        self.state["seen_protocol_anomalies"] = list(seen)[-1000:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=BASELINE_FILE)
    parser.add_argument("--state", type=Path, default=STATE_FILE)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--path", action="append", dest="paths")
    args = parser.parse_args()
    paths = args.paths or DEFAULT_PATHS
    if args.initialize:
        atomic_write(args.baseline, snapshot(paths))
        return
    baseline = load(args.baseline, {})
    if not baseline:
        raise SystemExit("Integrity baseline is missing. Run with --initialize after reviewing installed files.")
    monitor = SecurityMonitor(paths, baseline, state=load(args.state, {}),
        notify=Notifier(os.getenv("WECHAT_WEBHOOK", "")), inspector_state_path=INSPECTOR_STATE_FILE,
        baseline_path=args.baseline, config_path=V3_CONFIG_FILE, secure_relay_config_path=SECURE_RELAY_CONFIG)
    while True:
        monitor.check()
        atomic_write(args.state, monitor.state)
        if args.once:
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
