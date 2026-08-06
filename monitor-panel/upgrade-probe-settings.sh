#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi
for file in stratum_admin_v3.py endpoint_monitor.py templates/v3_dashboard.html static/v3.css; do
  test -f "./$file" || { echo "Missing $file" >&2; exit 1; }
done

stamp=$(date +%Y%m%d-%H%M%S)
backup="/root/stratum-probe-settings-backup-$stamp"
install -d -m 0700 "$backup"
cp -a /opt/stratum-admin/stratum_admin_v3.py /opt/stratum-admin/endpoint_monitor.py "$backup/"
if [[ -f /opt/stratum-admin/templates/v3_dashboard.html ]]; then
  cp -a /opt/stratum-admin/templates/v3_dashboard.html "$backup/"
fi
if [[ -f /opt/stratum-admin/static/v3.css ]]; then
  cp -a /opt/stratum-admin/static/v3.css "$backup/"
fi

python3 -m py_compile ./stratum_admin_v3.py ./endpoint_monitor.py
systemctl stop stratum-security-monitor.service
trap 'systemctl start stratum-security-monitor.service >/dev/null 2>&1 || true' EXIT

install -m 0755 ./stratum_admin_v3.py ./endpoint_monitor.py /opt/stratum-admin/
install -d -m 0755 /opt/stratum-admin/templates /opt/stratum-admin/static
install -m 0644 ./templates/v3_dashboard.html /opt/stratum-admin/templates/v3_dashboard.html
install -m 0644 ./static/v3.css /opt/stratum-admin/static/v3.css

systemctl restart stratum-endpoint-monitor.service
systemctl restart stratum-admin.service

PYTHONPATH=/opt/stratum-admin python3 -c "from security_monitor import BASELINE_FILE,atomic_write,load,snapshot; p=['/opt/stratum-admin/stratum_admin_v3.py','/opt/stratum-admin/endpoint_monitor.py','/opt/stratum-admin/templates/v3_dashboard.html','/opt/stratum-admin/static/v3.css']; b=load(BASELINE_FILE,{}); b.update(snapshot(p)); atomic_write(BASELINE_FILE,b)"
systemctl start stratum-security-monitor.service
trap - EXIT
systemctl --no-pager --full status stratum-endpoint-monitor.service stratum-admin.service stratum-security-monitor.service
echo "Probe settings upgrade complete. Backup: $backup"
