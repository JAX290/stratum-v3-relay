#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
versions = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
required = {"panel", "secure_relay", "windows_client"}
if set(versions) != required or any(not re.fullmatch(r"\d+\.\d+\.\d+", str(value)) for value in versions.values()):
    raise SystemExit("version.json must contain only panel, secure_relay and windows_client in x.y.z format")

if len(sys.argv) > 1:
    expected_tag = "client-v" + versions["windows_client"]
    if sys.argv[1] != expected_tag:
        raise SystemExit(f"release tag must be {expected_tag}, got {sys.argv[1]}")

readme = (ROOT / "README.md").read_text(encoding="utf-8")
if f"当前 Windows 客户端版本为 `{versions['windows_client']}`" not in readme:
    raise SystemExit("README current client version does not match version.json")
print("version manifest is consistent")
