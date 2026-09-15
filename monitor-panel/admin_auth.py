"""Authentication, secure-cookie and login-throttling helpers."""

import os
import threading
import time

from flask import request
from flask.sessions import SecureCookieSessionInterface

LOGIN_FAILURES = {}
LOGIN_FAILURES_LOCK = threading.Lock()
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_FAILURES = 5
LOGIN_COOLDOWN_SECONDS = 900


def trusted_https_request():
    return request.is_secure or (request.remote_addr in {"127.0.0.1", "::1"} and
        request.headers.get("X-Forwarded-Proto", "").lower() == "https")


class RequestAwareSessionInterface(SecureCookieSessionInterface):
    def get_cookie_secure(self, flask_app):
        return trusted_https_request()


def tailscale_identity():
    if os.getenv("TAILSCALE_AUTO_LOGIN", "1").lower() not in {"1", "true", "yes", "on"}:
        return ""
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        return ""
    login_name = request.headers.get("Tailscale-User-Login", "").strip().lower()
    if not login_name:
        return ""
    allowed = {value.strip().lower() for value in os.getenv("TAILSCALE_ALLOWED_USERS", "").split(",") if value.strip()}
    return login_name if not allowed or login_name in allowed else ""


def login_attempt_key():
    return request.remote_addr or "unknown"


def login_retry_after(key, now=None):
    now = float(now if now is not None else time.time())
    with LOGIN_FAILURES_LOCK:
        row = LOGIN_FAILURES.get(key, {"attempts": [], "locked_until": 0})
        if float(row.get("locked_until", 0)) > now:
            return max(1, int(float(row["locked_until"]) - now))
        row["attempts"] = [stamp for stamp in row.get("attempts", []) if now - stamp <= LOGIN_WINDOW_SECONDS]
        row["locked_until"] = 0
        LOGIN_FAILURES[key] = row
        return 0


def record_login_failure(key, now=None):
    now = float(now if now is not None else time.time())
    with LOGIN_FAILURES_LOCK:
        row = LOGIN_FAILURES.setdefault(key, {"attempts": [], "locked_until": 0})
        row["attempts"] = [stamp for stamp in row.get("attempts", []) if now - stamp <= LOGIN_WINDOW_SECONDS]
        row["attempts"].append(now)
        if len(row["attempts"]) >= LOGIN_MAX_FAILURES:
            row["locked_until"] = now + LOGIN_COOLDOWN_SECONDS
