"""Tests for the web Config editor validation schema.

These target scripts/web/routes/config_schema.py directly — it is pure pydantic
with no hardware imports, so it runs on dev/CI as well as on the Pi (unlike the
full-app tests in test_web_config.py which import leds -> board).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from pydantic import ValidationError  # noqa: E402
from web.routes.config_schema import (  # noqa: E402
    clean_bender_config,
    clean_watchdog_config,
)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _production_blob():
    with open(os.path.join(_BASE_DIR, "bender_config.json")) as f:
        return json.load(f)


# ── The critical UI-compatibility test: whole-blob round-trip ──────────────


def test_full_production_blob_round_trips():
    """The Svelte editor PUTs the ENTIRE config blob back on save. It must not
    422 — that would brick the admin panel on a headless device."""
    clean = clean_bender_config(_production_blob())
    # Unknown keys the schema does not model must survive.
    for k in ("vision_model", "vision_confidence_threshold", "vision_allowlist"):
        assert k in clean
    # Nested structures preserved untouched.
    assert clean["ai_routing"]["conversation"] == "local_first"
    assert clean["briefings_news_feeds"][0][0] == "UK"
    assert isinstance(clean["ha_exclude_entities"], list)
    assert clean["led_colour"] == [255, 120, 0]


def test_unknown_key_passes_through():
    out = clean_bender_config({"some_future_key": 123})
    assert out["some_future_key"] == 123


def test_valid_partial_update_round_trips():
    assert clean_bender_config({"speech_rate": 0.8}) == {"speech_rate": 0.8}


# ── Secrets stripped, not rejected (UI has an ha_token field) ──────────────


def test_secret_ha_token_stripped_not_rejected():
    out = clean_bender_config({"ha_token": "sekret", "speech_rate": 1.0})
    assert "ha_token" not in out
    assert out["speech_rate"] == 1.0


def test_secret_anthropic_key_stripped():
    out = clean_bender_config({"anthropic_api_key": "sk-abc"})
    assert "anthropic_api_key" not in out


# ── Path / URL injection rejected ─────────────────────────────────────────


def test_path_traversal_model_path_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"oww_model_path": "../../tmp/evil.onnx"})


def test_absolute_path_outside_models_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"oww_model_path": "/etc/passwd"})


def test_wrong_model_suffix_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"oww_model_path": "models/evil.txt"})


def test_valid_model_path_accepted():
    assert clean_bender_config(
        {"oww_model_path": "models/hey_jarvis.onnx"}
    ) == {"oww_model_path": "models/hey_jarvis.onnx"}


def test_forbidden_piper_bin_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"piper_bin": "/bin/sh"})


def test_forbidden_model_path_key_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"model_path": "/bin/sh"})


def test_external_ha_url_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"ha_url": "http://evil.example.com:8123"})


def test_non_http_ha_url_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"ha_url": "file:///etc/passwd"})


@pytest.mark.parametrize("url", [
    "http://localhost:11434",
    "http://127.0.0.1:8123",
    "http://192.168.68.125:8123",
    "http://homeassistant.local:8123",
    "http://172.16.0.4:8123",
])
def test_lan_urls_accepted(url):
    assert clean_bender_config({"ha_url": url})["ha_url"] == url


# ── Range / type validation ────────────────────────────────────────────────


def test_oww_threshold_out_of_range_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"oww_threshold": 2})


def test_oww_threshold_valid():
    assert clean_bender_config({"oww_threshold": 0.35})["oww_threshold"] == 0.35


def test_vad_aggressiveness_out_of_range_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"vad_aggressiveness": 9})


def test_negative_timeout_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"http_timeout_s": -1})


def test_invalid_ai_backend_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"ai_backend": "magic"})


def test_string_where_number_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"speech_rate": "fast"})


def test_led_brightness_accepts_0_255():
    # UI slider is 0-255; production value is 1.0. Both must pass.
    assert clean_bender_config({"led_brightness": 200})["led_brightness"] == 200
    assert clean_bender_config({"led_brightness": 1.0})["led_brightness"] == 1.0


def test_led_brightness_out_of_range_rejected():
    with pytest.raises(ValidationError):
        clean_bender_config({"led_brightness": 500})


# ── Watchdog schema ─────────────────────────────────────────────────────────


def test_watchdog_blob_round_trips():
    with open(os.path.join(_BASE_DIR, "watchdog_config.json")) as f:
        wd = json.load(f)
    clean = clean_watchdog_config(wd)
    assert clean["lookback_hours"] == 168
    assert clean["error_rate_threshold"] == 0.05


def test_watchdog_negative_rejected():
    with pytest.raises(ValidationError):
        clean_watchdog_config({"error_rate_threshold": -1})


def test_watchdog_partial_update():
    assert clean_watchdog_config({"lookback_hours": 48}) == {"lookback_hours": 48}


def test_watchdog_unknown_key_passes_through():
    assert clean_watchdog_config({"new_threshold": 5})["new_threshold"] == 5
