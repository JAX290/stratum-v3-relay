#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --domain status.example.com [--email admin@example.com]" >&2
}

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

domain=""
email=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) domain="${2:-}"; shift 2 ;;
    --email) email="${2:-}"; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done

if [[ ! "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || [[ "$domain" != *.* ]]; then
  echo "Invalid domain name: $domain" >&2
  usage
  exit 2
fi
domain=${domain,,}

systemctl is-active --quiet stratum-public-status.service || {
  echo "stratum-public-status.service is not running. Run install-v3.sh or upgrade-v3-panel.sh first." >&2
  exit 1
}

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot python3-certbot-nginx

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

certbot_args=(--nginx --non-interactive --agree-tos --redirect -d "$domain")
if [[ -n "$email" ]]; then
  certbot_args+=(--email "$email")
else
  certbot_args+=(--register-unsafely-without-email)
fi
certbot "${certbot_args[@]}"

nginx -t
systemctl reload nginx
echo "Public read-only status page is ready: https://$domain/"
echo "Full administration remains available only through Tailscale on 127.0.0.1:8789."
