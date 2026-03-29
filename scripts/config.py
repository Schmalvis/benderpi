"""Centralised configuration for BenderPi.

Loading order:
  1. Hardcoded defaults
  2. bender_config.json overrides (committed, runtime-editable)
  3. .env overrides (secrets only)
  4. Environment variable overrides (BENDER_ prefix)
"""

import json
import os

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

    # STT
    whisper_model: str = "tiny.en"
    vad_aggressiveness: int = 3  # max aggressiveness — reduces false non-speech
    silence_frames: int = 15  # 15×30ms = 450ms — was 50 (1.5s)
    max_record_seconds: int = 15

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
    tts_noise_scale: float = 0.9      # Piper expressiveness (default 0.667)
    tts_noise_scale_w: float = 1.2    # Piper phoneme duration variance (default 0.8)
    ai_routing: dict = None  # set in __init__ to avoid mutable default

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

    # Secrets (from .env only, never in config JSON)
    anthropic_api_key: str = ""
    porcupine_access_key: str = ""

    # Briefings
    weather_ttl: int = 1800
    news_ttl: int = 7200

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
                    setattr(self, key, value)

        # Set mutable defaults after JSON overrides have been applied.
        if self.ai_routing is None:
            self.ai_routing = {
                "conversation": "cloud_first",
                "knowledge": "cloud_first",
                "creative": "cloud_first",
            }

        # IPC paths
        self.session_file: str = os.path.join(_BASE_DIR, ".session_active.json")
        self.end_session_file: str = os.path.join(_BASE_DIR, ".end_session")
        self.abort_file: str = os.path.join(_BASE_DIR, ".abort_playback")

        # HA exclude entities (loaded from bender_config.json)
        self.ha_exclude_entities: list = overrides.get("ha_exclude_entities", [])
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
        self.porcupine_access_key = os.environ.get("PORCUPINE_ACCESS_KEY", self.porcupine_access_key)

    def _load_dotenv(self, path: str):
        """Minimal .env parser — no dependency on python-dotenv for config module."""
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    if value:
                        os.environ.setdefault(key, value)
        except Exception:
            pass


# Singleton — import as: from config import cfg
cfg = Config()
