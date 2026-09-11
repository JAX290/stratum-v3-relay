#!/usr/bin/env python3
"""V3 endpoint health and stability monitor.

Active routes and endpoints referenced only by saved templates receive
configurable Stratum probes. Automatic probing can be disabled when upstream
pools rate-limit standalone mining.subscribe health checks.
"""

import argparse
import json
import math
import os
import socket
import statistics
import tempfile
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from ipaddress import ip_address
from pathlib import Path


CONFIG_FILE = Path(os.getenv("V3_CONFIG_FILE", "/etc/stratum-v3.json"))
STATE_FILE = Path(os.getenv("ENDPOINT_STATE_FILE", "/var/lib/stratum-monitor/endpoints.json"))
EVENT_FILE = Path(os.getenv("ENDPOINT_EVENT_FILE", "/var/log/stratum-endpoints.jsonl"))
BEIJING = ZoneInfo("Asia/Shanghai")


def beijing_time(timestamp):
    return datetime.fromtimestamp(int(timestamp), BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def describe_error(value):
    text = str(value or "未知错误")
    if text.startswith("TCP连接成功"):
        return text + "；这通常表示矿池限制探测频率或临时未响应，不代表已有矿工连接中断"
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "连接或 Stratum 响应超过 3 秒，可能是临时网络拥塞、矿池限流或上游响应变慢"
    if "no valid stratum subscribe response" in lowered:
        return "TCP 已连接，但未收到有效的 mining.subscribe 响应；矿池可能限流健康探测，不能据此认定矿工连接中断"
    if "name or service" in lowered or "getaddrinfo" in lowered or "dns" in lowered:
        return "DNS 解析失败，服务器暂时无法获得矿池域名对应的公网地址"
    if "refused" in lowered:
        return "目标主动拒绝 TCP 连接，可能是端口关闭、访问策略限制或矿池临时维护"
    if "unreachable" in lowered or "no route" in lowered:
        return "服务器到目标地址的网络路由不可达"
    return f"原始错误：{text}"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def resolve_public(host):
    addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})
    if not addresses:
        raise OSError("DNS returned no addresses")
    for value in addresses:
        address = ip_address(value)
        if not address.is_global:
            raise OSError(f"unsafe upstream address: {value}")
    return addresses


def _receive_response(connection, request_id, timeout):
    buffer = b""
    deadline = time.monotonic() + timeout
    while len(buffer) < 1024 * 1024 and time.monotonic() < deadline:
        connection.settimeout(max(0.1, deadline - time.monotonic()))
        chunk = connection.recv(65536)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            try:
                message = json.loads(raw.strip())
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(message, dict) and message.get("id") == request_id and ("result" in message or "error" in message):
                return message
    return None


def probe_stratum(endpoint, timeout=3.0, username="", password="x"):
    started = time.perf_counter()
    addresses = resolve_public(endpoint["host"])
    tcp_started = time.perf_counter()
    transport = endpoint.get("transport", "tcp")
    if transport != "tcp":
        raise OSError(f"unsupported transport: {transport}")
    with socket.create_connection((endpoint["host"], int(endpoint["port"])), timeout=timeout) as connection:
        tcp_ms = (time.perf_counter() - tcp_started) * 1000
        connection.settimeout(timeout)
        request = {"id": 73001, "method": "mining.subscribe", "params": ["stratum-v3-health/1.0"]}
        connection.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
        try:
            subscribe = _receive_response(connection, 73001, timeout)
        except socket.timeout:
            return {"ok": False, "tcp_ok": True, "checked_at": int(time.time()), "resolved_ips": addresses,
                "tcp_ms": round(tcp_ms, 2), "stratum_ms": None, "error_code": "stratum_timeout",
                "error": "TCP连接成功，但等待 mining.subscribe 响应超时"}
        if not subscribe:
            return {"ok": False, "tcp_ok": True, "checked_at": int(time.time()), "resolved_ips": addresses,
                "tcp_ms": round(tcp_ms, 2), "stratum_ms": None, "error_code": "stratum_no_response",
                "error": "TCP连接成功，但未收到有效的 mining.subscribe 响应"}
        if subscribe.get("error") or subscribe.get("result") is None:
            return {"ok": False, "tcp_ok": True, "checked_at": int(time.time()), "resolved_ips": addresses,
                "tcp_ms": round(tcp_ms, 2), "stratum_ms": None, "error_code": "stratum_rejected",
                "error": f"矿池拒绝 mining.subscribe：{str(subscribe.get('error') or '未返回订阅结果')[:200]}"}
        authorized = None
        authorization_error = ""
        if username:
            request = {"id": 73002, "method": "mining.authorize", "params": [str(username)[:200], str(password)[:200]]}
            connection.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
            try:
                response = _receive_response(connection, 73002, timeout)
            except socket.timeout:
                response = None
            if response is None:
                authorized = False
                authorization_error = "矿池账号认证等待超时"
            else:
                authorized = response.get("result") is True and not response.get("error")
                if not authorized:
                    authorization_error = str(response.get("error") or "矿池拒绝该账号")[:200]
    return {
        "ok": True,
        "tcp_ok": True,
        "checked_at": int(time.time()),
        "resolved_ips": addresses,
        "tcp_ms": round(tcp_ms, 2),
        "stratum_ms": round((time.perf_counter() - started) * 1000, 2),
        "protocol": "Stratum V1",
        "authorized": authorized,
        "authorization_error": authorization_error,
        "error": "",
    }


class EndpointMonitor:
    def __init__(self, config, state=None, probe=None, event_callback=None):
        self.config = config
        self.settings = config.get("settings", {})
        self.state = state or {"version": 1, "endpoints": {}, "last_hourly": 0, "last_daily": 0}
        self.probe = probe or probe_stratum
        self.event_callback = event_callback
        self.endpoint_map = {item["id"]: item for item in config.get("endpoints", []) if item.get("enabled", True)}
        self.active_ids = self._active_ids()
        self.template_ids = {
            endpoint_id
            for template in config.get("templates", []) if template.get("enabled", True)
            for endpoint_id in template.get("endpoint_ids", [])
        }
        # V3.1 migration: older builds allowed hourly/daily batch failures to
        # open operational alerts. Clear only those batch-derived alert states.
        for entry in self.state.get("endpoints", {}).values():
            if entry.get("alerting") and entry.get("last_result", {}).get("sample_type") in {"hourly", "daily"}:
                entry["alerting"] = False
                entry["consecutive_failures"] = 0
                entry["first_failure"] = 0
            if entry.get("alert_kind") == "endpoint_protocol_degraded":
                entry["alerting"] = False
                entry["consecutive_failures"] = 0
                entry["first_failure"] = 0
                entry["alert_kind"] = ""

    def _active_ids(self):
        active = {item["endpoint_id"] for item in self.config.get("fixed_routes", [])}
        for group in self.config.get("port_groups", []):
            active.update(group.get("endpoint_ids", []))
        active.update(item.get("endpoint_id") for item in self.config.get("canary_routes", []))
        active.discard(None)
        return active

    def _entry(self, endpoint_id):
        return self.state.setdefault("endpoints", {}).setdefault(endpoint_id, {
            "last_check": 0,
            "last_ok": 0,
            "consecutive_failures": 0,
            "alerting": False,
            "last_alert": 0,
            "samples": [],
        })

    def _emit(self, kind, endpoint, entry, message, now, result=None, extra=None):
        event = {
            "time": int(now), "type": kind, "endpoint_id": endpoint["id"],
            "endpoint": f"{endpoint['host']}:{endpoint['port']}", "message": message,
            "pool": endpoint.get("pool", "未知矿池"), "region": endpoint.get("region", "未知区域"),
            "failures": entry["consecutive_failures"], "last_ok": entry.get("last_ok", 0),
            "first_failure": entry.get("first_failure", 0), "result": result or {},
        }
        if extra:
            event.update(extra)
        self.state.setdefault("events", []).append(event)
        self.state["events"] = self.state["events"][-200:]
        if self.event_callback:
            self.event_callback(event)

    def record(self, endpoint, result, now=None, sample_type="health"):
        now = int(now or time.time())
        entry = self._entry(endpoint["id"])
        was_alerting = entry.get("alerting", False)
        result = {**result, "checked_at": now, "sample_type": sample_type}
        entry["last_check"] = now
        entry["last_result"] = result
        entry["samples"].append(result)
        entry["samples"] = entry["samples"][-2880:]
        if sample_type != "health":
            self._summarize(entry, now)
            return
        previous_failures = entry.get("consecutive_failures", 0)
        if result.get("ok"):
            entry["last_ok"] = now
            entry["consecutive_failures"] = 0
            entry["protocol_consecutive_failures"] = 0
            entry["alerting"] = False
            if was_alerting:
                self._emit("recovery", endpoint, entry, "上游 Stratum 健康探测已恢复正常", now, result,
                    {"previous_failures": previous_failures, "outage_seconds": max(0, now - entry.get("first_failure", now))})
            entry["first_failure"] = 0
        else:
            if result.get("tcp_ok"):
                # Many pools accept TCP but deliberately rate-limit or ignore
                # standalone mining.subscribe probes while real miners remain
                # healthy. Keep this as a stability statistic only; never send
                # an operational alert for it.
                entry["protocol_consecutive_failures"] = entry.get("protocol_consecutive_failures", 0) + 1
                entry["consecutive_failures"] = 0
                if was_alerting and entry.get("alert_kind") == "endpoint_down":
                    self._emit("tcp_recovery_protocol_degraded", endpoint, entry,
                        "上游TCP连接已恢复，但独立Stratum探测仍未收到有效响应", now, result,
                        {"previous_failures": previous_failures, "outage_seconds": max(0, now - entry.get("first_failure", now))})
                    entry["alerting"] = False
                    entry["alert_kind"] = ""
                    entry["first_failure"] = 0
                if entry.get("alert_kind") == "endpoint_protocol_degraded":
                    entry["alerting"] = False
                    entry["alert_kind"] = ""
                self._summarize(entry, now)
                return
            entry["protocol_consecutive_failures"] = 0
            entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
            if entry["consecutive_failures"] == 1:
                entry["first_failure"] = now
            threshold = int(self.settings.get("failure_alert_count", 3))
            alert_kind = "endpoint_down"
            if entry["consecutive_failures"] >= threshold and not was_alerting:
                entry["alerting"] = True
                entry["last_alert"] = now
                entry["alert_kind"] = alert_kind
                self._emit(alert_kind, endpoint, entry, describe_error(result.get("error")), now, result)
            elif entry["consecutive_failures"] >= threshold and was_alerting:
                repeat = int(self.settings.get("alert_repeat_seconds", 900))
                if now - entry.get("last_alert", 0) >= repeat:
                    entry["last_alert"] = now
                    self._emit("endpoint_still_down", endpoint, entry, describe_error(result.get("error")), now, result)
        self._summarize(entry, now)

    @staticmethod
    def _summarize(entry, now):
        window = [item for item in entry["samples"] if item.get("checked_at", 0) >= now - 86400]
        successes = [item for item in window if item.get("ok")]
        latencies = [item["stratum_ms"] for item in successes if item.get("stratum_ms") is not None]
        entry["stats_24h"] = {
            "samples": len(window),
            "success_percent": round(len(successes) * 100 / len(window), 2) if window else None,
            "median_ms": round(statistics.median(latencies), 2) if latencies else None,
            "p95_ms": round(percentile(latencies, 0.95), 2) if latencies else None,
            "jitter_ms": round(statistics.pstdev(latencies), 2) if len(latencies) > 1 else 0 if latencies else None,
        }

    def check(self, endpoint_id, now=None, sample_type="health"):
        endpoint = self.endpoint_map[endpoint_id]
        try:
            result = self.probe(endpoint)
        except Exception as exc:
            result = {"ok": False, "tcp_ms": None, "stratum_ms": None, "resolved_ips": [], "error": str(exc)[:200]}
        self.record(endpoint, result, now=now, sample_type=sample_type)

    def run_due(self, now=None):
        now = int(now or time.time())
        if self.settings.get("endpoint_probe_enabled", True) is False:
            self.state["updated_at"] = now
            self.state["probe_disabled"] = True
            return
        self.state["probe_disabled"] = False
        active_interval = int(self.settings.get("active_probe_seconds", 30))
        template_interval = int(self.settings.get("template_probe_seconds", 600))
        for endpoint_id in sorted(self.active_ids | self.template_ids):
            if endpoint_id not in self.endpoint_map:
                continue
            if endpoint_id in self.active_ids and active_interval <= 0:
                continue
            if endpoint_id not in self.active_ids and template_interval <= 0:
                continue
            interval = active_interval if endpoint_id in self.active_ids else template_interval
            if now - self._entry(endpoint_id).get("last_check", 0) >= interval:
                self.check(endpoint_id, now=now)

        if self.settings.get("endpoint_batch_probe_enabled", True) is False:
            self.state["updated_at"] = now
            return
        hour = now - now % 3600
        if not self.state.get("last_hourly"):
            self.state["last_hourly"] = hour
        elif self.state.get("last_hourly", 0) < hour:
            self.run_batch(int(self.settings.get("hourly_samples", 10)), now, "hourly")
            self.state["last_hourly"] = hour
        day = now - now % 86400
        if not self.state.get("last_daily"):
            self.state["last_daily"] = day
        elif self.state.get("last_daily", 0) < day:
            self.run_batch(int(self.settings.get("daily_samples", 20)), now, "daily")
            self.state["last_daily"] = day
        self.state["updated_at"] = now

    def run_batch(self, rounds, now, sample_type):
        delay = float(self.settings.get("sample_interval_seconds", 0.5))
        endpoint_ids = sorted(self.active_ids | self.template_ids)
        for round_number in range(rounds):
            for endpoint_id in endpoint_ids:
                if endpoint_id in self.endpoint_map:
                    self.check(endpoint_id, now=now + round_number, sample_type=sample_type)
            if delay and round_number + 1 < rounds:
                time.sleep(delay)


class Notifier:
    def __init__(self, webhook="", event_path=EVENT_FILE):
        self.webhook = webhook
        self.event_path = event_path

    def __call__(self, event):
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        with self.event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        if not self.webhook.startswith("https://"):
            return
        content = self.format_message(event)
        payload = json.dumps({"msgtype": "text", "text": {"content": content}}, ensure_ascii=False).encode()
        request = urllib.request.Request(self.webhook, data=payload, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=8).close()
        except OSError:
            pass

    @staticmethod
    def format_message(event):
        kind = event.get("type", "unknown")
        timestamp = beijing_time(event.get("time", time.time()))
        endpoint = event.get("endpoint", "未知")
        if kind in {"endpoint_down", "endpoint_protocol_degraded", "endpoint_still_down"}:
            if kind == "endpoint_down":
                title = "【严重】上游 Stratum TCP连接连续失败"
            elif kind == "endpoint_protocol_degraded":
                title = "【警告】上游TCP可达，但Stratum健康探测连续无响应"
            else:
                title = "【持续异常】上游 Stratum 探测仍未恢复"
            last_ok = beijing_time(event["last_ok"]) if event.get("last_ok") else "监控启动后尚无成功记录"
            return (f"{title}\n时间（北京时间）：{timestamp}\n矿池：{event.get('pool', '未知')}\n区域：{event.get('region', '未知')}"
                f"\n上游地址：{endpoint}\n连续失败次数：{event.get('failures', 0)} 次\n最近成功时间：{last_ok}"
                f"\n失败原因：{event.get('message', '未知')}\n影响说明：这是独立健康探测失败，不等同于现有矿工连接已经中断；请同时核对矿池后台有效算力、Share 和矿机连接状态。"
                "\n建议检查：服务器到矿池的网络、DNS、目标端口，以及矿池是否限制频繁的 mining.subscribe 探测。")
        if kind == "recovery":
            return (f"【恢复】上游 Stratum 健康探测恢复\n时间（北京时间）：{timestamp}\n矿池：{event.get('pool', '未知')}"
                f"\n区域：{event.get('region', '未知')}\n上游地址：{endpoint}\n异常期间累计失败：{event.get('previous_failures', 0)} 次"
                f"\n异常持续约：{event.get('outage_seconds', 0)} 秒\n说明：健康探测已重新收到有效响应。")
        if kind == "tcp_recovery_protocol_degraded":
            return (f"【恢复】上游TCP连接已恢复\n时间（北京时间）：{timestamp}\n矿池：{event.get('pool', '未知')}"
                f"\n区域：{event.get('region', '未知')}\n上游地址：{endpoint}\n说明：TCP已经重新可达；矿池仍未回应独立 mining.subscribe 探测，该情况只记录稳定性，不再发送异常通知。")
        if kind in {"canary_passed", "canary_failed", "verified_route_applied"}:
            titles = {"canary_passed": "【成功】新矿池单机测试通过并已自动切换",
                "canary_failed": "【退回】新矿池单机测试未通过",
                "verified_route_applied": "【切换】已验证矿池地址已启用"}
            shares = ""
            if kind != "verified_route_applied":
                shares = (f"\n测试Share（提交/接受/拒绝）：{event.get('submitted', 0)} / "
                    f"{event.get('accepted', 0)} / {event.get('rejected', 0)}"
                    f"\n测试拒绝率：{event.get('reject_percent', 0)}%")
            return (f"{titles[kind]}\n时间（北京时间）：{timestamp}\n端口：{event.get('port', '未知')}"
                f"\n测试矿机：{event.get('source_ip') or '无'}\n矿池：{event.get('pool', '未知')}"
                f"\n上游地址：{endpoint}{shares}\n结果：{event.get('message', '未提供')}")
        if kind == "integrity_changed":
            return (f"【严重】中转服务器受保护文件发生变化\n时间（北京时间）：{timestamp}\n文件：{endpoint}"
                f"\n变化说明：{event.get('message', '文件内容或存在状态变化')}\n原批准哈希：{event.get('expected', {}).get('sha256') or '文件原本不存在'}"
                f"\n当前哈希：{event.get('observed', {}).get('sha256') or '文件当前不存在'}\n影响说明：配置、程序或服务文件可能被人工修改、升级替换或异常篡改。"
                "\n建议检查：配置审计记录、最近升级操作和该文件的修改时间；未经授权的变化应立即回滚。")
        if kind == "integrity_recovery":
            return f"【恢复】受保护文件已恢复批准版本\n时间（北京时间）：{timestamp}\n文件：{endpoint}\n说明：当前文件哈希已与批准基线一致。"
        protocol_titles = {
            "worker_changed": "Worker 授权身份在同一连接内发生变化",
            "worker_mismatch": "Share 提交使用的 Worker 与授权 Worker 不一致",
            "unknown_job": "Share 使用了检查器未观察到的 Job",
        }
        if kind in protocol_titles:
            level = "【警告】" if kind == "unknown_job" else "【严重】"
            return (f"{level}Stratum 协议一致性异常\n时间（北京时间）：{timestamp}\n异常类型：{protocol_titles[kind]}"
                f"\n线路：{endpoint}\n来源矿机 IP：{event.get('source_ip') or '未知'}\nWorker：{event.get('worker') or '未知'}"
                f"\n本次聚合异常数：{event.get('occurrences', 1)} 次\n详细信息：{event.get('message', '无')}"
                "\n影响说明：可能是矿机重复授权、协议实现差异、任务切换竞态或流量被修改；未知Job单次出现不代表Share被拒绝，应结合后续Share接受情况判断。"
                "\n通知策略：相同线路、Worker和异常类型在15分钟内聚合，不再逐条发送。")
        return f"【监控通知】Stratum 中转事件\n时间（北京时间）：{timestamp}\n对象：{endpoint}\n详细信息：{event.get('message', '未提供')}"


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_FILE)
    parser.add_argument("--state", type=Path, default=STATE_FILE)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = load_json(args.config, {})
    state = load_json(args.state, {"version": 1, "endpoints": {}, "last_hourly": 0, "last_daily": 0})
    notifier = Notifier(os.getenv("WECHAT_WEBHOOK", ""))
    monitor = EndpointMonitor(config, state=state, event_callback=notifier)
    config_mtime = args.config.stat().st_mtime_ns if args.config.exists() else 0
    while True:
        current_mtime = args.config.stat().st_mtime_ns if args.config.exists() else 0
        if current_mtime != config_mtime:
            config = load_json(args.config, config)
            monitor = EndpointMonitor(config, state=monitor.state, event_callback=notifier)
            config_mtime = current_mtime
        monitor.run_due()
        atomic_json(args.state, monitor.state)
        if args.once:
            break
        time.sleep(5)


if __name__ == "__main__":
    main()
