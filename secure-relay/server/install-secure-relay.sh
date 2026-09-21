#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

cd "$(dirname "$0")"
for file in stratum_secure_server.py stratum_secure_monitor.py secure_relay_clients.py; do
  test -f "$file" || { echo "Missing $file" >&2; exit 1; }
done
test -f ../../version.json || { echo "Missing repository version.json." >&2; exit 1; }
test -f ../../monitor-panel/version_info.py || { echo "Missing version_info.py." >&2; exit 1; }
test -f /etc/stratum-v3.json || { echo "Deploy Stratum V3 first: /etc/stratum-v3.json is missing." >&2; exit 1; }
test -f /etc/stratum-inspector.json || { echo "Deploy Stratum V3 first: /etc/stratum-inspector.json is missing." >&2; exit 1; }
id stratum-relay >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin stratum-relay
install -d -o root -g stratum-relay -m 0750 /etc/stratum-secure-relay
install -d -o stratum-relay -g stratum-relay -m 2770 /var/lib/stratum-secure-relay
chown -R stratum-relay:stratum-relay /var/lib/stratum-secure-relay
chmod 2770 /var/lib/stratum-secure-relay

python3 - <<'PY'
import json
import sys

path = "/etc/stratum-inspector.json"
try:
    relays = json.load(open(path, encoding="utf-8")).get("relays", [])
except (OSError, ValueError) as exc:
    raise SystemExit(f"Cannot read {path}: {exc}")
if not relays or any(not relay.get("proxy_protocol") for relay in relays):
    raise SystemExit("Stratum V3 source-IP forwarding is not enabled. Re-run monitor-panel/install-v3.sh before installing the secure relay.")
print("Stratum V3 source-IP forwarding: enabled (PROXY protocol).")
PY

listen_port=""
cert_file=""
key_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) listen_port="$2"; shift 2 ;;
    --cert) cert_file="$2"; shift 2 ;;
    --key) key_file="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$listen_port" ]]; then
  if [[ -f /etc/stratum-secure-relay.json ]]; then
    listen_port=$(python3 -c 'import json; print(json.load(open("/etc/stratum-secure-relay.json")).get("listen_port", 443))')
  else
    listen_port=443
  fi
fi
[[ "$listen_port" =~ ^[0-9]+$ ]] && ((listen_port >= 1 && listen_port <= 65535)) || { echo "Invalid TLS port." >&2; exit 2; }
if [[ -z "$cert_file" && -z "$key_file" && -f /etc/stratum-secure-relay.json ]]; then
  mapfile -t existing_tls < <(python3 - <<'PY'
import json
try:
    data=json.load(open('/etc/stratum-secure-relay.json', encoding='utf-8'))
    print(data.get('certificate',''))
    print(data.get('private_key',''))
except (OSError, ValueError):
    print(); print()
PY
)
  if [[ -f "${existing_tls[0]:-}" && -f "${existing_tls[1]:-}" ]]; then
    cert_file="${existing_tls[0]}"
    key_file="${existing_tls[1]}"
  fi
fi
if [[ -n "$cert_file" || -n "$key_file" ]]; then
  test -f "$cert_file" && test -f "$key_file" || { echo "Both --cert and --key must point to existing files." >&2; exit 2; }
else
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y openssl
  cert_file=/etc/stratum-secure-relay/server.crt
  key_file=/etc/stratum-secure-relay/server.key
  if [[ ! -f "$cert_file" || ! -f "$key_file" ]]; then
    openssl req -x509 -newkey rsa:3072 -sha256 -days 825 -nodes \
      -subj "/CN=Stratum Secure Relay" \
      -keyout "$key_file" -out "$cert_file"
    chmod 0600 "$key_file"
    chmod 0644 "$cert_file"
  fi
fi

# Copy externally supplied certificates into a directory the dedicated service
# account can traverse, while keeping the private key unavailable to others.
managed_cert=/etc/stratum-secure-relay/server.crt
managed_key=/etc/stratum-secure-relay/server.key
if [[ "$cert_file" != "$managed_cert" ]]; then
  install -o root -g stratum-relay -m 0640 "$cert_file" "$managed_cert"
fi
if [[ "$key_file" != "$managed_key" ]]; then
  install -o root -g stratum-relay -m 0640 "$key_file" "$managed_key"
fi
cert_file="$managed_cert"
key_file="$managed_key"
chown root:stratum-relay "$cert_file" "$key_file"
chmod 0640 "$cert_file" "$key_file"

install -o root -g root -m 0755 stratum_secure_server.py /opt/stratum-secure-server.py
install -o root -g root -m 0755 ../../monitor-panel/version_info.py /opt/version_info.py
install -o root -g root -m 0644 ../../version.json /etc/stratum-version.json
install -o root -g root -m 0755 stratum_secure_monitor.py /opt/stratum-secure-monitor.py
install -o root -g root -m 0755 secure_relay_clients.py /usr/local/sbin/stratum-relay-client
token=$(openssl rand -hex 32)
if [[ -f /etc/stratum-secure-relay.json ]]; then
  token=$(python3 -c 'import json; d=json.load(open("/etc/stratum-secure-relay.json")); print((d.get("clients") or [{"token":d.get("token","")}])[0]["token"])')
fi

python3 - "$listen_port" "$cert_file" "$key_file" "$token" <<'PY'
import json, os, sys, tempfile
target = "/etc/stratum-secure-relay.json"
data = {
    "listen_host": "0.0.0.0",
    "listen_port": int(sys.argv[1]),
    "certificate": os.path.abspath(sys.argv[2]),
    "private_key": os.path.abspath(sys.argv[3]),
    "clients": [{"id": "default", "name": "默认矿场", "token": sys.argv[4], "enabled": True, "alert_enabled": True}],
    "max_connections": 1000,
    "offline_after_seconds": 180,
    "offline_confirm_checks": 2,
    "recovery_confirm_checks": 2,
    "notification_dedup_seconds": 600,
    "state_file": "/var/lib/stratum-secure-relay/sites.json",
}
if os.path.exists(target):
    old = json.load(open(target, encoding="utf-8"))
    clients = old.get("clients") or ([{"id": "default", "name": "默认矿场", "token": old["token"], "enabled": True, "alert_enabled": True}] if old.get("token") else [])
    if clients or "clients" in old:
        data["clients"] = clients
    for key in ("offline_after_seconds", "offline_confirm_checks", "recovery_confirm_checks", "notification_dedup_seconds"):
        if key in old:
            data[key] = old[key]
fd, temporary = tempfile.mkstemp(prefix="stratum-secure-relay.", dir="/etc")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")
os.chmod(temporary, 0o600)
os.replace(temporary, target)
PY
chown root:stratum-relay /etc/stratum-secure-relay.json
chmod 0640 /etc/stratum-secure-relay.json

cat >/etc/systemd/system/stratum-secure-relay.service <<'EOF'
[Unit]
Description=Stratum V3 encrypted TLS ingress
After=network-online.target stratum-inspector-v3.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/stratum-secure-server.py
Restart=always
RestartSec=3
LimitNOFILE=65536
Environment=PYTHONUNBUFFERED=1
User=stratum-relay
Group=stratum-relay
SupplementaryGroups=stratum-proxy
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true
ReadOnlyPaths=/etc/stratum-v3.json /etc/stratum-version.json /etc/stratum-secure-relay.json /etc/stratum-secure-relay
ReadWritePaths=/var/lib/stratum-secure-relay

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/stratum-secure-monitor.service <<'EOF'
[Unit]
Description=Stratum secure relay mine-site heartbeat monitor
After=network-online.target stratum-secure-relay.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/stratum-secure-monitor.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
User=root
Group=stratum-relay
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=/etc/stratum-secure-relay.json -/etc/stratum-v3.env
ReadWritePaths=/var/lib/stratum-secure-relay

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable stratum-secure-relay.service
systemctl enable stratum-secure-monitor.service
systemctl restart stratum-secure-relay.service
systemctl restart stratum-secure-monitor.service
systemctl --no-pager --full status stratum-secure-relay.service

fingerprint=$(openssl x509 -in "$cert_file" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':')
cat <<EOF

Secure relay installed.

Windows client settings:
  TLS port: $listen_port
  Shared key: $token
  Certificate SHA-256: $fingerprint

Open TCP port $listen_port in the VPS provider firewall if required.

Client credential commands:
  stratum-relay-client list
  stratum-relay-client add mine-a "矿场A"
  stratum-relay-client rotate mine-a
  stratum-relay-client disable mine-a
EOF
