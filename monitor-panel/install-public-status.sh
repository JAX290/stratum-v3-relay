#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<EOF
用法：
  $0
  $0 --domain status.example.com

可选参数：
  --email EMAIL       兼容旧版 Certbot 的注册邮箱参数；不是面板账号
  --skip-dns-check    跳过“域名必须指向本机公网 IP”的检查
  -h, --help          显示本说明

新手建议直接运行 $0，按屏幕提示填写。
EOF
}

domain=""
email=""
skip_dns_check=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      [[ $# -ge 2 ]] || { echo "--domain 后面缺少域名。" >&2; usage; exit 2; }
      domain="$2"; shift 2 ;;
    --email)
      [[ $# -ge 2 ]] || { echo "--email 后面缺少邮箱。" >&2; usage; exit 2; }
      email="$2"; shift 2 ;;
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

这个页面用于在没有 Tailscale 时查看脱敏运行状态。
它不能修改线路或配置，也不会显示矿机 IP、Worker、密钥和原始日志。

开始前请完成：
  1. 在 Cloudflare 添加 status 子域名的 A 记录，指向这台 VPS 公网 IP；
  2. 代理状态暂时设为“仅 DNS（灰色云朵）”；
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
echo "[1/5] 安装网页和证书工具……"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx curl

if [[ $skip_dns_check -eq 0 ]]; then
  echo "[2/5] 检查域名是否已经正确指向这台 VPS……"
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
    cat >&2 <<EOF
$domain 当前解析到：$resolved_ips
这台 VPS 的公网 IPv4 是：$public_ip
两者不一致，安装已停止，以免证书申请失败。

最常见原因：
  1. Cloudflare 中填写了旧 VPS 的 IP；
  2. Cloudflare 仍是“已代理（橙色云朵）”。申请证书前请暂时改为
     “仅 DNS（灰色云朵）”；网页安装成功后再改回橙色云朵。

如果这台 VPS 使用特殊 NAT 或端口映射，并且你已经确认公网流量能到达
本机，可以重新运行并加上 --skip-dns-check。
EOF
    exit 1
  fi
  echo "      域名解析正常：$domain → ${public_ip:-$resolved_ips}"
else
  echo "[2/5] 已按要求跳过 DNS 检查。"
fi

echo "[3/5] 配置只读网站……"
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

echo "[4/5] 申请并安装 HTTPS 证书……"
certbot_args=(--nginx --non-interactive --agree-tos --redirect -d "$domain")
if [[ -n "$email" ]]; then
  certbot_args+=(--email "$email")
else
  certbot_args+=(--register-unsafely-without-email)
fi
certbot "${certbot_args[@]}"

echo "[5/5] 检查 HTTPS 页面和自动续期……"
nginx -t
systemctl reload nginx
systemctl enable --now certbot.timer >/dev/null 2>&1 || true
curl -fsS --max-time 15 --resolve "$domain:443:127.0.0.1" "https://$domain/healthz" >/dev/null

cat <<EOF

============================================================
  安装成功
============================================================

只读面板地址：https://$domain/

现在请完成最后两步：
  1. 用浏览器打开上面的地址，确认能看到状态页面；
  2. 确认正常后，可把 Cloudflare 代理状态改为“已代理（橙色云朵）”。

安全提醒：
  - 安全组只需放行 TCP 80、443；
  - 不要对公网放行 8789 或 8790；
  - 完整管理面板仍只通过 Tailscale 或 SSH 隧道进入；
  - 邮箱不是面板登录账号，面板也不会显示或使用该邮箱。
EOF
