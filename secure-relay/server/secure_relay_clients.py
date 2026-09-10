#!/usr/bin/env python3
"""Manage independent client credentials for the secure relay."""

import argparse
import json
import os
import re
import secrets
import tempfile
from pathlib import Path


CONFIG_FILE = Path(os.getenv("SECURE_RELAY_CONFIG", "/etc/stratum-secure-relay.json"))
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$")


def load():
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if not data.get("clients") and data.get("token"):
        data["clients"] = [{"id": "default", "name": "默认矿场", "token": data.pop("token"), "enabled": True}]
    return data


def save(data):
    fd, temporary = tempfile.mkstemp(prefix=CONFIG_FILE.name + ".", dir=str(CONFIG_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, CONFIG_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description="Manage Stratum secure relay clients")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("client_id")
    add.add_argument("name")
    for command in ("rotate", "enable", "disable"):
        item = sub.add_parser(command)
        item.add_argument("client_id")
    args = parser.parse_args()
    data = load()
    clients = data.setdefault("clients", [])
    if args.command == "list":
        for item in clients:
            print(f"{item['id']}\t{item.get('name', item['id'])}\t{'enabled' if item.get('enabled', True) else 'disabled'}")
        return
    if not ID_RE.match(args.client_id):
        raise SystemExit("client id must use letters, numbers, underscore or hyphen")
    match = next((item for item in clients if item.get("id") == args.client_id), None)
    if args.command == "add":
        if match:
            raise SystemExit("client id already exists")
        token = secrets.token_hex(32)
        clients.append({"id": args.client_id, "name": args.name[:80], "token": token, "enabled": True})
        save(data)
        print(f"Client ID: {args.client_id}\nShared key: {token}")
        return
    if not match:
        raise SystemExit("client id not found")
    if args.command == "rotate":
        match["token"] = secrets.token_hex(32)
        match["enabled"] = True
        save(data)
        print(f"Client ID: {args.client_id}\nNew shared key: {match['token']}")
    else:
        match["enabled"] = args.command == "enable"
        save(data)
        print(f"{args.client_id}: {'enabled' if match['enabled'] else 'disabled'}")


if __name__ == "__main__":
    main()
