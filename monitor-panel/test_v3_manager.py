import json
import tempfile
import unittest
from pathlib import Path

from v3_manager import ConfigError, ConfigStore, render_haproxy_config, render_inspector_config, route_map, validate_config


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


if __name__ == "__main__":
    unittest.main()
