#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

env_file=/etc/stratum-admin.env
touch "$env_file"
chmod 0600 "$env_file"

if [[ ${1:-} == "--disable-password" ]]; then
  password_hash=""
else
  password_hash=$(python3 - <<'PY'
from getpass import getpass
from werkzeug.security import generate_password_hash

first = getpass("请输入新的应急管理密码（至少12个字符）：")
second = getpass("请再次输入：")
if len(first) < 12:
    raise SystemExit("密码少于12个字符，未修改。")
if first != second:
    raise SystemExit("两次密码不一致，未修改。")
print(generate_password_hash(first))
PY
)
fi

temp_file=$(mktemp)
grep -v '^PANEL_PASSWORD_HASH=' "$env_file" >"$temp_file" || true
printf 'PANEL_PASSWORD_HASH=%s\n' "$password_hash" >>"$temp_file"
install -o root -g root -m 0600 "$temp_file" "$env_file"
rm -f "$temp_file"
systemctl restart stratum-admin.service

if [[ -n "$password_hash" ]]; then
  echo "管理面板应急密码已更新。Tailscale Serve 访问仍会自动登录。"
else
  echo "密码登录已关闭。请确认 Tailscale Serve 可以访问面板。"
fi
