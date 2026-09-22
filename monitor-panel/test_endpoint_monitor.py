import json
import re
import tempfile
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

    def test_notification_closure_keeps_issue_id_scope_and_handling_link(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            notifier = Notifier(settings={"PANEL_URL": "https://relay.example.com"},
                event_path=root / "events.jsonl", result_path=root / "results.json")
            messages = []
            with patch.object(notifier, "send", side_effect=lambda content, only=None: messages.append(content)):
                notifier({"type": "endpoint_down", "time": 100, "endpoint": "pool.example:3333",
                    "pool": "测试池", "port": 11301, "message": "连接超时"})
                notifier({"type": "recovery", "time": 200, "endpoint": "pool.example:3333",
                    "pool": "测试池", "port": 11301, "message": "恢复"})
        issue = re.search(r"问题编号：(VPS-[0-9A-F]+)", messages[0]).group(1)
        self.assertIn("问题编号：" + issue, messages[1])
        self.assertIn("影响范围：生产端口 11301", messages[0])
        self.assertIn("处理入口：https://relay.example.com/logs", messages[0])
        self.assertIn("闭环状态：本次异常已恢复", messages[1])

    def test_notification_records_latest_result_per_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "results.json"
            notifier = Notifier(settings={"WECHAT_WEBHOOK_1": "https://one.example/send",
                "WECHAT_WEBHOOK_2": "https://two.example/send"}, result_path=path)
            with patch.object(notifier, "_post_json", side_effect=[None, OSError("timeout")]):
                sent, errors = notifier.send("test")
            self.assertEqual(sent, 1)
            self.assertEqual(len(errors), 1)
            result = json.loads(path.read_text(encoding="utf-8"))["channels"]["wechat"]
            self.assertEqual(result["status"], "partial")
            self.assertEqual((result["sent"], result["total"]), (1, 2))
            self.assertIn("timeout", result["error"])

    def test_notification_policy_maps_severity_to_channels(self):
        settings = {"WECHAT_WEBHOOK_1": "https://qyapi.example/send?key=one",
            "EMAIL_DELIVERY": "direct", "EMAIL_TO_1": "ops@example.com",
            "NOTIFY_CRITICAL_CHANNELS": "wechat", "NOTIFY_INFO_CHANNELS": "email"}
        notifier = Notifier(settings=settings)
        with patch.object(notifier, "send") as send:
            notifier({"type": "endpoint_down", "time": 0, "endpoint": "critical.example:3333"})
            notifier({"type": "recovery", "time": 0, "endpoint": "info.example:3333"})
        self.assertEqual(send.call_args_list[0].kwargs["only"], ["wechat"])
        self.assertEqual(send.call_args_list[1].kwargs["only"], ["email"])

    def test_quiet_hours_suppress_info_but_never_critical(self):
        with tempfile.TemporaryDirectory() as folder:
            result_path = Path(folder) / "results.json"
            settings = {"WECHAT_WEBHOOK_1": "https://qyapi.example/send?key=one",
                "NOTIFY_CRITICAL_CHANNELS": "wechat", "NOTIFY_INFO_CHANNELS": "wechat",
                "NOTIFY_QUIET_START": "22:00", "NOTIFY_QUIET_END": "07:00"}
            notifier = Notifier(settings=settings, result_path=result_path)
            # 1969-12-31 16:00 UTC is midnight in Beijing.
            with patch.object(notifier, "send") as send:
                notifier({"type": "recovery", "time": -28800, "endpoint": "pool.example:3333"})
                notifier({"type": "endpoint_down", "time": -28800, "endpoint": "pool.example:3333"})
            send.assert_called_once()
            self.assertEqual(send.call_args.kwargs["only"], ["wechat"])

    def test_all_channel_failure_persists_through_suppression_and_clears_on_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "results.json"
            notifier = Notifier(settings={}, result_path=path)
            notifier._save_outcomes({"wechat": {"status": "failed", "sent": 0, "total": 1,
                "error": "timeout"}}, all_failed=True)
            notifier._save_outcomes({"wechat": {"status": "suppressed", "sent": 0, "total": 0,
                "error": "免打扰时段内已抑制"}}, all_failed=False)
            self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["last_delivery"]["all_failed"])
            notifier._save_outcomes({"email": {"status": "success", "sent": 1, "total": 1,
                "error": ""}}, all_failed=False)
            self.assertFalse(json.loads(path.read_text(encoding="utf-8"))["last_delivery"]["all_failed"])

    def test_regular_multi_channel_delivery_does_not_raise_when_one_channel_fails(self):
        notifier = Notifier(settings={"WECHAT_WEBHOOK_1": "https://qyapi.example/send?key=one"})
        with patch.object(notifier, "_post_json"):
            sent, errors = notifier.send("test", only=["wechat", "email"])
        self.assertEqual(sent, 0)
        self.assertEqual(len(errors), 1)

    def test_notification_sends_wechat_and_dingtalk_without_exposing_secrets(self):
        settings = {"WECHAT_WEBHOOK_1": "https://qyapi.example/send?key=private-1",
            "WECHAT_WEBHOOK_2": "https://qyapi.example/send?key=private-2",
            "DINGTALK_WEBHOOK_1": "https://oapi.example/send?access_token=private-1", "DINGTALK_SECRET_1": "SEC-private",
            "DINGTALK_WEBHOOK_2": "https://oapi.example/send?access_token=private-2", "DINGTALK_SECRET_2": ""}
        notifier = Notifier(settings=settings)
        with patch("endpoint_monitor.urllib.request.urlopen") as urlopen:
            sent, errors = notifier.send("测试")
        self.assertEqual((sent, errors), (4, []))
        self.assertEqual(urlopen.call_count, 4)
        requests = [call.args[0] for call in urlopen.call_args_list]
        self.assertIn("timestamp=", requests[2].full_url)
        self.assertIn("sign=", requests[2].full_url)
        self.assertNotIn("SEC-private", requests[2].full_url)

    def test_duplicate_notification_targets_are_sent_once(self):
        settings = {"WECHAT_WEBHOOK_1": "https://qyapi.example/send?key=same",
            "WECHAT_WEBHOOK_2": "https://qyapi.example/send?key=same",
            "DINGTALK_WEBHOOK_1": "https://oapi.example/send?access_token=same",
            "DINGTALK_WEBHOOK_2": "https://oapi.example/send?access_token=same",
            "EMAIL_TO_1": "ops@example.com", "EMAIL_TO_2": "OPS@example.com", "EMAIL_DELIVERY": "direct"}
        notifier = Notifier(settings=settings)
        with patch("endpoint_monitor.urllib.request.urlopen") as urlopen, patch("endpoint_monitor.smtplib.SMTP") as smtp:
            sent, errors = notifier.send("去重测试")
        self.assertEqual((sent, errors), (3, []))
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(smtp.return_value.__enter__.return_value.send_message.call_count, 1)

    def test_notification_sends_email_through_configured_smtp(self):
        settings = {"SMTP_HOST": "smtp.example.com", "SMTP_PORT": "465", "SMTP_SECURITY": "ssl",
            "SMTP_USERNAME": "sender@example.com", "SMTP_PASSWORD": "secret",
            "SMTP_FROM": "sender@example.com", "SMTP_TO": "ops@example.com"}
        notifier = Notifier(settings=settings)
        with patch("endpoint_monitor.smtplib.SMTP_SSL") as smtp:
            sent, errors = notifier.send("邮件测试")
        self.assertEqual((sent, errors), (1, []))
        server = smtp.return_value.__enter__.return_value
        server.login.assert_called_once_with("sender@example.com", "secret")
        message = server.send_message.call_args.args[0]
        self.assertEqual(message["To"], "ops@example.com")
        self.assertIn("邮件测试", message.get_content())

    def test_notification_sends_direct_mail_to_each_recipient_without_sender_setup(self):
        settings = {"EMAIL_DELIVERY": "direct", "EMAIL_TO_1": "first@example.com",
            "EMAIL_TO_2": "second@example.com", "EMAIL_TO_3": ""}
        notifier = Notifier(settings=settings)
        with patch("endpoint_monitor.smtplib.SMTP") as smtp:
            sent, errors = notifier.send("直发测试")
        self.assertEqual((sent, errors), (2, []))
        server = smtp.return_value.__enter__.return_value
        self.assertEqual(server.send_message.call_count, 2)
        messages = [call.args[0] for call in server.send_message.call_args_list]
        self.assertEqual(messages[0]["To"], "first@example.com")
        self.assertEqual(messages[1]["To"], "second@example.com")
        self.assertTrue(messages[0]["From"].startswith("stratum-monitor@"))

    def test_old_batch_derived_alert_is_cleared_on_upgrade(self):
        state = {"version": 1, "endpoints": {"active": {"alerting": True, "consecutive_failures": 8,
            "first_failure": 10, "last_result": {"sample_type": "hourly"}, "samples": []}}}
        monitor = EndpointMonitor(self.config, state=state, probe=lambda endpoint: {"ok": True})
        entry = monitor.state["endpoints"]["active"]
        self.assertFalse(entry["alerting"])
        self.assertEqual(entry["consecutive_failures"], 0)

    def test_repository_config_is_consistent(self):
        config = json.loads((Path(__file__).resolve().parent / "v3-config.json").read_text(encoding="utf-8"))
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
