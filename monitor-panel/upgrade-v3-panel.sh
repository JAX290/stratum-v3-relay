#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi
cd "$(dirname "$0")"
for file in stratum_admin_v3.py stratum_public_status.py stratum_inspector.py endpoint_monitor.py security_monitor.py v3_manager.py version_info.py admin_auth.py privileged_helper.py route_switch_monitor.py vps_watchdog.py reset-panel-password.sh install-public-status.sh; do
  test -f "./$file"
done
test -f ./templates/v3_dashboard.html
test -f ./templates/public_status.html
test -f ./templates/public_login.html
test -f ./static/v3.css
test -f ./static/public.css
test -f ../version.json
secure_server="../secure-relay/server/stratum_secure_server.py"
secure_monitor="../secure-relay/server/stratum_secure_monitor.py"
test -f "$secure_server" || { echo "Missing $secure_server; update the complete Git repository first." >&2; exit 1; }
test -f "$secure_monitor" || { echo "Missing $secure_monitor; update the complete Git repository first." >&2; exit 1; }
test -f /etc/systemd/system/stratum-secure-relay.service || { echo "The encrypted relay is not installed on this VPS." >&2; exit 1; }

stamp=$(date +%Y%m%d-%H%M%S)
backup="/root/stratum-v3-panel-backup-$stamp"
install -d -m 0700 "$backup"
cp -a /opt/stratum-admin/stratum_admin_v3.py /opt/stratum-admin/stratum_inspector.py /opt/stratum-admin/endpoint_monitor.py /opt/stratum-admin/security_monitor.py /opt/stratum-admin/v3_manager.py "$backup/"
cp -aL /etc/stratum-inspector.json "$backup/stratum-inspector.json"
cp -a /etc/haproxy/haproxy.cfg "$backup/haproxy.cfg"
cp -aL /etc/stratum-v3.json "$backup/stratum-v3.json"
if [[ -f /var/log/stratum-audit.jsonl ]]; then cp -aL /var/log/stratum-audit.jsonl "$backup/stratum-audit.jsonl"; fi
if [[ -f /opt/stratum-secure-server.py ]]; then cp -a /opt/stratum-secure-server.py "$backup/"; fi
if [[ -f /opt/stratum-secure-monitor.py ]]; then cp -a /opt/stratum-secure-monitor.py "$backup/"; fi
if [[ -f /opt/stratum-admin/route_switch_monitor.py ]]; then cp -a /opt/stratum-admin/route_switch_monitor.py "$backup/"; fi
if [[ -f /opt/stratum-admin/vps_watchdog.py ]]; then cp -a /opt/stratum-admin/vps_watchdog.py "$backup/"; fi
if [[ -f /opt/stratum-admin/reset-panel-password.sh ]]; then cp -a /opt/stratum-admin/reset-panel-password.sh "$backup/"; fi
if [[ -f /opt/stratum-admin/templates/v3_dashboard.html ]]; then cp -a /opt/stratum-admin/templates/v3_dashboard.html "$backup/"; fi
if [[ -f /opt/stratum-admin/static/v3.css ]]; then cp -a /opt/stratum-admin/static/v3.css "$backup/"; fi
if [[ -f /opt/stratum-admin/stratum_public_status.py ]]; then cp -a /opt/stratum-admin/stratum_public_status.py "$backup/"; fi
if [[ -f /opt/stratum-admin/templates/public_status.html ]]; then cp -a /opt/stratum-admin/templates/public_status.html "$backup/"; fi
if [[ -f /opt/stratum-admin/templates/public_login.html ]]; then cp -a /opt/stratum-admin/templates/public_login.html "$backup/"; fi
if [[ -f /opt/stratum-admin/static/public.css ]]; then cp -a /opt/stratum-admin/static/public.css "$backup/"; fi
if [[ -f /etc/stratum-v3-peer.json ]]; then cp -a /etc/stratum-v3-peer.json "$backup/"; fi
if [[ -f /etc/systemd/system/stratum-vps-watchdog.service ]]; then cp -a /etc/systemd/system/stratum-vps-watchdog.service "$backup/"; fi
if [[ -f /etc/systemd/system/stratum-vps-watchdog.timer ]]; then cp -a /etc/systemd/system/stratum-vps-watchdog.timer "$backup/"; fi

python3 -m py_compile ./stratum_admin_v3.py ./stratum_public_status.py ./stratum_inspector.py ./endpoint_monitor.py ./security_monitor.py ./v3_manager.py ./version_info.py ./admin_auth.py ./privileged_helper.py ./route_switch_monitor.py ./vps_watchdog.py "$secure_server" "$secure_monitor"
systemctl stop stratum-security-monitor.service
systemctl stop stratum-admin.service
systemctl stop stratum-route-switch-monitor.service 2>/dev/null || true
systemctl stop stratum-vps-watchdog.timer 2>/dev/null || true
systemctl stop stratum-secure-relay.service
trap 'systemctl start stratum-secure-relay.service >/dev/null 2>&1 || true; systemctl start stratum-admin-helper.service >/dev/null 2>&1 || true; systemctl start stratum-admin.service >/dev/null 2>&1 || true; systemctl start stratum-route-switch-monitor.service >/dev/null 2>&1 || true; systemctl start stratum-security-monitor.service >/dev/null 2>&1 || true; systemctl start stratum-vps-watchdog.timer >/dev/null 2>&1 || true' EXIT
mail_name=$(hostname -f 2>/dev/null || hostname)
printf 'postfix postfix/mailname string %s\n' "$mail_name" | debconf-set-selections
printf 'postfix postfix/main_mailer_type select Internet Site\n' | debconf-set-selections
DEBIAN_FRONTEND=noninteractive apt-get install -y gunicorn postfix
postconf -e 'inet_interfaces = loopback-only'
postconf -e 'mynetworks = 127.0.0.0/8 [::1]/128'
postconf -e 'smtp_tls_security_level = may'
systemctl enable postfix
systemctl restart postfix
install -m 0755 ./stratum_admin_v3.py ./stratum_public_status.py ./stratum_inspector.py ./endpoint_monitor.py ./operations_center.py ./high_risk_wizard.py ./privileged_helper.py ./security_monitor.py ./v3_manager.py ./version_info.py ./admin_auth.py ./route_switch_monitor.py ./vps_watchdog.py ./reset-panel-password.sh ./install-public-status.sh /opt/stratum-admin/
install -o root -g root -m 0755 ./version_info.py /opt/version_info.py
install -o root -g root -m 0644 ../version.json /etc/stratum-version.json
if ! grep -q '^TAILSCALE_AUTO_LOGIN=' /etc/stratum-admin.env; then echo 'TAILSCALE_AUTO_LOGIN=1' >>/etc/stratum-admin.env; fi
install -d -m 0755 /opt/stratum-admin/templates /opt/stratum-admin/static
install -m 0644 ./templates/v3_dashboard.html /opt/stratum-admin/templates/v3_dashboard.html
install -m 0644 ./templates/public_status.html /opt/stratum-admin/templates/public_status.html
install -m 0644 ./templates/public_login.html /opt/stratum-admin/templates/public_login.html
install -m 0644 ./static/v3.css /opt/stratum-admin/static/v3.css
install -m 0644 ./static/public.css /opt/stratum-admin/static/public.css
install -o root -g root -m 0755 "$secure_server" /opt/stratum-secure-server.py
install -o root -g root -m 0755 "$secure_monitor" /opt/stratum-secure-monitor.py

# Keep /etc/stratum-v3.json as a stable compatibility path, while placing the
# writable file and its atomic-write temporary files inside the service state
# directory already granted to the sandboxed route worker.
canonical_config=/var/lib/stratum-monitor/config/stratum-v3.json
canonical_inspector=/var/lib/stratum-monitor/config/stratum-inspector.json
canonical_audit=/var/lib/stratum-monitor/config/stratum-audit.jsonl
getent group stratum-admin >/dev/null 2>&1 || groupadd --system stratum-admin
id stratum-admin >/dev/null 2>&1 || useradd --system --gid stratum-admin --home /nonexistent --shell /usr/sbin/nologin stratum-admin
chown root:stratum-admin /etc/stratum-admin.env
chmod 0640 /etc/stratum-admin.env
if [[ -f /etc/stratum-monitor.env ]]; then chown root:stratum-admin /etc/stratum-monitor.env; chmod 0640 /etc/stratum-monitor.env; fi
if [[ -f /etc/stratum-v3-peer.json ]]; then chown root:stratum-admin /etc/stratum-v3-peer.json; chmod 0640 /etc/stratum-v3-peer.json; fi
install -d -o root -g stratum-proxy -m 1770 /var/lib/stratum-monitor
install -d -o root -g stratum-proxy -m 0770 "$(dirname "$canonical_config")"
install -d -o root -g stratum-proxy -m 0770 /var/lib/stratum-monitor/history
install -d -o root -g stratum-admin -m 0750 /var/lib/stratum-monitor/candidates
install -d -o root -g stratum-proxy -m 0770 /var/lib/stratum-monitor/repair-backups
current_config=$(readlink -f /etc/stratum-v3.json)
if [[ "$current_config" != "$canonical_config" ]]; then
  install -o root -g stratum-proxy -m 0640 "$current_config" "$canonical_config"
fi
ln -sfn "$canonical_config" /etc/stratum-v3.json
current_inspector=$(readlink -f /etc/stratum-inspector.json)
if [[ "$current_inspector" != "$canonical_inspector" ]]; then
  install -o root -g stratum-proxy -m 0640 "$current_inspector" "$canonical_inspector"
fi
ln -sfn "$canonical_inspector" /etc/stratum-inspector.json
if [[ -f /var/log/stratum-audit.jsonl ]]; then
  current_audit=$(readlink -f /var/log/stratum-audit.jsonl)
  if [[ "$current_audit" != "$canonical_audit" ]]; then
    install -o root -g stratum-proxy -m 0660 "$current_audit" "$canonical_audit"
  fi
elif [[ ! -f "$canonical_audit" ]]; then
  install -o root -g stratum-proxy -m 0660 /dev/null "$canonical_audit"
fi
chown root:stratum-proxy "$canonical_audit"
chmod 0660 "$canonical_audit"
ln -sfn "$canonical_audit" /var/log/stratum-audit.jsonl

# Migrate the Internet-facing TLS process from root to its dedicated account.
id stratum-relay >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin stratum-relay
install -d -o root -g stratum-relay -m 0750 /etc/stratum-secure-relay
install -d -o stratum-relay -g stratum-relay -m 2770 /var/lib/stratum-secure-relay
chown -R stratum-relay:stratum-relay /var/lib/stratum-secure-relay
chmod 2770 /var/lib/stratum-secure-relay
python3 - <<'PY'
import json, os, shutil, tempfile
path = "/etc/stratum-secure-relay.json"
data = json.load(open(path, encoding="utf-8"))
for key, name in (("certificate", "server.crt"), ("private_key", "server.key")):
    source = os.path.abspath(data[key])
    target = os.path.join("/etc/stratum-secure-relay", name)
    if source != target:
        shutil.copyfile(source, target)
    os.chown(target, 0, __import__("grp").getgrnam("stratum-relay").gr_gid)
    os.chmod(target, 0o640)
    data[key] = target
fd, temporary = tempfile.mkstemp(prefix="stratum-secure-relay.", dir="/etc")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
os.chown(temporary, 0, __import__("grp").getgrnam("stratum-relay").gr_gid)
os.chmod(temporary, 0o640)
os.replace(temporary, path)
PY

cat >/etc/systemd/system/stratum-secure-relay.service <<'EOF'
[Unit]
Description=Stratum V3 encrypted TLS ingress
After=network-online.target stratum-inspector-v3.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/stratum-secure-server.py
Restart=always
RestartSec=3
LimitNOFILE=65536
Environment=PYTHONUNBUFFERED=1
User=stratum-relay
Group=stratum-relay
SupplementaryGroups=stratum-proxy
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true
ReadOnlyPaths=/etc/stratum-v3.json /etc/stratum-version.json /etc/stratum-secure-relay.json /etc/stratum-secure-relay
ReadWritePaths=/var/lib/stratum-secure-relay

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/stratum-route-switch-monitor.service <<'EOF'
[Unit]
Description=Stratum timed single-miner route switch monitor
After=network-online.target stratum-inspector-v3.service stratum-secure-relay.service stratum-admin-helper.service
Wants=network-online.target
Requires=stratum-admin-helper.service

[Service]
Type=simple
User=stratum-admin
Group=stratum-admin
SupplementaryGroups=stratum-proxy stratum-relay
EnvironmentFile=-/etc/stratum-v3.env
Environment=V3_CONFIG_FILE=/etc/stratum-v3.json
Environment=INSPECTOR_STATE_FILE=/var/lib/stratum-inspector/state.json
Environment=INSPECTOR_CONFIG_FILE=/etc/stratum-inspector.json
Environment=HAPROXY_V3_CONFIG=/etc/haproxy/haproxy.cfg
Environment=V3_RELOAD_SERVICES=1
Environment=V3_PRIVILEGED_HELPER_SOCKET=/run/stratum-admin-helper.sock
ExecStart=/usr/bin/python3 /opt/stratum-admin/route_switch_monitor.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/stratum-monitor -/var/lib/stratum-secure-relay -/var/log/stratum-audit.jsonl

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/stratum-admin-helper.service <<'EOF'
[Unit]
Description=Stratum administration privileged helper
After=local-fs.target
Before=stratum-admin.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/stratum-admin
Environment=V3_PRIVILEGED_HELPER_SOCKET=/run/stratum-admin-helper.sock
ExecStart=/usr/bin/python3 /opt/stratum-admin/privileged_helper.py --serve
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/stratum-admin.service <<'EOF'
[Unit]
Description=Stratum V3 administration panel
After=network-online.target stratum-admin-helper.service
Wants=network-online.target
Requires=stratum-admin-helper.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=stratum-admin
Group=stratum-admin
SupplementaryGroups=stratum-proxy stratum-relay
WorkingDirectory=/opt/stratum-admin
EnvironmentFile=/etc/stratum-admin.env
EnvironmentFile=-/etc/stratum-v3.env
Environment=V3_CONFIG_FILE=/etc/stratum-v3.json
Environment=ENDPOINT_STATE_FILE=/var/lib/stratum-monitor/endpoints.json
Environment=INSPECTOR_CONFIG_FILE=/etc/stratum-inspector.json
Environment=HAPROXY_V3_CONFIG=/etc/haproxy/haproxy.cfg
Environment=V3_RELOAD_SERVICES=1
Environment=V3_PRIVILEGED_HELPER_SOCKET=/run/stratum-admin-helper.sock
ExecStart=/usr/bin/python3 /opt/stratum-admin/stratum_admin_v3.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/stratum-monitor -/var/lib/stratum-secure-relay -/var/log/stratum-audit.jsonl

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/stratum-public-status.service <<'EOF'
[Unit]
Description=Stratum public read-only status panel
After=network-online.target stratum-inspector-v3.service stratum-secure-relay.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=stratum-proxy
Group=stratum-proxy
SupplementaryGroups=stratum-relay
WorkingDirectory=/opt/stratum-admin
Environment=V3_CONFIG_FILE=/etc/stratum-v3.json
Environment=INSPECTOR_STATE_FILE=/var/lib/stratum-inspector/state.json
Environment=ENDPOINT_EVENT_FILE=/var/lib/stratum-monitor/endpoint-events.jsonl
Environment=SECURE_RELAY_STATE_FILE=/var/lib/stratum-secure-relay/sites.json
Environment=SECURE_RELAY_MONITOR_FILE=/var/lib/stratum-secure-relay/monitor.json
Environment=SECURE_RELAY_EVENT_FILE=/var/lib/stratum-secure-relay/events.jsonl
EnvironmentFile=-/etc/stratum-public-status.env
ExecStart=/usr/bin/gunicorn --bind 127.0.0.1:8790 --workers 2 --threads 2 --timeout 30 stratum_public_status:app
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ReadOnlyPaths=/etc/stratum-v3.json /etc/stratum-version.json /var/lib/stratum-inspector /var/lib/stratum-monitor -/var/lib/stratum-secure-relay

[Install]
WantedBy=multi-user.target
EOF

install -d -m 0755 /etc/systemd/system/stratum-secure-monitor.service.d
cat >/etc/systemd/system/stratum-secure-monitor.service.d/20-state-readers.conf <<'EOF'
[Service]
Group=stratum-relay
EOF

cat >/etc/systemd/system/stratum-vps-watchdog.service <<'EOF'
[Unit]
Description=Stratum VPS local health and recovery watchdog
After=network-online.target

[Service]
Type=oneshot
User=root
Group=root
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /opt/stratum-admin/vps_watchdog.py
TimeoutStartSec=90
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/stratum-monitor
EOF

cat >/etc/systemd/system/stratum-vps-watchdog.timer <<'EOF'
[Unit]
Description=Run Stratum VPS watchdog every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
RandomizedDelaySec=10
Persistent=true

[Install]
WantedBy=timers.target
EOF

for service in stratum-secure-relay stratum-secure-monitor stratum-inspector-v3 stratum-endpoint-monitor stratum-security-monitor stratum-route-switch-monitor stratum-admin stratum-public-status; do
  install -d -m 0755 "/etc/systemd/system/${service}.service.d"
  cat >"/etc/systemd/system/${service}.service.d/90-unattended.conf" <<'EOF'
[Unit]
StartLimitIntervalSec=0

[Service]
Environment=PYTHONUNBUFFERED=1
EOF
done
cat >>/etc/systemd/system/stratum-secure-relay.service.d/90-unattended.conf <<'EOF'
LimitNOFILE=65536
EOF
cat >>/etc/systemd/system/stratum-inspector-v3.service.d/90-unattended.conf <<'EOF'
LimitNOFILE=65536
EOF
systemctl daemon-reload
systemctl enable stratum-admin-helper.service
systemctl enable stratum-admin.service
systemctl enable stratum-route-switch-monitor.service
systemctl enable stratum-public-status.service
systemctl enable stratum-vps-watchdog.timer

candidate_inspector="/root/stratum-inspector-$stamp.json"
candidate_haproxy="/root/haproxy-$stamp.cfg"
python3 /opt/stratum-admin/v3_manager.py --config /etc/stratum-v3.json --inspector "$candidate_inspector" --haproxy "$candidate_haproxy"
haproxy -c -f "$candidate_haproxy"
install -o root -g stratum-proxy -m 0640 "$candidate_inspector" "$canonical_inspector"
install -o root -g root -m 0644 "$candidate_haproxy" /etc/haproxy/haproxy.cfg

# The inspector restart is brief; miners should reconnect automatically.
systemctl restart stratum-inspector-v3.service
systemctl reload haproxy.service
systemctl restart stratum-endpoint-monitor.service
systemctl restart stratum-admin-helper.service
systemctl restart stratum-admin.service
systemctl restart stratum-public-status.service
systemctl start stratum-secure-relay.service
systemctl restart stratum-secure-monitor.service
systemctl start stratum-route-switch-monitor.service
systemctl start stratum-vps-watchdog.timer

PYTHONPATH=/opt/stratum-admin python3 -c "from security_monitor import BASELINE_FILE,DEFAULT_PATHS,atomic_write,load,snapshot; p=DEFAULT_PATHS+['/opt/stratum-secure-server.py','/opt/version_info.py','/opt/stratum-secure-monitor.py']; b=load(BASELINE_FILE,{}); b.update(snapshot(p)); atomic_write(BASELINE_FILE,b)"
chown root:stratum-proxy /var/lib/stratum-monitor/integrity.json
chmod 0660 /var/lib/stratum-monitor/integrity.json
rm -f "$candidate_inspector" "$candidate_haproxy"
systemctl start stratum-security-monitor.service
trap - EXIT
systemctl --no-pager --full status haproxy.service stratum-inspector-v3.service stratum-endpoint-monitor.service stratum-route-switch-monitor.service stratum-security-monitor.service stratum-admin-helper.service stratum-admin.service stratum-public-status.service stratum-secure-relay.service stratum-secure-monitor.service stratum-vps-watchdog.timer
echo "Panel upgrade complete. Backup: $backup"
