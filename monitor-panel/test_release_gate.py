import json
import unittest
import uuid
from pathlib import Path

import release_gate as gate

HERE = Path(__file__).resolve().parent


class GateStateTest(unittest.TestCase):
    def test_default_is_closed(self):
        state = gate.load_state(HERE / "does-not-exist.json")
        self.assertEqual(state["mode"], gate.MODE_CLOSED)
        self.assertFalse(gate.status(state)["open"])

    def test_open_for_minutes_opens_then_expires(self):
        state = gate.open_for_minutes(30, now=1000)
        self.assertTrue(gate.status(state, now=1000)["open"])
        self.assertEqual(gate.status(state, now=1000)["seconds_left"], 1800)
        self.assertFalse(gate.status(state, now=1000 + 1800)["open"])
        self.assertEqual(gate.status(state, now=1000 + 1800)["reason"], "expired")

    def test_closed_gate_refuses_and_stays_closed(self):
        allowed, reason, state = gate.consume(gate.close(now=1000), now=1000)
        self.assertFalse(allowed)
        self.assertEqual(reason, "closed")
        self.assertEqual(state["served_total"], 0)

    def test_duration_window_counts_each_download(self):
        state = gate.open_for_minutes(10, now=1000)
        allowed, _, state = gate.consume(state, now=1001)
        self.assertTrue(allowed)
        allowed, _, state = gate.consume(state, now=1002)
        self.assertTrue(allowed)
        self.assertEqual(state["served_total"], 2)

    def test_expired_window_refuses_and_closes_itself(self):
        state = gate.open_for_minutes(1, now=1000)
        allowed, reason, state = gate.consume(state, now=1000 + 60)
        self.assertFalse(allowed)
        self.assertEqual(reason, "expired")
        self.assertEqual(state["mode"], gate.MODE_CLOSED, "an expired window must reset to closed")

    def test_count_budget_is_exact_and_closes(self):
        state = gate.open_for_downloads(2, now=1000)
        allowed1, _, state = gate.consume(state, now=1001)
        allowed2, _, state = gate.consume(state, now=1002)
        self.assertTrue(allowed1 and allowed2)
        self.assertFalse(gate.status(state, now=1002)["open"], "a spent budget reads as closed")
        self.assertEqual(gate.status(state, now=1002)["reason"], "exhausted")
        allowed3, reason, state = gate.consume(state, now=1003)
        self.assertFalse(allowed3)
        self.assertEqual(reason, "exhausted")
        self.assertEqual(state["mode"], gate.MODE_CLOSED)
        self.assertEqual(state["served_total"], 2)

    def test_limits_are_clamped(self):
        self.assertLessEqual(gate.open_for_minutes(10 ** 6)["expires_at"] - gate.open_for_minutes(10 ** 6)["opened_at"],
                             gate.DEFAULT_MAX_MINUTES * 60)
        self.assertEqual(gate.open_for_downloads(0)["remaining"], 1, "a zero budget must not open unlimited")
        self.assertEqual(gate.open_for_downloads(10 ** 6)["remaining"], gate.DEFAULT_MAX_DOWNLOADS)

    def test_malformed_state_fails_closed(self):
        for payload in ('{not json', '[]', json.dumps({"mode": "forever"}),
                        json.dumps({"mode": "duration", "expires_at": "x"})):
            path = HERE / f".gate-test-{uuid.uuid4().hex[:8]}.json"
            path.write_text(payload, encoding="utf-8")
            try:
                state = gate.load_state(path)
                self.assertEqual(state["mode"], gate.MODE_CLOSED, payload[:30])
                self.assertFalse(gate.status(state)["open"], payload[:30])
            finally:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def test_state_round_trip_through_disk(self):
        path = HERE / f".gate-test-{uuid.uuid4().hex[:8]}.json"
        try:
            gate.save_state(path, gate.open_for_downloads(3, now=5000))
            state = gate.load_state(path)
            self.assertEqual(state["mode"], gate.MODE_COUNT)
            self.assertEqual(state["remaining"], 3)
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.root = HERE / f".gate-rel-{uuid.uuid4().hex[:8]}"
        self.root.mkdir()
        self.name = "MulinSenRelay-2.3.14.exe"
        (self.root / self.name).write_bytes(b"MZ" + b"\0" * 1024)
        self.digest = gate.sha256_of(self.root / self.name)

    def tearDown(self):
        for child in self.root.iterdir():
            try:
                child.unlink()
            except OSError:
                pass
        try:
            self.root.rmdir()
        except OSError:
            pass

    def write_manifest(self, **overrides):
        payload = {"version": "2.3.14", "file": self.name, "sha256": self.digest}
        payload.update(overrides)
        (self.root / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_manifest_is_accepted_when_complete(self):
        self.write_manifest()
        manifest, error = gate.load_manifest(self.root)
        self.assertEqual(error, "")
        self.assertEqual(manifest["version"], "2.3.14")
        self.assertEqual(manifest["size"], 1026)

    def test_missing_or_broken_manifest_is_refused(self):
        manifest, error = gate.load_manifest(self.root)
        self.assertIsNone(manifest)
        self.assertIn("manifest", error)

        self.write_manifest(sha256="short")
        self.assertIsNone(gate.load_manifest(self.root)[0])

        self.write_manifest(file="../etc/passwd")
        self.assertIsNone(gate.load_manifest(self.root)[0], "path traversal must be rejected")

        self.write_manifest(file="other.exe")
        self.assertIsNone(gate.load_manifest(self.root)[0], "a missing release file must be rejected")


class RouteTest(unittest.TestCase):
    """Exercises the public surface through Flask's test client."""

    def setUp(self):
        self.root = HERE / f".gate-app-{uuid.uuid4().hex[:8]}"
        self.releases = self.root / "releases"
        self.releases.mkdir(parents=True)
        self.state = self.root / "gate.json"
        self.name = "MulinSenRelay-2.3.14.exe"
        (self.releases / self.name).write_bytes(b"MZ" + b"\x01" * 4096)
        (self.releases / "latest.json").write_text(json.dumps({
            "version": "2.3.14", "file": self.name,
            "sha256": gate.sha256_of(self.releases / self.name)}), encoding="utf-8")
        gate.save_state(self.state, gate.close(now=1))
        self.app = gate.create_app(self.state, self.releases)

    def tearDown(self):
        for directory in (self.releases, self.root):
            for child in list(directory.iterdir()) if directory.exists() else []:
                try:
                    child.unlink()
                except OSError:
                    pass
            try:
                directory.rmdir()
            except OSError:
                pass

    def test_manifest_is_served_even_when_gate_is_closed(self):
        response = self.app.test_client().get("/update/manifest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["sha256"], gate.sha256_of(self.releases / self.name))

    def test_download_is_refused_while_closed(self):
        response = self.app.test_client().get(f"/update/{self.name}")
        self.assertEqual(response.status_code, 410)
        self.assertIn("gate", response.get_json()["error"])

    def test_download_is_served_when_open(self):
        gate.save_state(self.state, gate.open_for_downloads(1, now=1))
        client = self.app.test_client()
        response = client.get(f"/update/{self.name}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4098)
        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))

    def test_count_budget_is_enforced_across_requests(self):
        gate.save_state(self.state, gate.open_for_downloads(1, now=1))
        client = self.app.test_client()
        self.assertEqual(client.get(f"/update/{self.name}").status_code, 200)
        self.assertEqual(client.get(f"/update/{self.name}").status_code, 410,
                         "the second download must be refused once the budget is spent")

    def test_unpublished_file_is_not_reachable(self):
        gate.save_state(self.state, gate.open_for_minutes(10, now=1))
        client = self.app.test_client()
        self.assertEqual(client.get("/update/other.exe").status_code, 404)
        self.assertEqual(client.get("/update/../etc/passwd").status_code, 404)

    def test_setup_tool_is_served_while_the_gate_is_closed(self):
        """The one-click script must be reachable even when the installer itself is not."""
        (self.releases / "watch-pc-setup-2.3.16.bat").write_bytes(b"@echo off\r\nrem setup\r\n")
        gate.save_state(self.state, gate.close(now=1))
        response = self.app.test_client().get("/update/setup")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"@echo off\r\nrem setup\r\n")
        self.assertIn("watch-pc-setup", response.headers.get("Content-Disposition", ""))

    def test_setup_tool_does_not_consume_the_download_budget(self):
        (self.releases / "watch-pc-setup-2.3.16.bat").write_bytes(b"@echo off\r\n")
        gate.save_state(self.state, gate.open_for_downloads(1, now=1))
        client = self.app.test_client()
        client.get("/update/setup")
        self.assertEqual(client.get("/update/" + self.name).status_code, 200,
                         "fetching the setup script must not spend the installer budget")

    def test_setup_tool_prefers_the_manifest_hint(self):
        (self.releases / "watch-pc-setup-2.3.16.bat").write_bytes(b"old\r\n")
        (self.releases / "watch-pc-setup-2.3.17.bat").write_bytes(b"new\r\n")
        manifest = json.loads((self.releases / "latest.json").read_text(encoding="utf-8"))
        manifest["setup"] = "watch-pc-setup-2.3.16.bat"
        (self.releases / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(self.app.test_client().get("/update/setup").data, b"old\r\n")

    def test_setup_tool_is_404_when_not_published(self):
        self.assertEqual(self.app.test_client().get("/update/setup").status_code, 404)

    def test_bare_latest_serves_the_published_file(self):
        """A browser hitting the bare /exe URL must get the installer, not an index."""
        gate.save_state(self.state, gate.open_for_downloads(1, now=1))
        response = self.app.test_client().get("/update/latest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4098)
        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))

    def test_bare_latest_is_also_metered(self):
        gate.save_state(self.state, gate.open_for_downloads(1, now=1))
        client = self.app.test_client()
        self.assertEqual(client.get("/update/latest").status_code, 200)
        self.assertEqual(client.get("/update/latest").status_code, 410,
                         "the bare URL must consume the same budget as the named file")

    def test_bare_latest_is_refused_while_closed(self):
        gate.save_state(self.state, gate.close(now=1))
        self.assertEqual(self.app.test_client().get("/update/latest").status_code, 410)

    def test_status_reports_mirror_state(self):
        (self.releases / "sync.json").write_text(json.dumps({
            "action": "none", "local": "2.3.15", "remote": "2.3.13"}), encoding="utf-8")
        payload = self.app.test_client().get("/update/status").get_json()
        self.assertEqual(payload["mirror"]["local"], "2.3.15")

    def test_status_endpoint_reports_the_window(self):
        # The route reads the wall clock, so the window has to be opened relative to now.
        gate.save_state(self.state, gate.open_for_minutes(10))
        payload = self.app.test_client().get("/update/status").get_json()
        self.assertTrue(payload["open"])
        self.assertEqual(payload["mode"], gate.MODE_DURATION)


if __name__ == "__main__":
    unittest.main()
