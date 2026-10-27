import hashlib
import json
import unittest
import uuid
from pathlib import Path

import release_mirror as mirror

HERE = Path(__file__).resolve().parent


class FakeOpener:
    """Serves canned bodies keyed by URL and records what was requested."""

    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.requested.append(url)
        if url not in self.responses:
            raise OSError(f"unexpected url {url}")
        return _FakeResponse(self.responses[url])


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self, limit=None):
        return self._body if limit is None else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def release_json(version, *, draft=False, prerelease=False, installer=True, sums=True):
    assets = []
    if installer:
        assets.append({"name": f"MulinSenRelay-{version}.exe",
                       "browser_download_url": f"https://github.com/x/r/releases/download/t/MulinSenRelay-{version}.exe"})
    if sums:
        assets.append({"name": "SHA256SUMS.txt",
                       "browser_download_url": "https://github.com/x/r/releases/download/t/SHA256SUMS.txt"})
    return json.dumps({"tag_name": f"client-v{version}", "draft": draft,
                       "prerelease": prerelease, "assets": assets}).encode()


def sums_text(version, digest):
    return f"{digest}  MulinSenRelay-{version}.exe\n".encode()


def build_opener(version, data, *, digest=None, draft=False, prerelease=False,
                 installer=True, sums=True):
    digest = digest or hashlib.sha256(data).hexdigest()
    responses = {mirror.API_LATEST: release_json(version, draft=draft, prerelease=prerelease,
                                                installer=installer, sums=sums)}
    if installer:
        responses[f"https://github.com/x/r/releases/download/t/MulinSenRelay-{version}.exe"] = data
    if sums:
        responses["https://github.com/x/r/releases/download/t/SHA256SUMS.txt"] = sums_text(version, digest)
    return FakeOpener(responses)


class VersionCompareTest(unittest.TestCase):
    def test_compare(self):
        self.assertGreater(mirror.compare_versions("2.3.15", "2.3.14"), 0)
        self.assertEqual(mirror.compare_versions("2.3.15", "2.3.15"), 0)
        self.assertLess(mirror.compare_versions("2.3.9", "2.3.10"), 0)
        self.assertEqual(mirror.compare_versions("bad", "2.3.15"), 0, "unparsable versions never look newer")


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.dir = HERE / f".mirror-test-{uuid.uuid4().hex[:8]}"
        self.dir.mkdir()
        self.data = b"MZ" + bytes(range(256)) * 8

    def tearDown(self):
        for child in list(self.dir.iterdir()) if self.dir.exists() else []:
            try:
                child.unlink()
            except OSError:
                pass
        try:
            self.dir.rmdir()
        except OSError:
            pass

    def write_local(self, version, data=None):
        body = data if data is not None else b"OLD"
        (self.dir / f"MulinSenRelay-{version}.exe").write_bytes(body)
        (self.dir / "latest.json").write_text(json.dumps({
            "version": version, "file": f"MulinSenRelay-{version}.exe",
            "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}), encoding="utf-8")

    def test_adopts_release_when_nothing_is_local_yet(self):
        result = mirror.sync(self.dir, opener=build_opener("2.3.15", self.data), now=1000)
        self.assertEqual(result["action"], "updated")
        self.assertEqual((self.dir / "MulinSenRelay-2.3.15.exe").read_bytes(), self.data)
        manifest = json.loads((self.dir / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(manifest["source"], "github")
        self.assertEqual(manifest["synced_at"], 1000)

    def test_replaces_an_older_local_copy(self):
        self.write_local("2.3.14")
        result = mirror.sync(self.dir, opener=build_opener("2.3.15", self.data), now=2000)
        self.assertEqual(result["action"], "updated")
        self.assertEqual(sorted(p.name for p in self.dir.glob("MulinSenRelay-*.exe")),
                         ["MulinSenRelay-2.3.15.exe"], "the stale build must be removed")

    def test_never_downgrades_a_newer_local_build(self):
        self.write_local("2.3.16")
        opener = build_opener("2.3.15", self.data)
        result = mirror.sync(self.dir, opener=opener, now=3000)
        self.assertEqual(result["action"], "none")
        self.assertEqual(result["local"], "2.3.16")
        self.assertEqual(sorted(p.name for p in self.dir.glob("MulinSenRelay-*.exe")),
                         ["MulinSenRelay-2.3.16.exe"])
        self.assertEqual(opener.requested, [mirror.API_LATEST],
                         "nothing should be downloaded when no update is needed")

    def test_same_version_is_not_downloaded_again(self):
        self.write_local("2.3.15", self.data)
        opener = build_opener("2.3.15", self.data)
        result = mirror.sync(self.dir, opener=opener, now=4000)
        self.assertEqual(result["action"], "none")
        self.assertEqual(opener.requested, [mirror.API_LATEST])

    def test_draft_release_is_ignored(self):
        result = mirror.sync(self.dir, opener=build_opener("2.3.15", self.data, draft=True), now=5000)
        self.assertEqual(result["action"], "error")
        self.assertIn("草稿", result["reason"])
        self.assertFalse((self.dir / "latest.json").exists())

    def test_prerelease_is_ignored(self):
        result = mirror.sync(self.dir, opener=build_opener("2.3.15", self.data, prerelease=True), now=5000)
        self.assertEqual(result["action"], "error")

    def test_missing_checksums_refuses_to_install(self):
        result = mirror.sync(self.dir, opener=build_opener("2.3.15", self.data, sums=False), now=6000)
        self.assertEqual(result["action"], "error")
        self.assertIn("SHA256SUMS", result["reason"])
        self.assertFalse((self.dir / "MulinSenRelay-2.3.15.exe").exists())

    def test_hash_mismatch_refuses_to_install(self):
        opener = build_opener("2.3.15", self.data, digest="0" * 64)
        result = mirror.sync(self.dir, opener=opener, now=7000)
        self.assertEqual(result["action"], "error")
        self.assertIn("摘要", result["reason"])
        self.assertFalse((self.dir / "MulinSenRelay-2.3.15.exe").exists())

    def test_network_failure_is_recorded_not_raised(self):
        def broken(request, timeout=None):
            raise OSError("network down")

        result = mirror.sync(self.dir, opener=broken, now=8000)
        self.assertEqual(result["action"], "error")
        self.assertIn("network", result["reason"])
        state = json.loads((self.dir / "sync.json").read_text(encoding="utf-8"))
        self.assertEqual(state["action"], "error")

    def test_sync_state_is_written_every_time(self):
        mirror.sync(self.dir, opener=build_opener("2.3.15", self.data), now=9000)
        state = json.loads((self.dir / "sync.json").read_text(encoding="utf-8"))
        self.assertEqual(state["remote"], "2.3.15")
        self.assertEqual(state["action"], "updated")

    def test_repeat_sync_is_idempotent(self):
        opener = build_opener("2.3.15", self.data)
        mirror.sync(self.dir, opener=opener, now=1000)
        second = mirror.sync(self.dir, opener=opener, now=2000)
        self.assertEqual(second["action"], "none")


if __name__ == "__main__":
    unittest.main()
