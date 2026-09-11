#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi
cd "$(dirname "$0")"
for file in stratum_admin_v3.py stratum_inspector.py endpoint_monitor.py security_monitor.py v3_manager.py route_switch_monitor.py; do
  test -f "./$file"
done
test -f ./templates/v3_dashboard.html
test -f ./static/v3.css
secure_server="../secure-relay/server/stratum_secure_server.py"
test -f "$secure_server" || { echo "Missing $secure_server; update the complete Git repository first." >&2; exit 1; }
test -f /etc/systemd/system/stratum-secure-relay.service || { echo "The encrypted relay is not installed on this VPS." >&2; exit 1; }

stamp=$(date +%Y%m%d-%H%M%S)
backup="/root/stratum-v3-panel-backup-$stamp"
install -d -m 0700 "$backup"
cp -a /opt/stratum-admin/stratum_admin_v3.py /opt/stratum-admin/stratum_inspector.py /opt/stratum-admin/endpoint_monitor.py /opt/stratum-admin/security_monitor.py /opt/stratum-admin/v3_manager.py "$backup/"
cp -a /etc/stratum-inspector.json /etc/haproxy/haproxy.cfg "$backup/"
if [[ -f /opt/stratum-secure-server.py ]]; then cp -a /opt/stratum-secure-server.py "$backup/"; fi
if [[ -f /opt/stratum-admin/route_switch_monitor.py ]]; then cp -a /opt/stratum-admin/route_switch_monitor.py "$backup/"; fi
if [[ -f /opt/stratum-admin/templates/v3_dashboard.html ]]; then cp -a /opt/stratum-admin/templates/v3_dashboard.html "$backup/"; fi
if [[ -f /opt/stratum-admin/static/v3.css ]]; then cp -a /opt/stratum-admin/static/v3.css "$backup/"; fi

python3 -m py_compile ./stratum_admin_v3.py ./stratum_inspector.py ./endpoint_monitor.py ./security_monitor.py ./v3_manager.py ./route_switch_monitor.py "$secure_server"
systemctl stop stratum-security-monitor.service
systemctl stop stratum-route-switch-monitor.service 2>/dev/null || true
systemctl stop stratum-secure-relay.service
trap 'systemctl start stratum-secure-relay.service >/dev/null 2>&1 || true; systemctl start stratum-route-switch-monitor.service >/dev/null 2>&1 || true; systemctl start stratum-security-monitor.service >/dev/null 2>&1 || true' EXIT
install -m 0755 ./stratum_admin_v3.py ./stratum_inspector.py ./endpoint_monitor.py ./security_monitor.py ./v3_manager.py ./route_switch_monitor.py /opt/stratum-admin/
install -d -m 0755 /opt/stratum-admin/templates /opt/stratum-admin/static
install -m 0644 ./templates/v3_dashboard.html /opt/stratum-admin/templates/v3_dashboard.html
install -m 0644 ./static/v3.css /opt/stratum-admin/static/v3.css
install -o root -g root -m 0755 "$secure_server" /opt/stratum-secure-server.py

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
systemctl daemon-reload
systemctl enable stratum-route-switch-monitor.service

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
systemctl start stratum-secure-relay.service
systemctl start stratum-route-switch-monitor.service

PYTHONPATH=/opt/stratum-admin python3 -c "from security_monitor import BASELINE_FILE,atomic_write,load,snapshot; p=['/opt/stratum-admin/stratum_admin_v3.py','/opt/stratum-admin/stratum_inspector.py','/opt/stratum-admin/endpoint_monitor.py','/opt/stratum-admin/security_monitor.py','/opt/stratum-admin/route_switch_monitor.py','/opt/stratum-admin/templates/v3_dashboard.html','/opt/stratum-admin/static/v3.css','/opt/stratum-secure-server.py','/etc/systemd/system/stratum-route-switch-monitor.service','/etc/stratum-inspector.json','/etc/haproxy/haproxy.cfg']; b=load(BASELINE_FILE,{}); b.update(snapshot(p)); atomic_write(BASELINE_FILE,b)"
rm -f "$candidate_inspector" "$candidate_haproxy"
systemctl start stratum-security-monitor.service
trap - EXIT
systemctl --no-pager --full status haproxy.service stratum-inspector-v3.service stratum-endpoint-monitor.service stratum-route-switch-monitor.service stratum-security-monitor.service stratum-admin.service stratum-secure-relay.service
echo "Panel upgrade complete. Backup: $backup"
