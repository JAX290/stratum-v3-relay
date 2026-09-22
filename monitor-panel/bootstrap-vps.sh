#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "请使用 root 用户运行此脚本。" >&2
  exit 1
fi

cd "$(dirname "$0")"

for file in install-v3.sh install-public-status.sh v3-config.json v3_manager.py version_info.py admin_auth.py endpoint_monitor.py operations_center.py security_monitor.py stratum_inspector.py stratum_admin_v3.py stratum_public_status.py route_switch_monitor.py vps_watchdog.py reset-panel-password.sh templates/v3_dashboard.html templates/public_status.html templates/public_login.html static/v3.css static/public.css ../version.json; do
  test -f "./$file" || { echo "Missing $file" >&2; exit 1; }
done

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y haproxy python3-flask python3-werkzeug gunicorn

if [[ ! -f /etc/stratum-admin.env ]]; then
  cat <<'EOF'

应急管理密码说明：
  - 日常通过 Tailscale 管理时不会使用这个密码，可以直接回车关闭备用密码登录。
  - 只有 Tailscale 暂时不可用、但 SSH 仍可连接 VPS 时，才通过 SSH 本地隧道使用它。
  - 它不是 VPS 密码、HTTPS 只读面板密码、矿池密码或 Windows 客户端共享密钥。
  - 不要为了使用应急入口而把管理端口 8789 开放到公网。
EOF
  while true; do
    read -r -s -p "应急管理密码（至少 12 个字符；直接回车表示关闭此备用入口）：" PANEL_PASSWORD
    echo
    if [[ -z "$PANEL_PASSWORD" ]]; then
      break
    fi
    if [[ ${#PANEL_PASSWORD} -ge 12 ]]; then
      break
    fi
    echo "密码少于 12 个字符，请重新输入。" >&2
  done
  if [[ -n "$PANEL_PASSWORD" ]]; then
    PASSWORD_HASH=$(PANEL_PASSWORD="$PANEL_PASSWORD" python3 - <<'PY'
import os
from werkzeug.security import generate_password_hash
print(generate_password_hash(os.environ["PANEL_PASSWORD"]))
PY
)
  else
    PASSWORD_HASH=""
  fi
  SECRET_KEY=$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)
  unset PANEL_PASSWORD
  cat >/etc/stratum-admin.env <<EOF
PANEL_SECRET_KEY=$SECRET_KEY
PANEL_PASSWORD_HASH=$PASSWORD_HASH
TAILSCALE_AUTO_LOGIN=1
EOF
  chmod 600 /etc/stratum-admin.env
fi

if ! grep -q '^TAILSCALE_AUTO_LOGIN=' /etc/stratum-admin.env; then
  echo 'TAILSCALE_AUTO_LOGIN=1' >>/etc/stratum-admin.env
fi

if [[ ! -f /etc/stratum-v3.env ]]; then
  : >/etc/stratum-v3.env
  chmod 0640 /etc/stratum-v3.env
fi

if ! grep -q '^WECHAT_WEBHOOK=' /etc/stratum-v3.env; then
  read -r -p "企业微信机器人 Webhook（不需要时直接回车）：" WECHAT_WEBHOOK
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

V3 面板和监控组件安装完成。

检查命令：
  systemctl is-active haproxy stratum-inspector-v3 stratum-endpoint-monitor stratum-route-switch-monitor stratum-security-monitor stratum-admin stratum-public-status
  systemctl is-active stratum-vps-watchdog.timer
  ss -lnt | grep -E ':(9999|10001|10002|10010|10011|10012|11001|11101|11201|11301)\b'

管理面板：
  - SSH 本地隧道：ssh -N -L 8789:127.0.0.1:8789 root@<VPS公网IP>
  - 或登录 Tailscale 后发布：
      tailscale serve --bg http://127.0.0.1:8789

EOF
