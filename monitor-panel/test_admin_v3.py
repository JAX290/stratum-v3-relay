import json
import base64
import hashlib
import hmac
import io
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from werkzeug.security import generate_password_hash

try:
    import stratum_admin_v3 as admin
except ModuleNotFoundError as exc:
    if exc.name == "flask":
        raise unittest.SkipTest("Flask is installed by the Linux deployment script")
    raise
from v3_manager import ConfigStore

HERE = Path(__file__).resolve().parent


class AdminV3Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        config = json.loads((HERE / "v3-config.json").read_text(encoding="utf-8"))
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        admin.CONFIG_FILE = config_path
        admin.STATE_FILE = root / "state.json"
        admin.INSPECTOR_CONFIG = root / "inspector.json"
        admin.HAPROXY_CONFIG = root / "haproxy.cfg"
        admin.INTEGRITY_BASELINE = root / "integrity.json"
        admin.ENV_FILE = root / "monitor.env"
        admin.CRON_FILE = root / "monitor.cron"
        admin.ENDPOINT_EVENT_FILE = root / "events.jsonl"
        admin.INSPECTOR_STATE_FILE = root / "inspector-state.json"
        admin.PUBLIC_IP_ALERT_STATE_FILE = root / "public-ip-alert.json"
        admin.RELAY_CONTROL_FILE = root / "relay-control.json"
        admin.PEER_SYNC_FILE = root / "peer.json"
        admin.PEER_OUTBOX_FILE = root / "peer-outbox.json"
        admin.PEER_STATE_FILE = root / "peer-state.json"
        admin.DISCONNECT_HISTORY_DIR = root / "disconnect-history"
        admin.AUDIT_FILE = root / "audit.jsonl"
        admin.SECURE_RELAY_CONFIG = root / "secure-relay.json"
        admin.SECURE_RELAY_STATE = root / "relay-sites.json"
        admin.SECURE_RELAY_MONITOR_STATE = root / "relay-monitor.json"
        admin.SECURE_RELAY_EVENT_FILE = root / "relay-events.jsonl"
        admin.ACCESS_PACKAGE_DIR = root / "access-packages"
        admin.CLIENT_ACTION_FILE = root / "client-actions.json"
        admin.ISSUE_STATE_FILE = root / "issues.json"
        admin.REPAIR_GUARD_FILE = root / "repair-guard.json"
        admin.REPAIR_BACKUP_DIR = root / "repair-backups"
        admin.HIGH_RISK_CANDIDATE_DIR = root / "candidates"
        admin.HIGH_RISK_CANDIDATE_DIR.mkdir()
        admin.NOTIFICATION_RESULT_FILE = root / "notification-results.json"
        admin.SECURE_NOTIFICATION_RESULT_FILE = root / "secure-notification-result.json"
        admin.store = ConfigStore(config_path, root / "history", admin.AUDIT_FILE)
        admin.app.config.update(TESTING=True, SECRET_KEY="test")
        admin.LOGIN_FAILURES.clear()
        self.client = admin.app.test_client()
        with self.client.session_transaction() as session:
            session["authenticated"] = True
            session["csrf"] = "token"

    def tearDown(self):
        self.temp.cleanup()

    def test_dashboard_and_group_update(self):
        with patch.object(admin, "detect_relay_public_ip", return_value={"ok": True, "host": "93.184.216.34", "source": "test", "message": ""}):
            response = self.client.get("/overview")
        self.assertEqual(response.status_code, 200)
        for label in ("总览", "矿机", "线路与端口", "报警", "设置", "日志"):
            self.assertIn(label.encode(), response.data)
        self.assertIn("管理员总览".encode(), response.data)
        self.assertIn("当前活跃转发线路".encode(), response.data)
        self.assertIn("矿机仍填写值守电脑地址".encode(), response.data)
        response = self.client.post("/group/backup-1", data={
            "csrf": "token", "template_id": "viabtc-default", "mode": "template",
            "endpoint_0": "hashhut-ru", "endpoint_1": "hashhut-eu", "endpoint_2": "hashhut-by",
        })
        self.assertEqual(response.status_code, 302)
        config = admin.store.load()
        group = next(item for item in config["port_groups"] if item["id"] == "backup-1")
        self.assertEqual(group["endpoint_ids"], ["viabtc-io", "viabtc-top", "viabtc-cc"])
        self.assertTrue(admin.HAPROXY_CONFIG.exists())

    def test_custom_endpoint_can_be_assigned(self):
        def add_custom(config, target, label):
            config["endpoints"].append({"id": "custom-test", "pool": "自定义", "region": label,
                "host": "pool.example.com", "port": 4444, "transport": "tcp", "source": "panel", "enabled": True})
            return "custom-test"

        with patch.object(admin, "ensure_custom_endpoint", side_effect=add_custom):
            response = self.client.post("/route/10020/apply", data={
                "csrf": "token", "endpoint_id": "__custom__", "custom_target": "pool.example.com:4444",
            })
        self.assertEqual(response.status_code, 302)
        group = next(item for item in admin.store.load()["port_groups"] if item["id"] == "backup-2")
        self.assertEqual(group["endpoint_ids"][0], "custom-test")

    def test_single_port_update_does_not_change_sibling_ports(self):
        before = next(item for item in admin.store.load()["port_groups"] if item["id"] == "private")
        siblings = list(before["endpoint_ids"][1:])
        response = self.client.post("/route/9999/apply", data={"csrf": "token", "endpoint_id": "viabtc-io", "custom_target": ""})
        self.assertEqual(response.status_code, 302)
        after = next(item for item in admin.store.load()["port_groups"] if item["id"] == "private")
        self.assertEqual(after["endpoint_ids"][0], "viabtc-io")
        self.assertEqual(after["endpoint_ids"][1:], siblings)
        self.assertEqual(admin.read_actual_route_ids()[9999], "viabtc-io")

    def test_disconnect_history_can_be_listed_and_downloaded(self):
        admin.DISCONNECT_HISTORY_DIR.mkdir()
        history = admin.DISCONNECT_HISTORY_DIR / "disconnect-2026-09-13.jsonl"
        history.write_text('{"worker":"owner.test"}\n', encoding="utf-8")
        response = self.client.get("/logs")
        self.assertEqual(response.status_code, 200)
        self.assertIn(history.name.encode(), response.data)
        with self.client.get("/downloads/disconnect-history/" + history.name) as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"owner.test", response.data)
        self.assertEqual(self.client.get("/downloads/disconnect-history/../config.json").status_code, 404)

    def test_fixed_route_supports_single_miner_canary_and_promotion(self):
        admin.INSPECTOR_STATE_FILE.write_text(json.dumps({"pools": [{"id": "longpool-asia-8080",
            "connections": [{"source_ip": "192.168.1.20", "public_port": 11301}], "workers": []}]}), encoding="utf-8")
        with patch.object(admin, "probe_stratum", return_value={"ok": True, "stratum_ms": 12, "authorized": None}), \
                patch.object(admin, "request_reconnect") as reconnect:
            response = self.client.post("/route/11301/canary/start", data={"csrf": "token",
                "endpoint_id": "f2pool-global", "source_ip": "192.168.1.20", "duration_minutes": "10"})
        self.assertEqual(response.status_code, 302)
        config = admin.store.load()
        self.assertEqual(config["canary_routes"][0]["source_ip"], "192.168.1.20")
        self.assertEqual(config["canary_routes"][0]["endpoint_id"], "f2pool-global")
        reconnect.assert_called_once_with(11301, "192.168.1.20")

        admin.INSPECTOR_STATE_FILE.write_text(json.dumps({"pools": [{"id": "f2pool-global", "workers": [{
            "details": [{"source_ip": "192.168.1.20", "public_port": 11301, "status": "在线",
                "submitted": 1, "accepted": 1, "rejected": 0}]}]}]}), encoding="utf-8")
        with patch.object(admin, "request_reconnect") as reconnect:
            response = self.client.post("/route/11301/canary/promote", data={"csrf": "token"})
        self.assertEqual(response.status_code, 302)
        config = admin.store.load()
        route = next(item for item in config["fixed_routes"] if item["port"] == 11301)
        self.assertEqual(route["endpoint_id"], "f2pool-global")
        self.assertEqual(config.get("canary_routes"), [])
        self.assertEqual(admin.route_change_history(config)[0]["previous_endpoint_id"], "longpool-asia-8080")
        reconnect.assert_called_once_with(11301)

        with patch.object(admin, "request_reconnect") as reconnect:
            response = self.client.post("/route/11301/restore", data={"csrf": "token"})
        self.assertEqual(response.status_code, 302)
        config = admin.store.load()
        route = next(item for item in config["fixed_routes"] if item["port"] == 11301)
        self.assertEqual(route["endpoint_id"], "longpool-asia-8080")
        self.assertEqual(len(admin.route_change_history(config)), 2)
        self.assertEqual(admin.route_change_history(config)[0]["endpoint_id"], "longpool-asia-8080")
        reconnect.assert_called_once_with(11301)

    def test_canary_blocks_mismatched_algorithm_before_reconnect(self):
        config = admin.store.load()
        next(item for item in config["endpoints"] if item["id"] == "f2pool-global")["algorithm"] = "sha256d"
        admin.store.save(config, action="test-algorithm")
        admin.INSPECTOR_STATE_FILE.write_text(json.dumps({"pools": [{"id": "longpool-asia-8080",
            "connections": [{"source_ip": "192.168.1.20", "public_port": 11301}], "workers": []}]}), encoding="utf-8")
        with patch.object(admin, "probe_stratum") as probe, patch.object(admin, "request_reconnect") as reconnect:
            response = self.client.post("/route/11301/canary/start", data={"csrf": "token",
                "endpoint_id": "f2pool-global", "source_ip": "192.168.1.20", "duration_minutes": "10"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(admin.store.load().get("canary_routes", []), [])
        self.assertFalse(probe.called)
        self.assertFalse(reconnect.called)

    def test_verified_address_can_switch_without_another_trial(self):
        config = admin.store.load()
        endpoint = next(item for item in config["endpoints"] if item["id"] == "f2pool-global")
        endpoint["verified"] = True
        admin.store.save(config, action="mark-verified")
        with patch.object(admin, "request_reconnect") as reconnect, patch.object(admin, "send_route_event") as notify:
            response = self.client.post("/route/11301/verified/apply", data={
                "csrf": "token", "endpoint_id": "f2pool-global"})
        self.assertEqual(response.status_code, 302)
        route = next(item for item in admin.store.load()["fixed_routes"] if item["port"] == 11301)
        self.assertEqual(route["endpoint_id"], "f2pool-global")
        reconnect.assert_called_once_with(11301)
        notify.assert_called_once()

    def test_overview_does_not_tell_miners_to_bypass_encrypted_relay(self):
        def fake_run(command, **kwargs):
            class Result:
                stdout = "1.1.1.1 via 93.184.216.1 dev ens17 src 93.184.216.34 uid 0\n"
                stderr = ""
            return Result()

        with patch.object(admin.subprocess, "run", side_effect=fake_run):
            response = self.client.get("/overview")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"stratum+tcp://93.184.216.34:9999", response.data)
        self.assertIn("矿机仍填写值守电脑地址".encode(), response.data)

    def test_missing_public_ip_does_not_create_misleading_mining_alarm(self):
        admin.ENV_FILE.write_text("WECHAT_WEBHOOK=https://qyapi.example/webhook\n", encoding="utf-8")
        status = {"ok": False, "host": "", "source": "", "message": "无法从默认出公网路由识别 VPS 公网 IPv4"}
        with patch.object(admin, "detect_relay_public_ip", return_value=status), \
                patch.object(admin.urllib.request, "urlopen") as urlopen:
            response = self.client.get("/overview")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"stratum+tcp://93.184.216.34", response.data)
        self.assertFalse(urlopen.called)
        self.assertFalse(admin.ENDPOINT_EVENT_FILE.exists())

    def test_all_dashboard_pages_render(self):
        for page in ("overview", "miners", "routes", "alerts", "settings", "logs"):
            response = self.client.get("/" + page)
            self.assertEqual(response.status_code, 200, page)

    def test_active_pools_are_prioritized_and_inactive_pools_are_collapsed(self):
        active, inactive = admin.prioritize_pools([
            {"name": "空闲矿池", "summary": {"connections": 0, "workers": 0}},
            {"name": "11301生产矿池", "summary": {"connections": 3, "workers": 1}},
        ])
        self.assertEqual([item["name"] for item in active], ["11301生产矿池"])
        self.assertEqual([item["name"] for item in inactive], ["空闲矿池"])
        response = self.client.get("/miners")
        self.assertIn("未使用的矿池".encode(), response.data)

    def test_notification_settings_require_tailscale_preserve_blank_secrets_and_can_be_tested(self):
        original = "WECHAT_WEBHOOK=https://qyapi.example/private\nDINGTALK_SECRET=SEC-private\nSMTP_PASSWORD=mail-private\n"
        admin.ENV_FILE.write_text(original, encoding="utf-8")
        data = {"csrf": "token", "dingtalk_webhook_1": "https://oapi.example/send?access_token=x",
            "smtp_host": "smtp.example.com", "smtp_port": "465", "smtp_security": "ssl",
            "smtp_username": "sender@example.com", "smtp_from": "sender@example.com", "email_to_1": "ops@example.com",
            "email_delivery": "smtp"}
        self.assertEqual(self.client.post("/notification-settings", data=data).status_code, 404)
        headers = {"Tailscale-User-Login": "owner@example.com"}
        with patch.object(admin.subprocess, "run"), patch.object(admin, "approve_integrity"):
            response = self.client.post("/notification-settings", data=data, headers=headers)
        self.assertEqual(response.status_code, 302)
        values = admin.read_env()
        self.assertEqual(values["WECHAT_WEBHOOK_1"], "https://qyapi.example/private")
        self.assertEqual(values["DINGTALK_SECRET_1"], "SEC-private")
        self.assertEqual(values["WECHAT_WEBHOOK"], "")
        self.assertEqual(values["SMTP_PASSWORD"], "mail-private")
        self.assertEqual(values["EMAIL_TO_1"], "ops@example.com")
        with patch.object(admin.Notifier, "send") as send:
            response = self.client.post("/notification-settings/test/dingtalk", data={"csrf": "token"}, headers=headers)
        self.assertEqual(response.status_code, 302)
        send.assert_called_once()
        page = self.client.get("/settings", headers=headers)
        self.assertNotIn(b"private", page.data)

    def test_notification_policy_is_saved_and_rendered(self):
        headers = {"Tailscale-User-Login": "owner@example.com"}
        data = {"csrf": "token", "email_delivery": "direct",
            "critical_channels": ["wechat", "email"], "warning_channels": ["dingtalk"],
            "info_channels": ["email"], "quiet_start": "22:30", "quiet_end": "07:15"}
        with patch.object(admin.subprocess, "run"), patch.object(admin, "approve_integrity"):
            response = self.client.post("/notification-settings", data=data, headers=headers)
        self.assertEqual(response.status_code, 302)
        values = admin.read_env()
        self.assertEqual(values["NOTIFY_CRITICAL_CHANNELS"], "wechat,email")
        self.assertEqual(values["NOTIFY_WARNING_CHANNELS"], "dingtalk")
        self.assertEqual(values["NOTIFY_INFO_CHANNELS"], "email")
        self.assertEqual((values["NOTIFY_QUIET_START"], values["NOTIFY_QUIET_END"]), ("22:30", "07:15"))
        page = self.client.get("/settings", headers=headers)
        self.assertIn("通知策略".encode(), page.data)

    def test_alert_page_keeps_all_channel_failure_warning_until_success(self):
        admin.NOTIFICATION_RESULT_FILE.write_text(json.dumps({"last_delivery": {
            "time": 100, "channels": ["wechat", "email"], "all_failed": True, "suppressed": False}}),
            encoding="utf-8")
        response = self.client.get("/alerts")
        self.assertIn("所有通知渠道最近一次发送均失败".encode(), response.data)
        admin.NOTIFICATION_RESULT_FILE.write_text(json.dumps({"last_delivery": {
            "time": 200, "channels": ["email"], "all_failed": False, "suppressed": False}}),
            encoding="utf-8")
        response = self.client.get("/alerts")
        self.assertNotIn("所有通知渠道最近一次发送均失败".encode(), response.data)

    def test_direct_email_only_requires_up_to_three_recipients(self):
        headers = {"Tailscale-User-Login": "owner@example.com"}
        data = {"csrf": "token", "email_delivery": "direct", "email_to_1": "one@example.com",
            "email_to_2": "two@example.com", "email_to_3": "three@example.com"}
        with patch.object(admin.subprocess, "run"), patch.object(admin, "approve_integrity"):
            response = self.client.post("/notification-settings", data=data, headers=headers)
        self.assertEqual(response.status_code, 302)
        values = admin.read_env()
        self.assertEqual(values["EMAIL_DELIVERY"], "direct")
        self.assertEqual([values[f"EMAIL_TO_{number}"] for number in range(1, 4)],
            ["one@example.com", "two@example.com", "three@example.com"])

    def test_single_robot_can_be_deleted_without_removing_other_targets(self):
        admin.ENV_FILE.write_text("WECHAT_WEBHOOK_1=https://qyapi.example/one\n"
            "WECHAT_WEBHOOK_2=https://qyapi.example/two\nWECHAT_WEBHOOK_3=\n", encoding="utf-8")
        headers = {"Tailscale-User-Login": "owner@example.com"}
        with patch.object(admin.subprocess, "run"), patch.object(admin, "approve_integrity"):
            response = self.client.post("/notification-settings", data={"csrf": "token",
                "remove_wechat_1": "1", "email_delivery": "smtp"}, headers=headers)
        self.assertEqual(response.status_code, 302)
        values = admin.read_env()
        self.assertEqual(values["WECHAT_WEBHOOK_1"], "")
        self.assertEqual(values["WECHAT_WEBHOOK_2"], "https://qyapi.example/two")

    def test_vps_logs_are_explained_for_nontechnical_administrators(self):
        raw = json.dumps({"_SYSTEMD_UNIT": "stratum-secure-relay.service", "PRIORITY": "3",
            "__REALTIME_TIMESTAMP": "1789000000000000", "MESSAGE": "Permission denied while writing state"})
        with patch.object(admin.subprocess, "run") as run, \
                patch.object(admin, "service_state", return_value="active"), \
                patch.object(admin, "detect_relay_public_ip", return_value={"ok": True,
                    "host": "93.184.216.34", "source": "test", "message": ""}):
            run.return_value.stdout = raw
            response = self.client.get("/logs")
        self.assertEqual(response.status_code, 200)
        self.assertIn("运维问题中心".encode(), response.data)
        self.assertIn("服务没有所需的文件权限".encode(), response.data)
        self.assertIn("检查服务账户和目录权限".encode(), response.data)
        self.assertIn(b"Permission denied while writing state", response.data)
        command = run.call_args.args[0]
        self.assertIn("stratum-secure-relay", command)
        self.assertIn("stratum-vps-watchdog", command)

    def test_maintenance_center_prioritizes_stopped_services(self):
        center = admin.maintenance_center({"加密入口": "failed", "流量转发": "active"},
            {"attention": []})
        self.assertEqual(center["level"], "danger")
        self.assertEqual(center["bad"], 1)
        self.assertIn("加密入口没有正常运行", center["issues"][0]["title"])

    def test_comprehensive_diagnostics_groups_all_required_checks(self):
        config = admin.store.load()
        endpoints = {item["id"]: {"last_result": {"ok": True}} for item in config["endpoints"]}
        result = admin.comprehensive_diagnostics(config,
            {"HAProxy": "active", "加密入口": "active", "管理面板": "failed"},
            {"disk": "91.0%", "memory": "42.0%"},
            {"available": True, "days_left": 30}, {"endpoints": endpoints},
            {"enabled": True, "pending": 1, "last_status": "failed"},
            [{"name": "一号矿场", "status": "offline", "client_outdated": False,
              "server_outdated": False, "last_share": "尚无"}],
            {"submitted": 10, "accepted": 9},
            {"entries": [{"level": "good", "title": "服务已经恢复", "service": "安全监控",
                "display_time": "今天", "message": "Service recovered"}]},
            {"panel": "3.2.7", "secure_relay": "2.2.3", "windows_client": "2.3.11"},
            lambda port: port != 11303, now=1_700_000_000)
        self.assertEqual(set(result["counts"]), {"normal", "recovered", "repairable", "manual"})
        self.assertGreater(result["counts"]["repairable"], 0)
        self.assertGreater(result["counts"]["manual"], 0)
        self.assertEqual(result["counts"]["recovered"], 1)
        self.assertIn("未监听端口：11303", next(item for item in result["items"] if item["key"] == "ports")["detail"])

    def test_logs_page_shows_comprehensive_diagnostics(self):
        with patch.object(admin, "detect_relay_public_ip", return_value={"ok": True, "host": "93.184.216.34", "source": "test", "message": ""}), \
                patch.object(admin, "port_listening", return_value=True):
            response = self.client.get("/logs")
        self.assertEqual(response.status_code, 200)
        for label in ("一键全面诊断", "TLS 加密入口", "双 VPS 同步", "Windows 心跳", "最近 Share", "需人工处理"):
            self.assertIn(label.encode(), response.data)

    def test_issue_registry_keeps_id_impact_duration_and_attempts(self):
        diagnostics = {"items": [{"key": "heartbeat", "title": "Windows 心跳",
            "detail": "一号矿场离线", "category": "manual"}]}
        sites = [{"name": "一号矿场", "status": "offline", "miner_count": 0, "impact_miner_count": 12}]
        first = admin.reconcile_issues(diagnostics, {}, sites, 1000)
        issue = first["rows"][0]
        issue["attempted_actions"] = [{"time": 1030, "action": "重新探测", "result": "失败"}]
        second = admin.reconcile_issues(diagnostics, {"issues": {"heartbeat": issue}}, sites, 1120)
        current = second["rows"][0]
        self.assertEqual(current["id"], issue["id"])
        self.assertEqual(current["affected_sites"], ["一号矿场"])
        self.assertEqual(current["miner_count"], 12)
        self.assertEqual(current["duration"], "2分钟")
        self.assertTrue(current["ongoing"])
        self.assertEqual(current["attempted_actions"][0]["action"], "重新探测")

    def test_issue_registry_marks_missing_problem_resolved(self):
        previous = {"issues": {"ports": {"id": "VPS-ABC", "key": "ports", "title": "端口",
            "first_seen": 100, "last_seen": 120, "ongoing": True, "affected_sites": [],
            "miner_count": 0, "attempted_actions": []}}}
        result = admin.reconcile_issues({"items": []}, previous, [], 200)
        self.assertFalse(result["rows"][0]["ongoing"])
        self.assertEqual(result["rows"][0]["resolved_at"], 200)

    def test_vps_support_bundle_is_whitelisted_and_redacted(self):
        admin.ENV_FILE.write_text("WECHAT_WEBHOOK=https://hooks.example.com/secret-path\nSMTP_PASSWORD=topsecret\n", encoding="utf-8")
        admin.SECURE_RELAY_CONFIG.write_text(json.dumps({"clients": [{"id": "mine-a", "token": "a" * 64}]}), encoding="utf-8")
        with patch.object(admin, "detect_relay_public_ip", return_value={"ok": True, "host": "198.51.100.20", "source": "test", "message": ""}), \
                patch.object(admin, "port_listening", return_value=True), \
                patch.object(admin, "recent_logs", return_value={"journal": "connect 198.51.100.20\ntoken=" + "a" * 64,
                    "entries": [], "attention": [], "events": ""}):
            response = self.client.post("/downloads/support-bundle", data={"csrf": "token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            self.assertEqual(set(archive.namelist()), {"versions.json", "health.json", "config-summary.json",
                "recent-logs.txt", "manifest.sha256"})
            combined = b"\n".join(archive.read(name) for name in archive.namelist()).decode("utf-8")
        self.assertNotIn("198.51.100.20", combined)
        self.assertNotIn("a" * 64, combined)
        self.assertNotIn("topsecret", combined)
        self.assertNotIn("hooks.example.com", combined)
        self.assertIn("[IP REMOVED]", combined)

    def test_support_bundle_rejects_failed_secret_scan(self):
        with patch("operations_center.redact_support_text", side_effect=lambda value, sensitive: str(value)):
            with self.assertRaises(ValueError):
                admin.create_support_bundle({}, {"items": []}, {"rows": []}, {}, {"journal": "leaked-secret"}, ["leaked-secret"])

    def test_safe_service_repair_is_allowlisted_and_rechecked(self):
        admin.ISSUE_STATE_FILE.write_text(json.dumps({"issues": {"service:加密入口": {
            "attempted_actions": []}}}), encoding="utf-8")
        with patch.object(admin, "current_tailscale_admin", return_value=True), \
                patch.object(admin.subprocess, "run") as run, patch.object(admin, "service_state", return_value="active"):
            run.return_value.returncode = 0
            response = self.client.post("/repairs/restart-service",
                data={"csrf": "token", "service": "stratum-secure-relay"})
        self.assertEqual(response.status_code, 302)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["systemctl", "restart", "stratum-secure-relay"])
        state = json.loads(admin.ISSUE_STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(state["issues"]["service:加密入口"]["attempted_actions"][-1]["result"], "成功")
        with patch.object(admin, "current_tailscale_admin", return_value=True):
            self.assertEqual(self.client.post("/repairs/restart-service",
                data={"csrf": "token", "service": "ssh"}).status_code, 404)

    def test_verified_reload_rechecks_every_production_port(self):
        checked = []
        with patch.object(admin, "current_tailscale_admin", return_value=True), \
                patch.object(admin, "save_and_reload") as reload_config, \
                patch.object(admin, "validate_config") as validate, \
                patch.object(admin, "port_listening", side_effect=lambda port: checked.append(port) or True):
            response = self.client.post("/repairs/reload-config", data={"csrf": "token"})
        self.assertEqual(response.status_code, 302)
        validate.assert_called_once()
        reload_config.assert_called_once()
        self.assertEqual(len(checked), 24)

    def test_reprobe_updates_endpoint_state(self):
        config = admin.store.load()
        with patch.object(admin, "probe_stratum", return_value={"ok": True, "checked_at": 123, "stratum_ms": 10}):
            result = admin.reprobe_active_endpoints(config)
        self.assertEqual(result, {"checked": 15, "healthy": 15})
        state = json.loads(admin.STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(state["endpoints"]), 15)
        self.assertTrue(all(row["last_result"]["ok"] for row in state["endpoints"].values()))

    def test_log_cleanup_only_removes_old_project_disconnect_logs(self):
        admin.DISCONNECT_HISTORY_DIR.mkdir()
        old = admin.DISCONNECT_HISTORY_DIR / "disconnect-2026-01-01.jsonl"
        fresh = admin.DISCONNECT_HISTORY_DIR / "disconnect-2026-09-22.jsonl"
        unrelated = admin.DISCONNECT_HISTORY_DIR / "other.log"
        for path in (old, fresh, unrelated):
            path.write_text("data", encoding="utf-8")
        os.utime(old, (100, 100))
        os.utime(fresh, (1_000_000, 1_000_000))
        self.assertEqual(admin.clean_expired_project_logs(now=700_000), 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(unrelated.exists())

    def test_repair_backup_restores_config_and_old_logs(self):
        original = admin.CONFIG_FILE.read_bytes()
        admin.DISCONNECT_HISTORY_DIR.mkdir()
        old = admin.DISCONNECT_HISTORY_DIR / "disconnect-2026-01-01.jsonl"
        old.write_text("old log", encoding="utf-8")
        os.utime(old, (100, 100))
        backup = admin.create_repair_backup("clean-logs", now=700_000)
        admin.CONFIG_FILE.write_text("broken", encoding="utf-8")
        old.unlink()
        restored = admin.restore_repair_backup(backup)
        self.assertEqual(admin.CONFIG_FILE.read_bytes(), original)
        self.assertEqual(old.read_text(encoding="utf-8"), "old log")
        self.assertIn("main-config", restored)
        self.assertIn("log:" + old.name, restored)

    def test_failed_repair_restores_backup_and_stops_after_three_failures(self):
        original = admin.CONFIG_FILE.read_text(encoding="utf-8")
        def break_reload(*args, **kwargs):
            admin.CONFIG_FILE.write_text("broken", encoding="utf-8")
            raise OSError("reload failed")
        with patch.object(admin, "current_tailscale_admin", return_value=True), \
                patch.object(admin, "validate_config"), patch.object(admin, "save_and_reload", side_effect=break_reload):
            for _ in range(4):
                response = self.client.post("/repairs/reload-config", data={"csrf": "token"})
                self.assertEqual(response.status_code, 302)
        self.assertEqual(admin.CONFIG_FILE.read_text(encoding="utf-8"), original)
        guard = json.loads(admin.REPAIR_GUARD_FILE.read_text(encoding="utf-8"))["reload-config"]
        self.assertEqual(guard["failures"], 3)
        self.assertTrue(admin.REPAIR_BACKUP_DIR.exists())

    def test_successful_repair_resets_failure_guard(self):
        admin.REPAIR_GUARD_FILE.write_text(json.dumps({"clean-logs": {"failures": 2}}), encoding="utf-8")
        with patch.object(admin, "current_tailscale_admin", return_value=True), \
                patch.object(admin, "clean_expired_project_logs", return_value=0):
            self.client.post("/repairs/clean-logs", data={"csrf": "token"})
        self.assertEqual(admin.repair_guard("clean-logs")["failures"], 0)

    def test_high_risk_route_plan_validates_config_probe_and_impact(self):
        config = admin.store.load()
        with patch.object(admin, "validate_config") as validate, \
                patch.object(admin, "probe_stratum", return_value={"ok": True}):
            plan = admin.build_high_risk_plan("route", {"port": "11301", "endpoint_id": "f2pool-global"})
        validate.assert_called_once()
        self.assertTrue(plan["validated"])
        self.assertEqual(plan["affected_ports"], [11301])
        self.assertEqual(plan["data"]["endpoint_id"], "f2pool-global")

    def test_high_risk_firewall_requires_all_production_ports(self):
        config = admin.store.load()
        relay = {"listen_port": 452}
        required = sorted({22, 452} | {port for port, _endpoint, _kind, _group in admin.route_map(config)})
        plan = admin.validate_plan("firewall", {"ports": ",".join(map(str, required))}, config, relay,
            admin.VERSIONS, admin.HIGH_RISK_CANDIDATE_DIR, admin.validate_config,
            admin.validate_candidate_certificate, admin.probe_stratum, now=100)
        self.assertEqual(plan["data"]["ports"], required)
        with self.assertRaises(ValueError):
            admin.validate_plan("firewall", {"ports": "22,452"}, config, relay, admin.VERSIONS,
                admin.HIGH_RISK_CANDIDATE_DIR, admin.validate_config,
                admin.validate_candidate_certificate, admin.probe_stratum, now=100)

    def test_high_risk_dns_and_upgrade_candidates_are_staged(self):
        config = admin.store.load()
        dns = admin.validate_plan("dns", {"hostname": "relay.example.com"}, config, {}, admin.VERSIONS,
            admin.HIGH_RISK_CANDIDATE_DIR, admin.validate_config, admin.validate_candidate_certificate,
            admin.probe_stratum, resolver=lambda host: ["93.184.216.34"], now=100)
        self.assertEqual(dns["data"]["hostname"], "relay.example.com")
        upgrade = admin.HIGH_RISK_CANDIDATE_DIR / "release-4"
        (upgrade / "monitor-panel").mkdir(parents=True)
        (upgrade / "version.json").write_text(json.dumps({**admin.VERSIONS, "panel": "9.0.0"}), encoding="utf-8")
        (upgrade / "monitor-panel" / "upgrade-v3-panel.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        plan = admin.validate_plan("upgrade", {"candidate": "release-4"}, config, {}, admin.VERSIONS,
            admin.HIGH_RISK_CANDIDATE_DIR, admin.validate_config, admin.validate_candidate_certificate,
            admin.probe_stratum, now=100)
        self.assertEqual(plan["data"]["versions"]["panel"], "9.0.0")
        with self.assertRaises(ValueError):
            admin.validate_plan("upgrade", {"candidate": "../outside"}, config, {}, admin.VERSIONS,
                admin.HIGH_RISK_CANDIDATE_DIR, admin.validate_config, admin.validate_candidate_certificate,
                admin.probe_stratum, now=100)

    def test_high_risk_execute_requires_matching_unexpired_plan_and_revalidates(self):
        plan = {"id": "a" * 24, "operation": "route", "created_at": int(time.time()),
            "expires_at": int(time.time()) + 600, "data": {"port": 11301, "endpoint_id": "f2pool-global"},
            "summary": "test", "checks": [], "affected_ports": [11301], "validated": True}
        with self.client.session_transaction() as session:
            session["high_risk_plan"] = plan
        backup = admin.REPAIR_BACKUP_DIR / "backup.zip"
        with patch.object(admin, "current_tailscale_admin", return_value=True), \
                patch.object(admin, "build_high_risk_plan", return_value=plan) as revalidate, \
                patch.object(admin, "create_repair_backup", return_value=backup), \
                patch.object(admin, "save_and_reload") as apply, patch.object(admin, "sync_routes_immediately"):
            response = self.client.post("/high-risk/execute", data={"csrf": "token", "plan_id": plan["id"]})
        self.assertEqual(response.status_code, 302)
        revalidate.assert_called_once()
        apply.assert_called_once()
        with self.client.session_transaction() as session:
            self.assertNotIn("high_risk_plan", session)

    def test_notification_results_are_visible_without_exposing_settings(self):
        admin.NOTIFICATION_RESULT_FILE.write_text(json.dumps({"channels": {"wechat": {
            "status": "success", "sent": 2, "total": 2, "error": "", "display_time": "2026-09-22 12:00:00"}}}), encoding="utf-8")
        with patch.object(admin, "detect_relay_public_ip", return_value={"ok": True, "host": "93.184.216.34", "source": "test", "message": ""}):
            response = self.client.get("/alerts")
        self.assertEqual(response.status_code, 200)
        self.assertIn("各渠道最近发送结果".encode(), response.data)
        self.assertIn("发送成功 · 2/2".encode(), response.data)

    def test_recovered_service_does_not_leave_an_old_problem_open(self):
        records = [
            {"_SYSTEMD_UNIT": "haproxy.service", "PRIORITY": "3", "MESSAGE": "Connection failed"},
            {"_SYSTEMD_UNIT": "haproxy.service", "PRIORITY": "5", "MESSAGE": "Service recovered"},
        ]
        with patch.object(admin.subprocess, "run") as run:
            run.return_value.stdout = "\n".join(json.dumps(record) for record in records)
            logs = admin.recent_logs()
        self.assertEqual(logs["attention"], [])

    def test_administrator_overview_groups_sites_versions_and_expiry(self):
        admin.SECURE_RELAY_CONFIG.write_text(json.dumps({"listen_port": 452, "certificate": "",
            "clients": [{"id": "mine-a", "name": "一号矿场", "token": "a" * 64, "enabled": True}],
            "offline_after_seconds": 180}), encoding="utf-8")
        admin.SECURE_RELAY_STATE.write_text(json.dumps({"server_version": "2.2.0", "sites": {"mine-a": {
            "last_seen": 200, "last_ip": "198.51.100.20", "active": 8, "miner_count": 3,
            "miners": ["192.168.1.20", "192.168.1.21", "192.168.1.22"], "client_version": "2.2.0"}}}), encoding="utf-8")
        admin.SECURE_RELAY_MONITOR_STATE.write_text(json.dumps({"clients": {"mine-a": "offline"}}), encoding="utf-8")
        with patch.object(admin, "certificate_summary", return_value={"available": True, "name": "relay.example.com",
                "fingerprint": "F" * 64, "expires": "2026-10-01", "days_left": 17}), \
                patch.object(admin, "detect_relay_public_ip", return_value={"ok": True, "host": "93.184.216.34", "source": "test", "message": ""}):
            response = self.client.get("/overview")
        self.assertEqual(response.status_code, 200)
        for value in ("管理员总览", "一号矿场", "v2.2.0", "到期提醒", "当前生产存在明确异常"):
            self.assertIn(value.encode(), response.data)

    def test_administrator_reminder_settings_are_saved(self):
        response = self.client.post("/administrator-reminders", data={"csrf": "token", "vps_name": "美国主VPS",
            "vps_expiry": "2027-01-02", "domain_name": "relay.example.com", "domain_expiry": "2027-02-03",
            "expiry_reminder_days": "45"})
        self.assertEqual(response.status_code, 302)
        settings = admin.store.load()["settings"]
        self.assertEqual(settings["vps_name"], "美国主VPS")
        self.assertEqual(settings["expiry_reminder_days"], 45)

    def test_tailscale_identity_can_log_in_without_panel_password(self):
        with self.client.session_transaction() as session:
            session.clear()
        with patch.dict(os.environ, {"TAILSCALE_AUTO_LOGIN": "1", "TAILSCALE_ALLOWED_USERS": "owner@example.com"}):
            response = self.client.get("/overview", headers={"Tailscale-User-Login": "owner@example.com"})
            self.assertEqual(response.status_code, 200)
            with self.client.session_transaction() as session:
                self.assertEqual(session["tailscale_identity"], "owner@example.com")

    def test_tailscale_header_is_rejected_from_nonlocal_connection(self):
        with self.client.session_transaction() as session:
            session.clear()
        with patch.dict(os.environ, {"TAILSCALE_AUTO_LOGIN": "1"}):
            response = self.client.get("/overview", headers={"Tailscale-User-Login": "owner@example.com"},
                environ_base={"REMOTE_ADDR": "192.0.2.10"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_emergency_password_is_rate_limited(self):
        with self.client.session_transaction() as session:
            session.clear()
        password_hash = generate_password_hash("correct-password")
        with patch.dict(os.environ, {"PANEL_PASSWORD_HASH": password_hash}):
            for _ in range(5):
                self.assertEqual(self.client.post("/login", data={"password": "wrong"}).status_code, 200)
            blocked = self.client.post("/login", data={"password": "correct-password"})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)
        self.assertIn("尝试次数过多".encode(), blocked.data)

    def test_https_proxy_gets_secure_cookie_and_security_headers(self):
        with self.client.session_transaction() as session:
            session.clear()
        password_hash = generate_password_hash("correct-password")
        with patch.dict(os.environ, {"PANEL_PASSWORD_HASH": password_hash}):
            response = self.client.post("/login", data={"password": "correct-password"},
                headers={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", response.headers.get("Set-Cookie", ""))
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("max-age=31536000", response.headers["Strict-Transport-Security"])

    def test_missing_session_secret_is_rejected_for_production_start(self):
        previous = admin.app.secret_key
        try:
            admin.app.secret_key = None
            with self.assertRaisesRegex(RuntimeError, "PANEL_SECRET_KEY"):
                admin.require_secret_key()
        finally:
            admin.app.secret_key = previous

    def test_client_access_is_available_only_through_current_tailscale_request(self):
        secret = "a" * 64
        admin.SECURE_RELAY_CONFIG.write_text(json.dumps({"listen_port": 452,
            "certificate": str(Path(self.temp.name) / "server.crt"), "private_key": "/secret/server.key",
            "clients": [{"id": "mine-a", "name": "一号矿场", "token": secret, "enabled": True}]}), encoding="utf-8")
        admin.SECURE_RELAY_STATE.write_text(json.dumps({"sites": {"mine-a": {
            "last_seen": 1789000000, "last_ip": "198.51.100.20", "active": 12}}}), encoding="utf-8")
        self.assertEqual(self.client.get("/access").status_code, 404)
        with patch.object(admin, "certificate_summary", return_value={"available": True,
                "name": "relay.example.com", "fingerprint": "F" * 64}), \
                patch.object(admin, "detect_relay_public_ip", return_value={"ok": True,
                    "host": "93.184.216.34", "source": "test", "message": ""}):
            response = self.client.get("/access", headers={"Tailscale-User-Login": "owner@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("客户端接入资料".encode(), response.data)
        self.assertIn("新增矿场并生成接入密钥".encode(), response.data)
        self.assertIn("编辑矿场名称".encode(), response.data)
        self.assertIn(("F" * 64).encode(), response.data)
        self.assertIn(("••••••••••••" + secret[-4:]).encode(), response.data)
        self.assertNotIn(secret.encode(), response.data)
        self.assertNotIn(b"/secret/server.key", response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")

    def test_access_package_is_encrypted_authenticated_and_downloaded_once(self):
        secret = "b" * 64
        certificate = {"available": True, "name": "relay.example.com", "fingerprint": "F" * 64}
        admin.SECURE_RELAY_CONFIG.write_text(json.dumps({"listen_port": 452, "certificate": "server.crt",
            "clients": [{"id": "mine-a", "name": "一号矿场", "token": secret, "enabled": True}]}), encoding="utf-8")
        headers = {"Tailscale-User-Login": "owner@example.com"}
        with patch.object(admin, "certificate_summary", return_value=certificate), \
                patch.object(admin, "detect_relay_public_ip", return_value={"ok": True, "host": "198.51.100.8"}):
            response = self.client.post("/client-access/mine-a/package", data={"csrf": "token", "hours": "24"}, headers=headers)
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            pending = dict(session["access_package"])
        package_path = admin.ACCESS_PACKAGE_DIR / f"{pending['id']}.msrelay"
        raw = package_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        envelope = json.loads(raw)
        salt, nonce, cipher = (base64.b64decode(envelope[name]) for name in ("salt", "nonce", "ciphertext"))
        keys = hashlib.pbkdf2_hmac("sha256", pending["code"].replace("-", "").encode(), salt, 200000, dklen=64)
        aad = f"MSRA1|{envelope['id']}|{envelope['expires']}|1".encode()
        self.assertTrue(hmac.compare_digest(base64.b64decode(envelope["mac"]),
            hmac.new(keys[32:], aad + salt + nonce + cipher, hashlib.sha256).digest()))
        stream = admin._access_package_keystream(keys[:32], nonce, len(cipher))
        payload = json.loads(bytes(a ^ b for a, b in zip(cipher, stream)))
        self.assertEqual(payload["shared_key"], secret)
        download = self.client.get(f"/client-access/package/{pending['id']}/download", headers=headers)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers["Cache-Control"], "no-store, max-age=0")
        download.close()
        self.assertEqual(self.client.get(f"/client-access/package/{pending['id']}/download", headers=headers).status_code, 404)

    def test_remote_client_action_requires_tailscale_admin_and_is_limited(self):
        admin.SECURE_RELAY_CONFIG.write_text(json.dumps({"listen_port": 452,
            "clients": [{"id": "mine-a", "name": "一号矿场", "token": "b" * 64, "enabled": True}]}), encoding="utf-8")
        self.assertEqual(self.client.post("/client-action/mine-a/reconnect", data={"csrf": "token"}).status_code, 404)
        headers = {"Tailscale-User-Login": "owner@example.com"}
        response = self.client.post("/client-action/mine-a/reconnect", data={"csrf": "token"}, headers=headers)
        self.assertEqual(response.status_code, 302)
        queued = json.loads(admin.CLIENT_ACTION_FILE.read_text(encoding="utf-8"))
        self.assertEqual(queued["clients"]["mine-a"]["action"], "reconnect")
        self.assertEqual(queued["results"]["mine-a"]["status"], "queued")
        self.assertEqual(self.client.post("/client-action/mine-a/shell", data={"csrf": "token"}, headers=headers).status_code, 404)

    def test_create_and_rename_farm_preserve_legacy_access_and_settings(self):
        secret = "a" * 64
        original = {"listen_port": 452, "token": secret, "certificate": "/old/cert", "max_connections": 500}
        admin.SECURE_RELAY_CONFIG.write_text(json.dumps(original), encoding="utf-8")
        headers = {"Tailscale-User-Login": "owner@example.com"}
        response = self.client.post("/client-access/create", data={"csrf": "token", "name": "云南矿场"}, headers=headers)
        self.assertEqual(response.status_code, 302)
        updated = json.loads(admin.SECURE_RELAY_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(updated["clients"][0]["token"], secret)
        added = updated["clients"][1]
        self.assertEqual(added["name"], "云南矿场")
        self.assertEqual(len(added["token"]), 64)
        self.assertNotEqual(added["token"], secret)
        self.assertEqual(updated["listen_port"], 452)
        self.assertEqual(updated["certificate"], "/old/cert")
        self.assertEqual(updated["max_connections"], 500)
        self.assertEqual(json.loads(admin.SECURE_RELAY_CONFIG.with_suffix(".json.backup").read_text()), original)
        response = self.client.post("/client-access/default/rename", data={"csrf": "token", "name": "原矿场"}, headers=headers)
        self.assertEqual(response.status_code, 302)
        renamed = json.loads(admin.SECURE_RELAY_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(renamed["clients"][0]["name"], "原矿场")
        self.assertEqual(renamed["clients"][0]["token"], secret)
        self.assertEqual(renamed["clients"][1], added)
        self.assertEqual(admin.site_overview_rows()[0]["name"], "原矿场")
        audit = admin.AUDIT_FILE.read_text(encoding="utf-8")
        self.assertIn("新增矿场客户端:", audit)
        self.assertIn("修改矿场名称:default", audit)
        self.assertNotIn(added["token"], audit)
        self.assertNotIn(secret, audit)

    def test_farm_writes_require_current_tailscale_csrf_and_valid_name(self):
        admin.SECURE_RELAY_CONFIG.write_text(json.dumps({"token": "a" * 64}), encoding="utf-8")
        original = admin.SECURE_RELAY_CONFIG.read_bytes()
        headers = {"Tailscale-User-Login": "owner@example.com"}
        self.assertEqual(self.client.post("/client-access/create", data={"csrf": "token", "name": "矿场"}).status_code, 404)
        self.assertEqual(self.client.post("/client-access/create", data={"csrf": "wrong", "name": "矿场"}, headers=headers).status_code, 403)
        for name in ("", "x" * 65, "矿场\n名称"):
            self.assertEqual(self.client.post("/client-access/create", data={"csrf": "token", "name": name}, headers=headers).status_code, 302)
        self.assertEqual(self.client.post("/client-access/missing/rename", data={"csrf": "token", "name": "矿场"}, headers=headers).status_code, 404)
        self.assertEqual(admin.SECURE_RELAY_CONFIG.read_bytes(), original)

    def test_delete_farm_requires_confirmation_and_preserves_other_clients(self):
        headers = {"Tailscale-User-Login": "owner@example.com"}
        clients = [{"id": "a", "name": "矿场A", "token": "a" * 64}, {"id": "b", "name": "矿场B", "token": "b" * 64}]
        admin.SECURE_RELAY_CONFIG.write_text(json.dumps({"clients": clients, "listen_port": 452, "token": "a" * 64}), encoding="utf-8")
        original = admin.SECURE_RELAY_CONFIG.read_bytes()
        self.assertEqual(self.client.get("/client-access/a/delete").status_code, 404)
        self.assertEqual(self.client.post("/client-access/a/delete", data={"csrf": "token"}).status_code, 404)
        with patch.object(admin, "detect_relay_public_ip", return_value={"host": "198.51.100.1"}):
            response = self.client.get("/client-access/a/delete", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("删除可能导致整个矿场中转掉线".encode(), response.data)
        self.assertNotIn(("a" * 64).encode(), response.data)
        self.assertEqual(admin.SECURE_RELAY_CONFIG.read_bytes(), original)
        with self.client.session_transaction() as session:
            nonce = session["delete_client_confirmation"]["nonce"]
        data = {"csrf": "token", "confirmation": nonce, "confirm_name": "矿场A", "acknowledge": "yes"}
        self.assertEqual(self.client.post("/client-access/a/delete", data=dict(data, csrf="wrong"), headers=headers).status_code, 403)
        for changes in ({"confirm_name": "矿场B"}, {"acknowledge": ""}, {"confirmation": "wrong"}):
            self.client.post("/client-access/a/delete", data=dict(data, **changes), headers=headers)
            self.assertEqual(admin.SECURE_RELAY_CONFIG.read_bytes(), original)
        self.client.post("/client-access/a/delete", data=data, headers=headers)
        updated = json.loads(admin.SECURE_RELAY_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(updated["clients"], [clients[1]])
        self.assertEqual(updated["listen_port"], 452)
        self.assertNotIn("token", updated)
        audit = admin.AUDIT_FILE.read_text(encoding="utf-8")
        self.assertIn("删除矿场客户端:a", audit)
        self.assertNotIn("a" * 64, audit)
        self.client.post("/client-access/a/delete", data=data, headers=headers)
        self.assertEqual(json.loads(admin.SECURE_RELAY_CONFIG.read_text(encoding="utf-8")), updated)

    def test_delete_confirmation_rejects_stale_or_expired_and_allows_empty_clients(self):
        headers = {"Tailscale-User-Login": "owner@example.com"}
        admin.SECURE_RELAY_CONFIG.write_text(json.dumps({"token": "a" * 64}), encoding="utf-8")
        with patch.object(admin, "detect_relay_public_ip", return_value={"host": "198.51.100.1"}):
            self.client.get("/client-access/default/delete", headers=headers)
        with self.client.session_transaction() as session:
            pending = dict(session["delete_client_confirmation"])
            session["delete_client_confirmation"] = dict(pending, expires=0)
        data = {"csrf": "token", "confirmation": pending["nonce"], "confirm_name": pending["name"], "acknowledge": "yes"}
        self.client.post("/client-access/default/delete", data=data, headers=headers)
        self.assertTrue(admin.find_relay_client("default"))
        with self.client.session_transaction() as session:
            session["delete_client_confirmation"] = pending
        admin.SECURE_RELAY_CONFIG.write_text(json.dumps({"token": "b" * 64}), encoding="utf-8")
        self.client.post("/client-access/default/delete", data=data, headers=headers)
        self.assertTrue(admin.find_relay_client("default"))
        with patch.object(admin, "detect_relay_public_ip", return_value={"host": "198.51.100.1"}):
            self.client.get("/client-access/default/delete", headers=headers)
        with self.client.session_transaction() as session:
            data["confirmation"] = session["delete_client_confirmation"]["nonce"]
        self.client.post("/client-access/default/delete", data=data, headers=headers)
        updated = json.loads(admin.SECURE_RELAY_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(updated["clients"], [])
        self.assertNotIn("token", updated)
        self.assertEqual(admin.site_overview_rows(), [])
        self.client.post("/client-access/create", data={"csrf": "token", "name": "新矿场"}, headers=headers)
        self.assertEqual(len(json.loads(admin.SECURE_RELAY_CONFIG.read_text(encoding="utf-8"))["clients"]), 1)

    def test_client_secret_reveal_and_copy_are_audited(self):
        secret = "b" * 64
        admin.SECURE_RELAY_CONFIG.write_text(json.dumps({"listen_port": 452, "certificate": "",
            "private_key": "/secret/server.key", "clients": [{"id": "mine-a", "name": "一号矿场",
                "token": secret, "enabled": True}]}), encoding="utf-8")
        headers = {"Tailscale-User-Login": "owner@example.com"}
        response = self.client.post("/client-access/mine-a/reveal", data={"csrf": "token"}, headers=headers)
        self.assertEqual(response.status_code, 302)
        with patch.object(admin, "detect_relay_public_ip", return_value={"ok": True,
                "host": "93.184.216.34", "source": "test", "message": ""}):
            response = self.client.get("/access", headers=headers)
        self.assertIn(secret.encode(), response.data)
        response = self.client.post("/client-access/mine-a/copy", data={"csrf": "token"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["value"], secret)
        audit = admin.AUDIT_FILE.read_text(encoding="utf-8")
        self.assertIn("查看客户端共享密钥:mine-a", audit)
        self.assertIn("复制客户端共享密钥:mine-a", audit)
        self.assertNotIn(secret, audit)
        self.assertEqual(self.client.post("/client-access/mine-a/copy", data={"csrf": "token"}).status_code, 404)

    def test_same_worker_from_multiple_routes_is_merged(self):
        config = admin.store.load()
        inspector = {"pools": [
            {"id": "hashhut-eu", "summary": {"connections": 1, "workers": 1, "submitted": 10, "accepted": 10, "rejected": 0},
             "routes": [{"upstream_ok": True, "upstream_latency_ms": 20}],
             "workers": [{"name": "owner.same", "agent": "Board/A", "sources": ["192.0.2.1"], "active": 1,
                "status": "online", "status_text": "在线", "inactive_seconds": 0, "last_seen": "2026-06-24 10:00:00",
                "submitted": 10, "accepted": 10, "rejected": 0, "latency_ms": 20, "hashrate_value": 1000,
                "last_share": "2026-06-24 10:00:00", "last_error": "", "details": [{"id": "1", "source_ip": "192.0.2.1",
                "source_port": 5001, "agent": "Board/A", "status": "在线", "connected_at": "2026-06-24 09:00:00",
                "disconnected_at": "", "submitted": 10, "accepted": 10, "rejected": 0, "reject_percent": 0,
                "latency_ms": 20, "last_share": "2026-06-24 10:00:00", "last_error": ""}]}]},
            {"id": "hashhut-ru", "summary": {"connections": 1, "workers": 1, "submitted": 8, "accepted": 8, "rejected": 0},
             "routes": [{"upstream_ok": True, "upstream_latency_ms": 30}],
             "workers": [{"name": "owner.same", "agent": "Board/B", "sources": ["192.0.2.1"], "active": 1,
                "status": "online", "status_text": "在线", "inactive_seconds": 0, "last_seen": "2026-06-24 10:00:01",
                "submitted": 8, "accepted": 8, "rejected": 0, "latency_ms": 30, "hashrate_value": 2000,
                "last_share": "2026-06-24 10:00:01", "last_error": "", "details": [{"id": "2", "source_ip": "192.0.2.1",
                "source_port": 5002, "agent": "Board/B", "status": "在线", "connected_at": "2026-06-24 09:00:00",
                "disconnected_at": "", "submitted": 8, "accepted": 8, "rejected": 0, "reject_percent": 0,
                "latency_ms": 30, "last_share": "2026-06-24 10:00:01", "last_error": ""}]}]},
        ]}
        pools = admin.stratum_pool_rows(config, inspector)
        hashhut = next(pool for pool in pools if pool["name"] == "Hash-Hut")
        self.assertEqual(len(hashhut["workers"]), 1)
        self.assertEqual(hashhut["workers"][0]["active"], 2)
        self.assertEqual(len(hashhut["workers"][0]["details"]), 2)
        self.assertEqual(hashhut["workers"][0]["submitted"], 18)

    def test_alert_settings_and_monitor_toggle(self):
        response = self.client.post("/alerts", data={"csrf": "token", "failure_count": "4",
            "repeat_minutes": "20", "min_connections": "2", "mem_threshold": "80", "disk_threshold": "90"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(admin.store.load()["settings"]["failure_alert_count"], 4)
        self.assertIn("MIN_CONNECTIONS=2", admin.ENV_FILE.read_text(encoding="utf-8"))
        response = self.client.post("/toggle-monitor", data={"csrf": "token", "enabled": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(admin.monitor_enabled())

    def test_probe_settings_can_disable_or_slow_endpoint_checks(self):
        response = self.client.post("/probe-settings", data={"csrf": "token", "endpoint_probe_enabled": "0",
            "active_probe_minutes": "240", "template_probe_minutes": "360",
            "endpoint_batch_probe_enabled": "0", "hourly_samples": "0", "daily_samples": "0"})
        self.assertEqual(response.status_code, 302)
        settings = admin.store.load()["settings"]
        self.assertFalse(settings["endpoint_probe_enabled"])
        self.assertFalse(settings["endpoint_batch_probe_enabled"])
        self.assertEqual(settings["active_probe_seconds"], 14400)
        self.assertEqual(settings["template_probe_seconds"], 21600)

        response = self.client.post("/probe-settings", data={"csrf": "token", "endpoint_probe_enabled": "1",
            "active_probe_minutes": "360", "template_probe_minutes": "360",
            "endpoint_batch_probe_enabled": "0", "hourly_samples": "0", "daily_samples": "0"})
        self.assertEqual(response.status_code, 302)
        settings = admin.store.load()["settings"]
        self.assertTrue(settings["endpoint_probe_enabled"])
        self.assertEqual(settings["active_probe_seconds"], 21600)
        self.assertEqual(settings["template_probe_seconds"], 21600)

    def test_custom_target_parser_rejects_credentials_and_paths(self):
        self.assertEqual(admin.parse_custom_target("stratum+tcp://pool.example.com:3333"), ("pool.example.com", 3333))
        with self.assertRaises(Exception):
            admin.parse_custom_target("stratum+tcp://user:pass@pool.example.com:3333/path")

    def test_route_history_keeps_ten_and_can_restore_an_older_record(self):
        config = admin.store.load()
        for number in range(12):
            admin.remember_route_change(config, 11301, "longpool-asia-8080", "f2pool-global")
        admin.store.save(config, action="history-test")
        history = admin.route_change_history(admin.store.load())
        self.assertEqual(len(history), 10)
        self.assertEqual(len({item["id"] for item in history}), 10)
        config = admin.store.load()
        admin.set_route_endpoint(config, 11301, "f2pool-global")
        admin.store.save(config, action="set-current")
        with patch.object(admin, "request_reconnect"):
            response = self.client.post("/route/11301/restore", data={"csrf": "token", "change_id": history[-1]["id"]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(admin.route_endpoint_id(admin.store.load(), 11301), "longpool-asia-8080")

    def test_route_events_are_saved_and_visible_without_wechat(self):
        config = admin.store.load()
        endpoint = next(item for item in config["endpoints"] if item["id"] == "f2pool-global")
        admin.send_route_event("route_restored", 11301, endpoint, "测试线路记录")
        with patch.object(admin, "detect_relay_public_ip", return_value={"ok": True, "host": "93.184.216.34", "source": "test", "message": ""}):
            response = self.client.get("/overview")
        self.assertIn("测试线路记录".encode(), response.data)
        self.assertTrue(admin.ENDPOINT_EVENT_FILE.exists())

    def test_peer_settings_and_authenticated_inbound_sync(self):
        token = "a" * 64
        response = self.client.post("/peer-settings", data={"csrf": "token", "enabled": "1",
            "token": token, "peers": "https://peer.tail1234.ts.net"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(admin.load_peer_settings()["token"], token)
        def confirm_all(**kwargs):
            admin.ConfigStore._atomic_write(admin.PEER_OUTBOX_FILE,
                json.dumps({"items": []}, ensure_ascii=False) + "\n", mode=0o600)
            return {"sent": len(kwargs.get("only_ids", [])), "pending": 0}
        with patch("route_switch_monitor.flush_peer_outbox", side_effect=confirm_all):
            response = self.client.post("/peer-sync-all", data={"csrf": "token"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(admin.PEER_OUTBOX_FILE.read_text(encoding="utf-8"))["items"], [])
        self.assertIn("双 VPS 同步成功".encode(), response.data)
        self.assertEqual(admin.peer_sync_summary()["last_status"], "success")
        endpoint = next(item for item in admin.store.load()["endpoints"] if item["id"] == "f2pool-global")
        payload = {"event_id": "b" * 32, "source": "peer-vps", "revision": 9999999999999999999, "port": 11301,
            "action": "test", "endpoint": endpoint}
        denied = self.client.post("/api/v3/route-sync", json=payload)
        self.assertEqual(denied.status_code, 404)
        with patch.object(admin, "request_reconnect") as reconnect:
            accepted = self.client.post("/api/v3/route-sync", json=payload,
                headers={"Authorization": "Bearer " + token})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(admin.route_endpoint_id(admin.store.load(), 11301), "f2pool-global")
        reconnect.assert_called_once_with(11301)
        stale = {**payload, "event_id": "c" * 32, "revision": 0,
            "endpoint": next(item for item in admin.store.load()["endpoints"] if item["id"] == "longpool-asia-8080")}
        accepted = self.client.post("/api/v3/route-sync", json=stale,
            headers={"Authorization": "Bearer " + token})
        self.assertTrue(accepted.get_json()["stale"])
        self.assertEqual(admin.route_endpoint_id(admin.store.load(), 11301), "f2pool-global")

    def test_authenticated_peer_health_summary_and_difference_detection(self):
        token = "h" * 64
        peer = "https://peer.tail1234.ts.net"
        admin.PEER_SYNC_FILE.write_text(json.dumps({"enabled": True, "peers": [peer], "token": token}),
            encoding="utf-8")
        denied = self.client.get("/api/v3/health-summary")
        self.assertEqual(denied.status_code, 404)
        with patch.object(admin, "service_state", return_value="active"), \
                patch.object(admin, "server_metrics", return_value={"memory": "20%", "disk": "30%"}):
            accepted = self.client.get("/api/v3/health-summary",
                headers={"Authorization": "Bearer " + token})
        self.assertEqual(accepted.status_code, 200)
        health = accepted.get_json()["health"]
        self.assertEqual(health["panel_version"], admin.PANEL_VERSION)
        self.assertEqual(health["services_active"], health["services_total"])

        class Response:
            def read(self):
                return json.dumps({"ok": True, "health": {**health, "name": "peer-vps",
                    "panel_version": "0.0.1", "route_digest": "different"}}).encode()
            def close(self):
                pass

        services = {"HAProxy": "active", "管理面板": "active"}
        with patch.object(admin.urllib.request, "urlopen", return_value=Response()):
            summary = admin.dual_vps_health_summary(admin.store.load(), services,
                {"memory": "20%", "disk": "30%"}, admin.load_peer_settings())
        self.assertEqual(len(summary["nodes"]), 2)
        self.assertIn("管理面板版本不一致", summary["differences"])
        self.assertIn("线路配置不一致", summary["differences"])

    def test_overview_renders_unified_dual_vps_health(self):
        with patch.object(admin, "detect_relay_public_ip", return_value={"ok": True,
                "host": "93.184.216.34", "source": "test", "message": ""}):
            response = self.client.get("/overview")
        self.assertEqual(response.status_code, 200)
        self.assertIn("双 VPS 统一健康摘要".encode(), response.data)
        self.assertIn("等待配置对端".encode(), response.data)

    def test_immediate_peer_sync_failure_is_reported_and_kept_for_retry(self):
        token = "f" * 64
        admin.PEER_SYNC_FILE.write_text(json.dumps({"enabled": True,
            "peers": ["https://peer.tail1234.ts.net"], "token": token}), encoding="utf-8")

        def leave_failed(**kwargs):
            outbox = admin.load_json(admin.PEER_OUTBOX_FILE, {"items": []})
            for item in outbox["items"]:
                if item["id"] in kwargs.get("only_ids", set()):
                    item["last_error"] = "连接超时"
            admin.ConfigStore._atomic_write(admin.PEER_OUTBOX_FILE,
                json.dumps(outbox, ensure_ascii=False) + "\n", mode=0o600)
            return {"sent": 0, "pending": len(outbox["items"])}

        with patch("route_switch_monitor.flush_peer_outbox", side_effect=leave_failed):
            result = admin.sync_routes_immediately(admin.store.load(), [(11301, "test")])
        self.assertEqual(result["status"], "failed")
        self.assertIn("连接超时", result["message"])
        self.assertEqual(result["pending"], 1)
        self.assertEqual(admin.peer_sync_summary()["last_status"], "failed")

    def test_logical_peer_version_is_not_blocked_by_old_clock_revision(self):
        admin.PEER_STATE_FILE.write_text(json.dumps({"received": [], "route_versions": {
            "11301": {"revision": 9999999999999999999, "source": "old-clock"}}}), encoding="utf-8")
        endpoint = next(item for item in admin.store.load()["endpoints"] if item["id"] == "f2pool-global")
        payload = {"event_id": "d" * 32, "source": "new-peer", "created_at": 1,
            "version": {"node_id": "e" * 32, "sequence": 1}, "port": 11301,
            "action": "test", "endpoint": endpoint}
        with patch.object(admin, "request_reconnect") as reconnect:
            result = admin.apply_peer_payload(payload)
        self.assertTrue(result["changed"])
        self.assertEqual(admin.route_endpoint_id(admin.store.load(), 11301), "f2pool-global")
        state = json.loads(admin.PEER_STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(state["route_versions"]["11301"]["nodes"]["e" * 32], 1)
        self.assertGreater(state["route_versions"]["11301"]["clock_skew_seconds"], 300)
        reconnect.assert_called_once_with(11301)


if __name__ == "__main__":
    unittest.main()
