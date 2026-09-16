#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<EOF
用法：
  $0
  $0 --domain status.example.com

可选参数：
  --email EMAIL       兼容旧版 Certbot 的注册邮箱参数；不是面板账号
  --reset-login       重新设置只读面板的值守账号和密码
  --skip-dns-check    跳过“域名必须指向本机公网 IP”的检查
  -h, --help          显示本说明

新手建议直接运行 $0，按屏幕提示填写。
EOF
}

domain=""
email=""
skip_dns_check=0
reset_login=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      [[ $# -ge 2 ]] || { echo "--domain 后面缺少域名。" >&2; usage; exit 2; }
      domain="$2"; shift 2 ;;
    --email)
      [[ $# -ge 2 ]] || { echo "--email 后面缺少邮箱。" >&2; usage; exit 2; }
      email="$2"; shift 2 ;;
    --reset-login) reset_login=1; shift ;;
    --skip-dns-check) skip_dns_check=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "无法识别的参数：$1" >&2; usage; exit 2 ;;
  esac
done

if [[ $(id -u) -ne 0 ]]; then
  echo "请使用 root 用户运行此脚本。" >&2
  exit 1
fi

cat <<'EOF'

============================================================
  HTTPS 公网只读状态面板安装向导
============================================================

这个页面用于在没有 Tailscale 时供值守人员查看完整运行状态。
登录后可以查看矿机 IP、Worker、Share、矿池线路和故障原因，但不能修改
线路或配置，也不会显示共享密钥、密码、Webhook 和证书私钥。

开始前请完成：
  1. 在 Cloudflare 添加 status 子域名的 A 记录，指向这台 VPS 公网 IP；
  2. HTTPS 状态面板可以保持“已代理（橙色云朵）”；
  3. 在云服务商安全组和 VPS 防火墙放行 TCP 80、443；
  4. 确认 V3 面板已经安装完成。

邮箱说明：
  邮箱不是面板账号，也不会显示在网页中。Let's Encrypt 已在 2025 年
  停止证书到期提醒邮件，因此新版安装不要求填写邮箱。公网只读网站的
  证书由 Certbot 定时器自动续期，本向导会检查并启用该定时器。
EOF

if [[ -z "$domain" ]]; then
  if [[ -t 0 ]]; then
    echo
    read -r -p "请输入只读面板域名（例如 status1.mulinsen.win）：" domain
  else
    echo "没有提供域名。请使用 --domain status.example.com，或直接在终端运行脚本进入向导。" >&2
    exit 2
  fi
fi

if [[ ! "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || [[ "$domain" != *.* ]]; then
  echo "域名格式不正确：$domain" >&2
  echo "只填写域名，例如 status1.mulinsen.win；不要填写 https://、端口或斜杠。" >&2
  exit 2
fi
domain=${domain,,}

if [[ -n "$email" ]] && [[ ! "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
  echo "邮箱格式不正确：$email" >&2
  exit 2
fi

systemctl is-active --quiet stratum-public-status.service || {
  echo "只读状态服务尚未运行。请先执行 bootstrap-vps.sh，或运行 upgrade-v3-panel.sh 完成升级。" >&2
  exit 1
}

echo
echo "[1/6] 安装网页和证书工具……"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx curl python3-werkzeug

login_env=/etc/stratum-public-status.env
if [[ ! -f "$login_env" || $reset_login -eq 1 ]]; then
  echo "[2/6] 设置只读值守面板登录账号……"
  if [[ ! -t 0 && ( -z "${PUBLIC_STATUS_USERNAME:-}" || -z "${PUBLIC_STATUS_PASSWORD:-}" ) ]]; then
    echo "首次安装必须在终端中设置值守账号和密码；请直接运行本脚本，或通过 PUBLIC_STATUS_USERNAME 和 PUBLIC_STATUS_PASSWORD 环境变量提供。" >&2
    exit 2
  fi
  username=${PUBLIC_STATUS_USERNAME:-}
  while [[ ! "$username" =~ ^[A-Za-z0-9._@-]{3,64}$ ]]; do
    [[ -z "$username" ]] || echo "账号只能使用 3-64 位英文字母、数字、点、横线、下划线或 @。" >&2
    read -r -p "请设置值守账号（例如 operator）：" username
  done
  password=${PUBLIC_STATUS_PASSWORD:-}
  if [[ -z "$password" ]]; then
    while true; do
      read -r -s -p "请设置登录密码（至少 12 个字符）：" password
      echo
      read -r -s -p "请再次输入密码：" password_confirm
      echo
      if [[ ${#password} -lt 12 ]]; then
        echo "密码少于 12 个字符，请重新设置。" >&2
      elif [[ "$password" != "$password_confirm" ]]; then
        echo "两次输入不一致，请重新设置。" >&2
      else
        break
      fi
    done
  elif [[ ${#password} -lt 12 ]]; then
    echo "PUBLIC_STATUS_PASSWORD 少于 12 个字符。" >&2
    exit 2
  fi
  password_hash=$(PUBLIC_STATUS_PASSWORD="$password" python3 - <<'PY'
import os
from werkzeug.security import generate_password_hash
print(generate_password_hash(os.environ["PUBLIC_STATUS_PASSWORD"]))
PY
)
  secret_key=$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)
  umask 077
  cat >"$login_env" <<EOF
PUBLIC_STATUS_USERNAME=$username
PUBLIC_STATUS_PASSWORD_HASH=$password_hash
PUBLIC_STATUS_SECRET_KEY=$secret_key
EOF
  chmod 0600 "$login_env"
  unset password password_confirm PUBLIC_STATUS_PASSWORD password_hash secret_key
  echo "      值守账号已设置：$username"
else
  echo "[2/6] 保留现有值守账号和密码。需要修改时使用 --reset-login。"
fi

install -d -m 0755 /etc/systemd/system/stratum-public-status.service.d
cat >/etc/systemd/system/stratum-public-status.service.d/20-login.conf <<'EOF'
[Service]
EnvironmentFile=/etc/stratum-public-status.env
EOF
systemctl daemon-reload
systemctl restart stratum-public-status.service

if [[ $skip_dns_check -eq 0 ]]; then
  echo "[3/6] 检查域名是否已经正确指向这台 VPS……"
  resolved_ips=$(getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | sort -u | paste -sd ' ' - || true)
  public_ip=$(curl -4 -fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)
  if [[ -z "$resolved_ips" ]]; then
    cat >&2 <<EOF
没有查到 $domain 的 IPv4 地址，安装已停止。

请回到 Cloudflare 检查：
  - 记录类型：A
  - 名称：只填子域名前缀，例如 status1
  - IPv4 地址：这台 VPS 的公网 IP
  - 代理状态：仅 DNS（灰色云朵）

DNS 保存后通常需要等待几分钟，再重新运行本脚本。
EOF
    exit 1
  fi
  if [[ -n "$public_ip" ]] && [[ " $resolved_ips " != *" $public_ip "* ]]; then
    cat <<EOF
$domain 当前解析到：$resolved_ips
这台 VPS 的公网 IPv4 是：$public_ip
如果 Cloudflare 使用“已代理（橙色云朵）”，这里显示 Cloudflare 的代理 IP
而不是 VPS IP，这是正常现象。脚本无法从公网 DNS 核对橙色记录背后的真实 IP。
EOF
    if [[ -t 0 ]]; then
      read -r -p "请确认 Cloudflare 中该记录的内容是 $public_ip，并且状态为橙色云朵。确认无误后继续？[Y/n] " answer || true
      if [[ "$answer" =~ ^[Nn]$ ]]; then
        echo "已停止。请修正 Cloudflare 记录后重新运行同一条命令。" >&2
        exit 1
      fi
      echo "      已由管理员确认 Cloudflare 代理记录指向本机，继续安装。"
    else
      cat >&2 <<EOF
当前不是交互式终端，无法请管理员确认。确认 Cloudflare 记录内容无误后，
重新运行并加上 --skip-dns-check。
EOF
      exit 1
    fi
  else
    echo "      域名解析正常：$domain → ${public_ip:-$resolved_ips}"
  fi
else
  echo "[3/6] 已按要求跳过 DNS 检查。"
fi

echo "[4/6] 配置只读网站……"
site="/etc/nginx/sites-available/stratum-public-status"
if [[ -f "$site" ]]; then
  cp -a "$site" "${site}.backup-$(date +%Y%m%d-%H%M%S)"
fi
cat >"$site" <<EOF
limit_req_zone \$binary_remote_addr zone=stratum_status:10m rate=5r/s;

server {
    listen 80;
    listen [::]:80;
    server_name $domain;

    access_log /var/log/nginx/stratum-public-status.access.log;
    error_log /var/log/nginx/stratum-public-status.error.log warn;

    location / {
        limit_req zone=stratum_status burst=20 nodelay;
        proxy_pass http://127.0.0.1:8790;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Operator-IP \$remote_addr;
        proxy_connect_timeout 3s;
        proxy_read_timeout 15s;
    }
}
EOF

ln -sfn "$site" /etc/nginx/sites-enabled/stratum-public-status
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx

echo "[5/6] 申请并安装 HTTPS 证书……"
certbot_args=(--nginx --non-interactive --agree-tos --redirect -d "$domain")
if [[ -n "$email" ]]; then
  certbot_args+=(--email "$email")
else
  certbot_args+=(--register-unsafely-without-email)
fi
certbot "${certbot_args[@]}"

# If a previous run obtained the certificate but stopped before deployment,
# Certbot may keep the existing certificate without adding the HTTPS listener.
# Installing the named lineage is idempotent and repairs that interrupted state.
certbot install --cert-name "$domain" --nginx --non-interactive

echo "[6/6] 检查 HTTPS 页面和自动续期……"
nginx -t
systemctl enable --now nginx
systemctl restart nginx
systemctl enable --now certbot.timer >/dev/null 2>&1 || true
if ! curl -fsS --max-time 15 --resolve "$domain:443:127.0.0.1" "https://$domain/healthz" >/dev/null; then
  cat >&2 <<EOF
HTTPS 证书已经签发，但本机 443 端口的页面检查没有通过。

请依次执行下面三条命令查看原因：
  systemctl status nginx stratum-public-status --no-pager -l
  grep -R "listen .*443" /etc/nginx/sites-enabled /etc/nginx/sites-available
  ss -lntp | grep ':443'
EOF
  exit 1
fi

cat <<EOF

============================================================
  安装成功
============================================================

只读面板地址：https://$domain/

现在请完成最后两步：
  1. 用浏览器打开上面的地址，确认能看到状态页面；
  2. Cloudflare 可保持“已代理（橙色云朵）”，SSL/TLS 模式建议使用“完全（严格）”。

安全提醒：
  - 安全组只需放行 TCP 80、443；
  - 不要对公网放行 8789 或 8790；
  - 完整管理面板仍只通过 Tailscale 或 SSH 隧道进入；
  - 邮箱不是面板登录账号，面板也不会显示或使用该邮箱。
EOF
