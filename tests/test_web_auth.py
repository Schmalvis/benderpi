"""Tests for web UI PIN authentication."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

def test_valid_pin_passes(monkeypatch):
    monkeypatch.setenv("BENDER_WEB_PIN", "1234")
    from web.auth import verify_pin
    assert verify_pin("1234") is True

def test_invalid_pin_fails(monkeypatch):
    monkeypatch.setenv("BENDER_WEB_PIN", "1234")
    from web.auth import verify_pin
    assert verify_pin("wrong") is False

def test_default_pin(monkeypatch):
    monkeypatch.delenv("BENDER_WEB_PIN", raising=False)
    from web.auth import verify_pin
    assert verify_pin("2904") is True

def test_api_rejects_without_pin():
    from fastapi.testclient import TestClient
    from web.app import app
    resp = TestClient(app).get("/api/actions/service-status")
    assert resp.status_code == 401

def test_api_accepts_valid_pin(monkeypatch):
    monkeypatch.setenv("BENDER_WEB_PIN", "9999")
    from fastapi.testclient import TestClient
    from web.app import app
    resp = TestClient(app).get(
        "/api/actions/service-status",
        headers={"X-Bender-Pin": "9999"},
    )
    assert resp.status_code != 401
