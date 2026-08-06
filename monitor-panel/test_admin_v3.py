import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import stratum_admin_v3 as admin
except ModuleNotFoundError as exc:
    if exc.name == "flask":
        raise unittest.SkipTest("Flask is installed by the Linux deployment script")
    raise
from v3_manager import ConfigStore


class AdminV3Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        config = json.loads(Path("v3-config.json").read_text(encoding="utf-8"))
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
        admin.store = ConfigStore(config_path, root / "history", root / "audit.jsonl")
        admin.app.config.update(TESTING=True, SECRET_KEY="test")
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
        self.assertIn("关键看板".encode(), response.data)
        self.assertIn(b"stratum+tcp://93.184.216.34:9999", response.data)
        self.assertIn("复制".encode(), response.data)
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

    def test_relay_copy_address_uses_detected_vps_public_ip(self):
        def fake_run(command, **kwargs):
            class Result:
                stdout = "1.1.1.1 via 93.184.216.1 dev ens17 src 93.184.216.34 uid 0\n"
                stderr = ""
            return Result()

        with patch.object(admin.subprocess, "run", side_effect=fake_run):
            response = self.client.get("/overview")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"stratum+tcp://93.184.216.34:9999", response.data)
        self.assertNotIn(b"stratum+tcp://localhost:9999", response.data)

    def test_missing_public_ip_disables_copy_and_sends_wechat_alert(self):
        admin.ENV_FILE.write_text("WECHAT_WEBHOOK=https://qyapi.example/webhook\n", encoding="utf-8")
        status = {"ok": False, "host": "", "source": "", "message": "无法从默认出公网路由识别 VPS 公网 IPv4"}
        with patch.object(admin, "detect_relay_public_ip", return_value=status), \
                patch.object(admin.urllib.request, "urlopen") as urlopen:
            response = self.client.get("/overview")
        self.assertEqual(response.status_code, 200)
        self.assertIn("当前 VPS 公网 IP 无法获取".encode(), response.data)
        self.assertIn("当前IP无法获取".encode(), response.data)
        self.assertIn("不可复制".encode(), response.data)
        self.assertNotIn(b"stratum+tcp://", response.data)
        self.assertTrue(urlopen.called)
        events = admin.ENDPOINT_EVENT_FILE.read_text(encoding="utf-8")
        self.assertIn("public_ip_missing", events)

    def test_all_dashboard_pages_render(self):
        for page in ("overview", "miners", "routes", "alerts", "settings", "logs"):
            response = self.client.get("/" + page)
            self.assertEqual(response.status_code, 200, page)

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


if __name__ == "__main__":
    unittest.main()
