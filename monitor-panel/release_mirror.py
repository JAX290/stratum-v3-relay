#!/usr/bin/env python3
"""Mirror the published Windows installer from GitHub Releases onto the VPS.

Run by a daily systemd timer. The gate serves whatever sits in the release directory, so
this job keeps that copy at most one day behind GitHub — a watch computer can then always
fetch the installer from the VPS even when GitHub is unreachable.

Rules that matter:

* **Never downgrade.** A local copy newer than GitHub (for example a build staged ahead of
  the public release) is left alone; only strictly newer GitHub versions replace it.
* **Only published releases.** Drafts and pre-releases are skipped, so mirroring cannot
  leak an unreleased build to the public gate.
* **Fail closed on verification.** The new file must match the SHA-256 in the release's own
  ``SHA256SUMS.txt``, otherwise nothing is replaced. Clients still verify signature and
  pinned publisher, so a tampered mirror fails there too.
* **Atomic replacement.** The candidate is downloaded and verified before the live file and
  manifest are swapped, so a failed sync never leaves a half-written installer behind.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path

API_LATEST = "https://api.github.com/repos/JAX290/stratum-v3-relay/releases/latest"
USER_AGENT = "StratumReleaseMirror/1.0"
DEFAULT_TIMEOUT = 30
MAX_METADATA_BYTES = 512 * 1024
MAX_INSTALLER_BYTES = 64 * 1024 * 1024
TAG_PATTERN = re.compile(r"^client-v(\d+\.\d+\.\d+)$")
HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def version_tuple(value):
    try:
        parts = tuple(int(part) for part in str(value).split("."))
    except (TypeError, ValueError):
        return None
    return parts if len(parts) == 3 and all(part >= 0 for part in parts) else None


def compare_versions(left, right):
    """>0 when left is newer, 0 when equal or incomparable, <0 when older."""
    a, b = version_tuple(left), version_tuple(right)
    if a is None or b is None:
        return 0
    return (a > b) - (a < b)


def read_manifest(release_dir):
    try:
        payload = json.loads((Path(release_dir) / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def fetch_latest_release(opener=None, timeout=DEFAULT_TIMEOUT):
    """Return the published client release, or raise ValueError when it is unusable."""
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(API_LATEST, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with opener(request, timeout=timeout) as response:
        payload = json.loads(response.read(MAX_METADATA_BYTES).decode("utf-8", "replace"))

    if not isinstance(payload, dict):
        raise ValueError("GitHub 返回的不是发布信息")
    if payload.get("draft") or payload.get("prerelease"):
        raise ValueError("最新发布是草稿或预发布，跳过")
    match = TAG_PATTERN.match(str(payload.get("tag_name", "")))
    if not match:
        raise ValueError("标签不是 client-v<版本> 格式")
    version = match.group(1)
    installer = f"MulinSenRelay-{version}.exe"

    installer_url = sums_url = ""
    for asset in payload.get("assets") or []:
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        if not url.startswith("https://github.com/"):
            continue
        if name == installer:
            installer_url = url
        elif name == "SHA256SUMS.txt":
            sums_url = url
    if not installer_url:
        raise ValueError(f"发布里没有 {installer}")
    if not sums_url:
        raise ValueError("发布里没有 SHA256SUMS.txt，无法校验")
    return {"version": version, "installer_url": installer_url, "sums_url": sums_url}


def parse_expected_hash(sums_text, version):
    for line in str(sums_text).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == f"MulinSenRelay-{version}.exe":
            digest = parts[0].lower()
            return digest if HASH_PATTERN.match(digest) else None
    return None


def _download(url, opener=None, timeout=DEFAULT_TIMEOUT, limit=MAX_INSTALLER_BYTES):
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener(request, timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("安装包超过大小上限")
    return data


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sync(release_dir, opener=None, now=None, timeout=DEFAULT_TIMEOUT):
    """One sync attempt. Never raises for expected conditions; returns a summary dict."""
    release_dir = Path(release_dir)
    now = int(now if now is not None else time.time())
    local = read_manifest(release_dir)
    local_version = str(local.get("version", ""))
    result = {"checked_at": now, "action": "none", "reason": "",
              "local": local_version, "remote": "", "synced_at": int(local.get("synced_at", 0) or 0)}

    try:
        release = fetch_latest_release(opener=opener, timeout=timeout)
    except (OSError, ValueError) as exc:
        result.update(action="error", reason=f"github: {exc}"[:200])
        _write_json(release_dir / "sync.json", result)
        return result

    result["remote"] = release["version"]
    # An empty local version means nothing has been mirrored yet, so adopt the release.
    if local_version and compare_versions(release["version"], local_version) <= 0:
        result["reason"] = "已是最新或本地版本更高" if local_version else "本地版本相同"
        _write_json(release_dir / "sync.json", result)
        return result

    staging = release_dir / f".incoming-{release['version']}.exe"
    try:
        sums = _download(release["sums_url"], opener=opener, timeout=timeout,
                         limit=MAX_METADATA_BYTES).decode("utf-8", "replace")
        expected = parse_expected_hash(sums, release["version"])
        if not expected:
            result.update(action="error", reason="SHA256SUMS 中没有对应安装包的摘要")
            return result

        release_dir.mkdir(parents=True, exist_ok=True)
        data = _download(release["installer_url"], opener=opener, timeout=timeout)
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            result.update(action="error", reason="下载内容摘要与发布摘要不一致")
            return result

        staging.write_bytes(data)
        target = release_dir / f"MulinSenRelay-{release['version']}.exe"
        os.replace(staging, target)
        _write_json(release_dir / "latest.json", {
            "version": release["version"], "file": target.name,
            "sha256": actual, "size": len(data),
            "source": "github", "synced_at": now})
        for stale in release_dir.glob("MulinSenRelay-*.exe"):
            if stale.name != target.name:
                try:
                    stale.unlink()
                except OSError:
                    pass
        result.update(action="updated", reason="", synced_at=now)
    except (OSError, ValueError) as exc:
        result.update(action="error", reason=f"download: {exc}"[:200])
    finally:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass

    _write_json(release_dir / "sync.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="把 GitHub 上已发布的客户端同步到 VPS 下载闸门")
    parser.add_argument("--release-dir", default=os.getenv("RELEASE_GATE_DIR",
                        "/var/lib/stratum-gate/releases"))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--status", action="store_true", help="只打印上次同步结果")
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(read_manifest(args.release_dir), ensure_ascii=False, indent=2))
        try:
            print((Path(args.release_dir) / "sync.json").read_text(encoding="utf-8").strip())
        except OSError:
            print("{}")
        return 0

    result = sync(args.release_dir, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["action"] != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
