"""Token-based authentication for BenderPi web UI.

Design (single-user LAN device, session-less, dependency-free):

- The PIN is verified once via ``POST /api/auth/login`` using
  ``hmac.compare_digest`` (constant-time). On success the client receives an
  HMAC-signed *auth token* with a ~12h expiry and stores it (not the PIN) in
  sessionStorage. Every subsequent request carries ``X-Bender-Token``.

- Two endpoints genuinely cannot set a header — the MJPEG ``<img src>`` camera
  stream and the mic websocket. They accept a short-lived (~60s) *stream token*
  as a query param, minted by ``GET /api/auth/stream-token``. A stream token
  that leaks into access logs / browser history is worthless within a minute,
  and it is scoped so it cannot be replayed against the regular JSON API.

- The HMAC signing key is a random secret generated at process boot. It is
  *not* derived from the PIN, so tokens carry no PIN material. The trade-off is
  that restarting ``bender-web`` invalidates existing tokens (the browser tab
  re-logs-in). That is acceptable for one user on a LAN.

- Login is rate-limited with a single in-memory global backoff (no per-IP
  bookkeeping: one user, no proxy). After a handful of free attempts, failures
  incur an exponential lockout capped at 60s, returned as HTTP 429.

- ``get_pin()`` fails closed: it raises if ``BENDER_WEB_PIN`` is unset, empty,
  or still one of the shipped placeholder values.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request, WebSocket

# ---------------------------------------------------------------------------
# Configuration / fail-closed PIN
# ---------------------------------------------------------------------------

# Placeholder values that must never be accepted as a real PIN. The service
# refuses to authenticate anyone while the PIN is still one of these, forcing
# the operator to set a genuine secret in the environment.
_PLACEHOLDER_PINS = frozenset({"", "2904", "CHANGE_ME"})


class PinNotConfigured(RuntimeError):
    """Raised when BENDER_WEB_PIN is missing or still a placeholder."""


def get_pin() -> str:
    """Return the configured PIN, or raise ``PinNotConfigured`` (fail closed)."""
    pin = os.environ.get("BENDER_WEB_PIN", "")
    if pin in _PLACEHOLDER_PINS:
        raise PinNotConfigured(
            "BENDER_WEB_PIN is unset or still a placeholder — set a real PIN in "
            "the environment before starting bender-web."
        )
    return pin


def require_configured_pin() -> None:
    """Startup guard: raise if the PIN is not properly configured."""
    get_pin()


# ---------------------------------------------------------------------------
# Token signing
# ---------------------------------------------------------------------------

# Random per-boot secret. Regenerated every process start, so a restart
# invalidates all outstanding tokens (graceful re-login on the client).
_SIGNING_KEY = secrets.token_bytes(32)

_AUTH_TOKEN_TTL_S = 12 * 60 * 60   # ~12 hours for the main session token
_STREAM_TOKEN_TTL_S = 60           # ~60 seconds for URL-embedded stream tokens

SCOPE_AUTH = "auth"
SCOPE_STREAM = "stream"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _issue(scope: str, ttl_s: int) -> str:
    """Mint a signed token: ``<scope>.<exp>.<sig>`` (all url-safe base64/ascii)."""
    exp = int(time.time()) + ttl_s
    payload = f"{scope}.{exp}".encode("ascii")
    sig = hmac.new(_SIGNING_KEY, payload, hashlib.sha256).digest()
    return f"{scope}.{exp}.{_b64e(sig)}"


def _verify(token: str, expected_scope: str) -> bool:
    """Constant-time verify a token's signature, scope, and expiry."""
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    scope, exp_str, sig_b64 = parts
    if scope != expected_scope:
        return False
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    payload = f"{scope}.{exp_str}".encode("ascii")
    expected_sig = hmac.new(_SIGNING_KEY, payload, hashlib.sha256).digest()
    try:
        provided_sig = _b64d(sig_b64)
    except (ValueError, base64.binascii.Error):
        return False
    if not hmac.compare_digest(expected_sig, provided_sig):
        return False
    # Signature valid — now (and only now) trust the expiry.
    return time.time() < exp


def issue_token() -> str:
    """Mint a session auth token (~12h)."""
    return _issue(SCOPE_AUTH, _AUTH_TOKEN_TTL_S)


def verify_token(token: str) -> bool:
    return _verify(token, SCOPE_AUTH)


def issue_stream_token() -> str:
    """Mint a short-lived stream token (~60s) for URL-embedded auth."""
    return _issue(SCOPE_STREAM, _STREAM_TOKEN_TTL_S)


def verify_stream_token(token: str) -> bool:
    return _verify(token, SCOPE_STREAM)


# ---------------------------------------------------------------------------
# Login rate limiting (single global in-memory backoff)
# ---------------------------------------------------------------------------

_FREE_ATTEMPTS = 5          # failures allowed before lockout kicks in
_LOCKOUT_BASE_S = 2         # base backoff after the free attempts are used
_LOCKOUT_MAX_S = 60         # cap on the exponential backoff

_failed_attempts = 0
_locked_until = 0.0


def _lockout_remaining() -> float:
    return max(0.0, _locked_until - time.monotonic())


def check_login_allowed() -> None:
    """Raise HTTP 429 if login is currently locked out."""
    remaining = _lockout_remaining()
    if remaining > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Try again in {int(remaining) + 1}s.",
            headers={"Retry-After": str(int(remaining) + 1)},
        )


def record_login_failure() -> None:
    """Register a failed login and (re)arm the exponential lockout."""
    global _failed_attempts, _locked_until
    _failed_attempts += 1
    over = _failed_attempts - _FREE_ATTEMPTS
    if over > 0:
        backoff = min(_LOCKOUT_MAX_S, _LOCKOUT_BASE_S * (2 ** (over - 1)))
        _locked_until = time.monotonic() + backoff


def record_login_success() -> None:
    """Clear failure state after a successful login."""
    global _failed_attempts, _locked_until
    _failed_attempts = 0
    _locked_until = 0.0


def attempt_login(pin: str) -> str:
    """Verify a PIN under the rate limiter; return an auth token or raise.

    Raises 429 while locked out, 401 on a wrong PIN, and 503 if the service
    has no PIN configured (fail-closed).
    """
    check_login_allowed()
    try:
        configured = get_pin()
    except PinNotConfigured:
        raise HTTPException(status_code=503, detail="Web UI PIN not configured")
    if hmac.compare_digest(pin, configured):
        record_login_success()
        return issue_token()
    record_login_failure()
    # Surface a lockout immediately if this failure triggered one.
    check_login_allowed()
    raise HTTPException(status_code=401, detail="Invalid PIN")


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def require_token(request: Request):
    """FastAPI dependency — validates the ``X-Bender-Token`` header."""
    token = request.headers.get("X-Bender-Token", "")
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def require_stream_token_ws(websocket: WebSocket) -> bool:
    """Validate a websocket's ``token`` query param; close + return False if bad.

    Validated only at connection open — never per-frame — so an expiring token
    cannot kill a live stream.
    """
    token = websocket.query_params.get("token", "")
    if not verify_stream_token(token):
        await websocket.close(code=4001)
        return False
    return True
