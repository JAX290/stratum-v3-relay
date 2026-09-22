#!/usr/bin/env python3
"""Validation plans for high-risk VPS operations."""

import json
import re
import secrets
import socket
import time
from ipaddress import ip_address
from pathlib import Path


HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def validate_plan(operation, values, config, relay_config, versions, candidate_root,
        validate_config, certificate_checker, endpoint_probe, resolver=None, now=None):
    now = int(now or time.time())
    candidate_root = Path(candidate_root).resolve()
    resolver = resolver or _resolve_public
    checks, data, summary, affected_ports = [], {}, "", []
    if operation == "route":
        port, endpoint_id = int(values.get("port", 0)), str(values.get("endpoint_id", ""))
        routes = list(_routes(config))
        if port not in {item[0] for item in routes}:
            raise ValueError("线路端口不在当前生产配置中")
        endpoint = next((item for item in config.get("endpoints", []) if item.get("id") == endpoint_id), None)
        if not endpoint:
            raise ValueError("候选矿池地址不存在")
        candidate = json.loads(json.dumps(config))
        _set_route(candidate, port, endpoint_id)
        validate_config(candidate, resolve=True)
        checks.append("候选线路配置和 DNS 已验证")
        result = endpoint_probe(endpoint)
        if not result.get("ok"):
            raise ValueError("候选矿池 Stratum 探测未通过：" + str(result.get("error", "未知错误")))
        checks.append("候选矿池 TCP 和 Stratum 探测已通过")
        data, affected_ports = {"port": port, "endpoint_id": endpoint_id}, [port]
        summary = f"把端口 {port} 切换到 {endpoint.get('pool', endpoint_id)} · {endpoint.get('region', '')}"
    elif operation == "certificate":
        path = _candidate_file(candidate_root, values.get("candidate", ""), ".pem")
        certificate = certificate_checker(path)
        if not certificate.get("available") or certificate.get("days_left") is None or certificate["days_left"] < 7:
            raise ValueError("候选证书无效、已过期或有效期不足 7 天")
        checks.extend(["候选 PEM 文件位于受限暂存目录", "证书结构和有效期已验证"])
        data = {"candidate": path.name}
        affected_ports = [int(relay_config.get("listen_port", 0) or 0)]
        summary = f"替换加密入口证书；候选证书剩余 {certificate['days_left']} 天"
    elif operation == "firewall":
        desired = sorted({int(item.strip()) for item in str(values.get("ports", "")).split(",") if item.strip()})
        required = sorted({22, int(relay_config.get("listen_port", 0) or 0)} |
            {port for port, _endpoint in _routes(config)})
        required = [port for port in required if port]
        if any(port < 1 or port > 65535 for port in desired) or not set(required).issubset(desired):
            raise ValueError("候选防火墙端口必须保留 SSH、TLS 入口和全部生产转发端口")
        checks.extend(["SSH 管理端口已保留", "TLS 和全部生产端口已保留", "端口范围合法"])
        data, affected_ports = {"ports": desired}, required
        summary = f"应用 {len(desired)} 个 TCP 放行端口（仅增加规则，不自动删除现有规则）"
    elif operation == "dns":
        hostname = str(values.get("hostname", "")).strip().lower()
        if not HOST_RE.fullmatch(hostname):
            raise ValueError("候选 DNS 名称格式不正确")
        addresses = resolver(hostname)
        if not addresses or any(not ip_address(item).is_global for item in addresses):
            raise ValueError("候选 DNS 没有全部解析到公网地址")
        checks.extend(["DNS 名称格式已验证", f"解析得到 {len(addresses)} 个公网地址"])
        data = {"hostname": hostname}
        affected_ports = [int(relay_config.get("listen_port", 0) or 0)]
        summary = "把加密入口公开地址改为已验证的候选 DNS"
    elif operation == "upgrade":
        directory = _candidate_directory(candidate_root, values.get("candidate", ""))
        manifest_path = directory / "version.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not all(VERSION_RE.fullmatch(str(manifest.get(key, ""))) for key in versions):
            raise ValueError("候选升级包版本清单不完整")
        if not any(_version_tuple(manifest[key]) > _version_tuple(versions[key]) for key in versions):
            raise ValueError("候选升级包没有更高版本")
        script = directory / "monitor-panel" / "upgrade-v3-panel.sh"
        if not script.is_file():
            raise ValueError("候选升级包缺少受控升级脚本")
        checks.extend(["候选目录位于受限暂存目录", "版本清单完整且至少一个组件升级", "受控升级脚本存在"])
        data, summary = {"candidate": directory.name, "versions": manifest}, "执行已暂存并通过结构检查的版本升级"
    else:
        raise ValueError("不支持的高风险操作")
    return {"id": secrets.token_hex(12), "operation": operation, "created_at": now,
        "expires_at": now + 600, "summary": summary, "checks": checks, "data": data,
        "affected_ports": affected_ports, "validated": True}


def _candidate_file(root, value, suffix):
    name = str(value or "")
    if Path(name).name != name or not name.lower().endswith(suffix):
        raise ValueError("候选文件名不合法")
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise ValueError("候选文件不在受限暂存目录")
    return path


def _candidate_directory(root, value):
    name = str(value or "")
    if Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
        raise ValueError("候选目录名不合法")
    path = (root / name).resolve()
    if path.parent != root or not path.is_dir():
        raise ValueError("候选目录不在受限暂存目录")
    return path


def _resolve_public(hostname):
    return sorted({row[4][0] for row in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)})


def _version_tuple(value):
    return tuple(int(item) for item in str(value).split("."))


def _routes(config):
    for group in config.get("port_groups", []):
        for index, port in enumerate(group.get("ports", [])):
            yield int(port), group.get("endpoint_ids", [])[index]
    for route in config.get("fixed_routes", []):
        yield int(route["port"]), route["endpoint_id"]


def _set_route(config, port, endpoint_id):
    for group in config.get("port_groups", []):
        if port in group.get("ports", []):
            group["endpoint_ids"][group["ports"].index(port)] = endpoint_id
            return
    for route in config.get("fixed_routes", []):
        if int(route.get("port", 0)) == port:
            route["endpoint_id"] = endpoint_id
            return
    raise ValueError("线路端口不存在")
