import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import vps_watchdog as watchdog


class WatchdogTests(unittest.TestCase):
    def test_empty_farm_configuration_checks_tls_without_restart_failure(self):
        with patch.object(watchdog, "read_json", return_value={"clients": [], "listen_port": 452}), \
                patch.object(watchdog.socket, "create_connection"), \
                patch.object(watchdog.ssl, "SSLContext") as context:
            ok, message = watchdog.relay_health()
            self.assertTrue(ok)
            self.assertIn("跳过密钥认证检查", message)
            context.return_value.wrap_socket.assert_called_once()
            context.return_value.wrap_socket.return_value.__enter__.return_value.sendall.assert_not_called()

    def test_restarts_only_after_three_consecutive_failures(self):
        state = {"services": {}, "events": []}
        restarted = []
        callback = lambda service: restarted.append(service) or True
        for now in (1000, 1060):
            watchdog.evaluate({"demo.service": (False, "stalled")}, state, now, callback)
        self.assertEqual(restarted, [])
        watchdog.evaluate({"demo.service": (False, "stalled")}, state, 1120, callback)
        self.assertEqual(restarted, ["demo.service"])

    def test_restart_cooldown_prevents_a_restart_loop(self):
        state = {"services": {"demo.service": {"failures": 3, "last_restart": 1000}}, "events": []}
        restarted = []
        watchdog.evaluate({"demo.service": (False, "stalled")}, state, 1100,
            lambda service: restarted.append(service) or True)
        self.assertEqual(restarted, [])

    def test_success_resets_failure_counter(self):
        state = {"services": {"demo.service": {"failures": 2, "last_restart": 0}}, "events": []}
        watchdog.evaluate({"demo.service": (True, "")}, state, 1000, lambda service: True)
        self.assertEqual(state["services"]["demo.service"]["failures"], 0)
        self.assertEqual(state["events"][-1]["type"], "recovered")

    def test_stale_inspector_state_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text("{}", encoding="utf-8")
            original = watchdog.INSPECTOR_STATE
            watchdog.INSPECTOR_STATE = path
            try:
                modified = path.stat().st_mtime
                self.assertFalse(watchdog.inspector_health(now=modified + 500, maximum_age=120)[0])
            finally:
                watchdog.INSPECTOR_STATE = original


if __name__ == "__main__":
    unittest.main()
