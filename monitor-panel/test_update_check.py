import json
import unittest
import uuid
from pathlib import Path

import update_check

HERE = Path(__file__).resolve().parent


class VersionParsingTest(unittest.TestCase):
    def test_valid_and_invalid_versions(self):
        self.assertEqual(update_check.version_tuple("3.2.23"), (3, 2, 23))
        for bad in ("3.2", "3.2.23.1", "a.b.c", "", None, "3.2.-1"):
            self.assertIsNone(update_check.version_tuple(bad), bad)

    def test_manifest_accepts_plain_and_api_envelope(self):
        plain = json.dumps({"panel": "3.2.24", "secure_relay": "2.2.8", "windows_client": "2.3.15"})
        self.assertEqual(update_check.parse_manifest(plain)["panel"], "3.2.24")

        import base64
        envelope = json.dumps({"encoding": "base64", "content": base64.b64encode(plain.encode()).decode()})
        self.assertEqual(update_check.parse_manifest(envelope)["secure_relay"], "2.2.8")

    def test_manifest_fails_closed(self):
        """A truncated body or captive portal must never look like 'up to date'."""
        for bad in ("", "not json", "{}", json.dumps({"panel": "3.2.23"}),
                    json.dumps({"panel": "x", "secure_relay": "2.2.7", "windows_client": "2.3.14"}),
                    json.dumps([]), json.dumps({"encoding": "base64", "content": "!!!"})):
            self.assertIsNone(update_check.parse_manifest(bad), bad[:40])


class CompareTest(unittest.TestCase):
    def test_compare_marks_only_newer_components(self):
        local = {"panel": "3.2.22", "secure_relay": "2.2.7", "windows_client": "2.3.14"}
        remote = {"panel": "3.2.23", "secure_relay": "2.2.7", "windows_client": "2.3.13"}
        rows = update_check.compare(local, remote)
        flags = {row["key"]: row["newer"] for row in rows}
        self.assertTrue(flags["panel"])
        self.assertFalse(flags["secure_relay"])
        self.assertFalse(flags["windows_client"], "a remote downgrade must not count as an update")
        self.assertTrue(update_check.any_newer(rows))

    def test_compare_without_remote_is_all_clean(self):
        rows = update_check.compare({"panel": "3.2.22"}, None)
        self.assertFalse(update_check.any_newer(rows))
        self.assertEqual([row["latest"] for row in rows], ["", "", ""])

    def test_labels_are_user_facing(self):
        rows = update_check.compare({}, {})
        self.assertEqual([row["label"] for row in rows], ["管理面板", "VPS 加密入口", "Windows 客户端"])


class StateContextTest(unittest.TestCase):
    def setUp(self):
        # Flat files beside the tests: creating a subdirectory here is unreliable on some
        # Windows sandboxes, and a directory left behind by a failed cleanup is worse than
        # a file that can simply be unlinked.
        token = uuid.uuid4().hex[:8]
        self.state = HERE / f".update-state-{token}.json"
        self.local = HERE / f".update-version-{token}.json"
        self.local.write_text(json.dumps({"panel": "3.2.22", "secure_relay": "2.2.7",
                                          "windows_client": "2.3.14"}), encoding="utf-8")

    def tearDown(self):
        for path in (self.state, self.local):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def test_missing_state_reports_not_checked(self):
        context = update_check.build_update_context(self.state, self.local)
        self.assertFalse(context["checked_at"])
        self.assertFalse(context["has_newer"])
        self.assertEqual(context["checked_text"], "")
        self.assertEqual(len(context["rows"]), 3)

    def test_cached_state_is_used_without_network(self):
        self.state.write_text(json.dumps({
            "checked_at": 1000, "source": "raw",
            "latest": {"panel": "3.2.23", "secure_relay": "2.2.7", "windows_client": "2.3.14"}}),
            encoding="utf-8")
        context = update_check.build_update_context(self.state, self.local, now=1060)
        self.assertTrue(context["has_newer"])
        self.assertEqual(context["age_seconds"], 60)
        self.assertEqual(context["source"], "raw")

    def test_record_check_caches_success(self):
        class FakeResponse:
            status = 200
            def __init__(self, body):
                self._body = body
            def read(self, _limit):
                return self._body
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        body = json.dumps({"panel": "3.2.23", "secure_relay": "2.2.7", "windows_client": "2.3.14"}).encode()
        context = update_check.record_check(self.state, self.local,
                                            opener=lambda request, timeout: FakeResponse(body))
        self.assertTrue(context["has_newer"])
        self.assertEqual(context["error"], "")
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["latest"]["panel"], "3.2.23")

    def test_record_check_records_failure_without_raising(self):
        def failing(request, timeout):
            raise OSError("network down")

        context = update_check.record_check(self.state, self.local, opener=failing)
        self.assertFalse(context["has_newer"])
        self.assertIn("network", context["error"])
        self.assertTrue(context["checked_at"], "a failed check still records when it ran")
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["latest"], {}, "a failed check must not cache stale versions")

    def test_write_state_swallows_an_unwritable_path(self):
        """A read-only or full state directory must not break the settings page."""
        blocked = self.local / "child.json"   # parent is a file, so mkdir cannot succeed
        update_check.write_state(blocked, {"checked_at": 1})
        self.assertFalse(blocked.exists())

    def test_corrupt_state_file_does_not_break_the_page(self):
        self.state.write_text("{not json", encoding="utf-8")
        context = update_check.build_update_context(self.state, self.local)
        self.assertFalse(context["has_newer"])
        self.assertEqual(len(context["rows"]), 3)


if __name__ == "__main__":
    unittest.main()
