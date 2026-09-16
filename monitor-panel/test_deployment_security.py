import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class DeploymentSecurityTest(unittest.TestCase):
    def test_one_click_deployer_updates_then_selects_safe_install_or_upgrade(self):
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("git pull --ff-only origin main", script)
        self.assertIn("git status --porcelain --untracked-files=no", script)
        self.assertIn("monitor-panel/bootstrap-vps.sh", script)
        self.assertIn("monitor-panel/upgrade-v3-panel.sh", script)
        self.assertIn("secure-relay/server/install-secure-relay.sh", script)
        self.assertIn("install-public-status.sh", script)
        self.assertIn("systemctl is-active", script)
        self.assertNotIn("git reset --hard", script)

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
        self.assertIn("Cloudflare 的代理 IP", script)
        self.assertIn("确认无误后继续？", script)
        self.assertIn("--resolve", script)
        self.assertIn('certbot install --cert-name "$domain"', script)
        self.assertIn("HTTPS 证书已经签发，但本机 443 端口", script)
        self.assertIn("PUBLIC_STATUS_PASSWORD_HASH", script)
        self.assertIn("generate_password_hash", script)
        self.assertIn("--reset-login", script)
        self.assertIn("不要对公网放行 8789 或 8790", script)

        install = (ROOT / "monitor-panel" / "install-v3.sh").read_text(encoding="utf-8")
        upgrade = (ROOT / "monitor-panel" / "upgrade-v3-panel.sh").read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=-/etc/stratum-public-status.env", install)
        self.assertIn("EnvironmentFile=-/etc/stratum-public-status.env", upgrade)
        self.assertIn("SupplementaryGroups=stratum-relay", install)
        self.assertIn("SupplementaryGroups=stratum-relay", upgrade)
        self.assertIn("stratum-secure-monitor.service.d/20-state-readers.conf", upgrade)


if __name__ == "__main__":
    unittest.main()
