#!/usr/bin/env python3
"""Public download gate for the Windows client installer.

The installer is already published on GitHub Releases, so this service is not about
confidentiality — it exists so a Chinese watch computer can fetch the file from the VPS
when GitHub is unreachable, without turning the VPS into an unlimited public file host.

The gate is **closed by default** and opens in one of two ways:

* ``duration`` — open for N minutes, then close automatically.
* ``count``    — open until N downloads have been served, then close.

State lives in a small JSON file so the panel and this service stay decoupled: the panel
writes "open for 30 minutes", the service reads it per request and closes itself when the
window or budget is exhausted.
"""

import argparse
import hashlib
import hmac
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

MODE_CLOSED = "closed"
MODE_DURATION = "duration"
MODE_COUNT = "count"
DEFAULT_MAX_MINUTES = 24 * 60
DEFAULT_MAX_DOWNLOADS = 1000

DEFAULT_STATE = {
    "mode": MODE_CLOSED,
    "opened_at": 0,
    "expires_at": 0,
    "remaining": 0,
    "served_total": 0,
    "served_last_at": 0,
    "updated_at": 0,
}


@contextmanager
def file_lock(path):
    """Serialize the read/change/write cycle so two downloads cannot over-count."""
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":          # keeps the module importable for tests on Windows
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_state(path):
    """Read gate state, repairing anything malformed into the safe 'closed' default."""
    state = dict(DEFAULT_STATE)
    try:
        stored = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if not isinstance(stored, dict):
        return state
    if stored.get("mode") in (MODE_CLOSED, MODE_DURATION, MODE_COUNT):
        state["mode"] = stored["mode"]
    for key in ("opened_at", "expires_at", "remaining", "served_total", "served_last_at", "updated_at"):
        try:
            state[key] = max(0, int(stored.get(key, 0) or 0))
        except (TypeError, ValueError):
            state[key] = 0
    # A window without a usable expiry is not a window; treat it as closed rather than
    # leaving a mode that can never legitimately open.
    if state["mode"] == MODE_DURATION and state["expires_at"] <= 0:
        state["mode"] = MODE_CLOSED
    return state


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def open_for_minutes(minutes, now=None):
    minutes = max(1, min(DEFAULT_MAX_MINUTES, int(minutes)))
    now = int(now if now is not None else time.time())
    return {"mode": MODE_DURATION, "opened_at": now, "expires_at": now + minutes * 60,
            "remaining": 0, "served_total": 0, "served_last_at": 0, "updated_at": now}


def open_for_downloads(count, now=None):
    count = max(1, min(DEFAULT_MAX_DOWNLOADS, int(count)))
    now = int(now if now is not None else time.time())
    return {"mode": MODE_COUNT, "opened_at": now, "expires_at": 0,
            "remaining": count, "served_total": 0, "served_last_at": 0, "updated_at": now}


def close(now=None, previous=None):
    """Close the gate but keep the served counter: "closed after 2 downloads" is useful."""
    now = int(now if now is not None else time.time())
    state = dict(DEFAULT_STATE)
    if isinstance(previous, dict):
        state["served_total"] = max(0, int(previous.get("served_total", 0) or 0))
        state["opened_at"] = max(0, int(previous.get("opened_at", 0) or 0))
    state["updated_at"] = now
    return state


def status(state, now=None):
    """Human-facing view. Expired windows and exhausted budgets read as closed."""
    now = int(now if now is not None else time.time())
    mode = state.get("mode", MODE_CLOSED)
    if mode == MODE_DURATION:
        seconds_left = max(0, int(state.get("expires_at", 0)) - now)
        if seconds_left <= 0:
            return {"open": False, "mode": mode, "reason": "expired", "remaining": 0,
                    "seconds_left": 0, "served_total": int(state.get("served_total", 0))}
        return {"open": True, "mode": mode, "reason": "open", "remaining": 0,
                "seconds_left": seconds_left, "served_total": int(state.get("served_total", 0))}
    if mode == MODE_COUNT:
        remaining = int(state.get("remaining", 0))
        return {"open": remaining > 0, "mode": mode, "reason": "open" if remaining > 0 else "exhausted",
                "remaining": remaining, "seconds_left": 0, "served_total": int(state.get("served_total", 0))}
    return {"open": False, "mode": MODE_CLOSED, "reason": "closed", "remaining": 0,
            "seconds_left": 0, "served_total": int(state.get("served_total", 0))}


def consume(state, now=None):
    """Charge one download. Returns (allowed, reason, updated_state).

    Duration windows close themselves once elapsed; a count budget cannot go negative.
    """
    now = int(now if now is not None else time.time())
    state = dict(state)
    mode = state.get("mode", MODE_CLOSED)
    if mode == MODE_DURATION:
        if now >= int(state.get("expires_at", 0)):
            return False, "expired", close(now, state)
        state["served_total"] = int(state.get("served_total", 0)) + 1
        state["served_last_at"] = now
        state["updated_at"] = now
        return True, "open", state
    if mode == MODE_COUNT:
        remaining = int(state.get("remaining", 0))
        if remaining <= 0:
            # Report the real cause once, then settle into the closed mode.
            return False, "exhausted", close(now, state)
        state["remaining"] = remaining - 1
        state["served_total"] = int(state.get("served_total", 0)) + 1
        state["served_last_at"] = now
        state["updated_at"] = now
        # The budget reaching zero is reported as "exhausted" by status(); the mode stays
        # count so an operator can tell "预算用尽" from "从未打开" before it settles closed.
        return True, "open", state
    return False, "closed", state


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(release_dir):
    """Read and validate the published manifest; returns (manifest, error)."""
    release_dir = Path(release_dir)
    try:
        manifest = json.loads((release_dir / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "manifest is missing or unreadable"
    if not isinstance(manifest, dict):
        return None, "manifest is not an object"
    name = str(manifest.get("file", ""))
    version = str(manifest.get("version", ""))
    expected = str(manifest.get("sha256", "")).lower()
    if not name or Path(name).name != name or not version or len(expected) != 64:
        return None, "manifest fields are invalid"
    target = release_dir / name
    if not target.is_file():
        return None, "release file is missing"
    return {"version": version, "file": name, "sha256": expected,
            "size": target.stat().st_size}, ""


def read_mirror_state(release_dir):
    """Last sync summary written by release_mirror.py; empty when it never ran."""
    try:
        payload = json.loads((Path(release_dir) / "sync.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):        return {}
    return payload if isinstance(payload, dict) else {}


def create_app(gate_state_path, release_dir, admin_token=""):
    app = Flask(__name__)
    app.config["GATE_STATE"] = Path(gate_state_path)
    app.config["RELEASE_DIR"] = Path(release_dir)
    app.config["ADMIN_TOKEN"] = admin_token

    def serve_current(name=None):
        """Serve the published installer, charging one download against the gate."""
        manifest, error = load_manifest(app.config["RELEASE_DIR"])
        if error:
            return jsonify({"error": error}), 404
        if name is not None and not hmac.compare_digest(name, manifest["file"]):
            return jsonify({"error": "file is not published"}), 404

        with file_lock(app.config["GATE_STATE"]):
            current = load_state(app.config["GATE_STATE"])
            allowed, reason, updated = consume(current)
            save_state(app.config["GATE_STATE"], updated)
        if not allowed:
            return jsonify({"error": "download gate is not open", "reason": reason}), 410

        path = app.config["RELEASE_DIR"] / manifest["file"]
        response = send_file(path, mimetype="application/octet-stream",
                             as_attachment=True, download_name=manifest["file"],
                             conditional=False)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.route("/update/latest")
    def latest_route():
        """Bare URL that downloads whatever is currently published."""
        return serve_current()

    def resolve_setup(release_dir):
        """Locate the one-file setup tool: manifest hint first, then the newest bundle."""
        release_dir = Path(release_dir)
        try:
            payload = json.loads((release_dir / "latest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        name = str(payload.get("setup", "")) if isinstance(payload, dict) else ""
        if name and Path(name).name == name and (release_dir / name).is_file():
            return release_dir / name
        candidates = sorted(release_dir.glob("watch-pc-setup-*.bat"))
        return candidates[-1] if candidates else None

    @app.route("/update/setup")
    def setup_route():
        """The one-click setup tool, metered by the same gate as the installer.

        The operator downloads exactly one file, and that file is as much a release artifact
        as the installer, so it goes through the same budget instead of being freely
        downloadable.
        """
        path = resolve_setup(app.config["RELEASE_DIR"])
        if path is None:
            return jsonify({"error": "setup file is not published"}), 404

        with file_lock(app.config["GATE_STATE"]):
            current = load_state(app.config["GATE_STATE"])
            allowed, reason, updated = consume(current)
            save_state(app.config["GATE_STATE"], updated)
        if not allowed:
            return jsonify({"error": "download gate is not open", "reason": reason}), 410

        response = send_file(path, mimetype="application/octet-stream",
                             as_attachment=True, download_name=path.name,
                             conditional=False)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.route("/update/manifest")
    def manifest_route():
        manifest, error = load_manifest(app.config["RELEASE_DIR"])
        if error:
            return jsonify({"error": error}), 404
        # The manifest is intentionally not metered: a client must be able to learn the
        # expected hash even when the gate is closed for the binary itself.
        return jsonify(manifest)

    @app.route("/update/status")
    def status_route():
        payload = status(load_state(app.config["GATE_STATE"]))
        payload["mirror"] = read_mirror_state(app.config["RELEASE_DIR"])
        return jsonify(payload)

    @app.route("/update/<name>")
    def download_route(name):
        if Path(name).name != name:
            return jsonify({"error": "invalid name"}), 400
        return serve_current(name)

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="木林森中转客户端下载闸门")
    parser.add_argument("--state", default=os.getenv("RELEASE_GATE_STATE", "/var/lib/stratum-release-gate/gate.json"))
    parser.add_argument("--releases", default=os.getenv("RELEASE_GATE_DIR", "/var/lib/stratum-release-gate/releases"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("RELEASE_GATE_PORT", "12050")))
    parser.add_argument("--open-minutes", type=int, help="打开闸门并在 N 分钟后自动关闭")
    parser.add_argument("--open-downloads", type=int, help="打开闸门并允许 N 次下载后自动关闭")
    parser.add_argument("--close", action="store_true", help="立即关闭闸门")
    parser.add_argument("--status", action="store_true", help="只打印当前状态")
    args = parser.parse_args(argv)

    state_path = Path(args.state)
    with file_lock(state_path):
        if args.close:
            save_state(state_path, close(previous=load_state(state_path)))
        elif args.open_minutes:
            save_state(state_path, open_for_minutes(args.open_minutes))
        elif args.open_downloads:
            save_state(state_path, open_for_downloads(args.open_downloads))
        current = load_state(state_path)

    if args.status or args.close or args.open_minutes or args.open_downloads:
        print(json.dumps(status(current), ensure_ascii=False, indent=2))
        return 0

    app = create_app(state_path, args.releases, os.getenv("RELEASE_GATE_ADMIN_TOKEN", ""))
    app.run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
