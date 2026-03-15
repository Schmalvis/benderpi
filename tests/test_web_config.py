"""Tests for config and action API endpoints."""
import json
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

PIN = "testpin"


def get_client():
    os.environ["BENDER_WEB_PIN"] = PIN
    import importlib
    import web.app
    importlib.reload(web.app)
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def headers():
    return {"X-Bender-Pin": PIN}


# ── Config endpoints ──────────────────────────────


def test_config_get(tmp_path):
    client = get_client()
    cfg_data = {"whisper_model": "tiny.en", "speech_rate": 1.0}
    cfg_file = tmp_path / "bender_config.json"
    cfg_file.write_text(json.dumps(cfg_data))
    with patch("web.app._CONFIG_PATH", str(cfg_file)):
        resp = client.get("/api/config", headers=headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["whisper_model"] == "tiny.en"
    assert data["speech_rate"] == 1.0


def test_config_put_merges(tmp_path):
    client = get_client()
    cfg_file = tmp_path / "bender_config.json"
    cfg_file.write_text(json.dumps({"whisper_model": "tiny.en", "speech_rate": 1.0}))
    with patch("web.app._CONFIG_PATH", str(cfg_file)):
        resp = client.put(
            "/api/config",
            json={"speech_rate": 0.8},
            headers=headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["config"]["speech_rate"] == 0.8
    assert data["config"]["whisper_model"] == "tiny.en"  # preserved


def test_config_requires_pin():
    client = get_client()
    resp = client.get("/api/config")
    assert resp.status_code == 401


def test_config_watchdog_get(tmp_path):
    client = get_client()
    wd_file = tmp_path / "watchdog_config.json"
    wd_file.write_text(json.dumps({"error_rate_threshold": 0.05}))
    with patch("web.app._WATCHDOG_CONFIG_PATH", str(wd_file)):
        resp = client.get("/api/config/watchdog", headers=headers())
    assert resp.status_code == 200
    assert resp.json()["error_rate_threshold"] == 0.05


def test_config_watchdog_put_merges(tmp_path):
    client = get_client()
    wd_file = tmp_path / "watchdog_config.json"
    wd_file.write_text(json.dumps({"error_rate_threshold": 0.05, "lookback_hours": 168}))
    with patch("web.app._WATCHDOG_CONFIG_PATH", str(wd_file)):
        resp = client.put(
            "/api/config/watchdog",
            json={"lookback_hours": 48},
            headers=headers(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["config"]["lookback_hours"] == 48
    assert data["config"]["error_rate_threshold"] == 0.05


# ── Action endpoints ──────────────────────────────


def test_service_status_dev_mode():
    """On non-Linux (Windows dev), returns graceful fallback."""
    client = get_client()
    with patch("web.app._IS_LINUX", False):
        resp = client.get("/api/actions/service-status", headers=headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is False
    assert "not on Pi" in data["uptime"]


def test_restart_dev_mode():
    client = get_client()
    with patch("web.app._IS_LINUX", False):
        resp = client.post("/api/actions/restart", json={}, headers=headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_refresh_briefings_dev_mode():
    client = get_client()
    with patch("web.app._IS_LINUX", False):
        resp = client.post("/api/actions/refresh-briefings", json={}, headers=headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_toggle_mode_puppet_only():
    client = get_client()
    with patch("web.app._IS_LINUX", False):
        resp = client.post(
            "/api/actions/toggle-mode",
            json={"mode": "puppet_only"},
            headers=headers(),
        )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "puppet_only"


def test_toggle_mode_converse():
    client = get_client()
    with patch("web.app._IS_LINUX", False):
        resp = client.post(
            "/api/actions/toggle-mode",
            json={"mode": "converse"},
            headers=headers(),
        )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "converse"


def test_toggle_mode_invalid():
    client = get_client()
    resp = client.post(
        "/api/actions/toggle-mode",
        json={"mode": "invalid"},
        headers=headers(),
    )
    assert resp.status_code == 400


def test_generate_status_action():
    client = get_client()
    with patch("generate_status.generate", return_value=None):
        resp = client.post("/api/actions/generate-status", json={}, headers=headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_actions_require_pin():
    client = get_client()
    assert client.post("/api/actions/restart", json={}).status_code == 401
    assert client.post("/api/actions/toggle-mode", json={"mode": "converse"}).status_code == 401
