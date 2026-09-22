import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import privileged_helper as helper


class PrivilegedHelperTest(unittest.TestCase):
    def test_service_operation_uses_fixed_argv_allowlist(self):
        with patch.object(helper, "run_checked", return_value="ok") as run:
            result = helper.dispatch({"operation": "service", "action": "restart",
                "service": "stratum-secure-relay"})
        self.assertEqual(result["output"], "ok")
        run.assert_called_once_with(["systemctl", "restart", "stratum-secure-relay"], timeout=30)

    def test_service_operation_rejects_command_injection_and_unknown_targets(self):
        for request in (
                {"operation": "service", "action": "restart;id", "service": "haproxy"},
                {"operation": "service", "action": "restart", "service": "ssh"},
                {"operation": "unknown"}):
            with self.assertRaises(ValueError):
                helper.dispatch(request)

    def test_firewall_operation_validates_port_and_never_uses_a_shell(self):
        with patch.object(helper, "run_checked", return_value="ok") as run:
            helper.dispatch({"operation": "firewall_allow", "port": 452})
        run.assert_called_once_with(["ufw", "allow", "452/tcp"], timeout=30)
        with self.assertRaises(ValueError):
            helper.dispatch({"operation": "firewall_allow", "port": "452;id"})

    def test_upgrade_is_restricted_to_existing_candidate_directory(self):
        with self.assertRaises(ValueError):
            helper.dispatch({"operation": "upgrade", "directory": "/tmp/untrusted"})

    def test_haproxy_is_generated_from_managed_config(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder) / "candidate.cfg"
            fd = os.open(candidate, os.O_CREAT | os.O_RDWR)
            with patch.object(helper, "build_haproxy_config", return_value="defaults\n    mode tcp\n") as build, \
                    patch.object(helper.tempfile, "mkstemp", return_value=(fd, str(candidate))), \
                    patch.object(helper.os, "replace") as replace, \
                    patch.object(helper, "run_checked", return_value="ok") as run:
                helper.dispatch({"operation": "haproxy_apply", "content": "global\n  lua-load /tmp/evil"})
            build.assert_called_once_with()
            replace.assert_called_once_with(str(candidate), "/etc/haproxy/haproxy.cfg")
            self.assertEqual(run.call_args_list[0].args[0], ["haproxy", "-c", "-f", str(candidate)])

    def test_candidate_tree_rejects_group_writable_content(self):
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder) / "upgrade"
            candidate.mkdir()
            script = candidate / "upgrade.sh"
            script.write_text("#!/bin/sh\n", encoding="utf-8")
            script.chmod(0o664)
            with self.assertRaises(ValueError):
                helper.require_root_owned_tree(candidate)

    def test_monitor_toggle_writes_only_the_fixed_cron_entry(self):
        with patch.object(helper, "atomic_write") as write:
            helper.dispatch({"operation": "toggle_monitor", "enabled": True})
        path, content, mode = write.call_args.args
        self.assertEqual(path, Path("/etc/cron.d/stratum-monitor"))
        self.assertIn("root /root/stratum-monitor.sh", content)
        self.assertEqual(mode, 0o644)
        with self.assertRaises(ValueError):
            helper.dispatch({"operation": "toggle_monitor", "enabled": "yes"})

    def test_monitor_environment_rejects_unapproved_process_variables(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "monitor.env"
            managed = {**helper.MANAGED_FILES,
                "monitor_env": (target, 0o640, "text", "stratum-admin")}
            with patch.object(helper, "MANAGED_FILES", managed):
                helper.dispatch({"operation": "write_managed", "name": "monitor_env",
                    "content": "MIN_CONNECTIONS=2\nSMTP_HOST=smtp.example.com\n"})
                self.assertIn("MIN_CONNECTIONS=2", target.read_text(encoding="utf-8"))
                with self.assertRaises(ValueError):
                    helper.dispatch({"operation": "write_managed", "name": "monitor_env",
                        "content": "LD_PRELOAD=/tmp/evil.so\n"})

    def test_client_management_preserves_system_settings_and_allowed_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "relay.json"
            original = {"listen_port": 452, "certificate": "/etc/relay.pem",
                "clients": [{"id": "mine-a", "name": "A", "token": "a" * 64,
                    "enabled": True, "alert_enabled": True}]}
            target.write_text(json.dumps(original), encoding="utf-8")
            managed = {**helper.MANAGED_FILES,
                "secure_relay_config": (target, 0o640, "json", "stratum-relay")}
            updated = {**original, "clients": [dict(original["clients"][0], name="B")]}
            with patch.object(helper, "MANAGED_FILES", managed):
                helper.dispatch({"operation": "write_managed", "name": "secure_relay_config",
                    "content": json.dumps(updated)})
                changed = dict(updated, listen_port=22)
                with self.assertRaises(ValueError):
                    helper.dispatch({"operation": "write_managed", "name": "secure_relay_config",
                        "content": json.dumps(changed)})


if __name__ == "__main__":
    unittest.main()
