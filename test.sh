#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m unittest discover -s monitor-panel -p 'test_*.py'
python3 -m unittest discover -s secure-relay/server -p 'test_*.py'

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
python3 monitor-panel/v3_manager.py --config monitor-panel/v3-config.json \
  --inspector "$temporary/stratum-inspector.json" --haproxy "$temporary/haproxy.cfg"
test -s "$temporary/stratum-inspector.json"
test -s "$temporary/haproxy.cfg"
