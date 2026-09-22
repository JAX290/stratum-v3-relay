import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import recovery_guard as guard


class RecoveryGuardTests(unittest.TestCase):
    def test_systemd_drop_in_maps_to_owning_service(self):
        path = Path("/etc/systemd/system/stratum-admin.service.d/90-unattended.conf")
        self.assertEqual(guard.service_for_path(path), "stratum-admin.service")
        self.assertIsNone(guard.service_for_path(Path("/etc/systemd/system/stratum-vps-watchdog.service")))

    def test_changed_static_file_is_restored_from_root_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            protected = root / "protected.py"
            protected.write_text("trusted\n", encoding="utf-8")
            recovery = root / "recovery"
            with patch.object(guard, "RECOVERY_ROOT", recovery), \
                    patch.object(guard, "MANIFEST_FILE", recovery / "manifest.json"):
                self.assertEqual(guard.initialize([protected]), 1)
                protected.write_text("changed\n", encoding="utf-8")
                events, services, reload_systemd = guard.restore_static(now=1000)
            self.assertEqual(protected.read_text(encoding="utf-8"), "trusted\n")
            self.assertEqual(events[0]["type"], "file_restored")
            self.assertEqual(services, set())
            self.assertFalse(reload_systemd)

    def test_generated_files_are_rebuilt_from_valid_business_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "stratum-v3.json"
            inspector = root / "inspector.json"
            haproxy = root / "haproxy.cfg"
            source = Path(__file__).with_name("v3-config.json")
            config.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            inspector.write_text("{}\n", encoding="utf-8")
            haproxy.write_text("modified\n", encoding="utf-8")
            runner = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr="")
            with patch.object(guard, "CONFIG_FILE", config), patch.object(guard, "HISTORY_DIR", root / "history"), \
                    patch.object(guard, "INSPECTOR_CONFIG", inspector), patch.object(guard, "HAPROXY_CONFIG", haproxy):
                events, services = guard.repair_generated(now=1000, command_runner=runner)
            self.assertEqual({item["type"] for item in events}, {"generated_config_restored"})
            self.assertEqual(services, {"stratum-inspector-v3.service", "haproxy.service"})
            self.assertIn("frontend stratum_v3_9999", haproxy.read_text(encoding="utf-8"))
            self.assertIn('"relays"', inspector.read_text(encoding="utf-8"))

    def test_invalid_business_config_uses_newest_valid_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "stratum-v3.json"
            history = root / "history"
            history.mkdir()
            config.write_text("{broken", encoding="utf-8")
            valid = Path(__file__).with_name("v3-config.json").read_text(encoding="utf-8")
            (history / "latest.json").write_text(valid, encoding="utf-8")
            events = []
            with patch.object(guard, "CONFIG_FILE", config), patch.object(guard, "HISTORY_DIR", history), \
                    patch.object(guard, "RECOVERY_ROOT", root / "recovery"):
                restored = guard.load_or_recover_config(1000, events)
            self.assertEqual(restored["version"], 3)
            self.assertEqual(events[0]["type"], "config_restored")
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["version"], 3)

    def test_missing_baseline_is_recorded_without_skipping_generated_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(guard, "MANIFEST_FILE", root / "missing.json"), \
                    patch.object(guard, "STATE_FILE", root / "state.json"), \
                    patch.object(guard, "repair_generated", return_value=([], set())):
                events = guard.run_once(now=1000)
            self.assertEqual(events[0]["type"], "restore_failed")
            self.assertIn("恢复基线不存在", events[0]["message"])


if __name__ == "__main__":
    unittest.main()
