"""Security helpers: CSRF protection, AI rate limiting and secret redaction."""
import hmac
import logging
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import abort, current_app, request, session

# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
CSRF_SESSION_KEY = "_csrf_token"


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def verify_csrf() -> None:
    """Reject state-changing requests that lack the session's CSRF token."""
    if not current_app.config.get("CSRF_ENABLED", True):
        return
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    expected = session.get(CSRF_SESSION_KEY)
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected or not sent or not hmac.compare_digest(str(expected), str(sent)):
        abort(400, description="Your form session expired. Please reload the page and try again.")


# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory sliding window, per user)
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self) -> None:
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def ai_rate_limited(view):
    """Limit how often a single user can trigger AI API calls."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        from flask import g

        limiter: RateLimiter = current_app.extensions["ai_rate_limiter"]
        limit = int(current_app.config.get("AI_RATE_LIMIT_PER_MINUTE", 10))
        user = getattr(g, "user", None)
        key = f"user:{user['id']}" if user else f"ip:{request.remote_addr}"
        if not limiter.allow(key, limit):
            abort(429, description=(
                f"Too many AI requests. The limit is {limit} per minute; "
                "please wait a moment and try again."
            ))
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Secret redaction for logs
# ---------------------------------------------------------------------------
# Anthropic (sk-ant-...), Google (AIza...) and Groq (gsk_...) API keys.
_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}|AIza[0-9A-Za-z_\-]{20,}|gsk_[A-Za-z0-9]{16,}")
_secret_values: set = set()
_factory_installed = False


def redact(text: str) -> str:
    if not text:
        return text
    for value in _secret_values:
        if value and value in text:
            text = text.replace(value, "[REDACTED]")
    return _KEY_PATTERN.sub("[REDACTED]", text)


def install_log_redaction(*secret_values: str) -> None:
    """Make every log record pass through `redact` before it is emitted."""
    global _factory_installed
    for value in secret_values:
        if value and len(value) >= 8:
            _secret_values.add(value)
    if _factory_installed:
        return
    original_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = original_factory(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed log call
            return record
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return record

    logging.setLogRecordFactory(factory)
    _factory_installed = True
