#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-flask

install -d -m 0755 /opt/stratum-admin
install -m 0755 ./stratum_admin.py /opt/stratum-admin/stratum_admin.py

read -r -s -p "Set panel password (at least 12 characters): " PANEL_PASSWORD
echo
if [[ ${#PANEL_PASSWORD} -lt 12 ]]; then
  echo "Password is too short." >&2
  exit 1
fi

PASSWORD_HASH=$(PANEL_PASSWORD="$PANEL_PASSWORD" python3 - <<'PY'
import os
from werkzeug.security import generate_password_hash
print(generate_password_hash(os.environ["PANEL_PASSWORD"]))
PY
)
SECRET_KEY=$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)
unset PANEL_PASSWORD

cat >/etc/stratum-admin.env <<EOF
PANEL_SECRET_KEY=$SECRET_KEY
PANEL_PASSWORD_HASH=$PASSWORD_HASH
EOF
chmod 600 /etc/stratum-admin.env

cat >/etc/systemd/system/stratum-admin.service <<'EOF'
[Unit]
Description=Stratum monitoring administration panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/stratum-admin.env
ExecStart=/usr/bin/python3 /opt/stratum-admin/stratum_admin.py
Restart=on-failure
RestartSec=3
User=root
Group=root
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
EOF

if [[ ! -f /etc/cron.d/stratum-monitor ]]; then
  cat >/etc/cron.d/stratum-monitor <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
* * * * * root /root/stratum-monitor.sh
EOF
  chmod 644 /etc/cron.d/stratum-monitor
  crontab -l 2>/dev/null | grep -v 'stratum-monitor.sh' | crontab - || true
fi

systemctl daemon-reload
systemctl enable --now stratum-admin.service
systemctl --no-pager --full status stratum-admin.service
echo
echo "Panel is listening only on 127.0.0.1:8789"
echo "From Windows PowerShell run:"
echo "ssh -N -L 8789:127.0.0.1:8789 -p <SSH_PORT> root@<VPS_PUBLIC_IP>"
echo "Then open: http://127.0.0.1:8789"
