#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then echo "Please run as root." >&2; exit 1; fi
backup=${1:-$(cat /root/stratum-v3-last-backup 2>/dev/null || true)}
if [[ -z "$backup" || ! -d "$backup" ]]; then echo "Backup directory not found." >&2; exit 1; fi
resolved=$(readlink -f "$backup")
case "$resolved" in /root/stratum-v3-backup-*) ;; *) echo "Refusing unexpected backup path: $resolved" >&2; exit 1 ;; esac

systemctl disable --now stratum-security-monitor.service stratum-endpoint-monitor.service stratum-inspector-v3.service 2>/dev/null || true
for name in haproxy.cfg stratum-inspector.json stratum-v3.json stratum-inspector.service stratum-admin.service; do
  source="$resolved/$name"
  if [[ -f "$source" ]]; then
    case "$name" in
      haproxy.cfg) target=/etc/haproxy/haproxy.cfg ;;
      stratum-inspector.json) target=/etc/stratum-inspector.json ;;
      stratum-v3.json) target=/etc/stratum-v3.json ;;
      *) target="/etc/systemd/system/$name" ;;
    esac
    install -m 0644 "$source" "$target"
  fi
done
haproxy -c -f /etc/haproxy/haproxy.cfg
systemctl daemon-reload
systemctl restart haproxy
systemctl enable --now stratum-inspector.service 2>/dev/null || true
systemctl restart stratum-admin.service
echo "Rolled back from $resolved"
