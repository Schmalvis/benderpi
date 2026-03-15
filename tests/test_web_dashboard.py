"""Tests for dashboard API."""
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


def _mock_generate_dict():
    return {
        "health": {"errors_7d": 0, "alert_count": 0},
        "performance": {
            "stt_record_ms": 120.5,
            "stt_transcribe_ms": 340.0,
            "tts_generate_ms": 250.0,
            "ai_api_call_ms": None,
            "audio_play_ms": 80.0,
            "response_total_ms": 700.0,
        },
        "usage": {
            "sessions": 5,
            "turns": 20,
            "local": 18,
            "api": 2,
            "errors": 0,
            "local_pct": 90,
            "top_intents": {"GREETING": 5, "WEATHER": 3},
        },
        "alerts": [],
        "recent_errors": [],
        "git_log": "abc1234 Test commit",
    }


def test_status_returns_structured_data():
    client = get_client()
    with patch("generate_status.generate_dict", _mock_generate_dict):
        resp = client.get("/api/status", headers={"X-Bender-Pin": PIN})
    assert resp.status_code == 200
    data = resp.json()
    assert "health" in data
    assert "performance" in data
    assert "usage" in data
    assert "alerts" in data


def test_status_requires_pin():
    client = get_client()
    resp = client.get("/api/status")
    assert resp.status_code == 401


def test_status_refresh_returns_structured_data():
    client = get_client()
    with patch("generate_status.generate_dict", _mock_generate_dict), \
         patch("generate_status.generate", return_value=None):
        resp = client.post("/api/status/refresh", headers={"X-Bender-Pin": PIN})
    assert resp.status_code == 200
    data = resp.json()
    assert "health" in data
    assert "performance" in data


def test_generate_dict_returns_dict():
    from generate_status import generate_dict
    # Patch out filesystem/subprocess dependencies
    with patch("generate_status._load_metrics", return_value=[]), \
         patch("generate_status.run_checks", return_value=[]), \
         patch("generate_status._recent_errors", return_value=[]), \
         patch("generate_status._recent_git_log", return_value="abc test"):
        result = generate_dict()
    assert isinstance(result, dict)
    assert "health" in result
    assert "performance" in result
    assert "usage" in result
    assert "alerts" in result
    assert "recent_errors" in result
    assert "git_log" in result


def test_generate_dict_performance_keys():
    from generate_status import generate_dict
    with patch("generate_status._load_metrics", return_value=[]), \
         patch("generate_status.run_checks", return_value=[]), \
         patch("generate_status._recent_errors", return_value=[]), \
         patch("generate_status._recent_git_log", return_value=""):
        result = generate_dict()
    perf = result["performance"]
    for key in ("stt_record_ms", "stt_transcribe_ms", "tts_generate_ms",
                "ai_api_call_ms", "audio_play_ms", "response_total_ms"):
        assert key in perf
        assert perf[key] is None  # no data → None


def test_generate_dict_none_when_no_timers():
    from generate_status import generate_dict
    with patch("generate_status._load_metrics", return_value=[]), \
         patch("generate_status.run_checks", return_value=[]), \
         patch("generate_status._recent_errors", return_value=[]), \
         patch("generate_status._recent_git_log", return_value=""):
        result = generate_dict()
    assert result["performance"]["stt_record_ms"] is None


def test_generate_dict_computes_avg_timer():
    from generate_status import generate_dict
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    events = [
        {"type": "timer", "name": "stt_record", "duration_ms": 100.0, "ts": ts},
        {"type": "timer", "name": "stt_record", "duration_ms": 200.0, "ts": ts},
    ]
    with patch("generate_status._load_metrics", return_value=events), \
         patch("generate_status.run_checks", return_value=[]), \
         patch("generate_status._recent_errors", return_value=[]), \
         patch("generate_status._recent_git_log", return_value=""):
        result = generate_dict()
    assert result["performance"]["stt_record_ms"] == 150.0
