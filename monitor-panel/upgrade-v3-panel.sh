#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi
cd "$(dirname "$0")"
for file in stratum_admin_v3.py stratum_public_status.py stratum_inspector.py endpoint_monitor.py security_monitor.py v3_manager.py route_switch_monitor.py vps_watchdog.py reset-panel-password.sh install-public-status.sh; do
  test -f "./$file"
done
test -f ./templates/v3_dashboard.html
test -f ./templates/public_status.html
test -f ./static/v3.css
test -f ./static/public.css
secure_server="../secure-relay/server/stratum_secure_server.py"
secure_monitor="../secure-relay/server/stratum_secure_monitor.py"
test -f "$secure_server" || { echo "Missing $secure_server; update the complete Git repository first." >&2; exit 1; }
test -f "$secure_monitor" || { echo "Missing $secure_monitor; update the complete Git repository first." >&2; exit 1; }
test -f /etc/systemd/system/stratum-secure-relay.service || { echo "The encrypted relay is not installed on this VPS." >&2; exit 1; }

stamp=$(date +%Y%m%d-%H%M%S)
backup="/root/stratum-v3-panel-backup-$stamp"
install -d -m 0700 "$backup"
cp -a /opt/stratum-admin/stratum_admin_v3.py /opt/stratum-admin/stratum_inspector.py /opt/stratum-admin/endpoint_monitor.py /opt/stratum-admin/security_monitor.py /opt/stratum-admin/v3_manager.py "$backup/"
cp -a /etc/stratum-inspector.json /etc/haproxy/haproxy.cfg "$backup/"
if [[ -f /opt/stratum-secure-server.py ]]; then cp -a /opt/stratum-secure-server.py "$backup/"; fi
if [[ -f /opt/stratum-secure-monitor.py ]]; then cp -a /opt/stratum-secure-monitor.py "$backup/"; fi
if [[ -f /opt/stratum-admin/route_switch_monitor.py ]]; then cp -a /opt/stratum-admin/route_switch_monitor.py "$backup/"; fi
if [[ -f /opt/stratum-admin/vps_watchdog.py ]]; then cp -a /opt/stratum-admin/vps_watchdog.py "$backup/"; fi
if [[ -f /opt/stratum-admin/reset-panel-password.sh ]]; then cp -a /opt/stratum-admin/reset-panel-password.sh "$backup/"; fi
if [[ -f /opt/stratum-admin/templates/v3_dashboard.html ]]; then cp -a /opt/stratum-admin/templates/v3_dashboard.html "$backup/"; fi
if [[ -f /opt/stratum-admin/static/v3.css ]]; then cp -a /opt/stratum-admin/static/v3.css "$backup/"; fi
if [[ -f /opt/stratum-admin/stratum_public_status.py ]]; then cp -a /opt/stratum-admin/stratum_public_status.py "$backup/"; fi
if [[ -f /opt/stratum-admin/templates/public_status.html ]]; then cp -a /opt/stratum-admin/templates/public_status.html "$backup/"; fi
if [[ -f /opt/stratum-admin/static/public.css ]]; then cp -a /opt/stratum-admin/static/public.css "$backup/"; fi
if [[ -f /etc/stratum-v3-peer.json ]]; then cp -a /etc/stratum-v3-peer.json "$backup/"; fi
if [[ -f /etc/systemd/system/stratum-vps-watchdog.service ]]; then cp -a /etc/systemd/system/stratum-vps-watchdog.service "$backup/"; fi
if [[ -f /etc/systemd/system/stratum-vps-watchdog.timer ]]; then cp -a /etc/systemd/system/stratum-vps-watchdog.timer "$backup/"; fi

python3 -m py_compile ./stratum_admin_v3.py ./stratum_public_status.py ./stratum_inspector.py ./endpoint_monitor.py ./security_monitor.py ./v3_manager.py ./route_switch_monitor.py ./vps_watchdog.py "$secure_server" "$secure_monitor"
systemctl stop stratum-security-monitor.service
systemctl stop stratum-route-switch-monitor.service 2>/dev/null || true
systemctl stop stratum-vps-watchdog.timer 2>/dev/null || true
systemctl stop stratum-secure-relay.service
trap 'systemctl start stratum-secure-relay.service >/dev/null 2>&1 || true; systemctl start stratum-route-switch-monitor.service >/dev/null 2>&1 || true; systemctl start stratum-security-monitor.service >/dev/null 2>&1 || true; systemctl start stratum-vps-watchdog.timer >/dev/null 2>&1 || true' EXIT
DEBIAN_FRONTEND=noninteractive apt-get install -y gunicorn
install -m 0755 ./stratum_admin_v3.py ./stratum_public_status.py ./stratum_inspector.py ./endpoint_monitor.py ./security_monitor.py ./v3_manager.py ./route_switch_monitor.py ./vps_watchdog.py ./reset-panel-password.sh ./install-public-status.sh /opt/stratum-admin/
if ! grep -q '^TAILSCALE_AUTO_LOGIN=' /etc/stratum-admin.env; then echo 'TAILSCALE_AUTO_LOGIN=1' >>/etc/stratum-admin.env; fi
install -d -m 0755 /opt/stratum-admin/templates /opt/stratum-admin/static
install -m 0644 ./templates/v3_dashboard.html /opt/stratum-admin/templates/v3_dashboard.html
install -m 0644 ./templates/public_status.html /opt/stratum-admin/templates/public_status.html
install -m 0644 ./static/v3.css /opt/stratum-admin/static/v3.css
install -m 0644 ./static/public.css /opt/stratum-admin/static/public.css
install -o root -g root -m 0755 "$secure_server" /opt/stratum-secure-server.py
install -o root -g root -m 0755 "$secure_monitor" /opt/stratum-secure-monitor.py

# Migrate the Internet-facing TLS process from root to its dedicated account.
id stratum-relay >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin stratum-relay
install -d -o root -g stratum-relay -m 0750 /etc/stratum-secure-relay
install -d -o stratum-relay -g stratum-relay -m 0750 /var/lib/stratum-secure-relay
chown -R stratum-relay:stratum-relay /var/lib/stratum-secure-relay
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
ReadOnlyPaths=/etc/stratum-v3.json /etc/stratum-secure-relay.json /etc/stratum-secure-relay
ReadWritePaths=/var/lib/stratum-secure-relay

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/stratum-route-switch-monitor.service <<'EOF'
[Unit]
Description=Stratum timed single-miner route switch monitor
After=network-online.target stratum-inspector-v3.service stratum-secure-relay.service
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
EnvironmentFile=-/etc/stratum-v3.env
Environment=V3_CONFIG_FILE=/etc/stratum-v3.json
Environment=INSPECTOR_STATE_FILE=/var/lib/stratum-inspector/state.json
Environment=INSPECTOR_CONFIG_FILE=/etc/stratum-inspector.json
Environment=HAPROXY_V3_CONFIG=/etc/haproxy/haproxy.cfg
Environment=V3_RELOAD_SERVICES=1
ExecStart=/usr/bin/python3 /opt/stratum-admin/route_switch_monitor.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/etc/stratum-v3.json /etc/stratum-inspector.json /etc/haproxy /var/lib/stratum-monitor -/var/lib/stratum-secure-relay -/var/log/stratum-audit.jsonl

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/stratum-public-status.service <<'EOF'
[Unit]
Description=Stratum public read-only status panel
After=network-online.target stratum-inspector-v3.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=stratum-proxy
Group=stratum-proxy
WorkingDirectory=/opt/stratum-admin
Environment=V3_CONFIG_FILE=/etc/stratum-v3.json
Environment=INSPECTOR_STATE_FILE=/var/lib/stratum-inspector/state.json
Environment=ENDPOINT_EVENT_FILE=/var/lib/stratum-monitor/endpoint-events.jsonl
ExecStart=/usr/bin/gunicorn --bind 127.0.0.1:8790 --workers 2 --threads 2 --timeout 30 stratum_public_status:app
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ReadOnlyPaths=/etc/stratum-v3.json /var/lib/stratum-inspector /var/lib/stratum-monitor

[Install]
WantedBy=multi-user.target
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
systemctl enable stratum-route-switch-monitor.service
systemctl enable stratum-public-status.service
systemctl enable stratum-vps-watchdog.timer

candidate_inspector="/root/stratum-inspector-$stamp.json"
candidate_haproxy="/root/haproxy-$stamp.cfg"
python3 /opt/stratum-admin/v3_manager.py --config /etc/stratum-v3.json --inspector "$candidate_inspector" --haproxy "$candidate_haproxy"
haproxy -c -f "$candidate_haproxy"
install -o root -g stratum-proxy -m 0640 "$candidate_inspector" /etc/stratum-inspector.json
install -o root -g root -m 0644 "$candidate_haproxy" /etc/haproxy/haproxy.cfg

# The inspector restart is brief; miners should reconnect automatically.
systemctl restart stratum-inspector-v3.service
systemctl reload haproxy.service
systemctl restart stratum-endpoint-monitor.service
systemctl restart stratum-admin.service
systemctl restart stratum-public-status.service
systemctl start stratum-secure-relay.service
systemctl restart stratum-secure-monitor.service
systemctl start stratum-route-switch-monitor.service
systemctl start stratum-vps-watchdog.timer

PYTHONPATH=/opt/stratum-admin python3 -c "from security_monitor import BASELINE_FILE,atomic_write,load,snapshot; p=['/opt/stratum-admin/stratum_admin_v3.py','/opt/stratum-admin/stratum_public_status.py','/opt/stratum-admin/stratum_inspector.py','/opt/stratum-admin/endpoint_monitor.py','/opt/stratum-admin/security_monitor.py','/opt/stratum-admin/route_switch_monitor.py','/opt/stratum-admin/vps_watchdog.py','/opt/stratum-admin/reset-panel-password.sh','/opt/stratum-admin/install-public-status.sh','/opt/stratum-admin/templates/v3_dashboard.html','/opt/stratum-admin/templates/public_status.html','/opt/stratum-admin/static/v3.css','/opt/stratum-admin/static/public.css','/opt/stratum-secure-server.py','/opt/stratum-secure-monitor.py','/etc/systemd/system/stratum-public-status.service','/etc/systemd/system/stratum-route-switch-monitor.service','/etc/systemd/system/stratum-vps-watchdog.service','/etc/systemd/system/stratum-vps-watchdog.timer','/etc/stratum-v3-peer.json','/etc/stratum-inspector.json','/etc/haproxy/haproxy.cfg']; b=load(BASELINE_FILE,{}); b.update(snapshot(p)); atomic_write(BASELINE_FILE,b)"
rm -f "$candidate_inspector" "$candidate_haproxy"
systemctl start stratum-security-monitor.service
trap - EXIT
systemctl --no-pager --full status haproxy.service stratum-inspector-v3.service stratum-endpoint-monitor.service stratum-route-switch-monitor.service stratum-security-monitor.service stratum-admin.service stratum-public-status.service stratum-secure-relay.service stratum-secure-monitor.service stratum-vps-watchdog.timer
echo "Panel upgrade complete. Backup: $backup"
