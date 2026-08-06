#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

systemctl disable --now stratum-inspector.service || true
if [[ -f /opt/stratum-admin/stratum_admin.phase1-backup.py ]]; then
  install -m 0755 /opt/stratum-admin/stratum_admin.phase1-backup.py /opt/stratum-admin/stratum_admin.py
  systemctl restart stratum-admin.service
fi
echo "Phase 2 test service stopped and phase 1 panel restored."
