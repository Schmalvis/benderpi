"""Tests for web UI token authentication + rate limiting + fail-closed PIN."""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

PIN = "9999"


def _fresh_auth(monkeypatch, pin=PIN):
    """Reload web.auth so its per-boot signing key + throttle state reset."""
    if pin is None:
        monkeypatch.delenv("BENDER_WEB_PIN", raising=False)
    else:
        monkeypatch.setenv("BENDER_WEB_PIN", pin)
    import web.auth
    importlib.reload(web.auth)
    return web.auth


def _client(monkeypatch, pin=PIN):
    monkeypatch.setenv("BENDER_WEB_PIN", pin)
    import web.auth
    importlib.reload(web.auth)
    import web.app
    importlib.reload(web.app)
    from fastapi.testclient import TestClient
    return TestClient(web.app.app)


# ---------------------------------------------------------------------------
# Fail-closed PIN
# ---------------------------------------------------------------------------

def test_get_pin_raises_when_unset(monkeypatch):
    auth = _fresh_auth(monkeypatch, pin=None)
    with pytest.raises(auth.PinNotConfigured):
        auth.get_pin()


@pytest.mark.parametrize("bad", ["", "2904", "CHANGE_ME"])
def test_get_pin_raises_on_placeholder(monkeypatch, bad):
    auth = _fresh_auth(monkeypatch, pin=bad)
    with pytest.raises(auth.PinNotConfigured):
        auth.get_pin()


def test_app_import_fails_without_pin(monkeypatch):
    # Import web.app cleanly first (with a valid PIN), so the failure below is
    # unambiguously from the fail-closed reload — not a stale prior import.
    monkeypatch.setenv("BENDER_WEB_PIN", PIN)
    import web.auth
    importlib.reload(web.auth)
    import web.app
    importlib.reload(web.app)
    # Now drop the PIN and reload: the fail-closed guard must raise.
    monkeypatch.delenv("BENDER_WEB_PIN", raising=False)
    with pytest.raises(web.auth.PinNotConfigured):
        importlib.reload(web.app)


# ---------------------------------------------------------------------------
# Token issue / verify
# ---------------------------------------------------------------------------

def test_token_roundtrip(monkeypatch):
    auth = _fresh_auth(monkeypatch)
    token = auth.issue_token()
    assert auth.verify_token(token) is True


def test_tampered_token_rejected(monkeypatch):
    auth = _fresh_auth(monkeypatch)
    token = auth.issue_token()
    scope, exp, sig = token.split(".")
    tampered = f"{scope}.{int(exp) + 999999}.{sig}"  # extend expiry, sig now invalid
    assert auth.verify_token(tampered) is False


def test_expired_token_rejected(monkeypatch):
    auth = _fresh_auth(monkeypatch)
    monkeypatch.setattr(auth, "_AUTH_TOKEN_TTL_S", -1)
    token = auth.issue_token()
    assert auth.verify_token(token) is False


def test_stream_token_rejected_as_auth_token(monkeypatch):
    auth = _fresh_auth(monkeypatch)
    stok = auth.issue_stream_token()
    assert auth.verify_stream_token(stok) is True
    # A stream token must NOT satisfy the regular API (scope mismatch).
    assert auth.verify_token(stok) is False
    # ...and vice versa.
    assert auth.verify_stream_token(auth.issue_token()) is False


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_wrong_pin_then_lockout(monkeypatch):
    from fastapi import HTTPException
    auth = _fresh_auth(monkeypatch)
    # First few wrong PINs → plain 401.
    for _ in range(auth._FREE_ATTEMPTS):
        with pytest.raises(HTTPException) as exc:
            auth.attempt_login("wrong")
        assert exc.value.status_code == 401
    # Next failure trips the exponential backoff → 429.
    with pytest.raises(HTTPException) as exc:
        auth.attempt_login("wrong")
    assert exc.value.status_code == 429


def test_success_clears_failures(monkeypatch):
    from fastapi import HTTPException
    auth = _fresh_auth(monkeypatch)
    with pytest.raises(HTTPException):
        auth.attempt_login("wrong")
    token = auth.attempt_login(PIN)  # correct PIN while not locked out
    assert auth.verify_token(token) is True
    assert auth._failed_attempts == 0


# ---------------------------------------------------------------------------
# Endpoint integration
# ---------------------------------------------------------------------------

def test_login_endpoint_returns_token(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/api/auth/login", json={"pin": PIN})
    assert resp.status_code == 200
    assert "token" in resp.json()


def test_login_endpoint_wrong_pin_401(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/api/auth/login", json={"pin": "nope"})
    assert resp.status_code == 401


def test_api_rejects_without_token(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/api/actions/service-status")
    assert resp.status_code == 401


def test_api_accepts_valid_token(monkeypatch):
    client = _client(monkeypatch)
    token = client.post("/api/auth/login", json={"pin": PIN}).json()["token"]
    resp = client.get(
        "/api/actions/service-status",
        headers={"X-Bender-Token": token},
    )
    assert resp.status_code != 401


def test_stream_token_endpoint_requires_auth(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/api/auth/stream-token").status_code == 401
    token = client.post("/api/auth/login", json={"pin": PIN}).json()["token"]
    resp = client.get("/api/auth/stream-token", headers={"X-Bender-Token": token})
    assert resp.status_code == 200
    assert "token" in resp.json()


def test_security_headers_present(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/api/auth/login", json={"pin": PIN})
    assert "content-security-policy" in {k.lower() for k in resp.headers}
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
