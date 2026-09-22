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

    def test_reserved_listener_health_reports_every_missing_port(self):
        ok, message = watchdog.listener_health([452, 8789, 8790], "保留服务", {452})
        self.assertFalse(ok)
        self.assertIn("8789", message)
        self.assertIn("8790", message)

    def test_inactive_service_reports_foreign_reserved_port_owner(self):
        config = {"port_groups": [{"ports": [9999]}], "fixed_routes": [], "endpoints": []}
        with patch.object(watchdog, "read_json", side_effect=lambda path, default: config
                if path == watchdog.V3_CONFIG else default), \
                patch.object(watchdog, "listening_ports", return_value={9999}), \
                patch.object(watchdog, "service_active", return_value=False), \
                patch.object(watchdog, "port_owner_summary", return_value='9999=users:(("other",pid=42))'):
            checks = watchdog.collect_checks(now=1000)
        self.assertFalse(checks["haproxy.service"][0])
        self.assertIn("其他进程占用", checks["haproxy.service"][1])
        self.assertIn("pid=42", checks["haproxy.service"][1])


if __name__ == "__main__":
    unittest.main()
