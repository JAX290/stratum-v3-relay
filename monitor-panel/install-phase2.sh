#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

test -f ./stratum_inspector.py
test -f ./stratum_admin_server.py
test -f ./stratum-inspector.json

if ! id stratum-proxy >/dev/null 2>&1; then
  useradd --system --home /nonexistent --shell /usr/sbin/nologin stratum-proxy
fi

install -d -m 0755 /opt/stratum-admin
install -d -o stratum-proxy -g stratum-proxy -m 0750 /var/lib/stratum-inspector
if [[ ! -f /opt/stratum-admin/stratum_admin.phase1-backup.py ]]; then
  cp -a /opt/stratum-admin/stratum_admin.py /opt/stratum-admin/stratum_admin.phase1-backup.py
fi
install -m 0755 ./stratum_inspector.py /opt/stratum-admin/stratum_inspector.py
install -m 0755 ./stratum_admin_server.py /opt/stratum-admin/stratum_admin.py
install -m 0644 ./stratum-inspector.json /etc/stratum-inspector.json

cat >/etc/stratum-inspector.env <<'EOF'
CONFIG_FILE=/etc/stratum-inspector.json
STATE_FILE=/var/lib/stratum-inspector/state.json
MAX_CONNECTIONS=500
HASHRATE_WINDOW=1800
EOF
chmod 640 /etc/stratum-inspector.env
chown root:stratum-proxy /etc/stratum-inspector.env

cat >/etc/systemd/system/stratum-inspector.service <<'EOF'
[Unit]
Description=Stratum inspection relay (phase 2 test)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=stratum-proxy
Group=stratum-proxy
EnvironmentFile=/etc/stratum-inspector.env
ExecStart=/usr/bin/python3 /opt/stratum-admin/stratum_inspector.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/stratum-inspector
LimitNOFILE=32768

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now stratum-inspector.service
systemctl restart stratum-admin.service
sleep 2
systemctl --no-pager --full status stratum-inspector.service
ss -ltnp | grep -E ':(11001|11002|11003|11101|11102|11103|11201|11202|11203|11301|11302|11303) '
echo
echo "Hash-Hut unchanged: 9999 / 10001 / 10002"
echo "LitecoinPool:       11001 / 11002 / 11003"
echo "ViaBTC:             11101 / 11102 / 11103"
echo "F2Pool:             11201 / 11202 / 11203"
echo "LongPool:           11301 / 11302 / 11303"
