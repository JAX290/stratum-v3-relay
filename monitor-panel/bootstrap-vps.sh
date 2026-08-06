#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

cd "$(dirname "$0")"

for file in install-v3.sh v3-config.json v3_manager.py endpoint_monitor.py security_monitor.py stratum_inspector.py stratum_admin_v3.py templates/v3_dashboard.html static/v3.css; do
  test -f "./$file" || { echo "Missing $file" >&2; exit 1; }
done

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y haproxy python3-flask python3-werkzeug

if [[ ! -f /etc/stratum-admin.env ]]; then
  while true; do
    read -r -s -p "Set admin panel password, at least 12 characters: " PANEL_PASSWORD
    echo
    if [[ ${#PANEL_PASSWORD} -ge 12 ]]; then
      break
    fi
    echo "Password is too short." >&2
  done
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
fi

if [[ ! -f /etc/stratum-v3.env ]]; then
  : >/etc/stratum-v3.env
  chmod 0640 /etc/stratum-v3.env
fi

if ! grep -q '^WECHAT_WEBHOOK=' /etc/stratum-v3.env; then
  read -r -p "Enterprise WeChat webhook URL, leave empty to skip: " WECHAT_WEBHOOK
  if [[ -n "$WECHAT_WEBHOOK" ]]; then
    printf 'WECHAT_WEBHOOK=%s\n' "$WECHAT_WEBHOOK" >>/etc/stratum-v3.env
  fi
fi

if ! grep -q '^ALERT_INTERVAL=' /etc/stratum-v3.env; then
  echo "ALERT_INTERVAL=900" >>/etc/stratum-v3.env
fi
if ! grep -q '^MIN_CONNECTIONS=' /etc/stratum-v3.env; then
  echo "MIN_CONNECTIONS=1" >>/etc/stratum-v3.env
fi
if ! grep -q '^MEM_THRESHOLD=' /etc/stratum-v3.env; then
  echo "MEM_THRESHOLD=85" >>/etc/stratum-v3.env
fi
if ! grep -q '^DISK_THRESHOLD=' /etc/stratum-v3.env; then
  echo "DISK_THRESHOLD=85" >>/etc/stratum-v3.env
fi
chmod 0640 /etc/stratum-v3.env

chmod +x ./install-v3.sh ./rollback-v3.sh
./install-v3.sh

cat <<'EOF'

Bootstrap complete.

Next checks:
  systemctl is-active haproxy stratum-inspector-v3 stratum-endpoint-monitor stratum-security-monitor stratum-admin
  ss -lnt | grep -E ':(9999|10001|10002|10010|10011|10012|11001|11101|11201|11301)\b'

Panel:
  - Direct local tunnel: ssh -N -L 8789:127.0.0.1:8789 root@<VPS_PUBLIC_IP>
  - Or expose through Tailscale Serve after Tailscale login:
      tailscale serve --bg http://127.0.0.1:8789

EOF
