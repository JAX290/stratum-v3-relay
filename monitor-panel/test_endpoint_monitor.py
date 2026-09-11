import json
import unittest
from pathlib import Path
from unittest.mock import patch

from endpoint_monitor import EndpointMonitor, Notifier, probe_stratum


class EndpointMonitorTest(unittest.TestCase):
    def setUp(self):
        self.config = {
            "settings": {
                "endpoint_probe_enabled": True,
                "endpoint_batch_probe_enabled": False,
                "active_probe_seconds": 30,
                "template_probe_seconds": 600,
                "hourly_samples": 2,
                "daily_samples": 2,
                "sample_interval_seconds": 0,
                "failure_alert_count": 3,
                "protocol_failure_alert_count": 6,
            },
            "endpoints": [
                {"id": "active", "host": "pool.example", "port": 3333},
                {"id": "saved", "host": "saved.example", "port": 3333},
            ],
            "templates": [{"id": "template", "enabled": True, "endpoint_ids": ["active", "saved"]}],
            "port_groups": [{"id": "group", "ports": [10010], "endpoint_ids": ["active"]}],
            "fixed_routes": [],
        }

    def test_active_and_saved_template_intervals(self):
        calls = []

        def probe(endpoint):
            calls.append(endpoint["id"])
            return {"ok": True, "tcp_ms": 1, "stratum_ms": 2, "resolved_ips": ["203.0.113.1"], "error": ""}

        monitor = EndpointMonitor(self.config, probe=probe)
        monitor.run_due(now=3600)
        self.assertEqual(calls, ["active", "saved"])
        calls.clear()
        monitor.run_due(now=3631)
        self.assertEqual(calls, ["active"])
        calls.clear()
        monitor.run_due(now=4201)
        self.assertEqual(calls, ["active", "saved"])

    def test_automatic_probe_can_be_disabled(self):
        calls = []
        self.config["settings"]["endpoint_probe_enabled"] = False
        monitor = EndpointMonitor(self.config, probe=lambda endpoint: calls.append(endpoint["id"]) or {"ok": True})
        monitor.run_due(now=7200)
        self.assertEqual(calls, [])
        self.assertTrue(monitor.state["probe_disabled"])

    def test_batch_sampling_can_be_disabled(self):
        calls = []
        self.config["settings"]["endpoint_batch_probe_enabled"] = False
        state = {"version": 1, "endpoints": {}, "last_hourly": 3600, "last_daily": 0}
        monitor = EndpointMonitor(self.config, state=state, probe=lambda endpoint: calls.append(endpoint["id"]) or {"ok": True})
        monitor.run_due(now=7200)
        self.assertEqual(calls, ["active", "saved"])

    def test_three_failures_alert_and_recovery(self):
        results = iter([
            {"ok": False, "error": "down"},
            {"ok": False, "error": "down"},
            {"ok": False, "error": "down"},
            {"ok": True, "tcp_ms": 1, "stratum_ms": 2},
        ])
        events = []
        monitor = EndpointMonitor(self.config, probe=lambda endpoint: next(results), event_callback=events.append)
        for number in range(4):
            monitor.check("active", now=100 + number)
        self.assertEqual([item["type"] for item in events], ["endpoint_down", "recovery"])

    def test_stability_samples_do_not_trigger_operational_alarm(self):
        events = []
        monitor = EndpointMonitor(self.config, probe=lambda endpoint: {"ok": False, "error": "timed out"}, event_callback=events.append)
        for number in range(10):
            monitor.check("active", now=100 + number, sample_type="hourly")
        entry = monitor.state["endpoints"]["active"]
        self.assertEqual(entry["consecutive_failures"], 0)
        self.assertFalse(entry["alerting"])
        self.assertEqual(events, [])

    def test_tcp_reachable_protocol_failure_never_opens_operational_alert(self):
        events = []
        monitor = EndpointMonitor(self.config, probe=lambda endpoint: {"ok": False, "tcp_ok": True,
            "error_code": "stratum_no_response", "error": "TCP连接成功，但未收到有效响应"}, event_callback=events.append)
        for number in range(30):
            monitor.check("active", now=200 + number)
        self.assertEqual(events, [])
        entry = monitor.state["endpoints"]["active"]
        self.assertFalse(entry["alerting"])
        self.assertEqual(entry["consecutive_failures"], 0)
        self.assertEqual(entry["protocol_consecutive_failures"], 30)

    def test_notification_uses_beijing_time_and_detailed_chinese(self):
        message = Notifier.format_message({"type": "endpoint_down", "time": 0, "endpoint": "pool.example:3333",
            "pool": "测试矿池", "region": "亚洲", "failures": 3, "last_ok": 0, "message": "连接超时"})
        self.assertIn("时间（北京时间）：1970-01-01 08:00:00", message)
        self.assertIn("不等同于现有矿工连接已经中断", message)

    def test_old_batch_derived_alert_is_cleared_on_upgrade(self):
        state = {"version": 1, "endpoints": {"active": {"alerting": True, "consecutive_failures": 8,
            "first_failure": 10, "last_result": {"sample_type": "hourly"}, "samples": []}}}
        monitor = EndpointMonitor(self.config, state=state, probe=lambda endpoint: {"ok": True})
        entry = monitor.state["endpoints"]["active"]
        self.assertFalse(entry["alerting"])
        self.assertEqual(entry["consecutive_failures"], 0)

    def test_repository_config_is_consistent(self):
        config = json.loads(Path("v3-config.json").read_text(encoding="utf-8"))
        endpoint_ids = {item["id"] for item in config["endpoints"]}
        ports = [port for group in config["port_groups"] for port in group["ports"]]
        ports += [item["port"] for item in config["fixed_routes"]]
        self.assertEqual(len(ports), len(set(ports)))
        self.assertEqual(
            [group["ports"] for group in config["port_groups"]],
            [[9999, 10001, 10002], [10010, 10011, 10012], [10020, 10021, 10022], [10030, 10031, 10032]],
        )
        for template in config["templates"]:
            self.assertTrue(set(template["endpoint_ids"]) <= endpoint_ids)
        for route in config["fixed_routes"]:
            self.assertIn(route["endpoint_id"], endpoint_ids)

    def test_manual_probe_can_verify_pool_authorization(self):
        class Connection:
            def __init__(self):
                self.responses = [
                    b'{"id":73001,"result":[[],"session",4],"error":null}\n',
                    b'{"id":73002,"result":true,"error":null}\n',
                ]
                self.sent = []
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def settimeout(self, value): pass
            def sendall(self, value): self.sent.append(json.loads(value))
            def recv(self, size): return self.responses.pop(0)

        connection = Connection()
        endpoint = {"host": "pool.example", "port": 3333, "transport": "tcp"}
        with patch("endpoint_monitor.resolve_public", return_value=["203.0.113.10"]), \
                patch("endpoint_monitor.socket.create_connection", return_value=connection):
            result = probe_stratum(endpoint, username="wallet.worker", password="secret")
        self.assertTrue(result["ok"])
        self.assertTrue(result["authorized"])
        self.assertEqual(connection.sent[1]["method"], "mining.authorize")
        self.assertEqual(connection.sent[1]["params"], ["wallet.worker", "secret"])


if __name__ == "__main__":
    unittest.main()
