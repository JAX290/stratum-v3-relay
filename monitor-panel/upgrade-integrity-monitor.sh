#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then echo "Please run as root." >&2; exit 1; fi
test -f ./security_monitor.py || { echo "Missing security_monitor.py" >&2; exit 1; }

stamp=$(date +%Y%m%d-%H%M%S)
backup="/root/stratum-integrity-monitor-backup-$stamp"
install -d -m 0700 "$backup"
cp -a /opt/stratum-admin/security_monitor.py "$backup/"

python3 -m py_compile ./security_monitor.py
systemctl stop stratum-security-monitor.service
trap 'systemctl start stratum-security-monitor.service >/dev/null 2>&1 || true' EXIT

install -m 0755 ./security_monitor.py /opt/stratum-admin/security_monitor.py
PYTHONPATH=/opt/stratum-admin python3 -c "from security_monitor import BASELINE_FILE,atomic_write,load,snapshot; p=['/opt/stratum-admin/security_monitor.py']; b=load(BASELINE_FILE,{}); b.update(snapshot(p)); atomic_write(BASELINE_FILE,b)"

systemctl start stratum-security-monitor.service
trap - EXIT
systemctl --no-pager --full status stratum-security-monitor.service
echo "Integrity monitor upgrade complete. Backup: $backup"
