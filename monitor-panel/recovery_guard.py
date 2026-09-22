#!/usr/bin/env python3
"""Restore trusted Stratum files and regenerated runtime configuration.

The protected baseline is created only by the root install/upgrade scripts.
Runtime configuration is never restored from that static baseline: it is
validated and rendered again from the current V3 business configuration, or
from the newest valid history entry when the current file is corrupt.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from endpoint_monitor import Notifier
from v3_manager import (ConfigError, ConfigStore, file_lock, render_haproxy_config,
    render_inspector_config, validate_config)


RECOVERY_ROOT = Path(os.getenv("STRATUM_RECOVERY_ROOT", "/var/lib/stratum-recovery"))
MANIFEST_FILE = RECOVERY_ROOT / "manifest.json"
STATE_FILE = Path(os.getenv("STRATUM_RECOVERY_STATE", "/var/lib/stratum-monitor/recovery-guard.json"))
EVENT_FILE = Path(os.getenv("ENDPOINT_EVENT_FILE", "/var/lib/stratum-monitor/endpoint-events.jsonl"))
CONFIG_FILE = Path(os.getenv("V3_CONFIG_FILE", "/etc/stratum-v3.json"))
HISTORY_DIR = Path(os.getenv("V3_HISTORY_DIR", "/var/lib/stratum-monitor/history"))
INSPECTOR_CONFIG = Path(os.getenv("INSPECTOR_CONFIG_FILE", "/etc/stratum-inspector.json"))
HAPROXY_CONFIG = Path(os.getenv("HAPROXY_V3_CONFIG", "/etc/haproxy/haproxy.cfg"))

STATIC_PATHS = [
    Path("/etc/stratum-version.json"),
    *[Path("/etc/systemd/system") / name for name in (
        "stratum-secure-relay.service", "stratum-secure-monitor.service",
        "stratum-inspector-v3.service", "stratum-endpoint-monitor.service",
        "stratum-security-monitor.service", "stratum-route-switch-monitor.service",
        "stratum-admin-helper.service", "stratum-admin.service",
        "stratum-public-status.service", "stratum-vps-watchdog.service",
        "stratum-vps-watchdog.timer")],
    Path("/etc/systemd/system/stratum-secure-monitor.service.d/20-state-readers.conf"),
    *[Path(f"/etc/systemd/system/{name}.service.d/90-unattended.conf") for name in (
        "stratum-secure-relay", "stratum-secure-monitor", "stratum-inspector-v3",
        "stratum-endpoint-monitor", "stratum-security-monitor", "stratum-route-switch-monitor",
        "stratum-admin", "stratum-public-status")],
    *[Path("/opt/stratum-admin") / name for name in (
        "v3_manager.py", "version_info.py", "admin_auth.py", "endpoint_monitor.py",
        "operations_center.py", "high_risk_wizard.py", "privileged_helper.py",
        "security_monitor.py", "stratum_inspector.py", "stratum_admin_v3.py",
        "stratum_public_status.py", "route_switch_monitor.py", "vps_watchdog.py",
        "recovery_guard.py", "reset-panel-password.sh", "install-public-status.sh")],
    Path("/opt/stratum-admin/templates/v3_dashboard.html"),
    Path("/opt/stratum-admin/templates/public_status.html"),
    Path("/opt/stratum-admin/templates/public_login.html"),
    Path("/opt/stratum-admin/static/v3.css"),
    Path("/opt/stratum-admin/static/public.css"),
    Path("/opt/stratum-secure-server.py"),
    Path("/opt/stratum-secure-monitor.py"),
    Path("/opt/version_info.py"),
]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def atomic_text(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def initialize(paths=None):
    paths = paths or STATIC_PATHS
    files = RECOVERY_ROOT / "files"
    files.mkdir(parents=True, exist_ok=True)
    os.chmod(RECOVERY_ROOT, 0o700)
    os.chmod(files, 0o700)
    entries = []
    for path in paths:
        path = Path(path)
        if not path.is_file() or path.is_symlink():
            continue
        info = path.stat()
        backup_name = hashlib.sha256(str(path).encode()).hexdigest()
        backup = files / backup_name
        shutil.copyfile(path, backup)
        os.chmod(backup, 0o600)
        entries.append({"path": str(path), "backup": backup_name, "sha256": digest(path),
            "mode": info.st_mode & 0o7777, "uid": info.st_uid, "gid": info.st_gid})
    atomic_text(MANIFEST_FILE, json.dumps({"version": 1, "created_at": int(time.time()),
        "files": entries}, ensure_ascii=False, indent=2) + "\n")
    return len(entries)


def service_for_path(path):
    name = path.name
    if path.parent == Path("/etc/systemd/system") and name.endswith((".service", ".timer")):
        if name in {"stratum-vps-watchdog.service", "stratum-vps-watchdog.timer"}:
            return None
        return name
    if path.parent.name.endswith(".service.d"):
        return path.parent.name[:-2]
    if name == "stratum-secure-server.py":
        return "stratum-secure-relay.service"
    if name == "stratum-secure-monitor.py":
        return "stratum-secure-monitor.service"
    if name == "stratum_inspector.py":
        return "stratum-inspector-v3.service"
    if name == "endpoint_monitor.py":
        return "stratum-endpoint-monitor.service"
    if name == "security_monitor.py":
        return "stratum-security-monitor.service"
    if name == "route_switch_monitor.py":
        return "stratum-route-switch-monitor.service"
    if name == "privileged_helper.py":
        return "stratum-admin-helper.service"
    if name in {"stratum_admin_v3.py", "admin_auth.py", "operations_center.py",
            "high_risk_wizard.py", "v3_manager.py", "v3_dashboard.html", "v3.css"}:
        return "stratum-admin.service"
    if name in {"stratum_public_status.py", "public_status.html", "public_login.html", "public.css"}:
        return "stratum-public-status.service"
    return None


def restore_entry(entry):
    target = Path(entry["path"])
    backup = RECOVERY_ROOT / "files" / entry["backup"]
    if not backup.is_file() or digest(backup) != entry["sha256"]:
        raise OSError("受保护恢复副本不存在或已损坏")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".recovery-", dir=str(target.parent))
    os.close(fd)
    try:
        shutil.copyfile(backup, temporary)
        os.chmod(temporary, int(entry["mode"]))
        if hasattr(os, "chown"):
            os.chown(temporary, int(entry["uid"]), int(entry["gid"]))
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_static(now=None):
    now = int(now or time.time())
    manifest = load_json(MANIFEST_FILE, {})
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        raise OSError("恢复基线不存在；拒绝自动信任当前文件")
    events, services, daemon_reload = [], set(), False
    for entry in manifest["files"]:
        path = Path(str(entry.get("path", "")))
        try:
            current = digest(path) if path.is_file() and not path.is_symlink() else None
            if current == entry.get("sha256"):
                continue
            restore_entry(entry)
            if digest(path) != entry.get("sha256"):
                raise OSError("恢复后摘要仍不一致")
            service = service_for_path(path)
            if service:
                services.add(service)
            daemon_reload = daemon_reload or Path("/etc/systemd/system") in path.parents
            events.append({"time": now, "type": "file_restored", "path": str(path),
                "message": "关键文件被删改，已从 root 保护基线恢复"})
        except (OSError, ValueError, KeyError) as exc:
            events.append({"time": now, "type": "restore_failed", "path": str(path),
                "message": str(exc)[:300]})
    return events, services, daemon_reload


def newest_valid_history():
    for path in sorted(HISTORY_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            validate_config(value, resolve=False)
            return value, path
        except (OSError, ValueError, TypeError, ConfigError):
            continue
    return None, None


def load_or_recover_config(now, events):
    try:
        value = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        validate_config(value, resolve=False)
        return value
    except (OSError, ValueError, TypeError, ConfigError) as original:
        value, source = newest_valid_history()
        if value is None:
            raise ConfigError(f"当前线路配置损坏且没有可用历史版本：{original}") from original
        RECOVERY_ROOT.mkdir(parents=True, exist_ok=True)
        os.chmod(RECOVERY_ROOT, 0o700)
        rejected = RECOVERY_ROOT / f"rejected-config-{now}.json"
        if CONFIG_FILE.is_file():
            shutil.copyfile(CONFIG_FILE, rejected)
            os.chmod(rejected, 0o600)
        ConfigStore._atomic_write(CONFIG_FILE, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        events.append({"time": now, "type": "config_restored", "path": str(CONFIG_FILE),
            "message": f"线路配置损坏，已恢复最近有效历史版本 {source.name}"})
        return value


def repair_generated(now=None, command_runner=None):
    now = int(now or time.time())
    command_runner = command_runner or subprocess.run
    events, services = [], set()
    with file_lock(CONFIG_FILE):
        config = load_or_recover_config(now, events)
        expected_inspector = json.dumps(render_inspector_config(config), ensure_ascii=False, indent=2) + "\n"
        if not INSPECTOR_CONFIG.is_file() or INSPECTOR_CONFIG.read_text(encoding="utf-8") != expected_inspector:
            ConfigStore._atomic_write(INSPECTOR_CONFIG, expected_inspector, mode=0o640)
            services.add("stratum-inspector-v3.service")
            events.append({"time": now, "type": "generated_config_restored", "path": str(INSPECTOR_CONFIG),
                "message": "检查器配置与合法线路配置不一致，已重新生成"})
        expected_haproxy = render_haproxy_config(config)
        current_haproxy = HAPROXY_CONFIG.read_text(encoding="utf-8") if HAPROXY_CONFIG.is_file() else ""
        if current_haproxy != expected_haproxy:
            fd, candidate = tempfile.mkstemp(prefix="stratum-recovery-", suffix=".cfg",
                dir=str(HAPROXY_CONFIG.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(expected_haproxy)
                result = command_runner(["haproxy", "-c", "-f", candidate], capture_output=True,
                    text=True, timeout=15, check=False)
                if result.returncode:
                    raise ConfigError((result.stderr or result.stdout or "HAProxy 恢复配置验证失败")[-500:])
                os.chmod(candidate, 0o644)
                os.replace(candidate, HAPROXY_CONFIG)
            finally:
                if os.path.exists(candidate):
                    os.unlink(candidate)
            services.add("haproxy.service")
            events.append({"time": now, "type": "generated_config_restored", "path": str(HAPROXY_CONFIG),
                "message": "HAProxy 配置与合法线路配置不一致，已验证并重新生成"})
    return events, services


def run_systemctl(arguments, timeout=45):
    try:
        return subprocess.run(["systemctl", *arguments], capture_output=True, text=True,
            timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return type("Result", (), {"returncode": 1, "stderr": str(exc), "stdout": ""})()


def run_once(now=None):
    now = int(now or time.time())
    events, services, daemon_reload = [], set(), False
    try:
        static_events, static_services, daemon_reload = restore_static(now)
        events.extend(static_events)
        services.update(static_services)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        events.append({"time": now, "type": "restore_failed", "path": str(MANIFEST_FILE),
            "message": str(exc)[:300]})
    try:
        generated_events, generated_services = repair_generated(now)
        events.extend(generated_events)
        services.update(generated_services)
    except (OSError, ValueError, TypeError, ConfigError) as exc:
        events.append({"time": now, "type": "restore_failed", "path": str(CONFIG_FILE),
            "message": str(exc)[:300]})
    if daemon_reload:
        result = run_systemctl(["daemon-reload"])
        if result.returncode:
            events.append({"time": now, "type": "restore_failed", "path": "systemd",
                "message": (result.stderr or result.stdout or "systemd daemon-reload 失败")[-300:]})
    for service in sorted(services):
        result = run_systemctl(["restart", service])
        events.append({"time": now, "type": "service_restarted" if result.returncode == 0 else "restart_failed",
            "service": service, "message": "恢复关键文件后已重启并交由健康检查复核" if result.returncode == 0
            else (result.stderr or result.stdout or "服务重启失败")[-300:]})
    state = load_json(STATE_FILE, {"version": 1, "events": []})
    state["events"] = (state.get("events", []) + events)[-200:]
    state["updated_at"] = now
    state["last_result"] = "failed" if any(item["type"] in {"restore_failed", "restart_failed"} for item in events) else "ok"
    atomic_text(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2) + "\n", mode=0o640)
    return events


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--initialize", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.initialize:
        print(f"Protected {initialize()} trusted files")
    else:
        events = run_once()
        for event in events:
            print(json.dumps(event, ensure_ascii=False))
        if events:
            failed = [event for event in events if event.get("type") in {"restore_failed", "restart_failed"}]
            targets = [event.get("path") or event.get("service", "VPS") for event in events[:3]]
            message = f"自动恢复处理 {len(events)} 项：" + "、".join(targets)
            if failed:
                message += f"；其中 {len(failed)} 项失败，需要人工处理"
            Notifier(settings=os.environ, event_path=EVENT_FILE)({"time": events[0]["time"],
                "type": "vps_auto_recovery", "endpoint": "VPS 自动恢复", "message": message})


if __name__ == "__main__":
    main()
