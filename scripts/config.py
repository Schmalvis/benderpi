"""Centralised configuration for BenderPi.

Loading order:
  1. Hardcoded defaults
  2. bender_config.json overrides (committed, runtime-editable)
  3. .env overrides (secrets only)
  4. Environment variable overrides (BENDER_ prefix)
"""

import json
import os

# NOTE: this module deliberately never imports scripts/logger.py.
# logger.get_logger() -> _init_root() does `from config import cfg` at call
# time, and `cfg` is constructed at the bottom of this module -- so if
# config.py imported logger.py (even indirectly), building the `cfg`
# singleton below would re-enter this still-partially-initialised module
# looking for an attribute that doesn't exist yet. Warnings emitted here use
# plain stderr prints (matching _override_type_ok / _clamp_local_llm_timeout
# below), not the `logging` module or scripts/logger.py.

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG_PATH = os.path.join(_BASE_DIR, "bender_config.json")
_DEFAULT_ENV_PATH = os.path.join(_BASE_DIR, ".env")

# Mapping of BENDER_ env var names to config fields
_ENV_OVERRIDES = {
    "BENDER_LOG_LEVEL": "log_level",
    "BENDER_LOG_LEVEL_FILE": "log_level_file",
    "BENDER_AI_MODEL": "ai_model",
    "BENDER_WHISPER_MODEL": "whisper_model",
    "BENDER_VAD_SILENCE_FRAMES": "silence_frames",
    "BENDER_VAD_AGGRESSIVENESS": "vad_aggressiveness",
}


class Config:
    """Single source of truth for all BenderPi tunables."""

    # --- Defaults ---

    # Audio
    sample_rate: int = 44100
    silence_pre: float = 0.02
    silence_post: float = 0.08
    output_device: int = 0  # PyAudio device index for hw:2,0
    input_device_name: str = "mic_shared"   # substring hint for PortAudio input discovery
    output_device_name: str = "seeed"       # substring hint for PortAudio output discovery
    audio_chunk: int = 512
    audio_rms_floor: int = 200
    audio_rms_ceiling: int = 8000
    post_play_flush_ms: int = 200  # ms of mic buffer to discard after playback (reverb flush)

    # STT
    whisper_model: str = "base.en"
    # 0–3 scale; higher = more aggressive non-speech filtering. Deployed
    # bender_config.json overrides this to 1 (gentler) — the XVF3800 array's
    # noise floor made 3 clip the starts of quiet utterances. Default reconciled
    # to the deployed value so a fresh checkout behaves the same.
    vad_aggressiveness: int = 1
    # Trailing silence before an utterance ends = silence_frames × 30ms frames.
    # 25 × 30ms = 750ms. Deployed bender_config.json also sets 25; default
    # reconciled so a fresh checkout matches deployment. Tune via
    # BENDER_VAD_SILENCE_FRAMES.
    silence_frames: int = 25
    max_record_seconds: int = 15

    # STT confidence gating (faster-whisper CPU path only — Hailo returns no
    # confidence signals, so the phrase blocklist remains its only defence).
    # A segment is dropped when it is BOTH probably-silence (no_speech_prob >
    # max) AND low-confidence (avg_logprob < min); or when compression_ratio
    # exceeds max (repetitive garbage). Canonical Whisper-lore values, kept
    # permissive — over-gating rejects quiet real speech. Tune from the
    # stt_confidence_reject metric in logs/metrics.jsonl.
    stt_no_speech_prob_max: float = 0.6
    stt_avg_logprob_min: float = -1.0
    stt_compression_ratio_max: float = 2.4
    hailo_stt_enabled: bool = True
    vlm_enabled: bool = True  # set False to skip Hailo VLM scene capture  # set False to use faster-whisper CPU only

    # TTS
    piper_bin: str = os.path.join(_BASE_DIR, "piper", "piper")
    model_path: str = os.path.join(_BASE_DIR, "models", "bender.onnx")
    speech_rate: float = 1.0  # Piper --length-scale: >1.0 slower, <1.0 faster

    # AI
    ai_model: str = "claude-haiku-4-5-20251001"
    ai_max_tokens: int = 150
    ai_max_history: int = 6

    # Local LLM
    ai_backend: str = "hybrid"  # "hybrid" | "local_only" | "cloud_only" (hybrid uses ai_routing per scenario)
    local_llm_model: str = "qwen2.5:1.5b"
    local_llm_url: str = "http://localhost:11434"
    local_llm_timeout: int = 3
    # Warm Hailo LLM session (opt-in, hardware-gated). When true, the Hailo
    # VDevice is held across turns within a session instead of released after
    # every AI turn — eliminating the per-turn HEF reload tax (see the
    # ai_hailo_load metric). Released at session end() instead. DEFAULT FALSE:
    # this assumes the Whisper + Qwen HEFs can coexist resident on the Hailo-10H;
    # if they cannot, STT fails on turn 2. Only flip to true after the on-device
    # HEF-coexistence spike passes. Revert by config edit if the chip misbehaves.
    llm_warm_session: bool = False
    tts_noise_scale: float = 0.9      # Piper expressiveness (default 0.667)
    tts_noise_scale_w: float = 1.2    # Piper phoneme duration variance (default 0.8)
    tts_cache_dir: str = os.path.join(_BASE_DIR, "speech", "responses", "cache")  # sha256 content-hash WAV cache
    tts_cache_max_mb: int = 100       # size cap for tts_cache_dir; oldest (by mtime) entries pruned first
    ai_routing: dict = None  # set in __init__ to avoid mutable default

    # Wake word (openWakeWord)
    oww_model_path: str = "models/hey_bender_v0.1.onnx"
    oww_threshold: float = 0.5
    # N-of-M temporal smoothing: fire only when >= oww_frames_required of the
    # last oww_window frame scores clear oww_threshold. Suppresses single-frame
    # spikes (false accepts) while a lowered threshold recovers recall. Set
    # oww_frames_required to 1 to disable smoothing (pure per-frame threshold).
    oww_frames_required: int = 2
    oww_window: int = 4

    # Conversation
    silence_timeout: float = 8.0
    thinking_sound: bool = True
    simple_intent_max_words: int = 6
    timer_alert_max_seconds: int = 60

    # HA
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""  # from .env only
    ha_weather_entity: str = "weather.forecast_home"
    location: str = "your location"  # shown in Bender persona + weather responses
    ha_room_synonyms: dict = {}
    ha_entity_cache_ttl_s: float = 60.0
    ha_exclude_keywords: list = None  # set in __init__ to avoid mutable default

    # Secrets (from .env only, never in config JSON)
    anthropic_api_key: str = ""

    # Briefings
    weather_ttl: int = 1800
    news_ttl: int = 7200
    briefings_weather_ttl_s: int = 1800
    briefings_news_ttl_s: int = 7200
    briefings_news_feeds: list = None  # set in __init__ to avoid mutable default

    # Logging
    log_level: str = "INFO"
    log_level_file: str = "DEBUG"

    # LED
    led_count: int = 45
    led_brightness: float = 0.8
    led_colour: tuple = (255, 120, 0)
    led_listening_colour: tuple = (0, 80, 255)   # blue when listening
    led_talking_colour: tuple = (255, 255, 255)   # white when talking
    led_listening_enabled: bool = False            # show LED colour while listening
    silent_wakeword: bool = False                  # skip audio greeting on wake (requires led_listening_enabled)

    # Stop behaviour
    dismissal_ends_session: bool = True

    # STT hallucination filter
    whisper_hallucinations: list = None  # set in __init__ to avoid mutable default

    # Vision
    vlm_timeout: float = 4.0          # seconds to wait for VLM inference (legacy, kept for compat)
    vlm_yolo_timeout_s: float = 8.0   # max seconds for YOLO inference inside vlm.py
    vlm_lazy_poll_s: float = 0.05     # how long to block at AI-call time waiting for scene
    vlm_prompt: str = "Briefly describe what you see."

    # Wake loop liveness
    wake_stall_seconds: float = 30.0    # raise RuntimeError if no PCM frames for this long
    wake_heartbeat_frames: int = 250    # emit heartbeat metric every N frames

    # Wake loop input-sanity (RMS sentinel + score logging)
    #   The 2026 XVF3800 incident had the mic feeding *zeros* (or near-zero
    #   garbage) for days: reads returned frames, so the stall detector never
    #   fired, but the wake word could never trigger. The RMS sentinel watches
    #   the rolling input level and, if it stays below wake_rms_floor for
    #   wake_silence_alarm_s, escalates through the same reinit-then-exit path
    #   as a hard stall. Floor must be calibrated against the mic's real noise
    #   floor (a quiet room is NOT zero) — too high and it reinit-loops.
    wake_rms_floor: float = 30.0        # rolling input RMS below this = presumed dead mic
    wake_silence_alarm_s: float = 120.0 # seconds below floor before escalating (0 disables)
    wake_score_log_interval_s: float = 60.0  # log max-score + RMS every N seconds (0 disables)

    # Mic read watchdog (MicReader) — applies to all blocking mic reads
    mic_read_timeout_s: float = 10.0    # raise MicStallError if no frame arrives in this window
    mic_stall_max_reinits: int = 1      # in-process mic reinit attempts before sys.exit(1) → systemd restart

    # Inference hard timeout
    response_hard_timeout_s: float = 20.0  # max seconds to wait for responder inference thread

    # Network timeouts
    http_timeout_s: float = 10.0  # timeout for all outbound HTTP calls (briefings + HA)

    # Web UI stream lifecycle caps. Wall-clock ceilings on the two unbounded
    # web streams so a backgrounded mobile tab that stops reading (but never
    # cleanly closes) cannot pin the camera / arecord — and thus the single-rate
    # mic — forever. 0 disables the cap.
    web_stream_max_s: float = 300.0   # MJPEG camera stream hard cap (5 min)
    web_mic_max_s: float = 120.0      # ambient mic websocket hard cap (2 min)

    def __init__(self, config_path: str = None, env_path: str = None):
        # 1. Load JSON config overrides
        path = config_path or _DEFAULT_CONFIG_PATH
        overrides = {}
        if os.path.exists(path):
            with open(path) as f:
                overrides = json.load(f)
            for key, value in overrides.items():
                if hasattr(self, key):
                    if key in ("led_colour", "led_listening_colour", "led_talking_colour") and isinstance(value, list):
                        value = tuple(value)
                    if not self._override_type_ok(key, value):
                        continue
                    setattr(self, key, value)

        # Set mutable defaults after JSON overrides have been applied.
        if self.ai_routing is None:
            self.ai_routing = {
                "conversation": "cloud_first",
                "knowledge": "cloud_first",
                "creative": "cloud_first",
            }

        if self.briefings_news_feeds is None:
            self.briefings_news_feeds = [
                ["UK",      "https://feeds.bbci.co.uk/news/uk/rss.xml",      2],
                ["England", "https://feeds.bbci.co.uk/news/england/rss.xml", 2],
            ]

        if self.ha_exclude_keywords is None:
            self.ha_exclude_keywords = []

        # IPC paths
        self.session_file: str = os.path.join(_BASE_DIR, ".session_active.json")
        self.end_session_file: str = os.path.join(_BASE_DIR, ".end_session")
        self.abort_file: str = os.path.join(_BASE_DIR, ".abort_playback")

        # HA exclude entities (loaded from bender_config.json)
        self.ha_exclude_entities: list = overrides.get("ha_exclude_entities", [])
        self.whisper_hallucinations: list = overrides.get("whisper_hallucinations", [])
        self.ha_room_synonyms: dict = overrides.get("ha_room_synonyms", {})

        # 2. Load .env for secrets
        ep = env_path or _DEFAULT_ENV_PATH
        if os.path.exists(ep):
            self._load_dotenv(ep)

        # 3. Env var overrides
        for env_var, field in _ENV_OVERRIDES.items():
            val = os.environ.get(env_var)
            if val is not None:
                setattr(self, field, val)

        # 4. Direct env var secrets
        self.ha_token = os.environ.get("HA_TOKEN", self.ha_token)
        self.ha_url = os.environ.get("HA_URL", self.ha_url)
        self.ha_weather_entity = os.environ.get("HA_WEATHER_ENTITY", self.ha_weather_entity)
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", self.anthropic_api_key)

        # 5. Load-time invariant: the local LLM timeout must sit below the
        # inference hard timeout, or session.py's hard-timeout join kills the
        # inference thread before the Ollama request can time out and fail over —
        # producing an error_fallback turn instead of a clean cloud escalation.
        # Warn-and-clamp rather than trust the two numbers being edited in sync.
        # CAVEAT: for the *streaming* Ollama path (generate_stream) the requests
        # timeout is per-read (inter-token gap), not a total-response budget, so
        # a slow-but-steady stream can still exceed response_hard_timeout_s. This
        # clamp only bounds the non-stream / connect case.
        self._clamp_local_llm_timeout()

    def _override_type_ok(self, key: str, value) -> bool:
        """Defensive guard: reject a JSON override whose type disagrees with the
        class-level default's type, so a hand-edited bad bender_config.json can't
        crash wake_converse.py at 3am. Log-and-skip (keep the default) rather than
        raise — a bad value for one key must not take the whole service down.

        Deliberately permissive so legitimate legacy values survive:
          * int is accepted where a float is declared (JSON has no float literal
            distinction; e.g. local_llm_timeout: 25 for a float field).
          * bool is NOT accepted for int/float (Python bool is an int subclass,
            but a JSON `true` where a number is expected is a real mistake).
          * None is always accepted (mutable-default fields start as None).
          * Fields whose declared default is None are unconstrained (their real
            type is set later in __init__).
        """
        if value is None:
            return True
        default = getattr(type(self), key, None)
        if default is None:
            return True
        expected = type(default)
        # bool masquerades as int — guard it explicitly both ways.
        if expected is bool:
            ok = isinstance(value, bool)
        elif expected in (int, float):
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            ok = isinstance(value, expected)
        if not ok:
            import sys
            print(
                f"[config] WARNING: ignoring override {key!r}={value!r} "
                f"(expected {expected.__name__}, got {type(value).__name__}); "
                f"keeping default {default!r}.",
                file=sys.stderr,
            )
        return ok

    def _clamp_local_llm_timeout(self) -> None:
        try:
            hard = float(self.response_hard_timeout_s)
            cur = float(self.local_llm_timeout)
        except (TypeError, ValueError):
            return
        ceiling = hard - 2.0
        if ceiling > 0 and cur > ceiling:
            import sys
            print(
                f"[config] WARNING: local_llm_timeout ({cur:g}s) >= "
                f"response_hard_timeout_s ({hard:g}s) - 2s; clamping to "
                f"{ceiling:g}s so Ollama can fail over before the hard-timeout "
                f"join kills the inference thread.",
                file=sys.stderr,
            )
            self.local_llm_timeout = ceiling

    def _load_dotenv(self, path: str):
        """Minimal .env parser — no dependency on python-dotenv for config module.

        Parses line-by-line so one malformed line is a loud, line-numbered
        warning rather than a silent parse of nothing (the previous
        try/except wrapped the whole file: a single bad line anywhere would
        quietly drop every secret in the file, indistinguishable from an
        empty .env).
        """
        import sys
        try:
            f = open(path)
        except OSError as e:
            print(f"[config] WARNING: could not read {path}: {e}", file=sys.stderr)
            return
        with f:
            for lineno, raw in enumerate(f, start=1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    print(
                        f"[config] WARNING: {path}:{lineno}: malformed line "
                        f"(missing '='), skipping: {raw.rstrip(chr(10))!r}",
                        file=sys.stderr,
                    )
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not key:
                    print(
                        f"[config] WARNING: {path}:{lineno}: empty key, "
                        f"skipping: {raw.rstrip(chr(10))!r}",
                        file=sys.stderr,
                    )
                    continue
                value = value.strip().strip("'\"")
                if value:
                    os.environ.setdefault(key, value)

    def validate(self) -> list:
        """Loud, non-fatal startup check for required-but-empty secrets,
        given the *active* config (ai_backend, etc). Offline-first means an
        empty secret is a valid degraded state -- what must not happen is
        *silent* degradation (the failure mode behind the 6-day mic outage:
        the wake loop kept reading zeroed frames without ever raising).

        Returns the list of missing secret names (also printed as WARNINGs)
        so callers can feed a `secrets_missing` metric/counter.
        """
        import sys
        missing = []

        # anthropic_api_key only matters when the config can actually reach
        # cloud: local_only never touches it.
        if self.ai_backend != "local_only" and not self.anthropic_api_key:
            missing.append("anthropic_api_key")
            print(
                f"[config] WARNING: ANTHROPIC_API_KEY is empty but "
                f"ai_backend={self.ai_backend!r} may fall back to cloud -- "
                f"those turns will degrade to error_fallback instead of a "
                f"Claude response. Set ANTHROPIC_API_KEY in .env, or set "
                f"ai_backend to 'local_only' if that's intentional.",
                file=sys.stderr,
            )

        # ha_token gates both HA device control and weather briefings.
        if not self.ha_token:
            missing.append("ha_token")
            print(
                "[config] WARNING: HA_TOKEN is empty -- Home Assistant "
                "device control and weather briefings will not work (HA "
                "REST calls will fail). Set HA_TOKEN in .env. See "
                ".env.example for least-privilege token setup.",
                file=sys.stderr,
            )

        return missing


# Singleton — import as: from config import cfg
cfg = Config()
