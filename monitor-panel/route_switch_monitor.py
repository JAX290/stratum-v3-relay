#!/usr/bin/env python3
"""Finish timed single-miner route trials without requiring the panel to stay open."""

import argparse
import logging
import os
import time
from pathlib import Path

import stratum_admin_v3 as admin
from endpoint_monitor import Notifier


def trial_result(canary, inspector):
    current = admin.endpoint_miner_totals(
        inspector, canary["endpoint_id"], canary["source_ip"], int(canary["port"])
    )
    baseline = canary.get("baseline", {})
    delta = {key: max(0, int(current.get(key, 0)) - int(baseline.get(key, 0)))
        for key in ("submitted", "accepted", "rejected")}
    reject_percent = round(delta["rejected"] * 100 / delta["submitted"], 2) if delta["submitted"] else 100.0
    passed = delta["accepted"] >= 1 and delta["submitted"] >= 1 and reject_percent <= 5.0
    if delta["submitted"] == 0:
        reason = "10分钟内没有观察到Share提交"
    elif delta["accepted"] == 0:
        reason = "矿池没有接受任何Share"
    elif reject_percent > 5.0:
        reason = f"拒绝率 {reject_percent}% 高于5%"
    else:
        reason = f"已接受 {delta['accepted']} 个Share，拒绝率 {reject_percent}%"
    return passed, delta, reject_percent, reason


def evaluate_due(now=None, notifier=None):
    now = int(now or time.time())
    notifier = notifier or Notifier(os.getenv("WECHAT_WEBHOOK", ""), admin.ENDPOINT_EVENT_FILE)
    completed = []
    snapshot = admin.store.load()
    due_ports = [int(item["port"]) for item in snapshot.get("canary_routes", [])
        if item.get("auto_switch", True) and int(item.get("review_after", 0)) <= now]
    for port in due_ports:
        config = admin.store.load()
        canary = next((item for item in config.get("canary_routes", [])
            if int(item.get("port", 0)) == port and item.get("auto_switch", True)), None)
        if not canary or int(canary.get("review_after", 0)) > now:
            continue
        endpoint_map = {item["id"]: item for item in config["endpoints"]}
        endpoint = endpoint_map[canary["endpoint_id"]]
        inspector = admin.load_json(admin.INSPECTOR_STATE_FILE, {"pools": []})
        passed, delta, reject_percent, reason = trial_result(canary, inspector)
        if admin.route_endpoint_id(config, port) != canary.get("original_endpoint_id"):
            passed = False
            reason = "测试期间该端口被人工修改，系统没有覆盖人工设置"
        config["canary_routes"] = [item for item in config.get("canary_routes", []) if item is not canary]
        if passed:
            previous = admin.set_route_endpoint(config, port, canary["endpoint_id"])
            admin.remember_route_change(config, port, previous, canary["endpoint_id"])
            endpoint["verified"] = True
            endpoint["verified_at"] = now
            endpoint["verified_test"] = {"source_ip": canary["source_ip"], **delta,
                "reject_percent": reject_percent}
            admin.save_and_reload(config, f"auto-promote-canary:{port}:{previous}->{canary['endpoint_id']}", actor_value="automatic")
            admin.request_reconnect(port)
            event_type = "canary_passed"
            message = f"单机试跑通过并已自动全量切换。{reason}"
        else:
            admin.save_and_reload(config, f"auto-stop-canary:{port}:{canary['source_ip']}", actor_value="automatic")
            admin.request_reconnect(port, canary["source_ip"])
            event_type = "canary_failed"
            message = f"单机试跑未通过，测试矿机已自动返回原线路。原因：{reason}"
        event = {"time": now, "type": event_type, "port": port, "source_ip": canary["source_ip"],
            "endpoint_id": endpoint["id"], "endpoint": f"{endpoint['host']}:{endpoint['port']}",
            "pool": endpoint.get("pool", "未知矿池"), "region": endpoint.get("region", "未知区域"),
            "message": message, "submitted": delta["submitted"], "accepted": delta["accepted"],
            "rejected": delta["rejected"], "reject_percent": reject_percent}
        notifier(event)
        completed.append(event)
    return completed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        try:
            evaluate_due()
        except Exception:
            logging.exception("automatic route trial evaluation failed")
        if args.once:
            break
        time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
