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
    from web.auth import issue_token
    return {"X-Bender-Token": issue_token()}


# ── Config endpoints ──────────────────────────────


def test_config_get(tmp_path):
    client = get_client()
    cfg_data = {"whisper_model": "tiny.en", "speech_rate": 1.0}
    cfg_file = tmp_path / "bender_config.json"
    cfg_file.write_text(json.dumps(cfg_data))
    with patch("web.routes.config._CONFIG_PATH", str(cfg_file)):
        resp = client.get("/api/config", headers=headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["whisper_model"] == "tiny.en"
    assert data["speech_rate"] == 1.0


def test_config_put_merges(tmp_path):
    client = get_client()
    cfg_file = tmp_path / "bender_config.json"
    cfg_file.write_text(json.dumps({"whisper_model": "tiny.en", "speech_rate": 1.0}))
    with patch("web.routes.config._CONFIG_PATH", str(cfg_file)):
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
    with patch("web.routes.config._WATCHDOG_CONFIG_PATH", str(wd_file)):
        resp = client.get("/api/config/watchdog", headers=headers())
    assert resp.status_code == 200
    assert resp.json()["error_rate_threshold"] == 0.05


def test_config_watchdog_put_merges(tmp_path):
    client = get_client()
    wd_file = tmp_path / "watchdog_config.json"
    wd_file.write_text(json.dumps({"error_rate_threshold": 0.05, "lookback_hours": 168}))
    with patch("web.routes.config._WATCHDOG_CONFIG_PATH", str(wd_file)):
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
    with patch("web.routes.actions._IS_LINUX", False):
        resp = client.get("/api/actions/service-status", headers=headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["running"] is False
    assert "not on Pi" in data["uptime"]


def test_restart_dev_mode():
    client = get_client()
    with patch("web.routes.actions._IS_LINUX", False):
        resp = client.post("/api/actions/restart", json={}, headers=headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_refresh_briefings_dev_mode():
    """This route does not consult _IS_LINUX — it calls briefings.refresh_all()
    for real, which needs the Piper binary. Patching _IS_LINUX (as this test
    used to) changed nothing and the route 500'd on any box without Piper."""
    client = get_client()
    with patch("briefings.refresh_all", return_value=None) as mock_refresh:
        resp = client.post("/api/actions/refresh-briefings", json={}, headers=headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    mock_refresh.assert_called_once()


def test_toggle_mode_puppet_only():
    client = get_client()
    with patch("web.routes.actions._IS_LINUX", False):
        resp = client.post(
            "/api/actions/toggle-mode",
            json={"mode": "puppet_only"},
            headers=headers(),
        )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "puppet_only"


def test_toggle_mode_converse():
    client = get_client()
    with patch("web.routes.actions._IS_LINUX", False):
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


# ── A rejected save must be a 422 with field detail, not a 500 ───────────────
#
# A pydantic validator that raises ValueError leaves the exception OBJECT in
# each error's `ctx`. `e.errors(include_url=False)` keeps it, Starlette tries to
# JSON-encode the HTTPException detail, and the 422 turned into a **500** with
# no field detail at all. Every custom validator in config_schema.py was
# affected: oww_threshold, ha_url, oww_model_path, the presence bounds.
#
# This survived because the schema tests exercise the MODEL, never the HTTP
# layer, and pydantic's own TYPE errors carry no ctx -- so a bad type returned a
# correct 422 and only a bad VALUE broke. These tests go through the route.
# Found 2026-10-14 by sending presence_clear_mm below presence_max_mm.

import pytest


@pytest.mark.parametrize("body,needle", [
    ({"presence_max_mm": 1000, "presence_clear_mm": 900},
     "presence_clear_mm must be greater"),
    ({"presence_confirm_reads": 0},  "presence_confirm_reads must be 1-20"),
    ({"presence_signal_rate_mcps": 1.5}, "presence_signal_rate_mcps must be 0-1"),
    ({"presence_max_mm": 8190},      "presence distances must be 0-8000"),
    ({"oww_threshold": 5},           "oww_threshold must be between 0 and 1"),
    ({"ha_url": "http://evil.example.com"}, "ha_url must point at a LAN"),
    ({"piper_bin": "/bin/sh"},       "not editable via web UI"),
])
def test_a_bad_value_is_422_with_the_reason(tmp_path, body, needle):
    client = get_client()
    cfg_file = tmp_path / "bender_config.json"
    cfg_file.write_text(json.dumps({"speech_rate": 1.0}))
    with patch("web.routes.config._CONFIG_PATH", str(cfg_file)):
        resp = client.put("/api/config", json=body, headers=headers())
    assert resp.status_code == 422, f"got {resp.status_code}: {resp.text[:200]}"
    detail = resp.json()["detail"]
    assert any(needle in d["msg"] for d in detail), detail
    # api.js reads only `loc` and `msg`. A field validator names the field; a
    # whole-model validator's `loc` is legitimately empty.
    assert all("msg" in d and "loc" in d for d in detail), detail
    # `input` would echo the submitted body back, secrets included.
    assert all("input" not in d for d in detail), detail
    # And nothing was written.
    assert json.loads(cfg_file.read_text()) == {"speech_rate": 1.0}


def test_the_422_body_is_json_encodable(tmp_path):
    """The actual regression: a ValueError in `ctx` is not JSON serializable."""
    client = get_client()
    cfg_file = tmp_path / "bender_config.json"
    cfg_file.write_text(json.dumps({}))
    with patch("web.routes.config._CONFIG_PATH", str(cfg_file)):
        resp = client.put("/api/config", json={"oww_threshold": 5},
                          headers=headers())
    assert resp.status_code == 422
    json.dumps(resp.json())          # would raise before the fix
    assert "ctx" not in resp.json()["detail"][0]


def test_a_bad_type_is_still_422(tmp_path):
    """Pydantic's own errors carry no ctx. They worked, and must keep working."""
    client = get_client()
    cfg_file = tmp_path / "bender_config.json"
    cfg_file.write_text(json.dumps({}))
    with patch("web.routes.config._CONFIG_PATH", str(cfg_file)):
        resp = client.put("/api/config", json={"speech_rate": "fast"},
                          headers=headers())
    assert resp.status_code == 422


def test_the_watchdog_route_rejects_the_same_way(tmp_path):
    client = get_client()
    wd_file = tmp_path / "watchdog_config.json"
    wd_file.write_text(json.dumps({"error_rate_threshold": 0.05}))
    with patch("web.routes.config._WATCHDOG_CONFIG_PATH", str(wd_file)):
        resp = client.put("/api/config/watchdog",
                          json={"error_rate_threshold": -1}, headers=headers())
    assert resp.status_code == 422
    json.dumps(resp.json())


def test_a_secret_in_the_body_is_not_echoed_back(tmp_path):
    """The schema strips secrets on the way in. The error body must not undo it."""
    client = get_client()
    cfg_file = tmp_path / "bender_config.json"
    cfg_file.write_text(json.dumps({}))
    body = {
        "ha_token": "sk-should-never-come-back",
        "anthropic_api_key": "sk-ant-should-never-come-back",
        "presence_max_mm": 1000,
        "presence_clear_mm": 900,        # trips the whole-model validator
    }
    with patch("web.routes.config._CONFIG_PATH", str(cfg_file)):
        resp = client.put("/api/config", json=body, headers=headers())
    assert resp.status_code == 422
    assert "should-never-come-back" not in resp.text, resp.text[:300]
