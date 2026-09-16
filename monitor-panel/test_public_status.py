import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

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
        public.SECURE_RELAY_STATE_FILE = root / "sites.json"
        public.SECURE_RELAY_MONITOR_FILE = root / "site-monitor.json"
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
        public.SECURE_RELAY_STATE_FILE.write_text(json.dumps({"sites": {"mine-a": {
            "name": "一号矿场", "last_seen": 1, "last_ip": "203.0.113.20",
            "reported_miner_count": 18, "reported_connections": 36, "client_version": "2.2.1"
        }}}), encoding="utf-8")
        public.SECURE_RELAY_MONITOR_FILE.write_text(json.dumps({"clients": {"mine-a": "offline"}}), encoding="utf-8")
        public.app.config.update(TESTING=True)
        self.environment = patch.dict(os.environ, {
            "PUBLIC_STATUS_USERNAME": "operator",
            "PUBLIC_STATUS_PASSWORD_HASH": generate_password_hash("correct horse battery"),
            "PUBLIC_STATUS_SECRET_KEY": "a" * 64,
        })
        self.environment.start()
        public.app.secret_key = os.environ["PUBLIC_STATUS_SECRET_KEY"]
        with public.LOGIN_FAILURES_LOCK:
            public.LOGIN_FAILURES.clear()
        self.client = public.app.test_client()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    @patch.object(public, "service_state", return_value="active")
    def test_page_requires_login_and_shows_operator_details(self, _state):
        self.assertEqual(self.client.get("/").status_code, 302)
        response = self.client.post("/login", data={"username": "operator", "password": "correct horse battery"},
                                    follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("示例矿池".encode(), response.data)
        self.assertIn("线路配置同步失败".encode(), response.data)
        for visible in (b"wallet.private-worker", b"192.168.1.55", b"secret.pool.example", b"9999"):
            self.assertIn(visible, response.data)
        self.assertIn("一号矿场".encode(), response.data)
        self.assertIn(b"203.0.113.20", response.data)
        self.assertNotIn(b"super-secret", response.data)
        self.assertIn("[已隐藏]".encode(), response.data)
        self.assertEqual(self.client.post("/").status_code, 405)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("noindex", response.headers["X-Robots-Tag"])
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")

    def test_login_throttles_repeated_failures(self):
        for _ in range(5):
            response = self.client.post("/login", data={"username": "operator", "password": "wrong"})
            self.assertEqual(response.status_code, 200)
        response = self.client.post("/login", data={"username": "operator", "password": "wrong"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)

    def test_missing_credentials_refuses_login(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.get("/login")
        self.assertEqual(response.status_code, 503)
        self.assertIn("尚未设置值守账号".encode(), response.data)

    @patch.object(public, "service_state", return_value="active")
    def test_health_endpoint_contains_no_internal_details(self, _state):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.get_json()), {"ok", "updated"})


if __name__ == "__main__":
    unittest.main()
