import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import route_switch_monitor as switcher
import stratum_admin_v3 as admin
from v3_manager import ConfigStore


class RouteSwitchMonitorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        config = json.loads(Path("v3-config.json").read_text(encoding="utf-8"))
        config["canary_routes"] = [{"port": 11301, "source_ip": "192.168.1.20",
            "endpoint_id": "f2pool-global", "original_endpoint_id": "longpool-asia-8080",
            "algorithm": "scrypt", "started_at": 100, "review_after": 700,
            "duration_minutes": 10, "auto_switch": True,
            "baseline": {"submitted": 0, "accepted": 0, "rejected": 0}}]
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        admin.CONFIG_FILE = config_path
        admin.INSPECTOR_CONFIG = root / "inspector.json"
        admin.HAPROXY_CONFIG = root / "haproxy.cfg"
        admin.INTEGRITY_BASELINE = root / "integrity.json"
        admin.INSPECTOR_STATE_FILE = root / "inspector-state.json"
        admin.RELAY_CONTROL_FILE = root / "control.json"
        admin.ENDPOINT_EVENT_FILE = root / "events.jsonl"
        admin.store = ConfigStore(config_path, root / "history", root / "audit.jsonl")
        self.events = []

    def tearDown(self):
        self.temp.cleanup()

    def write_shares(self, submitted, accepted, rejected):
        admin.INSPECTOR_STATE_FILE.write_text(json.dumps({"pools": [{"id": "f2pool-global", "workers": [{
            "details": [{"source_ip": "192.168.1.20", "public_port": 11301, "status": "在线",
                "submitted": submitted, "accepted": accepted, "rejected": rejected}]}]}]}), encoding="utf-8")

    def test_success_marks_verified_and_promotes_route(self):
        self.write_shares(20, 20, 0)
        with patch.object(admin, "request_reconnect") as reconnect:
            result = switcher.evaluate_due(now=701, notifier=self.events.append)
        config = admin.store.load()
        route = next(item for item in config["fixed_routes"] if item["port"] == 11301)
        endpoint = next(item for item in config["endpoints"] if item["id"] == "f2pool-global")
        self.assertEqual(result[0]["type"], "canary_passed")
        self.assertEqual(route["endpoint_id"], "f2pool-global")
        self.assertTrue(endpoint["verified"])
        self.assertEqual(config["canary_routes"], [])
        reconnect.assert_called_once_with(11301)

    def test_no_accepted_share_reverts_only_test_miner(self):
        self.write_shares(4, 0, 4)
        with patch.object(admin, "request_reconnect") as reconnect:
            result = switcher.evaluate_due(now=701, notifier=self.events.append)
        config = admin.store.load()
        route = next(item for item in config["fixed_routes"] if item["port"] == 11301)
        self.assertEqual(result[0]["type"], "canary_failed")
        self.assertEqual(route["endpoint_id"], "longpool-asia-8080")
        self.assertEqual(config["canary_routes"], [])
        reconnect.assert_called_once_with(11301, "192.168.1.20")


if __name__ == "__main__":
    unittest.main()
