import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class DeploymentSecurityTest(unittest.TestCase):
    def test_vps_coexistence_boundaries_are_prominent_and_preserved(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        deployment = (ROOT / "docs" / "deployment-guide.md").read_text(encoding="utf-8")
        boundaries = (ROOT / "docs" / "vps-service-boundaries.md").read_text(encoding="utf-8")
        for entry in (agents, readme, deployment):
            self.assertIn("vps-service-boundaries.md", entry)
        for rule in ("tailscale serve reset", "/etc/haproxy/haproxy.cfg", "127.0.0.1:12001:8080",
                "20000-20299", "stratum-admin", "nginx -t"):
            self.assertIn(rule, boundaries if rule == "127.0.0.1:12001:8080" else agents + boundaries)
        self.assertIn("不得", agents)
        self.assertIn("必须先读", boundaries)

    def test_cloud_signing_workflow_is_opt_in_until_secrets_are_configured(self):
        workflow = (ROOT / ".github" / "workflows" / "release-client.yml").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")
        self.assertIn("vars.ENABLE_CLOUD_SIGNING == 'true'", workflow)
        self.assertIn("WINDOWS_SIGNING_PFX_BASE64", workflow)
        self.assertIn("ENABLE_CLOUD_SIGNING", guide)
        self.assertIn("标签推送只会跳过该任务", guide)

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

    def test_route_config_is_migrated_into_existing_writable_state_directory(self):
        install = (ROOT / "monitor-panel" / "install-v3.sh").read_text(encoding="utf-8")
        upgrade = (ROOT / "monitor-panel" / "upgrade-v3-panel.sh").read_text(encoding="utf-8")
        manager = (ROOT / "monitor-panel" / "v3_manager.py").read_text(encoding="utf-8")
        canonical = "/var/lib/stratum-monitor/config/stratum-v3.json"
        for script in (install, upgrade):
            self.assertIn(canonical, script)
            self.assertIn("/var/lib/stratum-monitor/config/stratum-inspector.json", script)
            self.assertIn("/var/lib/stratum-monitor/config/stratum-audit.jsonl", script)
            self.assertIn("ln -sfn", script)
            self.assertIn("ReadWritePaths=/var/lib/stratum-monitor -/var/lib/stratum-secure-relay", script)
            self.assertNotIn("ReadWritePaths=/etc /var/lib/stratum-monitor", script)
        self.assertIn("Path(config_path).resolve()", manager)
        self.assertIn("Path(path).resolve()", manager)

    def test_admin_panel_is_unprivileged_and_root_helper_is_allowlisted(self):
        helper = (ROOT / "monitor-panel" / "privileged_helper.py").read_text(encoding="utf-8")
        self.assertIn("ALLOWED_SERVICES", helper)
        self.assertIn("SO_PEERCRED", helper)
        self.assertIn("require_root_owned_tree", helper)
        self.assertIn("build_haproxy_config()", helper)
        self.assertNotIn("shell=True", helper)
        for name in ("install-v3.sh", "upgrade-v3-panel.sh"):
            script = (ROOT / "monitor-panel" / name).read_text(encoding="utf-8")
            unit = re.search(r"cat >/etc/systemd/system/stratum-admin\.service <<'EOF'\n(.*?)\nEOF", script, re.S)
            self.assertIsNotNone(unit)
            self.assertIn("User=stratum-admin", unit.group(1))
            self.assertNotIn("User=root", unit.group(1))
            self.assertIn("Requires=stratum-admin-helper.service", unit.group(1))
            self.assertIn("V3_PRIVILEGED_HELPER_SOCKET", unit.group(1))
            self.assertIn("privileged_helper.py --serve", script)
            self.assertIn("root -g stratum-admin -m 0750 /var/lib/stratum-monitor/candidates", script)
            self.assertIn("chmod 0660 /var/lib/stratum-monitor/integrity.json", script)

    def test_dashboard_posts_update_content_without_losing_scroll_position(self):
        template = (ROOT / "monitor-panel" / "templates" / "v3_dashboard.html").read_text(encoding="utf-8")
        stylesheet = (ROOT / "monitor-panel" / "static" / "v3.css").read_text(encoding="utf-8")
        self.assertIn("data-dashboard-main", template)
        self.assertIn("new DOMParser()", template)
        self.assertIn("window.scrollTo(scrollPosition.x, scrollPosition.y)", template)
        self.assertIn("restoreOpenDetails", template)
        self.assertIn("action-toast", template)
        self.assertIn(".action-toast", stylesheet)

    def test_emergency_password_and_administrator_guide_are_self_explanatory(self):
        bootstrap = (ROOT / "monitor-panel" / "bootstrap-vps.sh").read_text(encoding="utf-8")
        admin = (ROOT / "monitor-panel" / "stratum_admin_v3.py").read_text(encoding="utf-8")
        template = (ROOT / "monitor-panel" / "templates" / "v3_dashboard.html").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "administrator-guide.md").read_text(encoding="utf-8")
        self.assertIn("只有 Tailscale 暂时不可用", bootstrap)
        self.assertIn("SSH 本地隧道", bootstrap)
        self.assertIn("Stratum V3 应急管理登录", admin)
        self.assertIn("不是 HTTPS 只读面板密码", admin)
        self.assertIn("administrator-guide.md", template)
        self.assertIn("折叠只改变网页显示", guide)
        self.assertIn("断开不足 15 分钟", guide)
        self.assertIn("超过 24 小时", guide)

    def test_public_status_installer_guides_and_verifies_safe_setup(self):
        script = (ROOT / "monitor-panel" / "install-public-status.sh").read_text(encoding="utf-8")
        self.assertIn("请输入只读面板域名", script)
        self.assertIn("停止证书到期提醒邮件", script)
        self.assertIn("getent ahostsv4", script)
        self.assertIn("api.ipify.org", script)
        self.assertIn("Cloudflare 的代理 IP", script)
        self.assertIn("确认无误后继续？", script)
        self.assertIn("--resolve", script)
        self.assertIn('certbot install --cert-name "$domain" --nginx --non-interactive', script)
        self.assertIn("listen 127.0.0.1:443 ssl", script)
        self.assertIn("WEB_IPV4", script)
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
