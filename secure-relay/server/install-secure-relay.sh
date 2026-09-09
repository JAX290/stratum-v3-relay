#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

cd "$(dirname "$0")"
test -f stratum_secure_server.py || { echo "Missing stratum_secure_server.py" >&2; exit 1; }
test -f /etc/stratum-v3.json || { echo "Deploy Stratum V3 first: /etc/stratum-v3.json is missing." >&2; exit 1; }

listen_port=443
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

[[ "$listen_port" =~ ^[0-9]+$ ]] && ((listen_port >= 1 && listen_port <= 65535)) || { echo "Invalid TLS port." >&2; exit 2; }
if [[ -n "$cert_file" || -n "$key_file" ]]; then
  test -f "$cert_file" && test -f "$key_file" || { echo "Both --cert and --key must point to existing files." >&2; exit 2; }
else
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y openssl
  install -d -m 0700 /etc/stratum-secure-relay
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

install -o root -g root -m 0755 stratum_secure_server.py /opt/stratum-secure-server.py
token=$(openssl rand -hex 32)
if [[ -f /etc/stratum-secure-relay.json ]]; then
  token=$(python3 -c 'import json; print(json.load(open("/etc/stratum-secure-relay.json"))["token"])')
fi

python3 - "$listen_port" "$cert_file" "$key_file" "$token" <<'PY'
import json, os, sys, tempfile
target = "/etc/stratum-secure-relay.json"
data = {
    "listen_host": "0.0.0.0",
    "listen_port": int(sys.argv[1]),
    "certificate": os.path.abspath(sys.argv[2]),
    "private_key": os.path.abspath(sys.argv[3]),
    "token": sys.argv[4],
    "max_connections": 1000,
}
fd, temporary = tempfile.mkstemp(prefix="stratum-secure-relay.", dir="/etc")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")
os.chmod(temporary, 0o600)
os.replace(temporary, target)
PY

cat >/etc/systemd/system/stratum-secure-relay.service <<'EOF'
[Unit]
Description=Stratum V3 encrypted TLS ingress
After=network-online.target stratum-inspector-v3.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/stratum-secure-server.py
Restart=always
RestartSec=3
User=root
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=/etc/stratum-v3.json /etc/stratum-secure-relay.json

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable stratum-secure-relay.service
systemctl restart stratum-secure-relay.service
systemctl --no-pager --full status stratum-secure-relay.service

fingerprint=$(openssl x509 -in "$cert_file" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':')
cat <<EOF

Secure relay installed.

Windows client settings:
  TLS port: $listen_port
  Shared key: $token
  Certificate SHA-256: $fingerprint

Open TCP port $listen_port in the VPS provider firewall if required.
EOF
