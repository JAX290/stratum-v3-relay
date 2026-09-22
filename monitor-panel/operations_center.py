#!/usr/bin/env python3
"""Operational diagnostics and issue presentation for the V3 panel."""

import hashlib
from datetime import datetime


GROUPS = (("normal", "正常"), ("recovered", "已恢复"),
    ("repairable", "可自动修复"), ("manual", "需人工处理"))


def _percent(value):
    try:
        return float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return None


def _item(key, title, detail, category="normal", component="VPS"):
    return {"key": key, "title": title, "detail": detail, "category": category,
        "component": component}


def comprehensive_diagnostics(config, services, metrics, certificate, endpoint_state,
        peer, sites, overview, logs, versions, port_checker, now=None):
    """Build a side-effect-free operational snapshot from already loaded state."""
    items = []
    checked = datetime.fromtimestamp(now) if now else datetime.now()
    for name, state in services.items():
        items.append(_item("service:" + name, name, "systemd 状态：" + str(state),
            "normal" if state == "active" else "repairable", "服务"))

    relay = services.get("加密入口", "unknown")
    items.append(_item("tls", "TLS 加密入口", "加密入口服务已运行" if relay == "active" else "加密入口服务当前不可用",
        "normal" if relay == "active" else "repairable", "TLS"))
    days = certificate.get("days_left")
    if certificate.get("available") and days is not None and days >= 14:
        cert_category, cert_detail = "normal", f"证书有效，剩余 {days} 天"
    elif certificate.get("available") and days is not None and days >= 0:
        cert_category, cert_detail = "manual", f"证书将在 {days} 天内到期，请安排更换"
    else:
        cert_category, cert_detail = "manual", "没有读到有效证书，或证书已经过期"
    items.append(_item("certificate", "TLS 证书", cert_detail, cert_category, "证书"))

    for key, title, limit in (("disk", "磁盘空间", 90), ("memory", "内存使用", 95)):
        value, used = metrics.get(key, "-"), _percent(metrics.get(key, "-"))
        items.append(_item(key, title, f"当前使用 {value}" if used is not None else "当前系统未提供使用率",
            "manual" if used is not None and used >= limit else "normal", "系统资源"))

    routes = list(_route_map(config))
    ports = sorted({int(port) for port, _endpoint, _kind, _group in routes})
    closed = [port for port in ports if not port_checker(port)]
    items.append(_item("ports", "本机监听端口", f"{len(ports)} 个生产端口均在监听" if not closed else
        "未监听端口：" + "、".join(map(str, closed)), "normal" if not closed else "repairable", "端口"))

    haproxy = services.get("HAProxy", "unknown")
    items.append(_item("haproxy", "HAProxy 转发", "转发服务正常" if haproxy == "active" else f"转发服务状态：{haproxy}",
        "normal" if haproxy == "active" else "repairable", "HAProxy"))

    endpoint_ids = {str(endpoint) for _port, endpoint, _kind, _group in routes}
    unhealthy = [endpoint_id for endpoint_id in sorted(endpoint_ids)
        if not endpoint_state.get("endpoints", {}).get(endpoint_id, {}).get("last_result", {}).get("ok")]
    items.append(_item("pools", "生产矿池连通性", f"{len(endpoint_ids)} 个在用地址最近检测正常" if not unhealthy else
        "待重新探测：" + "、".join(unhealthy), "normal" if not unhealthy else "repairable", "矿池"))

    peer_enabled = bool(peer.get("enabled"))
    peer_failed = peer_enabled and (peer.get("pending", 0) or peer.get("last_status") == "failed")
    peer_detail = "未启用双 VPS 同步" if not peer_enabled else \
        (f"有 {peer.get('pending', 0)} 项等待重试" if peer_failed else "最近同步正常")
    items.append(_item("peer", "双 VPS 同步", peer_detail, "repairable" if peer_failed else "normal", "双 VPS"))

    offline = [site for site in sites if site.get("status") == "offline"]
    waiting = [site for site in sites if site.get("status") == "waiting"]
    heartbeat_detail = ("离线矿场：" + "、".join(site.get("name", "未命名") for site in offline)) if offline else \
        (f"{len(waiting)} 个矿场等待首次心跳" if waiting else f"{len(sites)} 个矿场心跳正常")
    items.append(_item("heartbeat", "Windows 心跳", heartbeat_detail, "manual" if offline else "normal", "Windows"))

    stale = [site.get("name", "未命名") for site in sites if site.get("client_outdated") or site.get("server_outdated")]
    version_text = "版本一致：面板 {panel} / 加密入口 {secure_relay} / Windows {windows_client}".format(**versions)
    items.append(_item("versions", "组件版本", version_text if not stale else "版本不一致的矿场：" + "、".join(stale),
        "normal" if not stale else "manual", "版本"))
    latest_share = max((str(site.get("last_share", "")) for site in sites
        if site.get("last_share") not in {"", "尚无"}), default="尚无")
    items.append(_item("share", "最近 Share", f"最近记录：{latest_share}；累计提交 {overview.get('submitted', 0)}，接受 {overview.get('accepted', 0)}",
        "normal" if latest_share != "尚无" or not sites else "manual", "生产"))

    seen = set()
    for index, entry in enumerate(reversed(logs.get("entries", []))):
        if entry.get("level") != "good" or entry.get("title") != "服务已经恢复" or entry.get("service") in seen:
            continue
        seen.add(entry.get("service"))
        items.append(_item(f"recovered:{index}", f"{entry.get('service')}已恢复",
            f"{entry.get('display_time', '')} · {entry.get('message', '')}", "recovered", "恢复记录"))
        if len(seen) >= 10:
            break

    grouped = [{"key": key, "label": label, "items": [item for item in items if item["category"] == key]}
        for key, label in GROUPS]
    return {"checked_at": checked.strftime("%Y-%m-%d %H:%M:%S"), "items": items, "groups": grouped,
        "counts": {key: sum(item["category"] == key for item in items) for key, _label in GROUPS}}


def reconcile_issues(diagnostics, previous, sites, now):
    """Keep stable issue IDs and attach administrator-facing business impact."""
    previous = previous if isinstance(previous, dict) else {}
    old = previous.get("issues", {}) if isinstance(previous.get("issues", {}), dict) else {}
    current_items = {item["key"]: item for item in diagnostics.get("items", [])
        if item.get("category") in {"repairable", "manual"}}
    online_sites = [site for site in sites if site.get("status") != "disabled"]
    records = {}
    for key, item in current_items.items():
        prior = old.get(key, {}) if isinstance(old.get(key, {}), dict) else {}
        first_seen = int(prior.get("first_seen", now) or now)
        impacted = _impacted_sites(key, online_sites)
        records[key] = {"id": prior.get("id") or _issue_id(key), "key": key,
            "title": item.get("title", key), "detail": item.get("detail", ""),
            "category": item.get("category"), "first_seen": first_seen, "last_seen": now,
            "ongoing": True, "affected_sites": [site.get("name", "未命名矿场") for site in impacted],
            "miner_count": sum(_site_miners(site) for site in impacted),
            "duration": _duration(now - first_seen),
            "attempted_actions": list(prior.get("attempted_actions", []))[-10:]}
    for key, prior in old.items():
        if key in records or not isinstance(prior, dict):
            continue
        resolved_at = int(prior.get("resolved_at", now) or now)
        if prior.get("ongoing", True):
            resolved_at = now
        if now - resolved_at > 7 * 86400:
            continue
        records[key] = {**prior, "ongoing": False, "resolved_at": resolved_at,
            "duration": _duration(resolved_at - int(prior.get("first_seen", resolved_at) or resolved_at))}
    issues = sorted(records.values(), key=lambda item: (not item.get("ongoing"), -int(item.get("last_seen", 0))))
    return {"updated_at": now, "issues": records, "rows": issues}


def _issue_id(key):
    return "VPS-" + hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:8].upper()


def _site_miners(site):
    return int(site.get("miner_count", site.get("impact_miner_count", 0)) or site.get("impact_miner_count", 0) or 0)


def _impacted_sites(key, sites):
    if key == "heartbeat":
        return [site for site in sites if site.get("status") == "offline"]
    if key == "versions":
        return [site for site in sites if site.get("client_outdated") or site.get("server_outdated")]
    if key in {"disk", "memory", "certificate", "peer"}:
        return []
    return sites


def _duration(seconds):
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return "不足1分钟"
    if seconds < 3600:
        return f"{seconds // 60}分钟"
    if seconds < 86400:
        return f"{seconds // 3600}小时{seconds % 3600 // 60}分钟"
    return f"{seconds // 86400}天{seconds % 86400 // 3600}小时"


def _route_map(config):
    endpoint_ids = {item.get("id") for item in config.get("endpoints", [])}
    for group in config.get("port_groups", []):
        group_endpoints = group.get("endpoint_ids", [])
        for index, port in enumerate(group.get("ports", [])):
            endpoint_id = group_endpoints[index] if index < len(group_endpoints) else ""
            if endpoint_id in endpoint_ids:
                yield int(port), endpoint_id, "group", group.get("name", "端口组")
    for route in config.get("fixed_routes", []):
        if route.get("endpoint_id") in endpoint_ids:
            yield int(route["port"]), route["endpoint_id"], "fixed", "固定线路"
