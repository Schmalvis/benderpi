# BenderPi Quality, Observability & Modularity — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured logging, metrics, health monitoring, session handover, and modular architecture to BenderPi, while improving audio quality, response speed, and intent accuracy.

**Architecture:** Interleaved approach — foundation (config, logging, metrics) first, then modularity refactoring (responder extraction, handler decoupling), then user-facing improvements (audio, TTS, STT, intents), then observability tools (status report, watchdog). Each commit leaves the system functional on the live Pi.

**Tech Stack:** Python 3.13, pytest (new), faster-whisper, Piper TTS (aarch64 binary), PyAudio, anthropic SDK, Home Assistant REST API. All modules run on Raspberry Pi 5 with Adafruit Voice Bonnet (WM8960 codec).

**Spec:** `docs/superpowers/specs/2026-03-15-benderpi-quality-observability-design.md`

**Key constraints:**
- The Pi runs the `bender-converse` systemd service — every commit to `main` auto-deploys within 5 minutes
- WM8960 codec can only operate at one sample rate at a time (mic 16kHz vs playback 44100Hz)
- `venv` must use `--system-site-packages` for hardware libs (lgpio, neopixel)
- Piper binary and voice model are gitignored — tests must not require them
- Audio, LED, and wake word code cannot be unit tested without hardware — test only pure-logic modules

**Testing strategy:**
- New `tests/` directory with pytest
- Tests run on the development machine (Windows/Linux), NOT on the Pi
- Mock hardware-dependent imports (pyaudio, board, neopixel_spi, pvporcupine) where needed
- Focus tests on: config loading, intent classification, metrics recording, watchdog checks, status generation, responder logic

---

## Chunk 1: Foundation — Bug Fixes, Config, Logging

### Task 1: Set up test infrastructure

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `pytest.ini`

- [ ] **Step 1: Add pytest to requirements**

Add to `requirements.txt`:
```
# Testing (dev only — not needed on Pi)
pytest==8.3.5
```

- [ ] **Step 2: Create pytest.ini**

```ini
[pytest]
testpaths = tests
pythonpath = scripts
```

- [ ] **Step 3: Create tests directory with conftest**

`tests/__init__.py` — empty file.

`tests/conftest.py`:
```python
"""Shared test fixtures for BenderPi tests."""
import os
import sys

# Ensure scripts/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
```

- [ ] **Step 4: Verify pytest runs (no tests yet)**

Run: `python -m pytest --co -q`
Expected: "no tests ran"

- [ ] **Step 5: Commit**

```bash
git add tests/ pytest.ini requirements.txt
git commit -m "Add pytest test infrastructure"
```

---

### Task 2: Bug fixes and dead code cleanup

**Files:**
- Delete: `scripts/handlers/ha_status.py`
- Delete: `scripts/handlers/weather.py`
- Modify: `scripts/ha_control.py` (remove duplicate check)
- Modify: `scripts/briefings.py` (add thread lock)
- Modify: `scripts/handlers/__init__.py` (if it imports deleted modules)

- [ ] **Step 1: Read handlers/__init__.py to check for imports of deleted modules**

Read: `scripts/handlers/__init__.py`

- [ ] **Step 2: Delete dead code files**

Delete `scripts/handlers/ha_status.py` and `scripts/handlers/weather.py`.
Update `scripts/handlers/__init__.py` if it imports either.

- [ ] **Step 3: Remove duplicate action check in ha_control.py**

In `scripts/ha_control.py`, find the second `if action is None:` block (approximately line 298, after the temperature-set path). Remove it — the first check at line 280 already handles this case.

- [ ] **Step 4: Add thread safety to briefings.py**

In `scripts/briefings.py`, add a threading lock around meta file access:

```python
import threading

_meta_lock = threading.Lock()

def _load_meta() -> dict:
    with _meta_lock:
        try:
            with open(META_PATH) as f:
                return json.load(f)
        except Exception:
            return {}

def _save_meta(meta: dict):
    with _meta_lock:
        with open(META_PATH, "w") as f:
            json.dump(meta, f)
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Fix bugs: remove dead code, thread-safe briefings meta, remove duplicate check"
```

---

### Task 3: Centralised configuration — `scripts/config.py`

**Files:**
- Create: `scripts/config.py`
- Create: `bender_config.json`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
"""Tests for centralised config module."""
import json
import os
import tempfile

def test_config_loads_defaults():
    """Config should have sensible defaults without any files."""
    from config import Config
    cfg = Config()
    assert cfg.sample_rate == 44100
    assert cfg.whisper_model == "tiny.en"
    assert cfg.silence_timeout == 8.0
    assert cfg.ai_model == "claude-haiku-4-5-20251001"
    assert cfg.log_level == "INFO"

def test_config_loads_json_overrides(tmp_path):
    """Config should load overrides from a JSON file."""
    from config import Config
    json_path = tmp_path / "bender_config.json"
    json_path.write_text(json.dumps({"whisper_model": "base.en", "silence_timeout": 12.0}))
    cfg = Config(config_path=str(json_path))
    assert cfg.whisper_model == "base.en"
    assert cfg.silence_timeout == 12.0
    assert cfg.sample_rate == 44100  # unchanged default

def test_config_env_overrides(tmp_path, monkeypatch):
    """Env vars should override JSON and defaults."""
    from config import Config
    monkeypatch.setenv("BENDER_LOG_LEVEL", "DEBUG")
    cfg = Config(config_path=str(tmp_path / "nonexistent.json"))
    assert cfg.log_level == "DEBUG"

def test_config_secrets_from_env(monkeypatch):
    """Secrets come from env/.env only, never from config JSON."""
    from config import Config
    monkeypatch.setenv("HA_TOKEN", "test-token-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cfg = Config()
    assert cfg.ha_token == "test-token-123"
    assert cfg.anthropic_api_key == "sk-test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Write config.py**

`scripts/config.py`:
```python
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
    vad_aggressiveness: int = 2
    silence_frames: int = 50
    max_record_seconds: int = 15

    # TTS
    piper_bin: str = os.path.join(_BASE_DIR, "piper", "piper")
    model_path: str = os.path.join(_BASE_DIR, "models", "bender.onnx")

    # AI
    ai_model: str = "claude-haiku-4-5-20251001"
    ai_max_tokens: int = 150
    ai_max_history: int = 6

    # Conversation
    silence_timeout: float = 8.0
    thinking_sound: bool = True
    simple_intent_max_words: int = 6

    # HA
    ha_url: str = "http://192.168.68.125:8123"
    ha_token: str = ""  # from .env only
    ha_weather_entity: str = "weather.forecast_home"

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
    led_count: int = 12
    led_brightness: float = 0.8
    led_colour: tuple = (255, 120, 0)

    def __init__(self, config_path: str = None, env_path: str = None):
        # 1. Load JSON config overrides
        path = config_path or _DEFAULT_CONFIG_PATH
        if os.path.exists(path):
            with open(path) as f:
                overrides = json.load(f)
            for key, value in overrides.items():
                if hasattr(self, key):
                    if key == "led_colour" and isinstance(value, list):
                        value = tuple(value)
                    setattr(self, key, value)

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
```

- [ ] **Step 4: Create bender_config.json**

`bender_config.json`:
```json
{
  "whisper_model": "tiny.en",
  "vad_aggressiveness": 2,
  "silence_timeout": 8.0,
  "silence_pre": 0.02,
  "silence_post": 0.08,
  "thinking_sound": true,
  "ai_model": "claude-haiku-4-5-20251001",
  "ai_max_tokens": 150,
  "weather_ttl": 1800,
  "news_ttl": 7200,
  "log_level": "INFO",
  "led_brightness": 0.8,
  "led_colour": [255, 120, 0],
  "simple_intent_max_words": 6
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add scripts/config.py bender_config.json tests/test_config.py
git commit -m "Add centralised config module with JSON + env override support"
```

---

### Task 4: Structured logging — `scripts/logger.py`

**Files:**
- Create: `scripts/logger.py`
- Create: `tests/test_logger.py`

- [ ] **Step 1: Write the failing test**

`tests/test_logger.py`:
```python
"""Tests for structured logging module."""
import logging

def test_get_logger_returns_child():
    from logger import get_logger
    log = get_logger("stt")
    assert log.name == "bender.stt"
    assert isinstance(log, logging.Logger)

def test_get_logger_same_instance():
    from logger import get_logger
    a = get_logger("audio")
    b = get_logger("audio")
    assert a is b

def test_root_logger_has_handlers():
    from logger import get_logger
    log = get_logger("test")
    root = logging.getLogger("bender")
    # Should have at least a console handler
    assert len(root.handlers) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logger.py -v`
Expected: FAIL

- [ ] **Step 3: Write logger.py**

`scripts/logger.py`:
```python
"""Structured logging for BenderPi.

Usage:
    from logger import get_logger
    log = get_logger("stt")
    log.info("Wake word detected")
    log.error("TTS failed", exc_info=True)
"""

import logging
import os
from logging.handlers import RotatingFileHandler

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_BASE_DIR, "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "bender.log")
_FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"

_initialised = False


def _init_root():
    """Set up the root 'bender' logger with console + file handlers."""
    global _initialised
    if _initialised:
        return
    _initialised = True

    # Import config lazily to avoid circular imports during early init
    try:
        from config import cfg
        console_level = getattr(logging, cfg.log_level.upper(), logging.INFO)
        file_level = getattr(logging, cfg.log_level_file.upper(), logging.DEBUG)
    except Exception:
        console_level = logging.INFO
        file_level = logging.DEBUG

    root = logging.getLogger("bender")
    root.setLevel(logging.DEBUG)  # handlers filter from here

    # Console handler (stdout → journald)
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)

    # Rotating file handler
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        fh = RotatingFileHandler(
            _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
        )
        fh.setLevel(file_level)
        fh.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(fh)
    except Exception:
        # If log dir is not writable (e.g. running tests on Windows), skip file handler
        pass


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'bender' namespace.

    E.g. get_logger("stt") → logger named 'bender.stt'
    """
    _init_root()
    return logging.getLogger(f"bender.{name}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_logger.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/logger.py tests/test_logger.py
git commit -m "Add structured logging module with console + rotating file handlers"
```

---

### Task 5: Migrate all print() to structured logging

**Files:**
- Modify: `scripts/wake_converse.py`
- Modify: `scripts/audio.py`
- Modify: `scripts/stt.py`
- Modify: `scripts/tts_generate.py`
- Modify: `scripts/ai_response.py`
- Modify: `scripts/intent.py`
- Modify: `scripts/briefings.py`
- Modify: `scripts/ha_control.py`
- Modify: `scripts/conversation_log.py`
- Modify: `scripts/leds.py`
- Modify: `scripts/prebuild_responses.py`

This is a mechanical migration. For each file:

1. Add `from logger import get_logger` and `log = get_logger("<module_name>")` at the top
2. Replace every `print(...)` with the appropriate log level:
   - `print("Listening for...")` → `log.info("Listening for...")`
   - `print(f"Error: {e}")` → `log.error("...: %s", e)`
   - `print(f"  HA: ...")` → `log.info("HA: ...")`
   - `print(f"  [briefing] ...")` → `log.info("[briefing] ...")`
   - `print(f"Heard: {text!r}")` → `log.info("Heard: %r", text)`
   - `print(f"Intent: ...")` → `log.info("Intent: %s%s", ...)`
   - `print(f"Wake word detected!")` → `log.info("Wake word detected")`
   - `print("Silence timeout...")` → `log.info("Silence timeout -- ending session")`
   - `print(f"Weather handler error: {e}")` → `log.error("Weather handler error: %s", e)`
3. Keep `print()` in `if __name__ == "__main__"` blocks (standalone test scripts)
4. Fix `conversation_log.py` docstring to add `handler_news` to the response methods list

- [ ] **Step 1: Migrate wake_converse.py**

Add at top: `from logger import get_logger` and `log = get_logger("converse")`
Replace all `print()` calls with appropriate log levels. Keep the main structure unchanged.

- [ ] **Step 2: Migrate audio.py, stt.py, tts_generate.py**

Each gets its own logger: `get_logger("audio")`, `get_logger("stt")`, `get_logger("tts")`.

- [ ] **Step 3: Migrate ai_response.py, intent.py, briefings.py**

Loggers: `get_logger("ai")`, `get_logger("intent")`, `get_logger("briefings")`.

- [ ] **Step 4: Migrate ha_control.py, conversation_log.py, leds.py, prebuild_responses.py**

Loggers: `get_logger("ha_control")`, `get_logger("conversation_log")`, `get_logger("leds")`, `get_logger("prebuild")`.
Fix the `conversation_log.py` docstring to include `handler_news`.

- [ ] **Step 5: Verify no print() remains in production code**

Run: `grep -rn "print(" scripts/ --include="*.py" | grep -v "__main__" | grep -v "^#"`
Expected: No matches (or only inside `if __name__` blocks).

- [ ] **Step 6: Commit**

```bash
git add scripts/
git commit -m "Migrate all print() to structured logging via logger.py"
```

---

## Chunk 2: Metrics, Responder Extraction, Handler Decoupling

### Task 6: Metrics module — `scripts/metrics.py`

**Files:**
- Create: `scripts/metrics.py`
- Create: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing test**

`tests/test_metrics.py`:
```python
"""Tests for metrics collection module."""
import json
import os
import time

def test_timer_records_duration(tmp_path):
    from metrics import MetricsWriter
    path = tmp_path / "metrics.jsonl"
    m = MetricsWriter(str(path))

    with m.timer("test_op"):
        time.sleep(0.01)

    lines = path.read_text().strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["type"] == "timer"
    assert event["name"] == "test_op"
    assert event["duration_ms"] >= 10
    assert "ts" in event

def test_count_records_event(tmp_path):
    from metrics import MetricsWriter
    path = tmp_path / "metrics.jsonl"
    m = MetricsWriter(str(path))

    m.count("intent", intent="GREETING")

    lines = path.read_text().strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["type"] == "count"
    assert event["name"] == "intent"
    assert event["intent"] == "GREETING"

def test_timer_with_tags(tmp_path):
    from metrics import MetricsWriter
    path = tmp_path / "metrics.jsonl"
    m = MetricsWriter(str(path))

    with m.timer("stt_transcribe", model="tiny.en"):
        pass

    event = json.loads(path.read_text().strip())
    assert event["model"] == "tiny.en"

def test_multiple_events(tmp_path):
    from metrics import MetricsWriter
    path = tmp_path / "metrics.jsonl"
    m = MetricsWriter(str(path))

    m.count("a")
    m.count("b")
    m.count("c")

    lines = path.read_text().strip().split("\n")
    assert len(lines) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_metrics.py -v`
Expected: FAIL

- [ ] **Step 3: Write metrics.py**

`scripts/metrics.py`:
```python
"""Lightweight metrics collection for BenderPi.

Writes timer and counter events to logs/metrics.jsonl.
Aggregation happens offline via generate_status.py.

Usage:
    from metrics import metrics
    with metrics.timer("stt_transcribe"):
        result = transcribe(audio)
    metrics.count("intent", intent="GREETING")
"""

import json
import os
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PATH = os.path.join(_BASE_DIR, "logs", "metrics.jsonl")


class MetricsWriter:
    """Thread-safe metrics writer to a JSONL file."""

    def __init__(self, path: str = None):
        self._path = path or _DEFAULT_PATH
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def _write(self, event: dict):
        event["ts"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with open(self._path, "a") as f:
                f.write(json.dumps(event) + "\n")

    @contextmanager
    def timer(self, name: str, **tags):
        """Context manager that records elapsed time in ms."""
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = round((time.monotonic() - start) * 1000, 1)
            self._write({"type": "timer", "name": name, "duration_ms": elapsed_ms, **tags})

    def count(self, name: str, **tags):
        """Record a counter event."""
        self._write({"type": "count", "name": name, **tags})


# Singleton — import as: from metrics import metrics
metrics = MetricsWriter()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_metrics.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/metrics.py tests/test_metrics.py
git commit -m "Add metrics module with timer context manager and counter events"
```

---

### Task 7: Instrument all modules with metrics

**Files:**
- Modify: `scripts/stt.py` (timer around record + transcribe)
- Modify: `scripts/tts_generate.py` (timer around speak)
- Modify: `scripts/ai_response.py` (timer around API call)
- Modify: `scripts/audio.py` (timer around play)
- Modify: `scripts/ha_control.py` (timer around HA REST calls)
- Modify: `scripts/briefings.py` (timer around generation)
- Modify: `scripts/wake_converse.py` (session count)

For each module, wrap the key operations:

**stt.py** — `listen_and_transcribe()` (wrap existing WAV-based flow; Task 11 will later rewrite to direct PCM):
```python
from metrics import metrics

def listen_and_transcribe() -> str:
    with metrics.timer("stt_record"):
        pcm = _record_utterance()
    if len(pcm) < FRAME_BYTES * 3:
        metrics.count("stt_empty", pcm_bytes=len(pcm))
        return ""
    wav_path = _pcm_to_wav(pcm)
    try:
        with metrics.timer("stt_transcribe", model=WHISPER_MODEL):
            text = transcribe(wav_path)
    finally:
        os.unlink(wav_path)
    return text
```

**tts_generate.py** — `speak()`:
```python
from metrics import metrics

def speak(text: str) -> str:
    with metrics.timer("tts_generate"):
        # ... existing logic ...
```

**ai_response.py** — `respond()` (also replace the `MODEL` module constant with `cfg.ai_model` throughout this file as part of the config migration):
```python
from config import cfg
from metrics import metrics

# Inside AIResponder.respond():
with metrics.timer("ai_api_call", model=cfg.ai_model):
    message = self.client.messages.create(...)
metrics.count("api_call", model=cfg.ai_model)
```

**audio.py** — `play()`:
```python
from metrics import metrics

def play(wav_path: str):
    with metrics.timer("audio_play"):
        # ... existing logic ...
```

**ha_control.py** — `_ha_call()`:
```python
from metrics import metrics

def _ha_call(domain, service, entity_id, extra=None):
    with metrics.timer("ha_call", entity=entity_id):
        # ... existing logic ...
```

**briefings.py** — generation functions:
```python
from metrics import metrics

# Inside get_weather_wav(), when regenerating:
with metrics.timer("briefing_generate", briefing="weather"):
    text = _generate_weather()
    wav = tts_generate.speak(text)
```

**wake_converse.py** — session tracking:
```python
from metrics import metrics

# In run_session():
metrics.count("session", event="start")
# At end:
metrics.count("session", event="end", turns=log.turn, reason=reason)
```

- [ ] **Step 1: Add metrics to stt.py, tts_generate.py, ai_response.py**

- [ ] **Step 2: Add metrics to audio.py, ha_control.py, briefings.py**

- [ ] **Step 3: Add metrics to wake_converse.py (session counts)**

- [ ] **Step 4: Commit**

```bash
git add scripts/
git commit -m "Instrument all modules with timing and counter metrics"
```

---

### Task 8: Extract response chain — `scripts/responder.py`

**Files:**
- Create: `scripts/responder.py`
- Create: `tests/test_responder.py`
- Modify: `scripts/wake_converse.py` (thin orchestrator)

- [ ] **Step 1: Write the failing test**

`tests/test_responder.py`:
```python
"""Tests for response priority chain."""
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

def _make_index(tmp_path):
    """Create a minimal test index.json."""
    index = {
        "greeting": ["speech/wav/hello.wav"],
        "dismissal": ["speech/wav/bye.wav"],
        "joke": ["speech/wav/joke.wav"],
        "affirmation": ["speech/wav/gotit.wav"],
        "personal": {"age": "speech/responses/personal/age.wav"},
        "ha_confirm": [],
        "promoted": [],
    }
    idx_path = tmp_path / "index.json"
    idx_path.write_text(json.dumps(index))
    return str(idx_path)


def test_response_dataclass():
    from responder import Response
    r = Response(
        text="hello", wav_path="/tmp/test.wav", method="real_clip",
        intent="GREETING", sub_key=None, is_temp=False,
        needs_thinking=False, model=None,
    )
    assert r.intent == "GREETING"
    assert r.is_temp is False
    assert r.needs_thinking is False


def test_greeting_returns_real_clip(tmp_path):
    from responder import Responder, Response
    idx = _make_index(tmp_path)
    # Create fake wav file
    wav_path = tmp_path / "speech" / "wav"
    wav_path.mkdir(parents=True)
    (wav_path / "hello.wav").write_bytes(b"RIFF")

    r = Responder(index_path=idx, base_dir=str(tmp_path))
    resp = r.get_response("hello", ai=None)
    assert resp.intent == "GREETING"
    assert resp.method == "real_clip"
    assert resp.is_temp is False


def test_unknown_falls_to_ai(tmp_path):
    from responder import Responder
    idx = _make_index(tmp_path)

    mock_ai = MagicMock()
    mock_ai.respond.return_value = "/tmp/ai_response.wav"
    mock_ai.history = [{"role": "assistant", "content": "test reply"}]

    r = Responder(index_path=idx, base_dir=str(tmp_path))
    resp = r.get_response("what is quantum computing", ai=mock_ai)
    assert resp.intent == "UNKNOWN"
    assert resp.method == "ai_fallback"
    assert resp.is_temp is True
    assert resp.needs_thinking is True
    mock_ai.respond.assert_called_once()


def test_dismissal_intent(tmp_path):
    from responder import Responder
    idx = _make_index(tmp_path)
    wav_path = tmp_path / "speech" / "wav"
    wav_path.mkdir(parents=True)
    (wav_path / "bye.wav").write_bytes(b"RIFF")

    r = Responder(index_path=idx, base_dir=str(tmp_path))
    resp = r.get_response("goodbye", ai=None)
    assert resp.intent == "DISMISSAL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_responder.py -v`
Expected: FAIL

- [ ] **Step 3: Write responder.py**

`scripts/responder.py`:
```python
"""Response priority chain for BenderPi.

Classifies user text, resolves through the priority chain, returns a Response.
Both the conversation loop and a future web UI consume this module.

Priority:
  1. Real Bender clip (speech/wav/)
  2. Pre-generated TTS (speech/responses/<category>/)
  3. Promoted TTS (speech/responses/promoted/)
  4. Dynamic handler (briefings, HA control)
  5. AI fallback (Claude API + TTS)
"""

import json
import os
import random
from dataclasses import dataclass

from config import cfg
from logger import get_logger
from metrics import metrics
import intent as intent_mod

log = get_logger("responder")


@dataclass
class Response:
    text: str
    wav_path: str
    method: str         # real_clip | pre_gen_tts | promoted_tts | handler_weather | handler_news | handler_ha | ai_fallback | error_fallback
    intent: str
    sub_key: str | None
    is_temp: bool       # True if caller must os.unlink(wav_path)
    needs_thinking: bool  # True if response required generation time
    model: str | None


class Responder:
    """Resolves user text to a Response via the priority chain."""

    def __init__(self, index_path: str = None, base_dir: str = None):
        self._base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        idx_path = index_path or os.path.join(self._base_dir, "speech", "responses", "index.json")
        with open(idx_path) as f:
            self._index = json.load(f)

    def _full_path(self, relative: str) -> str:
        return os.path.join(self._base_dir, relative)

    def _is_pre_gen(self, path: str) -> bool:
        return "speech/responses" in path.replace(self._base_dir, "")

    def _pick_clip(self, intent_name: str, sub_key: str = None) -> str | None:
        if intent_name == "PERSONAL":
            path = self._index.get("personal", {}).get(sub_key)
            return self._full_path(path) if path else None
        clips = self._index.get(intent_name.lower())
        if not clips or not isinstance(clips, list):
            return None
        return self._full_path(random.choice(clips))

    def get_response(self, user_text: str, ai) -> Response:
        """Classify intent, resolve through priority chain, return Response."""
        with metrics.timer("response_total"):
            intent_name, sub_key = intent_mod.classify(user_text)
            log.info("Intent: %s%s", intent_name, f" / {sub_key}" if sub_key else "")
            metrics.count("intent", intent=intent_name)

            # --- PROMOTED ---
            if intent_name == "PROMOTED":
                clip_path = self._full_path(sub_key)
                if os.path.exists(clip_path):
                    return Response(
                        text=os.path.basename(clip_path), wav_path=clip_path,
                        method="promoted_tts", intent="PROMOTED", sub_key=None,
                        is_temp=False, needs_thinking=False, model=None,
                    )
                # File missing — fall through to AI
                return self._respond_ai(user_text, ai, "PROMOTED")

            # --- GREETING / AFFIRMATION / JOKE / PERSONAL / DISMISSAL ---
            if intent_name in ("GREETING", "AFFIRMATION", "JOKE", "PERSONAL", "DISMISSAL"):
                clip = self._pick_clip(intent_name, sub_key)
                if clip and os.path.exists(clip):
                    method = "pre_gen_tts" if self._is_pre_gen(clip) else "real_clip"
                    return Response(
                        text=os.path.basename(clip), wav_path=clip,
                        method=method, intent=intent_name, sub_key=sub_key,
                        is_temp=False, needs_thinking=False, model=None,
                    )
                return self._respond_ai(user_text, ai, intent_name, sub_key)

            # --- WEATHER ---
            if intent_name == "WEATHER":
                return self._respond_handler("weather", user_text, ai, intent_name)

            # --- NEWS ---
            if intent_name == "NEWS":
                return self._respond_handler("news", user_text, ai, intent_name)

            # --- HA_CONTROL ---
            if intent_name == "HA_CONTROL":
                return self._respond_handler("ha", user_text, ai, intent_name)

            # --- UNKNOWN ---
            return self._respond_ai(user_text, ai)

    def _respond_handler(self, handler_type: str, user_text: str, ai, intent_name: str) -> Response:
        """Call a dynamic handler, falling back to AI on failure."""
        try:
            if handler_type == "weather":
                import briefings
                wav = briefings.get_weather_wav()
                return Response(
                    text="(weather briefing)", wav_path=wav,
                    method="handler_weather", intent=intent_name, sub_key=None,
                    is_temp=False, needs_thinking=False, model=None,
                )
            elif handler_type == "news":
                import briefings
                wav = briefings.get_news_wav()
                return Response(
                    text="(news briefing)", wav_path=wav,
                    method="handler_news", intent=intent_name, sub_key=None,
                    is_temp=False, needs_thinking=False, model=None,
                )
            elif handler_type == "ha":
                from handlers import ha_control
                wav = ha_control.control(user_text)
                return Response(
                    text="(HA control)", wav_path=wav,
                    method="handler_ha", intent=intent_name, sub_key=None,
                    is_temp=True, needs_thinking=True, model=None,
                )
        except Exception as e:
            log.error("%s handler error: %s", handler_type, e)
            metrics.count("error", category=f"handler_{handler_type}")
            return self._respond_ai(user_text, ai, intent_name)

    def _respond_ai(self, user_text: str, ai, intent_name: str = "UNKNOWN", sub_key: str = None) -> Response:
        """Call AI fallback."""
        if ai is None:
            import tts_generate
            text = "My AI brain isn't connected right now. Try again later."
            wav = tts_generate.speak(text)
            return Response(
                text=text, wav_path=wav, method="error_fallback",
                intent=intent_name, sub_key=sub_key, is_temp=True,
                needs_thinking=False, model=None,
            )
        try:
            wav = ai.respond(user_text)
            reply = ai.history[-1]["content"] if ai.history else ""
            return Response(
                text=reply, wav_path=wav, method="ai_fallback",
                intent=intent_name, sub_key=sub_key, is_temp=True,
                needs_thinking=True, model=cfg.ai_model,
            )
        except Exception as e:
            log.error("AI fallback error: %s", e)
            metrics.count("error", category="ai_fallback")
            import tts_generate
            text = f"Something went very wrong. Error: {type(e).__name__}."
            wav = tts_generate.speak(text)
            return Response(
                text=str(e), wav_path=wav, method="error_fallback",
                intent=intent_name, sub_key=sub_key, is_temp=True,
                needs_thinking=False, model=None,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_responder.py -v`
Expected: 4 passed

- [ ] **Step 5: Refactor wake_converse.py to use responder**

Replace the inline response chain in `run_session()` with:

```python
from responder import Responder

# In main() or module-level:
responder = Responder()

# In run_session():
while True:
    text = stt.listen_and_transcribe()
    if not text:
        if time.time() - last_heard > SILENCE_TIMEOUT:
            log.info("Silence timeout -- ending session")
            logger.session_end("timeout")
            audio.close_session()
            return
        continue

    last_heard = time.time()
    log.info("Heard: %r", text)

    response = responder.get_response(text, ai)

    # Play thinking sound if needed
    if response.needs_thinking and cfg.thinking_sound:
        thinking_clips = _get_thinking_clips()
        if thinking_clips:
            audio.play(random.choice(thinking_clips))

    # Play response
    audio.play(response.wav_path)
    if response.is_temp:
        try:
            os.unlink(response.wav_path)
        except OSError:
            pass

    logger.log_turn(text, response.intent, response.sub_key,
                    response.method, response.text, response.model)

    if response.intent == "DISMISSAL":
        logger.session_end("dismissal")
        audio.close_session()
        return

    last_heard = time.time()
```

Note: thinking clips don't exist yet — `_get_thinking_clips()` returns an empty list for now. They'll be added in Task 12.

**Important:** Also fix the greeting and dismissal TTS fallback paths (when no clip file exists and TTS is generated on the fly) to use `try/finally` for temp file cleanup. This addresses spec section 2.4 (temp file leak). All paths where `tts_generate.speak()` creates a temp file must have the `os.unlink()` in a `finally` block:

```python
# Greeting fallback
text = "Yo. What do you want?"
wav = tts_generate.speak(text)
try:
    audio.play(wav)
finally:
    os.unlink(wav)
```

- [ ] **Step 6: Commit**

```bash
git add scripts/responder.py tests/test_responder.py scripts/wake_converse.py
git commit -m "Extract response priority chain into responder.py, slim wake_converse.py"
```

---

### Task 9: Decouple ha_control — execute/control split

**Files:**
- Modify: `scripts/handlers/ha_control.py`
- Create: `tests/test_ha_control.py`

- [ ] **Step 1: Write the failing test**

`tests/test_ha_control.py`:
```python
"""Tests for HA control execute/control split."""

def test_parse_action_on():
    from handlers.ha_control import _parse_action
    assert _parse_action("turn on the lights") == "on"
    assert _parse_action("switch off the kitchen") == "off"
    assert _parse_action("what time is it") is None

def test_parse_room_term():
    from handlers.ha_control import _parse_room_term
    assert _parse_room_term("turn on the lights in my office") == "office"
    assert _parse_room_term("kitchen lights off") == "kitchen"

def test_parse_temperature():
    from handlers.ha_control import _parse_temperature
    assert _parse_temperature("set it to 21 degrees") == 21.0
    assert _parse_temperature("hello") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ha_control.py -v`
Expected: PASS or FAIL depending on import path resolution.

- [ ] **Step 3: Add execute() function to ha_control.py**

Add above the existing `control()` function:

```python
def execute(user_text: str) -> dict:
    """Parse user_text, call HA, return structured result.
    Returns: {
        "action": "on" | "off" | "set_temp" | None,
        "entities": [{"entity_id": str, "friendly_name": str, "success": bool}],
        "room_display": str,
        "temperature": float | None,
        "error": None | "no_room" | "no_match" | "no_action" | "ha_failed",
    }
    """
    action = _parse_action(user_text)
    room_term = _parse_room_term(user_text)

    if not room_term:
        return {"action": action, "entities": [], "room_display": "",
                "temperature": None, "error": "no_room"}

    matches = _find_entities(room_term)
    if not matches:
        return {"action": action, "entities": [], "room_display": room_term,
                "temperature": None, "error": "no_match"}

    # Temperature set
    target_temp = _parse_temperature(user_text)
    if target_temp and any(e["domain"] == "climate" for e in matches):
        results = []
        for e in [x for x in matches if x["domain"] == "climate"]:
            log.info("HA: climate.set_temperature → %s @ %s°", e["entity_id"], target_temp)
            success = _ha_call("climate", "set_temperature", e["entity_id"], {"temperature": target_temp})
            results.append({"entity_id": e["entity_id"], "friendly_name": e["friendly_name"], "success": success})
        room_name = _normalise(matches[0]["friendly_name"]).title()
        return {"action": "set_temp", "entities": results, "room_display": room_name,
                "temperature": target_temp, "error": None if any(r["success"] for r in results) else "ha_failed"}

    if action is None:
        return {"action": None, "entities": [], "room_display": room_term,
                "temperature": None, "error": "no_action"}

    # On/off
    results = []
    for e in matches:
        domain = e["domain"]
        if domain == "climate":
            hvac_mode = "heat" if action == "on" else "off"
            log.info("HA: climate.set_hvac_mode(%s) → %s", hvac_mode, e["entity_id"])
            success = _ha_call(domain, "set_hvac_mode", e["entity_id"], {"hvac_mode": hvac_mode})
        else:
            service = f"turn_{action}"
            log.info("HA: %s.%s → %s", domain, service, e["entity_id"])
            success = _ha_call(domain, service, e["entity_id"])
        results.append({"entity_id": e["entity_id"], "friendly_name": e["friendly_name"], "success": success})

    names = list({e["friendly_name"] for e in matches})
    display = _normalise(names[0]).title() if names else room_term.title()
    return {"action": action, "entities": results, "room_display": display,
            "temperature": None, "error": None if any(r["success"] for r in results) else "ha_failed"}
```

Then refactor `control()` to call `execute()`:

```python
def control(user_text: str) -> str:
    """Execute + wrap result in Bender TTS. Returns temp WAV path."""
    result = execute(user_text)
    text = _result_to_speech(result)
    return tts_generate.speak(text)

def _result_to_speech(result: dict) -> str:
    """Convert execute() result dict to Bender-style speech text."""
    error = result.get("error")
    if error == "no_room":
        return random.choice(UNKNOWN_ROOM_RESPONSES)
    if error == "no_match":
        return f"No idea what {result['room_display']!r} is. Check I have access to it in HA."
    if error == "no_action":
        return "On or off? Even I need a bit more to go on."
    if error == "ha_failed":
        return random.choice(FAILED_RESPONSES)

    action = result["action"]
    room = result["room_display"]
    if action == "set_temp":
        return f"Temperature set to {int(result['temperature'])} degrees in {room}. Don't blame me if you melt."
    templates = ON_RESPONSES if action == "on" else OFF_RESPONSES
    return random.choice(templates).format(room=room)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_ha_control.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/handlers/ha_control.py tests/test_ha_control.py
git commit -m "Split ha_control into execute() + control() for web UI readiness"
```

---

### Task 10: Decouple briefings — text/wav split

**Files:**
- Modify: `scripts/briefings.py`

- [ ] **Step 1: Extract text generation functions**

Rename `_generate_weather()` to `get_weather_text()` (public) and `_generate_news()` to `get_news_text()` (public). Keep them as the data layer. The existing `get_weather_wav()` and `get_news_wav()` call these internally.

```python
def get_weather_text() -> str:
    """Fetch weather data, return Bender-style text. No TTS."""
    # (existing _generate_weather code, unchanged)

def get_news_text() -> str:
    """Fetch news headlines, return Bender-style text. No TTS."""
    # (existing _generate_news code, unchanged)

def get_weather_wav() -> str:
    """Return path to cached weather briefing WAV, refreshing if stale."""
    if not _is_fresh("weather", WEATHER_TTL) or not os.path.exists(WEATHER_WAV):
        try:
            text = get_weather_text()  # was _generate_weather()
            with metrics.timer("briefing_generate", briefing="weather"):
                wav = tts_generate.speak(text)
            shutil.move(wav, WEATHER_WAV)
            _mark_fresh("weather")
            log.info("[briefing] Weather refreshed")
        except Exception as e:
            # ... existing fallback logic ...
    return WEATHER_WAV
```

Same pattern for news.

- [ ] **Step 2: Commit**

```bash
git add scripts/briefings.py
git commit -m "Expose get_weather_text() and get_news_text() for web UI readiness"
```

---

## Chunk 3: Audio & Speed Improvements

### Task 11: Audio improvements — configurable gaps, play_oneshot, shared PyAudio in STT

**Files:**
- Modify: `scripts/audio.py`
- Modify: `scripts/stt.py`

- [ ] **Step 1: Update audio.py to use config for silence gaps**

```python
from config import cfg

# Replace hardcoded constants:
SILENCE_PRE  = cfg.silence_pre    # was 0.05
SILENCE_POST = cfg.silence_post   # was 0.08
```

Log values at module load:
```python
log.debug("Audio config: silence_pre=%.3fs, silence_post=%.3fs", SILENCE_PRE, SILENCE_POST)
```

- [ ] **Step 2: Add play_oneshot() to audio.py**

```python
def play_oneshot(wav_path: str):
    """Open stream, play clip, close stream. For use outside a session.
    Thread-safe — blocks behind _lock if a session is active.
    """
    with _lock:
        was_open = _stream is not None and _stream.is_active()
        if not was_open:
            stream = _pa.open(
                format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE,
                output=True, output_device_index=OUTPUT_DEVICE,
                frames_per_buffer=CHUNK,
            )
        else:
            stream = _stream

        try:
            stream.write(_silence(SILENCE_PRE))
            with wave.open(wav_path, 'rb') as wf:
                sw = wf.getsampwidth()
                data = wf.readframes(CHUNK)
                while data:
                    stream.write(data)
                    leds.set_level(rms_to_ratio(rms(data, sw)))
                    data = wf.readframes(CHUNK)
            stream.write(_silence(SILENCE_POST))
        finally:
            if not was_open:
                stream.stop_stream()
                stream.close()
    leds.all_off()
```

- [ ] **Step 3: Fix stt.py to use shared PyAudio instance**

In `scripts/stt.py`, replace the PyAudio creation in `_record_utterance()`:

```python
# BEFORE:
import pyaudio
pa = pyaudio.PyAudio()
# ... recording code ...
pa.terminate()

# AFTER:
import audio as audio_mod

def _record_utterance() -> bytes:
    pa = audio_mod.get_pa()  # shared instance
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=int(SAMPLE_RATE * FRAME_MS / 1000),
        input_device_index=AUDIO_DEVICE,
    )
    # ... recording logic unchanged ...
    finally:
        stream.stop_stream()
        stream.close()
        # DO NOT call pa.terminate() — shared instance
    return b"".join(frames)
```

- [ ] **Step 4: Add direct PCM-to-Whisper (skip temp file)**

In `stt.py`, modify `listen_and_transcribe()`:

```python
import numpy as np

def listen_and_transcribe() -> str:
    with metrics.timer("stt_record"):
        pcm = _record_utterance()
    if len(pcm) < FRAME_BYTES * 3:
        metrics.count("stt_empty", pcm_bytes=len(pcm))
        return ""
    # Direct PCM → numpy → Whisper (no temp file)
    audio_array = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    model = _load_model()
    with metrics.timer("stt_transcribe", model=cfg.whisper_model):
        segments, _ = model.transcribe(audio_array, language="en", beam_size=1)
        text = " ".join(s.text for s in segments).strip()
    return text
```

Remove the `_pcm_to_wav()` function and the old `transcribe()` function (keep it for standalone test use under `__main__` if needed).

- [ ] **Step 5: Add Whisper hallucination filter**

In `stt.py`:

```python
WHISPER_HALLUCINATIONS = {
    "thank you", "thanks for watching", "subscribe",
    "like and subscribe", "thanks for listening",
    "please subscribe", "thank you for watching",
    "you", "the", "i", "a", "so", "okay",
}

def listen_and_transcribe() -> str:
    # ... after getting text from Whisper ...
    if text.lower().strip().rstrip(".!?,") in WHISPER_HALLUCINATIONS:
        log.warning("Whisper hallucination filtered: %r (pcm_bytes=%d)", text, len(pcm))
        metrics.count("stt_hallucination", text=text, pcm_bytes=len(pcm))
        return ""
    return text
```

- [ ] **Step 6: Commit**

```bash
git add scripts/audio.py scripts/stt.py
git commit -m "Audio improvements: configurable gaps, play_oneshot, shared PyAudio, direct PCM, hallucination filter"
```

---

### Task 12: Persistent Piper subprocess + thinking sounds

**Files:**
- Modify: `scripts/tts_generate.py`
- Modify: `scripts/prebuild_responses.py`
- Modify: `scripts/wake_converse.py`

- [ ] **Step 1: Check if Piper supports --json-input**

This must be verified on the Pi. SSH in and run:
```bash
ssh pi@192.168.68.132 "/home/pi/bender/piper/piper --help 2>&1 | grep json"
```

If `--json-input` is supported, proceed with persistent process. If not, implement the warm-up fallback (run a dummy synthesis at startup to pre-load the model, then continue with subprocess-per-call).

- [ ] **Step 2: Implement persistent Piper process (or warm-up fallback)**

In `scripts/tts_generate.py`, add a `PiperProcess` class:

```python
import threading

class PiperProcess:
    """Manages a persistent Piper process for low-latency TTS."""

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()

    def _ensure_running(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        piper_dir = os.path.dirname(cfg.piper_bin)
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = piper_dir + ":" + env.get("LD_LIBRARY_PATH", "")
        self._proc = subprocess.Popen(
            [cfg.piper_bin, "--model", cfg.model_path, "--json-input"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        log.info("Piper process started (pid=%d)", self._proc.pid)

    def speak(self, text: str) -> str:
        """Send text via --json-input, read WAV from output_file, return temp file path."""
        with self._lock:
            try:
                self._ensure_running()
                # Create temp file for Piper to write to
                raw_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                raw_tmp.close()
                # Send JSON request with output_file path
                req = json.dumps({"text": text, "output_file": raw_tmp.name}) + "\n"
                self._proc.stdin.write(req.encode())
                self._proc.stdin.flush()
                # Piper writes a JSON response to stdout when done
                response_line = self._proc.stdout.readline()
                if not response_line:
                    raise RuntimeError("Piper process produced no output")
                # Post-process: resample 22050→44100
                out_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                out_tmp.close()
                try:
                    _resample_and_pad(raw_tmp.name, out_tmp.name)
                finally:
                    os.unlink(raw_tmp.name)
                return out_tmp.name
            except Exception as e:
                log.warning("Persistent Piper failed, falling back to subprocess: %s", e)
                self._proc = None  # force restart next call
                return _speak_subprocess(text)

_piper = None  # initialized lazily
```

Before adding `PiperProcess`, first rename the existing `speak()` function to `_speak_subprocess()` so it can be used as the fallback. Then create the new `speak()` that delegates to `PiperProcess` or falls back to `_speak_subprocess()`.

If `--json-input` is not available, use a simpler warm-up approach:
```python
def _warm_up():
    """Run a dummy synthesis at startup to pre-load the model."""
    log.info("Warming up Piper TTS...")
    try:
        wav = _speak_subprocess("test")
        os.unlink(wav)
        log.info("Piper warm-up complete")
    except Exception as e:
        log.warning("Piper warm-up failed: %s", e)
```

- [ ] **Step 3: Add thinking sounds to prebuild_responses.py**

Add to `scripts/prebuild_responses.py`:

```python
THINKING_SOUNDS = [
    "Hmm.",
    "Let me think.",
    "Hang on.",
    "One sec.",
]

def build_thinking():
    out_dir = os.path.join(RESPONSES_DIR, "thinking")
    os.makedirs(out_dir, exist_ok=True)
    for i, text in enumerate(THINKING_SOUNDS, 1):
        generate(text, os.path.join(out_dir, f"thinking_{i:03d}.wav"))
```

Add `build_thinking()` call to the `__main__` block. Add thinking clips to `index.json`:

```python
# In build_index():
index["thinking"] = [
    f"speech/responses/thinking/thinking_{i:03d}.wav"
    for i in range(1, len(THINKING_SOUNDS) + 1)
]
```

- [ ] **Step 4: Wire thinking sounds in wake_converse.py**

The responder already returns `needs_thinking`. In `wake_converse.py`, load thinking clips from the index and play one before the main response when `needs_thinking` is True:

```python
# Load thinking clips at startup
_thinking_clips = [
    os.path.join(BASE_DIR, p)
    for p in INDEX.get("thinking", [])
    if os.path.exists(os.path.join(BASE_DIR, p))
]

# In the session loop, after get_response():
if response.needs_thinking and cfg.thinking_sound and _thinking_clips:
    audio.play(random.choice(_thinking_clips))
```

- [ ] **Step 5: Commit**

```bash
git add scripts/tts_generate.py scripts/prebuild_responses.py scripts/wake_converse.py
git commit -m "Add persistent Piper process, thinking sounds, and TTS warm-up"
```

---

## Chunk 4: Intent Hardening, Handover, Watchdog

### Task 13: Intent hardening

**Files:**
- Modify: `scripts/intent.py`
- Create: `tests/test_intent.py`

- [ ] **Step 1: Write the failing tests (new false-positive cases)**

`tests/test_intent.py`:
```python
"""Tests for intent classifier — focus on false positive prevention."""

from intent import classify

# === True positives (should still match) ===

def test_greeting_hello():
    assert classify("hello")[0] == "GREETING"

def test_dismissal_bye():
    assert classify("bye")[0] == "DISMISSAL"

def test_affirmation_thanks():
    assert classify("thanks")[0] == "AFFIRMATION"

def test_joke_request():
    assert classify("tell me a joke")[0] == "JOKE"

def test_weather():
    assert classify("what's the weather like")[0] == "WEATHER"

def test_news():
    assert classify("what's the news")[0] == "NEWS"

def test_ha_lights_on():
    assert classify("turn on the kitchen lights")[0] == "HA_CONTROL"

def test_personal_age():
    intent, sub = classify("how old are you")
    assert intent == "PERSONAL"
    assert sub == "age"

# === False positives (should NOT match simple intents) ===

def test_good_restaurant_not_affirmation():
    """'good' in a longer sentence should NOT match AFFIRMATION."""
    intent, _ = classify("what is a good restaurant near here")
    assert intent != "AFFIRMATION"

def test_stop_in_sentence_not_dismissal():
    """'don't stop' should NOT match DISMISSAL."""
    intent, _ = classify("please don't stop the music")
    assert intent != "DISMISSAL"

def test_ok_in_question_not_affirmation():
    """'ok' in a question should NOT match AFFIRMATION."""
    intent, _ = classify("is it ok to eat cheese before bed")
    assert intent != "AFFIRMATION"

def test_home_lights_not_personal():
    """'home' with 'lights' should match HA_CONTROL, not PERSONAL."""
    intent, _ = classify("turn on the home lights")
    assert intent == "HA_CONTROL"

def test_good_morning_not_affirmation():
    """'good morning' should match GREETING, not AFFIRMATION."""
    intent, _ = classify("good morning")
    assert intent == "GREETING"

def test_long_utterance_not_simple():
    """Long sentence with 'good' should not match AFFIRMATION."""
    intent, _ = classify("can you tell me what is a good way to learn python programming")
    assert intent == "UNKNOWN"

# === Multi-match logging (just verify classify returns something) ===

def test_unknown_fallthrough():
    assert classify("explain quantum entanglement to me")[0] == "UNKNOWN"
```

- [ ] **Step 2: Run tests — some should fail with current patterns**

Run: `python -m pytest tests/test_intent.py -v`
Expected: Several FAIL on the false-positive tests.

- [ ] **Step 3: Tighten patterns and reorder priority**

Update `scripts/intent.py`:

**Tighten AFFIRMATION patterns:**
```python
AFFIRMATION_PATTERNS = [
    r"\bthank(s| you)\b",
    r"^(great|brilliant|awesome|cheers|nice one)$",  # standalone only
    r"^ok(ay)?(\s+bender)?$",  # standalone only
]
```

**Tighten DISMISSAL patterns:**
```python
DISMISSAL_PATTERNS = [
    r"^bye\b",
    r"\bgoodbye\b",
    r"\bsee you\b",
    r"^stop(\s+(it|bender))?$",  # standalone or "stop it" / "stop bender"
    r"\bthat'?s?\s*all\b",
    r"\bno more\b",
]
```

**Add GREETING patterns for "good morning/evening":**
```python
GREETING_PATTERNS = [
    r"\b(hello|hi|hey|howdy)\b",
    r"\bgood (morning|evening|afternoon)\b",  # NEW — was falling to AFFIRMATION
    r"\bhow are you\b",
    r"\byou there\b",
    r"\bwake up\b",
    r"\byo\b",
]
```

**Fix PERSONAL/job pattern:**
```python
("job", r"\b(job|purpose|programmed|function)\b|\bwhat do you do\b"),  # removed bare "do"
```

**Fix PERSONAL/where_live pattern:**
```python
("where_live", r"\b(where.{0,10}(live|from|come from)|where are you from)\b(?!.*\b(light|lamp|on|off|heating)\b)"),
```

**Reorder priority (specific → vague):**
```python
def classify(text: str) -> tuple[str, str | None]:
    t = text.strip().lower()
    word_count = len(t.split())

    # Most specific first
    if _match_any(t, HA_CONTROL_PATTERNS):
        return ("HA_CONTROL", None)
    if _match_any(t, WEATHER_PATTERNS):
        return ("WEATHER", None)
    if _match_any(t, NEWS_PATTERNS):
        return ("NEWS", None)
    if _match_any(t, DISMISSAL_PATTERNS):
        return ("DISMISSAL", None)
    if _match_any(t, JOKE_PATTERNS):
        return ("JOKE", None)

    # Personal questions
    for sub_key, pattern in PERSONAL_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return ("PERSONAL", sub_key)

    # Promoted responses
    for entry in _promoted_patterns():
        if re.search(entry["pattern"], t, re.IGNORECASE):
            return ("PROMOTED", entry["file"])

    # Vague catch-all intents — only match on short utterances
    if word_count <= cfg.simple_intent_max_words:
        if _match_any(t, GREETING_PATTERNS):
            return ("GREETING", None)
        if _match_any(t, AFFIRMATION_PATTERNS):
            return ("AFFIRMATION", None)
    else:
        # Long utterance — log if it would have matched a simple intent
        for name, patterns in [("GREETING", GREETING_PATTERNS), ("AFFIRMATION", AFFIRMATION_PATTERNS)]:
            if _match_any(t, patterns):
                log.info("Long utterance (%d words) would match %s, falling through to UNKNOWN", word_count, name)

    return ("UNKNOWN", None)
```

**Add multi-match logging:**
```python
def _check_all_intents(t: str) -> list[str]:
    """Check which intents would match (for diagnostic logging)."""
    matched = []
    if _match_any(t, HA_CONTROL_PATTERNS): matched.append("HA_CONTROL")
    if _match_any(t, WEATHER_PATTERNS): matched.append("WEATHER")
    if _match_any(t, NEWS_PATTERNS): matched.append("NEWS")
    if _match_any(t, DISMISSAL_PATTERNS): matched.append("DISMISSAL")
    if _match_any(t, JOKE_PATTERNS): matched.append("JOKE")
    if _match_any(t, GREETING_PATTERNS): matched.append("GREETING")
    if _match_any(t, AFFIRMATION_PATTERNS): matched.append("AFFIRMATION")
    for sub_key, pattern in PERSONAL_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            matched.append(f"PERSONAL/{sub_key}")
    return matched
```

Call this at the end of `classify()` (before returning) and log if >1 match:
```python
all_matches = _check_all_intents(t)
if len(all_matches) > 1:
    log.warning("Multi-match: %s — resolved to %s", all_matches, result[0])
    metrics.count("intent_multi_match", resolved=result[0], others=str(all_matches))
```

- [ ] **Step 4: Run tests to verify they all pass**

Run: `python -m pytest tests/test_intent.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add scripts/intent.py tests/test_intent.py
git commit -m "Harden intent patterns: fix false positives, reorder priority, add multi-match logging"
```

---

### Task 14: Status report generator + watchdog

**Files:**
- Create: `scripts/generate_status.py`
- Create: `scripts/watchdog.py`
- Create: `watchdog_config.json`
- Create: `tests/test_watchdog.py`

- [ ] **Step 1: Create watchdog_config.json**

`watchdog_config.json`:
```json
{
  "stt_empty_rate_threshold": 0.10,
  "api_fallback_rate_threshold": 0.20,
  "error_rate_threshold": 0.05,
  "stt_latency_threshold_ms": 4000,
  "tts_latency_threshold_ms": 3000,
  "api_latency_threshold_ms": 5000,
  "promote_candidate_min_hits": 3,
  "briefing_stale_weather_s": 7200,
  "briefing_stale_news_s": 21600,
  "min_avg_session_turns": 1.5,
  "log_gap_threshold_s": 86400,
  "lookback_hours": 168
}
```

- [ ] **Step 2: Write watchdog tests**

`tests/test_watchdog.py`:
```python
"""Tests for health watchdog."""
import json

def _write_metrics(tmp_path, events):
    path = tmp_path / "metrics.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return str(path)

def test_high_error_rate_triggers_alert(tmp_path):
    from watchdog import run_checks
    events = [
        {"ts": "2026-03-15T10:00:00Z", "type": "count", "name": "error", "category": "tts"},
    ] * 10 + [
        {"ts": "2026-03-15T10:00:00Z", "type": "count", "name": "intent", "intent": "GREETING"},
    ] * 10
    metrics_path = _write_metrics(tmp_path, events)
    config = {"error_rate_threshold": 0.05, "lookback_hours": 168}
    alerts = run_checks(metrics_path=metrics_path, conversation_dir=str(tmp_path), config=config)
    error_alerts = [a for a in alerts if a.check == "error_rate"]
    assert len(error_alerts) > 0
    assert error_alerts[0].severity == "error"

def test_no_alerts_when_healthy(tmp_path):
    from watchdog import run_checks
    events = [
        {"ts": "2026-03-15T10:00:00Z", "type": "count", "name": "intent", "intent": "GREETING"},
    ] * 100
    metrics_path = _write_metrics(tmp_path, events)
    config = {"error_rate_threshold": 0.05, "lookback_hours": 168}
    alerts = run_checks(metrics_path=metrics_path, conversation_dir=str(tmp_path), config=config)
    error_alerts = [a for a in alerts if a.severity == "error"]
    assert len(error_alerts) == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_watchdog.py -v`
Expected: FAIL

- [ ] **Step 4: Write watchdog.py**

`scripts/watchdog.py`:
```python
"""Health watchdog — analyses metrics and logs to detect anomalies.

Not a daemon. Called by generate_status.py.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from logger import get_logger

log = get_logger("watchdog")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_METRICS = os.path.join(_BASE_DIR, "logs", "metrics.jsonl")
_DEFAULT_CONV_DIR = os.path.join(_BASE_DIR, "logs")
_DEFAULT_CONFIG = os.path.join(_BASE_DIR, "watchdog_config.json")


@dataclass
class Alert:
    severity: str   # "info" | "warning" | "error"
    check: str
    message: str
    data: dict = field(default_factory=dict)


def _load_config(config_path: str = None) -> dict:
    path = config_path or _DEFAULT_CONFIG
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _load_metrics(path: str, lookback_hours: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    events = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                ts = event.get("ts", "")
                try:
                    if datetime.fromisoformat(ts) >= cutoff:
                        events.append(event)
                except Exception:
                    events.append(event)  # include if we can't parse ts
    except FileNotFoundError:
        pass
    return events


def run_checks(metrics_path: str = None, conversation_dir: str = None,
               config: dict = None) -> list[Alert]:
    """Run all health checks, return list of alerts."""
    cfg = config or _load_config()
    lookback = cfg.get("lookback_hours", 168)
    events = _load_metrics(metrics_path or _DEFAULT_METRICS, lookback)

    alerts = []

    # Count events by type
    error_counts = [e for e in events if e.get("type") == "count" and e.get("name") == "error"]
    intent_counts = [e for e in events if e.get("type") == "count" and e.get("name") == "intent"]
    stt_empty = [e for e in events if e.get("type") == "count" and e.get("name") == "stt_empty"]
    api_calls = [e for e in events if e.get("type") == "count" and e.get("name") == "api_call"]
    total_turns = len(intent_counts)

    # Error rate
    if total_turns > 0:
        error_rate = len(error_counts) / total_turns
        threshold = cfg.get("error_rate_threshold", 0.05)
        if error_rate > threshold:
            alerts.append(Alert(
                severity="error", check="error_rate",
                message=f"Error rate {error_rate:.0%} exceeds {threshold:.0%} threshold",
                data={"error_rate": error_rate, "errors": len(error_counts), "turns": total_turns},
            ))

    # API fallback rate
    if total_turns > 0:
        api_rate = len(api_calls) / total_turns
        threshold = cfg.get("api_fallback_rate_threshold", 0.20)
        if api_rate > threshold:
            alerts.append(Alert(
                severity="warning", check="api_fallback_rate",
                message=f"API fallback rate {api_rate:.0%} exceeds {threshold:.0%}",
                data={"api_rate": api_rate},
            ))

    # STT empty rate
    stt_total = len(intent_counts) + len(stt_empty)
    if stt_total > 0:
        empty_rate = len(stt_empty) / stt_total
        threshold = cfg.get("stt_empty_rate_threshold", 0.10)
        if empty_rate > threshold:
            alerts.append(Alert(
                severity="warning", check="stt_empty_rate",
                message=f"STT empty rate {empty_rate:.0%} exceeds {threshold:.0%}",
                data={"empty_rate": empty_rate},
            ))

    # Latency checks
    for metric_name, config_key, label in [
        ("stt_transcribe", "stt_latency_threshold_ms", "STT"),
        ("tts_generate", "tts_latency_threshold_ms", "TTS"),
        ("ai_api_call", "api_latency_threshold_ms", "API"),
    ]:
        timers = [e for e in events if e.get("type") == "timer" and e.get("name") == metric_name]
        if timers:
            avg_ms = sum(e.get("duration_ms", 0) for e in timers) / len(timers)
            threshold = cfg.get(config_key, 5000)
            if avg_ms > threshold:
                alerts.append(Alert(
                    severity="warning", check=f"{metric_name}_latency",
                    message=f"{label} avg latency {avg_ms:.0f}ms exceeds {threshold}ms",
                    data={"avg_ms": avg_ms, "samples": len(timers)},
                ))

    return alerts
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_watchdog.py -v`
Expected: 2 passed

- [ ] **Step 6: Write generate_status.py**

`scripts/generate_status.py`:
```python
"""Auto-generate STATUS.md from metrics, logs, and watchdog checks.

Usage: venv/bin/python scripts/generate_status.py
Triggered by: git_pull.sh after a successful pull.
"""

import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone, timedelta

from watchdog import run_checks, _load_metrics

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_METRICS_PATH = os.path.join(_BASE_DIR, "logs", "metrics.jsonl")
_STATUS_PATH = os.path.join(_BASE_DIR, "STATUS.md")
_LOG_PATH = os.path.join(_BASE_DIR, "logs", "bender.log")


def _recent_git_log() -> str:
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, text=True, cwd=_BASE_DIR,
        )
        return result.stdout.strip() if result.returncode == 0 else "(git log unavailable)"
    except Exception:
        return "(git log unavailable)"


def _recent_errors() -> list[str]:
    errors = []
    try:
        with open(_LOG_PATH) as f:
            for line in f:
                if " ERROR " in line:
                    errors.append(line.strip())
    except FileNotFoundError:
        pass
    # Last 10 errors
    return errors[-10:]


def generate():
    events = _load_metrics(_METRICS_PATH, lookback_hours=168)
    alerts = run_checks()

    # Performance averages
    def avg_timer(name):
        timers = [e for e in events if e.get("type") == "timer" and e.get("name") == name]
        if not timers:
            return "N/A"
        avg = sum(e.get("duration_ms", 0) for e in timers) / len(timers)
        return f"{avg:.0f}ms"

    # Usage counts
    intent_events = [e for e in events if e.get("type") == "count" and e.get("name") == "intent"]
    total_turns = len(intent_events)
    api_calls = len([e for e in events if e.get("type") == "count" and e.get("name") == "api_call"])
    errors = len([e for e in events if e.get("type") == "count" and e.get("name") == "error"])
    local = total_turns - api_calls - errors

    intent_breakdown = Counter(e.get("intent", "?") for e in intent_events)
    top_intents = ", ".join(f"{k} {v}" for k, v in intent_breakdown.most_common(6))

    sessions = [e for e in events if e.get("type") == "count" and e.get("name") == "session" and e.get("event") == "start"]

    # Alerts
    alert_lines = []
    for a in alerts:
        icon = {"error": "!!!", "warning": "!", "info": ""}.get(a.severity, "")
        alert_lines.append(f"- {icon} [{a.severity.upper()}] {a.message}")
    if not alert_lines:
        alert_lines = ["- None"]

    # Recent errors
    recent = _recent_errors()
    error_lines = [f"- {e}" for e in recent[-5:]] if recent else ["- None"]

    # Git log
    git_log = _recent_git_log()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    status = f"""# BenderPi Status Report
Generated: {now}

## Health
- Errors (7d): {errors}
- Watchdog alerts: {len([a for a in alerts if a.severity in ('error', 'warning')])}

## Performance (7-day averages)
- STT record: {avg_timer("stt_record")}
- STT transcribe: {avg_timer("stt_transcribe")}
- TTS generation: {avg_timer("tts_generate")}
- API call: {avg_timer("ai_api_call")}
- Audio playback: {avg_timer("audio_play")}
- End-to-end response: {avg_timer("response_total")}

## Usage (7 days)
- Sessions: {len(sessions)} | Turns: {total_turns}
- Local: {local} ({100*local//total_turns if total_turns else 0}%) | API: {api_calls} | Errors: {errors}
- Top intents: {top_intents or 'N/A'}

## Attention Needed
{chr(10).join(alert_lines)}

## Recent Errors (from bender.log)
{chr(10).join(error_lines)}

## Recent Changes
{git_log}
"""
    with open(_STATUS_PATH, "w") as f:
        f.write(status)
    print(f"STATUS.md written to {_STATUS_PATH}")


if __name__ == "__main__":
    generate()
```

- [ ] **Step 7: Commit**

```bash
git add scripts/generate_status.py scripts/watchdog.py watchdog_config.json tests/test_watchdog.py
git commit -m "Add health watchdog and auto-generated STATUS.md report"
```

---

### Task 15: Session handover — HANDOVER.md, git_pull.sh, .gitignore, CLAUDE.md

**Files:**
- Create: `HANDOVER.md`
- Modify: `scripts/git_pull.sh`
- Modify: `.gitignore`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Create HANDOVER.md**

`HANDOVER.md`:
```markdown
# BenderPi Handover Context
Last updated: 2026-03-15

## Current Priorities
- Collect metrics baseline data (first week after deployment of observability changes)
- Monitor STT hallucination rate to decide if Whisper model upgrade is needed
- Watch intent multi-match warnings to identify patterns needing further tightening

## Recent Decisions
- Chose interleaved approach: foundation (logging/metrics) first, then modularity, then improvements
- Separated execute() from control() in ha_control.py for future web UI
- Extracted response chain into responder.py — wake_converse.py is now a thin orchestrator
- Chose not to purchase AI HAT+ for now — software improvements offer better ROI (see docs/ai-hat-plus-analysis.md)

## Known Issues
- Piper --json-input mode needs verification on the Pi (persistent subprocess may not be available)
- Thinking sounds need pre-generating on the Pi via prebuild_responses.py after deploy
- Intent false positives reduced but not eliminated — utterance-length heuristic may be too aggressive/conservative

## Future Considerations
- Web UI for log viewing, config adjustment, and puppet mode (architecture now supports it)
- Local ML intent classifier (collecting training data via improved logging)
- Whisper model upgrade to base.en or distil-whisper (needs metrics baseline first)
- Local LLM via llama.cpp to reduce API dependency
- AI HAT+ if camera/vision features are added (see docs/ai-hat-plus-analysis.md)
```

- [ ] **Step 2: Update git_pull.sh**

Add `generate_status.py` call after the service restart, guarded against failure:

```bash
# After: sudo systemctl restart "$SERVICE"
echo "Generating status report..."
"$REPO_DIR/venv/bin/python" "$REPO_DIR/scripts/generate_status.py" || true
echo "Done."
```

- [ ] **Step 3: Update .gitignore**

Add:
```
STATUS.md
logs/bender.log*
logs/metrics.jsonl*
```

- [ ] **Step 4: Update CLAUDE.md**

Add to the "Project Structure" section the new files. Add a new section:

```markdown
## Session Handover

- **`HANDOVER.md`** — committed context file for decisions, priorities, and notes between Claude sessions. Update this before finishing any session that makes changes.
- **`STATUS.md`** — auto-generated on the Pi by `scripts/generate_status.py`. Contains performance metrics, health alerts, usage stats. Read this at the start of a session for device status. Gitignored.
- **`scripts/generate_status.py`** — generates STATUS.md from logs/metrics. Called by git_pull.sh after deploys.
- **`scripts/watchdog.py`** — health anomaly detection, feeds into STATUS.md.
```

- [ ] **Step 5: Commit**

```bash
git add HANDOVER.md scripts/git_pull.sh .gitignore CLAUDE.md
git commit -m "Add session handover system: HANDOVER.md, STATUS.md generation, CLAUDE.md update"
```

---

## Summary

| Task | What | Commit message |
|---|---|---|
| 1 | Test infrastructure | "Add pytest test infrastructure" |
| 2 | Bug fixes + dead code | "Fix bugs: remove dead code, thread-safe briefings meta, remove duplicate check" |
| 3 | config.py | "Add centralised config module with JSON + env override support" |
| 4 | logger.py | "Add structured logging module with console + rotating file handlers" |
| 5 | Migrate print() | "Migrate all print() to structured logging via logger.py" |
| 6 | metrics.py | "Add metrics module with timer context manager and counter events" |
| 7 | Instrument modules | "Instrument all modules with timing and counter metrics" |
| 8 | responder.py | "Extract response priority chain into responder.py, slim wake_converse.py" |
| 9 | ha_control split | "Split ha_control into execute() + control() for web UI readiness" |
| 10 | briefings split | "Expose get_weather_text() and get_news_text() for web UI readiness" |
| 11 | Audio + STT | "Audio improvements: configurable gaps, play_oneshot, shared PyAudio, direct PCM, hallucination filter" |
| 12 | Piper + thinking | "Add persistent Piper process, thinking sounds, and TTS warm-up" |
| 13 | Intent hardening | "Harden intent patterns: fix false positives, reorder priority, add multi-match logging" |
| 14 | Watchdog + status | "Add health watchdog and auto-generated STATUS.md report" |
| 15 | Handover | "Add session handover system: HANDOVER.md, STATUS.md generation, CLAUDE.md update" |
