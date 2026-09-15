import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import stratum_public_status as public
except ModuleNotFoundError as exc:
    if exc.name == "flask":
        raise unittest.SkipTest("Flask is installed by the Linux deployment script")
    raise


class PublicStatusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        public.CONFIG_FILE = root / "config.json"
        public.INSPECTOR_STATE_FILE = root / "state.json"
        public.ENDPOINT_EVENT_FILE = root / "events.jsonl"
        public.SECURE_RELAY_EVENT_FILE = root / "relay-events.jsonl"
        public.CONFIG_FILE.write_text(json.dumps({"endpoints": [
            {"id": "secret-route", "pool": "示例矿池", "host": "secret.pool.example", "port": 9999}
        ]}), encoding="utf-8")
        public.INSPECTOR_STATE_FILE.write_text(json.dumps({"pools": [{
            "id": "secret-route", "summary": {"connections": 2, "submitted": 10, "accepted": 10, "rejected": 0},
            "routes": [{"upstream_ok": True, "target": "secret.pool.example:9999"}],
            "workers": [{"name": "wallet.private-worker", "status": "online", "sources": ["192.168.1.55"]}]
        }]}), encoding="utf-8")
        public.ENDPOINT_EVENT_FILE.write_text(json.dumps({"time": 1, "type": "route_sync_failed",
            "message": "token=super-secret source=192.168.1.55"}) + "\n", encoding="utf-8")
        public.app.config.update(TESTING=True)
        self.client = public.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    @patch.object(public, "service_state", return_value="active")
    def test_page_is_read_only_and_redacted(self, _state):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("示例矿池".encode(), response.data)
        self.assertIn("线路配置同步失败".encode(), response.data)
        for secret in (b"wallet.private-worker", b"192.168.1.55", b"secret.pool.example", b"super-secret", b"9999"):
            self.assertNotIn(secret, response.data)
        self.assertNotIn(b"<form", response.data)
        self.assertEqual(self.client.post("/").status_code, 405)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("noindex", response.headers["X-Robots-Tag"])

    @patch.object(public, "service_state", return_value="active")
    def test_health_endpoint_contains_no_internal_details(self, _state):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.get_json()), {"ok", "updated"})


if __name__ == "__main__":
    unittest.main()
