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
        admin.PEER_SYNC_FILE = root / "peer.json"
        admin.PEER_OUTBOX_FILE = root / "peer-outbox.json"
        admin.PEER_STATE_FILE = root / "peer-state.json"
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

    def test_peer_outbox_delivers_and_removes_item(self):
        token = "c" * 64
        admin.PEER_SYNC_FILE.write_text(json.dumps({"enabled": True,
            "peers": ["https://peer.tail1234.ts.net"], "token": token}), encoding="utf-8")
        admin.queue_route_sync(admin.store.load(), 11301, "test")
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"ok":true,"changed":true}'

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["auth"] = request.headers["Authorization"]
            return Response()

        result = switcher.flush_peer_outbox(now=1000, opener=opener, notifier=self.events.append)
        self.assertEqual(result, {"sent": 1, "pending": 0})
        self.assertEqual(captured["url"], "https://peer.tail1234.ts.net/api/v3/route-sync")
        self.assertEqual(captured["auth"], "Bearer " + token)
        self.assertEqual(json.loads(admin.PEER_OUTBOX_FILE.read_text(encoding="utf-8"))["items"], [])

    def test_peer_outbox_failure_is_retained_for_retry(self):
        token = "d" * 64
        admin.PEER_SYNC_FILE.write_text(json.dumps({"enabled": True,
            "peers": ["https://peer.tail1234.ts.net"], "token": token}), encoding="utf-8")
        admin.queue_route_sync(admin.store.load(), 11301, "test")

        def opener(request, timeout):
            raise OSError("offline")

        result = switcher.flush_peer_outbox(now=1000, opener=opener, notifier=self.events.append)
        self.assertEqual(result, {"sent": 0, "pending": 1})
        item = json.loads(admin.PEER_OUTBOX_FILE.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(item["attempts"], 1)
        self.assertGreater(item["next_attempt"], 1000)
        self.assertEqual(self.events[0]["type"], "route_sync_failed")


if __name__ == "__main__":
    unittest.main()
