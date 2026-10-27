#!/usr/bin/env python3
"""Compare installed components against the newest published version manifest.

Read-only by design: this module only *reads* the public manifest and the local one.
Performing the upgrade stays behind the privileged helper, which is the only component
allowed to touch root-owned files.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY = os.getenv("STRATUM_REPOSITORY", "JAX290/stratum-v3-relay")
BRANCH = os.getenv("STRATUM_REPOSITORY_BRANCH", "main")
RAW_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/{BRANCH}/version.json"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/contents/version.json?ref={BRANCH}"

COMPONENTS = ("panel", "secure_relay", "windows_client")
LABELS = {
    "panel": "管理面板",
    "secure_relay": "VPS 加密入口",
    "windows_client": "Windows 客户端",
}
# A version manifest is tiny; anything larger is not ours.
MAX_BYTES = 64 * 1024
DEFAULT_TIMEOUT = 12


def version_tuple(value):
    """Return a comparable tuple, or None when the value is not a plain x.y.z."""
    parts = str(value or "").split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def parse_manifest(raw):
    """Read either a plain manifest or a GitHub contents-API envelope.

    Returns a dict of the three components, or None when the payload is not a valid
    manifest. Failing closed matters: a truncated or captive-portal response must never
    be mistaken for "everything is up to date".
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict) and "content" in data and data.get("encoding") == "base64":
        try:
            data = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
        except (TypeError, ValueError, KeyError):
            return None
    if not isinstance(data, dict):
        return None
    versions = {}
    for key in COMPONENTS:
        value = str(data.get(key, "")).strip()
        if version_tuple(value) is None:
            return None
        versions[key] = value
    return versions


def _read(url, opener, timeout):
    request = urllib.request.Request(url, headers={
        "User-Agent": "stratum-panel-update-check",
        "Accept": "application/vnd.github+json, application/json",
    })
    with opener(request, timeout=timeout) as response:
        body = response.read(MAX_BYTES + 1)
        status = getattr(response, "status", 200)
    if status != 200 or len(body) > MAX_BYTES:
        raise OSError(f"unexpected response ({status}, {len(body)} bytes)")
    return body.decode("utf-8", "replace")


def fetch_remote_versions(opener=None, timeout=DEFAULT_TIMEOUT):
    """Fetch the newest manifest. Tries raw.githubusercontent, then the contents API.

    Returns (versions, source). Raises OSError with a readable reason on failure.
    """
    opener = opener or urllib.request.urlopen
    errors = []
    for label, url in (("raw", RAW_URL), ("api", API_URL)):
        try:
            versions = parse_manifest(_read(url, opener, timeout))
            if versions:
                return versions, label
            errors.append(f"{label}: manifest did not parse")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            detail = str(exc).strip().replace("\n", " ")[:60]
            errors.append(f"{label}: {type(exc).__name__}{(': ' + detail) if detail else ''}")
    raise OSError("；".join(errors))


def compare(local, remote):
    """Row-per-component comparison for display."""
    rows = []
    for key in COMPONENTS:
        current = str((local or {}).get(key, "")).strip()
        latest = str((remote or {}).get(key, "")).strip()
        current_tuple, latest_tuple = version_tuple(current), version_tuple(latest)
        rows.append({
            "key": key,
            "label": LABELS[key],
            "current": current,
            "latest": latest,
            "newer": bool(current_tuple and latest_tuple and latest_tuple > current_tuple),
        })
    return rows


def any_newer(rows):
    return any(row["newer"] for row in rows)


def read_local_versions(path=None):
    path = Path(path or os.getenv("STRATUM_VERSION_FILE", "/etc/stratum-version.json"))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def read_state(path):
    """Cached last check, so the settings page does not hit GitHub on every load."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def build_context(local, state, now=None):
    """Pure: assemble the settings-page context from already-parsed dictionaries."""
    now = int(now if now is not None else time.time())
    state = state if isinstance(state, dict) else {}
    remote = state.get("latest") if isinstance(state.get("latest"), dict) else None
    rows = compare(local, remote) if remote else [
        {"key": key, "label": LABELS[key], "current": str((local or {}).get(key, "")),
         "latest": "", "newer": False} for key in COMPONENTS
    ]
    checked_at = int(state.get("checked_at", 0) or 0)
    return {
        "rows": rows,
        "checked_at": checked_at,
        "checked_text": time.strftime("%Y-%m-%d %H:%M", time.localtime(checked_at)) if checked_at else "",
        "age_seconds": (now - checked_at) if checked_at else None,
        "has_newer": any_newer(rows),
        "source": str(state.get("source", "")),
        "error": str(state.get("error", "")),
    }


def build_update_context(state_path, local_path=None, now=None):
    """Settings-page context from the cached check. Never performs network I/O."""
    return build_context(read_local_versions(local_path), read_state(state_path), now)


def write_state(state_path, payload):
    """Persist the last check. A failed write must not break the settings page."""
    target = Path(state_path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        pass


def record_check(state_path, local_path=None, opener=None, timeout=DEFAULT_TIMEOUT, writer=None):
    """Perform one check and cache the outcome. Returns the fresh context."""
    local = read_local_versions(local_path)
    payload = {"checked_at": int(time.time()), "local": local}
    try:
        remote, source = fetch_remote_versions(opener=opener, timeout=timeout)
        payload.update({"latest": remote, "source": source, "error": ""})
    except (OSError, ValueError) as exc:
        payload.update({"latest": {}, "source": "", "error": str(exc)[:300]})
    (writer or write_state)(state_path, payload)
    return build_context(local, payload)
