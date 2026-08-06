#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then echo "Please run as root." >&2; exit 1; fi
for file in stratum_inspector.py stratum_admin_v3.py templates/v3_dashboard.html static/v3.css; do
  test -f "./$file" || { echo "Missing required file: $file" >&2; exit 1; }
done

stamp=$(date +%Y%m%d-%H%M%S)
backup="/root/stratum-worker-view-backup-$stamp"
install -d -m 0700 "$backup"
cp -a /opt/stratum-admin/stratum_inspector.py /opt/stratum-admin/stratum_admin_v3.py "$backup/"
cp -a /opt/stratum-admin/templates/v3_dashboard.html /opt/stratum-admin/static/v3.css "$backup/"

python3 -m py_compile ./stratum_inspector.py ./stratum_admin_v3.py
systemctl stop stratum-security-monitor.service
trap 'systemctl start stratum-security-monitor.service >/dev/null 2>&1 || true' EXIT

install -m 0755 ./stratum_inspector.py ./stratum_admin_v3.py /opt/stratum-admin/
install -m 0644 ./templates/v3_dashboard.html /opt/stratum-admin/templates/v3_dashboard.html
install -m 0644 ./static/v3.css /opt/stratum-admin/static/v3.css

PYTHONPATH=/opt/stratum-admin python3 -c "from security_monitor import BASELINE_FILE,atomic_write,load,snapshot; p=['/opt/stratum-admin/stratum_inspector.py','/opt/stratum-admin/stratum_admin_v3.py','/opt/stratum-admin/templates/v3_dashboard.html','/opt/stratum-admin/static/v3.css']; b=load(BASELINE_FILE,{}); b.update(snapshot(p)); atomic_write(BASELINE_FILE,b)"

# Restarting the inspector clears obsolete test Worker history and briefly
# reconnects miners. HAProxy and public port mappings remain unchanged.
systemctl restart stratum-inspector-v3.service
systemctl restart stratum-admin.service
systemctl start stratum-security-monitor.service
trap - EXIT

systemctl --no-pager --full status stratum-inspector-v3.service stratum-admin.service stratum-security-monitor.service
echo "Worker view upgrade complete. Backup: $backup"
