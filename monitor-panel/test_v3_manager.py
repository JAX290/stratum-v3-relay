import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v3_manager import ConfigError, ConfigStore, append_bounded_jsonl, render_haproxy_config, render_inspector_config, route_map, validate_config


class V3ManagerTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(Path("v3-config.json").read_text(encoding="utf-8"))

    def test_valid_config_and_rendered_routes(self):
        self.assertTrue(validate_config(self.config))
        routes = route_map(self.config)
        self.assertEqual(len(routes), 24)
        self.assertNotIn(10000, [item[0] for item in routes])
        self.assertNotIn(10003, [item[0] for item in routes])
        haproxy = render_haproxy_config(self.config)
        self.assertIn("bind *:9999", haproxy)
        self.assertIn("bind *:11303", haproxy)
        self.assertIn("send-proxy", haproxy)
        self.assertNotIn("send-proxy check", haproxy)
        inspector = render_inspector_config(self.config)
        self.assertEqual(len(inspector["relays"]), len(self.config["endpoints"]))
        self.assertTrue(all(item["proxy_protocol"] for item in inspector["relays"]))

    def test_rejects_sensitive_and_private_targets(self):
        config = json.loads(json.dumps(self.config))
        config["endpoints"][0]["host"] = "127.0.0.1"
        with self.assertRaises(ConfigError):
            validate_config(config)
        config = json.loads(json.dumps(self.config))
        config["endpoints"][0]["port"] = 22
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_validates_single_miner_canary(self):
        config = json.loads(json.dumps(self.config))
        config["canary_routes"] = [{"port": 11301, "source_ip": "192.168.1.20", "endpoint_id": "f2pool-global"}]
        self.assertTrue(validate_config(config))
        config["canary_routes"][0]["source_ip"] = "8.8.8.8"
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_legacy_route_history_does_not_block_upgrade(self):
        config = json.loads(json.dumps(self.config))
        config["last_route_changes"] = [{"port": 11301,
            "previous_endpoint_id": "longpool-asia-8080", "endpoint_id": "f2pool-global"}
            for _ in range(20)]
        self.assertTrue(validate_config(config))

    def test_history_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ConfigStore(root / "config.json", root / "history", root / "audit.jsonl")
            store.save(self.config)
            changed = json.loads(json.dumps(self.config))
            changed["templates"][0]["name"] = "Changed"
            store.save(changed)
            history = store.history()
            self.assertEqual(len(history), 1)
            restored = store.rollback(history[0])
            self.assertEqual(restored["templates"][0]["name"], self.config["templates"][0]["name"])

    def test_history_and_audit_files_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory, patch("v3_manager.HISTORY_LIMIT", 3):
            root = Path(directory)
            store = ConfigStore(root / "config.json", root / "history", root / "audit.jsonl")
            for number in range(10):
                config = json.loads(json.dumps(self.config))
                config["templates"][0]["name"] = f"Changed {number}"
                store.save(config)
            self.assertEqual(len(store.history()), 3)
            log = root / "bounded.jsonl"
            for number in range(20):
                append_bounded_jsonl(log, {"number": number, "message": "x" * 40},
                    maximum_bytes=240, keep_lines=3)
            records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            self.assertLessEqual(len(records), 5)
            self.assertEqual(records[-1]["number"], 19)

    def test_stale_loaded_config_cannot_overwrite_newer_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ConfigStore(root / "config.json", root / "history", root / "audit.jsonl")
            store.save(self.config)
            first = store.load()
            stale = store.load()
            first["templates"][0]["name"] = "first writer"
            store.save(first)
            stale["templates"][0]["name"] = "stale writer"
            with self.assertRaisesRegex(ConfigError, "另一个操作"):
                store.save(stale)
            self.assertEqual(store.load()["templates"][0]["name"], "first writer")


if __name__ == "__main__":
    unittest.main()
