#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

required=(v3-config.json v3_manager.py endpoint_monitor.py security_monitor.py stratum_inspector.py stratum_admin_v3.py route_switch_monitor.py)
for file in "${required[@]}"; do
  test -f "./$file" || { echo "Missing $file" >&2; exit 1; }
done
test -f ./templates/v3_dashboard.html
test -f ./static/v3.css

stamp=$(date +%Y%m%d-%H%M%S)
backup="/root/stratum-v3-backup-$stamp"
install -d -m 0700 "$backup"
for path in /etc/haproxy/haproxy.cfg /etc/stratum-inspector.json /etc/stratum-v3.json /etc/systemd/system/stratum-inspector.service /etc/systemd/system/stratum-admin.service; do
  if [[ -e "$path" ]]; then cp -a "$path" "$backup/"; fi
done
printf '%s\n' "$backup" >/root/stratum-v3-last-backup

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y haproxy python3-flask
id stratum-proxy >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin stratum-proxy
install -d -m 0755 /opt/stratum-admin
install -d -m 0755 /opt/stratum-admin/templates /opt/stratum-admin/static
install -d -o stratum-proxy -g stratum-proxy -m 0750 /var/lib/stratum-inspector
install -d -o root -g stratum-proxy -m 0770 /var/lib/stratum-monitor
install -d -m 0750 /var/lib/stratum-monitor/history
install -m 0755 v3_manager.py endpoint_monitor.py security_monitor.py stratum_inspector.py stratum_admin_v3.py route_switch_monitor.py /opt/stratum-admin/
install -m 0644 templates/v3_dashboard.html /opt/stratum-admin/templates/v3_dashboard.html
install -m 0644 static/v3.css /opt/stratum-admin/static/v3.css
install -m 0640 v3-config.json /etc/stratum-v3.json
chown root:stratum-proxy /etc/stratum-v3.json

python3 /opt/stratum-admin/v3_manager.py --config /etc/stratum-v3.json --inspector /etc/stratum-inspector.json --haproxy /etc/haproxy/stratum-v3.cfg --resolve
chown root:stratum-proxy /etc/stratum-inspector.json
chmod 0640 /etc/stratum-inspector.json
haproxy -c -f /etc/haproxy/stratum-v3.cfg

if [[ ! -f /etc/stratum-admin.env ]]; then
  echo "Missing /etc/stratum-admin.env. Install phase 1 first to create the panel password." >&2
  exit 1
fi
if [[ -f /etc/stratum-v3.env ]]; then
  :
elif [[ -f /etc/stratum-monitor.env ]]; then
  grep -E '^(WECHAT_WEBHOOK|ALERT_INTERVAL)=' /etc/stratum-monitor.env >/etc/stratum-v3.env || true
else
  : >/etc/stratum-v3.env
fi
chmod 0640 /etc/stratum-v3.env
chown root:stratum-proxy /etc/stratum-v3.env

cat >/etc/systemd/system/stratum-inspector-v3.service <<'EOF'
[Unit]
Description=Stratum V3 transparent protocol inspector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=stratum-proxy
Group=stratum-proxy
Environment=CONFIG_FILE=/etc/stratum-inspector.json
Environment=STATE_FILE=/var/lib/stratum-inspector/state.json
ExecStart=/usr/bin/python3 /opt/stratum-admin/stratum_inspector.py
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadOnlyPaths=/etc/stratum-inspector.json
ReadWritePaths=/var/lib/stratum-inspector

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/stratum-endpoint-monitor.service <<'EOF'
[Unit]
Description=Stratum endpoint health and stability monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=stratum-proxy
Group=stratum-proxy
EnvironmentFile=-/etc/stratum-v3.env
Environment=V3_CONFIG_FILE=/etc/stratum-v3.json
Environment=ENDPOINT_STATE_FILE=/var/lib/stratum-monitor/endpoints.json
Environment=ENDPOINT_EVENT_FILE=/var/lib/stratum-monitor/endpoint-events.jsonl
ExecStart=/usr/bin/python3 /opt/stratum-admin/endpoint_monitor.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadOnlyPaths=/etc/stratum-v3.json
ReadWritePaths=/var/lib/stratum-monitor

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/stratum-security-monitor.service <<'EOF'
[Unit]
Description=Stratum independent integrity monitor
After=stratum-inspector-v3.service stratum-endpoint-monitor.service

[Service]
Type=simple
User=root
Group=root
EnvironmentFile=-/etc/stratum-v3.env
ExecStart=/usr/bin/python3 /opt/stratum-admin/security_monitor.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only

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

cat >/etc/systemd/system/stratum-admin.service <<'EOF'
[Unit]
Description=Stratum V3 administration panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/stratum-admin.env
EnvironmentFile=-/etc/stratum-v3.env
Environment=V3_CONFIG_FILE=/etc/stratum-v3.json
Environment=ENDPOINT_STATE_FILE=/var/lib/stratum-monitor/endpoints.json
Environment=INSPECTOR_CONFIG_FILE=/etc/stratum-inspector.json
Environment=HAPROXY_V3_CONFIG=/etc/haproxy/haproxy.cfg
Environment=V3_RELOAD_SERVICES=1
ExecStart=/usr/bin/python3 /opt/stratum-admin/stratum_admin_v3.py
Restart=always
RestartSec=3
User=root
Group=root
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now stratum-inspector-v3.service
systemctl enable --now stratum-endpoint-monitor.service
systemctl enable --now stratum-route-switch-monitor.service

# Existing public listeners are released only after internal V3 listeners pass checks.
systemctl disable --now stratum-inspector.service 2>/dev/null || true
install -m 0644 /etc/haproxy/stratum-v3.cfg /etc/haproxy/haproxy.cfg
haproxy -c -f /etc/haproxy/haproxy.cfg
systemctl reload haproxy
systemctl restart stratum-admin.service

python3 /opt/stratum-admin/security_monitor.py --initialize
systemctl enable --now stratum-security-monitor.service
systemctl --no-pager --full status stratum-inspector-v3.service stratum-endpoint-monitor.service stratum-route-switch-monitor.service stratum-security-monitor.service stratum-admin.service haproxy
echo "V3 installed. Backup: $backup"
