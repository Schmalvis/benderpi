"""Validation schemas for the web Config editor's PUT endpoints.

Why this exists
---------------
`PUT /api/config` used to blindly ``dict.update()`` whatever JSON arrived into
``bender_config.json`` — the file that ``config.py`` loads into ``wake_converse.py``
at boot. That let any authenticated caller inject arbitrary keys, wrong types, or
path/URL values (e.g. ``oww_model_path: "../../etc/passwd"``) straight into the
process config. This module locks that down with a pydantic model.

Design constraint — DO NOT switch to ``extra="forbid"``
-------------------------------------------------------
The Svelte Config editor (``web/src/pages/Config.svelte`` via
``lib/stores/config.js``) PUTs the *entire* config blob back on save, not a diff.
That blob legitimately contains keys this module does not model (``vision_model``,
``vision_confidence_threshold``, ``vision_allowlist``, the UI-only hex colour
fields ``led_listening_color`` / ``led_talking_color``, etc.). Rejecting unknown
keys would 422 every save and brick the admin panel on a headless device.

So the policy is:
  * KNOWN editable keys      -> typed + range/format validated (422 on violation).
  * UNKNOWN keys             -> passed through untouched (``extra="allow"``).
  * SECRET keys              -> silently dropped, never written to the JSON file
                               (they belong in ``.env`` only). The UI has an
                               ``ha_token`` field, so dropping (not rejecting)
                               keeps saves working.
  * PATH-INJECTION keys      -> rejected (422). These have no UI field; their
                               presence is an attack, not a legitimate save.
"""
import os

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
_BASE_DIR = os.path.dirname(_SCRIPTS_DIR)
_MODELS_DIR = os.path.join(_BASE_DIR, "models")

# Secrets: never belong in the committed JSON file. Dropped silently so the UI's
# ha_token field (and any future secret field) can't leak into version control.
SECRET_KEYS = frozenset({"ha_token", "anthropic_api_key"})

# Path-ish fields with no business being web-editable. No UI exposes them, so
# their arrival is an injection attempt -> reject outright.
FORBIDDEN_KEYS = frozenset({
    "piper_bin",
    "model_path",
    "session_file",
    "end_session_file",
    "abort_file",
})

# http(s) hosts we consider safe for ha_url / local_llm_url. LAN/localhost only —
# these URLs are dialled by the on-device service, so an off-LAN host is either a
# mistake or an SSRF-style redirection of Bender's traffic.
_PRIVATE_HOST_PREFIXES = ("127.", "10.", "192.168.", "0.0.0.0")
_PRIVATE_HOST_EXACT = frozenset({"localhost", "homeassistant.local", "homeassistant"})


def _validate_lan_url(v: str, field: str) -> str:
    from urllib.parse import urlparse

    v = v.strip()
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{field} must be an http:// or https:// URL")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"{field} has no host")
    is_private = (
        host in _PRIVATE_HOST_EXACT
        or host.endswith(".local")
        or host.endswith(".lan")
        or any(host.startswith(p) for p in _PRIVATE_HOST_PREFIXES)
        or _is_172_private(host)
    )
    if not is_private:
        raise ValueError(
            f"{field} must point at a LAN/localhost host "
            f"(got {host!r}); external hosts are not allowed via the web UI"
        )
    return v


def _is_172_private(host: str) -> bool:
    # 172.16.0.0 – 172.31.255.255
    if not host.startswith("172."):
        return False
    try:
        second = int(host.split(".")[1])
    except (IndexError, ValueError):
        return False
    return 16 <= second <= 31


class BenderConfigUpdate(BaseModel):
    """Editable subset of bender_config.json with type + range + format validation.

    All fields Optional: the UI PUTs a full blob, but a partial PUT (e.g. just
    ``speech_rate``) must also validate. Unknown keys pass through (extra=allow);
    secrets are stripped and forbidden path keys rejected in a pre-validator.

    Note: ``ha_url`` / ``local_llm_url`` LAN restriction only covers the JSON
    path. Both are also overridable via ``.env`` (HA_URL) / env vars, which this
    schema does not touch — that path requires shell access on the device.
    """

    model_config = ConfigDict(extra="allow")

    # --- General ---
    speech_rate: float | None = None
    dismissal_ends_session: bool | None = None
    thinking_sound: bool | None = None
    silence_timeout: float | None = None
    simple_intent_max_words: int | None = None
    timer_alert_max_seconds: int | None = None
    location: str | None = None

    # --- STT ---
    whisper_model: str | None = None
    vad_aggressiveness: int | None = None
    silence_frames: int | None = None
    max_record_seconds: int | None = None
    hailo_stt_enabled: bool | None = None

    # --- AI backend ---
    ai_backend: str | None = None
    ai_model: str | None = None
    ai_max_tokens: int | None = None
    local_llm_model: str | None = None
    local_llm_url: str | None = None
    local_llm_timeout: float | None = None
    llm_warm_session: bool | None = None
    ai_routing: dict | None = None

    # --- Wake word ---
    oww_model_path: str | None = None
    oww_threshold: float | None = None
    oww_frames_required: int | None = None
    oww_window: int | None = None
    wake_rms_floor: float | None = None
    wake_silence_alarm_s: float | None = None

    # --- Home Assistant ---
    ha_url: str | None = None
    ha_weather_entity: str | None = None
    ha_room_synonyms: dict | None = None

    # --- Briefings ---
    weather_ttl: int | None = None
    news_ttl: int | None = None
    briefings_weather_ttl_s: int | None = None
    briefings_news_ttl_s: int | None = None
    briefings_news_feeds: list | None = None

    # --- Timeouts ---
    response_hard_timeout_s: float | None = None
    http_timeout_s: float | None = None
    mic_read_timeout_s: float | None = None

    # --- LED (brightness accepts 0-1 float OR 0-255 int; the UI slider is 0-255) ---
    led_brightness: float | None = None
    led_listening_enabled: bool | None = None
    silent_wakeword: bool | None = None

    # --- Logging ---
    log_level: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _strip_secrets_reject_paths(cls, data):
        if not isinstance(data, dict):
            return data
        # Reject path-injection keys before any per-field validation.
        offending = FORBIDDEN_KEYS & data.keys()
        if offending:
            raise ValueError(
                f"key(s) not editable via web UI: {sorted(offending)}"
            )
        # Drop secrets silently — they belong in .env, never the JSON file.
        return {k: v for k, v in data.items() if k not in SECRET_KEYS}

    @field_validator("oww_threshold")
    @classmethod
    def _threshold_0_1(cls, v):
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("oww_threshold must be between 0 and 1")
        return v

    @field_validator("led_brightness")
    @classmethod
    def _brightness_range(cls, v):
        if v is not None and not (0.0 <= v <= 255.0):
            raise ValueError("led_brightness must be between 0 and 255")
        return v

    @field_validator("vad_aggressiveness")
    @classmethod
    def _vad_0_3(cls, v):
        if v is not None and not (0 <= v <= 3):
            raise ValueError("vad_aggressiveness must be 0-3")
        return v

    @field_validator("speech_rate")
    @classmethod
    def _speech_rate_range(cls, v):
        if v is not None and not (0.1 <= v <= 5.0):
            raise ValueError("speech_rate must be between 0.1 and 5.0")
        return v

    @field_validator(
        "silence_timeout", "silence_frames", "max_record_seconds",
        "ai_max_tokens", "local_llm_timeout", "oww_frames_required",
        "oww_window", "weather_ttl", "news_ttl", "briefings_weather_ttl_s",
        "briefings_news_ttl_s", "response_hard_timeout_s", "http_timeout_s",
        "mic_read_timeout_s", "wake_rms_floor", "wake_silence_alarm_s",
        "simple_intent_max_words", "timer_alert_max_seconds",
    )
    @classmethod
    def _positive(cls, v, info):
        if v is not None and v <= 0:
            raise ValueError(f"{info.field_name} must be greater than 0")
        return v

    @field_validator("ai_backend")
    @classmethod
    def _backend_enum(cls, v):
        if v is not None and v not in ("hybrid", "local_only", "cloud_only"):
            raise ValueError("ai_backend must be hybrid, local_only or cloud_only")
        return v

    @field_validator("oww_model_path")
    @classmethod
    def _model_path_safe(cls, v):
        if v is None:
            return v
        if not (v.endswith(".onnx") or v.endswith(".tflite")):
            raise ValueError("oww_model_path must be a .onnx or .tflite file")
        # Resolve relative to base dir and confirm it stays under models/.
        candidate = v if os.path.isabs(v) else os.path.join(_BASE_DIR, v)
        resolved = os.path.realpath(candidate)
        models_root = os.path.realpath(_MODELS_DIR)
        if os.path.commonpath([resolved, models_root]) != models_root:
            raise ValueError("oww_model_path must resolve to a file under models/")
        return v

    @field_validator("ha_url")
    @classmethod
    def _ha_url_lan(cls, v):
        if v is None:
            return v
        return _validate_lan_url(v, "ha_url")

    @field_validator("local_llm_url")
    @classmethod
    def _local_llm_url_lan(cls, v):
        if v is None:
            return v
        return _validate_lan_url(v, "local_llm_url")


class WatchdogConfigUpdate(BaseModel):
    """Editable subset of watchdog_config.json. All thresholds must be positive."""

    model_config = ConfigDict(extra="allow")

    stt_empty_rate_threshold: float | None = None
    api_fallback_rate_threshold: float | None = None
    error_rate_threshold: float | None = None
    stt_latency_threshold_ms: float | None = None
    tts_latency_threshold_ms: float | None = None
    api_latency_threshold_ms: float | None = None
    promote_candidate_min_hits: int | None = None
    briefing_stale_weather_s: int | None = None
    briefing_stale_news_s: int | None = None
    min_avg_session_turns: float | None = None
    log_gap_threshold_s: int | None = None
    lookback_hours: int | None = None
    max_hours_without_session: int | None = None
    mic_stall_reinit_threshold: int | None = None
    mic_stall_exit_threshold: int | None = None
    hailo_lock_stuck_threshold: int | None = None
    log_retention_days: int | None = None
    watchdog_renotify_hours: float | None = None
    watchdog_quiet_hours_start: int | None = None
    watchdog_quiet_hours_end: int | None = None
    watchdog_notify_domain: str | None = None
    watchdog_notify_service: str | None = None

    @field_validator("*")
    @classmethod
    def _non_negative(cls, v, info):
        # Rates/thresholds are all >= 0; most are > 0 but error_rate etc. of 0 is
        # a legitimate "alert on any error" setting, so allow 0.
        if v is not None and isinstance(v, (int, float)) and v < 0:
            raise ValueError(f"{info.field_name} must be >= 0")
        return v

    @field_validator("watchdog_quiet_hours_start", "watchdog_quiet_hours_end")
    @classmethod
    def _hour_of_day(cls, v):
        if v is not None and not (0 <= v <= 23):
            raise ValueError("quiet hour must be 0-23")
        return v


def clean_bender_config(body: dict) -> dict:
    """Validate + normalise a bender_config PUT body.

    Returns the dict to merge into bender_config.json (secrets stripped, unknown
    keys preserved, ``None`` sentinels dropped). Raises pydantic ValidationError
    on any type/range/format violation.
    """
    model = BenderConfigUpdate.model_validate(body)
    # exclude_unset drops the Optional=None sentinels for keys the caller didn't
    # send; extra keys survive because model_config allows them.
    return model.model_dump(exclude_unset=True)


def clean_watchdog_config(body: dict) -> dict:
    model = WatchdogConfigUpdate.model_validate(body)
    return model.model_dump(exclude_unset=True)
