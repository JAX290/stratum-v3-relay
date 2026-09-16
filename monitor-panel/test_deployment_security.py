import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class DeploymentSecurityTest(unittest.TestCase):
    def test_secure_relay_installer_uses_dedicated_account_and_minimal_capability(self):
        script = (ROOT / "secure-relay" / "server" / "install-secure-relay.sh").read_text(encoding="utf-8")
        service = re.search(r"cat >/etc/systemd/system/stratum-secure-relay\.service <<'EOF'\n(.*?)\nEOF", script, re.S)
        self.assertIsNotNone(service)
        unit = service.group(1)
        self.assertIn("User=stratum-relay", unit)
        self.assertIn("Group=stratum-relay", unit)
        self.assertIn("SupplementaryGroups=stratum-proxy", unit)
        self.assertIn("CapabilityBoundingSet=CAP_NET_BIND_SERVICE", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertNotIn("User=root", unit)

    def test_upgrade_migrates_existing_secure_relay(self):
        script = (ROOT / "monitor-panel" / "upgrade-v3-panel.sh").read_text(encoding="utf-8")
        self.assertIn("id stratum-relay", script)
        self.assertIn("User=stratum-relay", script)
        self.assertIn("os.chown(temporary", script)
        self.assertIn("ReadWritePaths=/var/lib/stratum-secure-relay", script)

    def test_public_status_installer_guides_and_verifies_safe_setup(self):
        script = (ROOT / "monitor-panel" / "install-public-status.sh").read_text(encoding="utf-8")
        self.assertIn("请输入只读面板域名", script)
        self.assertIn("停止证书到期提醒邮件", script)
        self.assertIn("getent ahostsv4", script)
        self.assertIn("api.ipify.org", script)
        self.assertIn("--resolve", script)
        self.assertIn("不要对公网放行 8789 或 8790", script)


if __name__ == "__main__":
    unittest.main()
