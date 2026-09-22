#!/usr/bin/env python3
"""Small root helper for the unprivileged administration panel.

The Unix socket is restricted to the stratum-admin account and every request is
matched against a fixed operation and argument allowlist. No arbitrary command
or arbitrary filesystem path is accepted.
"""

import argparse
import json
import os
import shutil
import socket
import stat
import struct
import subprocess
import tempfile
import time
from pathlib import Path

try:
    import grp
    import pwd
except ImportError:  # Windows development and unit-test hosts
    grp = pwd = None


SOCKET_PATH = Path(os.getenv("V3_PRIVILEGED_HELPER_SOCKET", "/run/stratum-admin-helper.sock"))
MAX_REQUEST = 2 * 1024 * 1024
ALLOWED_SERVICES = {"haproxy", "stratum-secure-relay", "stratum-endpoint-monitor",
    "stratum-inspector-v3", "stratum-security-monitor", "stratum-route-switch-monitor", "stratum-secure-monitor"}
ALLOWED_SERVICES.add("stratum-vps-watchdog.timer")
ALLOWED_ACTIONS = {"reload", "restart", "try-restart"}
MANAGED_FILES = {
    "monitor_env": (Path("/etc/stratum-monitor.env"), 0o640, "text", "stratum-admin"),
    "peer_config": (Path("/etc/stratum-v3-peer.json"), 0o640, "json", "stratum-admin"),
    "secure_relay_config": (Path("/etc/stratum-secure-relay.json"), 0o640, "json", "stratum-relay"),
}
MONITOR_ENV_KEY = __import__("re").compile(
    r"^(?:WECHAT_WEBHOOK(?:_[1-3])?|DINGTALK_(?:WEBHOOK|SECRET)(?:_[1-3])?|"
    r"EMAIL_(?:DELIVERY|TO_[1-3])|SMTP_(?:TO|HOST|PORT|SECURITY|USERNAME|PASSWORD|FROM)|"
    r"NOTIFY_(?:CRITICAL|WARNING|INFO)_CHANNELS|NOTIFY_QUIET_(?:START|END)|"
    r"PANEL_URL|MIN_CONNECTIONS|MEM_THRESHOLD|DISK_THRESHOLD|ALERT_INTERVAL)$")


def build_haproxy_config():
    from v3_manager import render_haproxy_config, validate_config
    config = json.loads(Path("/etc/stratum-v3.json").read_text(encoding="utf-8"))
    validate_config(config, resolve=False)
    return render_haproxy_config(config)


def require_root_owned_tree(path):
    """Reject candidates that the unprivileged panel could replace or edit."""
    entries = [path, *path.rglob("*")] if path.is_dir() else [path]
    for entry in entries:
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("候选内容必须由 root 持有且不可由其他账户修改")
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError("候选内容包含不支持的文件类型")


def atomic_write(path, content, mode):
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


def run_checked(command, timeout=30, cwd=None):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise OSError((result.stderr or result.stdout or "特权操作失败")[-500:])
    return (result.stdout or "").strip()[-500:]


def dispatch(request):
    if not isinstance(request, dict):
        raise ValueError("请求格式不正确")
    operation = request.get("operation")
    if operation == "service":
        action, service = str(request.get("action", "")), str(request.get("service", ""))
        if action not in ALLOWED_ACTIONS or service not in ALLOWED_SERVICES:
            raise ValueError("服务操作不在允许列表")
        return {"output": run_checked(["systemctl", action, service], timeout=30)}
    if operation == "haproxy_apply":
        content = build_haproxy_config()
        if not content or len(content.encode()) > MAX_REQUEST:
            raise ValueError("生成的 HAProxy 配置大小不合法")
        fd, candidate = tempfile.mkstemp(prefix="stratum-haproxy-", suffix=".cfg", dir="/etc/haproxy")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            run_checked(["haproxy", "-c", "-f", candidate], timeout=15)
            os.chmod(candidate, 0o644)
            os.replace(candidate, "/etc/haproxy/haproxy.cfg")
            run_checked(["systemctl", "reload", "haproxy"], timeout=30)
        finally:
            if os.path.exists(candidate):
                os.unlink(candidate)
        return {"output": "HAProxy 配置已验证并平滑重载"}
    if operation == "firewall_allow":
        port = int(request.get("port", 0))
        if not 1 <= port <= 65535:
            raise ValueError("端口不合法")
        return {"output": run_checked(["ufw", "allow", f"{port}/tcp"], timeout=30)}
    if operation == "toggle_monitor":
        if not isinstance(request.get("enabled"), bool):
            raise ValueError("监控开关不合法")
        content = "SHELL=/bin/bash\nPATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        content += "* * * * * root /root/stratum-monitor.sh\n" if request["enabled"] else "# Monitoring paused from V3 panel\n"
        atomic_write(Path("/etc/cron.d/stratum-monitor"), content, 0o644)
        return {"output": "基础监控开关已更新"}
    if operation == "certificate_apply":
        candidate_name = str(request.get("candidate", ""))
        if not candidate_name or Path(candidate_name).name != candidate_name:
            raise ValueError("证书候选文件名不合法")
        candidate = (Path("/var/lib/stratum-monitor/candidates") / candidate_name).resolve()
        if candidate.parent != Path("/var/lib/stratum-monitor/candidates").resolve() or not candidate.is_file():
            raise ValueError("证书候选文件不存在")
        require_root_owned_tree(candidate)
        relay = json.loads(Path("/etc/stratum-secure-relay.json").read_text(encoding="utf-8"))
        target = Path(str(relay.get("certificate", ""))).resolve()
        if not target.is_file():
            raise ValueError("当前证书路径不存在")
        backup = Path("/var/lib/stratum-monitor/repair-backups") / f"certificate-before-{int(time.time())}.pem"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        shutil.copy2(candidate, target)
        run_checked(["systemctl", "restart", "stratum-secure-relay"], timeout=30)
        return {"output": "证书已替换并重启加密入口", "backup": str(backup)}
    if operation == "upgrade":
        directory = Path(str(request.get("directory", ""))).resolve()
        root = Path("/var/lib/stratum-monitor/candidates").resolve()
        if root not in directory.parents or not directory.is_dir():
            raise ValueError("升级目录不在候选区")
        require_root_owned_tree(directory)
        script = directory / "monitor-panel" / "upgrade-v3-panel.sh"
        if not script.is_file():
            raise ValueError("升级脚本不存在")
        return {"output": run_checked(["/bin/bash", str(script)], timeout=900,
            cwd=str(directory / "monitor-panel"))}
    if operation == "write_managed":
        name, content = str(request.get("name", "")), request.get("content")
        if name not in MANAGED_FILES or not isinstance(content, str) or len(content.encode()) > MAX_REQUEST:
            raise ValueError("托管文件请求不合法")
        path, mode, kind, group = MANAGED_FILES[name]
        if kind == "json":
            value = json.loads(content)
            if not isinstance(value, dict):
                raise ValueError("托管 JSON 必须是对象")
            if name == "peer_config":
                if set(value) - {"enabled", "peers", "token"} or not isinstance(value.get("enabled"), bool):
                    raise ValueError("双 VPS 配置字段不合法")
                if not isinstance(value.get("peers"), list) or len(value["peers"]) > 4 or not isinstance(value.get("token"), str):
                    raise ValueError("双 VPS 配置内容不合法")
            if name == "secure_relay_config" and path.is_file():
                previous = json.loads(path.read_text(encoding="utf-8"))
                for key in set(previous) | set(value):
                    if key not in {"clients", "token"} and previous.get(key) != value.get(key):
                        raise ValueError("客户端管理不能修改加密入口系统配置")
                if "token" in value and value.get("token") != previous.get("token"):
                    raise ValueError("客户端管理不能替换旧版共享密钥")
                clients = value.get("clients", [])
                if not isinstance(clients, list) or len(clients) > 256 or any(not isinstance(item, dict)
                        or set(item) - {"id", "name", "token", "enabled", "alert_enabled"} for item in clients):
                    raise ValueError("矿场客户端列表不合法")
        elif name == "monitor_env":
            for raw in content.splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line or not MONITOR_ENV_KEY.fullmatch(line.split("=", 1)[0].strip()):
                    raise ValueError("监控环境变量不在允许列表")
        if name == "secure_relay_config" and path.is_file():
            shutil.copyfile(path, path.with_suffix(".json.backup"))
            os.chmod(path.with_suffix(".json.backup"), 0o600)
        atomic_write(path, content, mode)
        if grp is not None and hasattr(os, "chown"):
            os.chown(path, 0, grp.getgrnam(group).gr_gid)
        return {"output": f"{name} 已更新"}
    raise ValueError("未知特权操作")


def request_helper(operation, **values):
    payload = json.dumps({"operation": operation, **values}, ensure_ascii=False).encode() + b"\n"
    if len(payload) > MAX_REQUEST:
        raise ValueError("特权请求过大")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(35 if operation != "upgrade" else 920)
        client.connect(str(SOCKET_PATH))
        client.sendall(payload)
        response = b""
        while b"\n" not in response and len(response) <= 65536:
            chunk = client.recv(65536)
            if not chunk:
                break
            response += chunk
    result = json.loads(response.split(b"\n", 1)[0])
    if not result.get("ok"):
        raise OSError(str(result.get("error", "特权助手拒绝请求")))
    return result.get("result", {})


def serve():
    if pwd is None:
        raise RuntimeError("特权助手只能在 Linux 上运行")
    allowed_uid = pwd.getpwnam("stratum-admin").pw_uid
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET_PATH))
        os.chown(SOCKET_PATH, 0, pwd.getpwnam("stratum-admin").pw_gid)
        os.chmod(SOCKET_PATH, 0o660)
        server.listen(16)
        while True:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(10)
                try:
                    _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid not in {0, allowed_uid}:
                        raise PermissionError("调用者身份不允许")
                    raw = b""
                    while b"\n" not in raw and len(raw) <= MAX_REQUEST:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        raw += chunk
                    if len(raw) > MAX_REQUEST:
                        raise ValueError("请求过大")
                    result = {"ok": True, "result": dispatch(json.loads(raw.split(b"\n", 1)[0]))}
                except Exception as exc:
                    result = {"ok": False, "error": str(exc)[:500]}
                try:
                    connection.sendall(json.dumps(result, ensure_ascii=False).encode() + b"\n")
                except OSError:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", required=True)
    parser.parse_args()
    serve()
