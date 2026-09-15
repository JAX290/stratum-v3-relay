import json
import os
from pathlib import Path


def load_versions():
    candidates = [
        Path(os.getenv("STRATUM_VERSION_FILE", "/etc/stratum-version.json")),
        Path(__file__).resolve().parent.parent / "version.json",
    ]
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if all(value.get(key) for key in ("panel", "secure_relay", "windows_client")):
                return value
        except (OSError, ValueError):
            continue
    raise RuntimeError("version.json is missing or invalid")
