import tempfile
import unittest
from pathlib import Path

from security_monitor import SecurityMonitor, atomic_write, snapshot


class SecurityMonitorTest(unittest.TestCase):
    def test_change_alert_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "protected.txt"
            path.write_text("approved", encoding="utf-8")
            paths = [str(path)]
            events = []
            monitor = SecurityMonitor(paths, snapshot(paths), notify=events.append)
            monitor.check(now=100)
            self.assertEqual(events, [])
            path.write_text("changed", encoding="utf-8")
            monitor.check(now=101)
            monitor.check(now=102)
            self.assertEqual([item["type"] for item in events], ["integrity_changed"])
            path.write_text("approved", encoding="utf-8")
            monitor.check(now=103)
            self.assertEqual([item["type"] for item in events], ["integrity_changed", "integrity_recovery"])

    def test_approved_baseline_is_reloaded_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "protected.txt"
            baseline_path = root / "integrity.json"
            path.write_text("approved", encoding="utf-8")
            paths = [str(path)]
            atomic_write(baseline_path, snapshot(paths))
            events = []
            monitor = SecurityMonitor(paths, snapshot(paths), notify=events.append,
                baseline_path=baseline_path)

            path.write_text("authorized change", encoding="utf-8")
            monitor.check(now=100)
            self.assertEqual([item["type"] for item in events], ["integrity_changed"])

            atomic_write(baseline_path, snapshot(paths))
            monitor.check(now=101)
            monitor.check(now=102)
            self.assertEqual([item["type"] for item in events],
                ["integrity_changed", "integrity_recovery"])
            self.assertEqual(monitor.state["active"], {})

    def test_new_protocol_anomaly_is_forwarded_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protected = root / "protected.txt"
            protected.write_text("approved", encoding="utf-8")
            inspector = root / "inspector.json"
            inspector.write_text('{"pools":[{"name":"Pool","anomalies":[{"type":"worker_mismatch","detail":"changed","route":"route-1"}]}]}', encoding="utf-8")
            events = []
            monitor = SecurityMonitor([str(protected)], snapshot([str(protected)]), notify=events.append, inspector_state_path=inspector)
            monitor.check(now=200)
            monitor.check(now=201)
            self.assertEqual([item["type"] for item in events], ["worker_mismatch"])

    def test_unknown_job_alarm_is_suppressed_entirely(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protected = root / "protected.txt"
            protected.write_text("approved", encoding="utf-8")
            inspector = root / "inspector.json"
            anomalies = [{"time": f"t{number}", "type": "unknown_job", "detail": f"job-{number}",
                "route": "route-1", "worker": "wallet.worker"} for number in range(20)]
            inspector.write_text(__import__("json").dumps({"pools": [{"name": "Pool", "anomalies": anomalies}]}), encoding="utf-8")
            events = []
            monitor = SecurityMonitor([str(protected)], snapshot([str(protected)]), notify=events.append,
                inspector_state_path=inspector, repeat_seconds=900)
            monitor.check(now=300)
            self.assertEqual(events, [])
            self.assertEqual(monitor.state["unknown_job_suppressed"], 20)


if __name__ == "__main__":
    unittest.main()
