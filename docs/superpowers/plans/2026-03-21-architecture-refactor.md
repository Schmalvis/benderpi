# Architecture Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make BenderPi's architecture modular and extensible by introducing a handler registry, unifying config access, decoupling audio from LEDs, extracting timer alert logic, and centralising IPC paths.

**Architecture:** A `Handler` base class with intent-keyed dispatch replaces the if/elif chain in `responder.py`. Each response type becomes a discrete handler class. Infrastructure modules (`stt`, `tts_generate`, `ha_control`, `briefings`) switch from independent config reads to the shared `cfg` singleton. `audio.py` drops its hard `leds` import in favour of optional callbacks.

**Tech Stack:** Python 3.13, pytest, dataclasses, FastAPI (web layer)

**Spec:** `docs/superpowers/specs/2026-03-21-architecture-refactor-design.md`

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `scripts/handler_base.py` | `Response` dataclass, `Handler` base class, `load_clips_from_index()` utility |
| `scripts/handlers/clip_handler.py` | `RealClipHandler` — original WAV clip responses |
| `scripts/handlers/pregen_handler.py` | `PreGenHandler` — pre-built TTS responses |
| `scripts/handlers/promoted_handler.py` | `PromotedHandler` — promoted AI responses |
| `scripts/handlers/weather_handler.py` | `WeatherHandler` — weather briefing |
| `scripts/handlers/news_handler.py` | `NewsHandler` — news briefing |
| `scripts/handlers/ha_handler.py` | `HAHandler` — Home Assistant control |
| `scripts/handlers/timer_alert.py` | `TimerAlertRunner` — timer alert interaction |
| `tests/test_handler_base.py` | Tests for Response, Handler, load_clips_from_index |
| `tests/test_clip_handler.py` | Tests for RealClipHandler |
| `tests/test_pregen_handler.py` | Tests for PreGenHandler |
| `tests/test_promoted_handler.py` | Tests for PromotedHandler |
| `tests/test_weather_handler.py` | Tests for WeatherHandler |
| `tests/test_news_handler.py` | Tests for NewsHandler |
| `tests/test_ha_handler.py` | Tests for HAHandler |
| `tests/test_timer_alert.py` | Tests for TimerAlertRunner |
| `tests/test_audio_callbacks.py` | Tests for audio.py callback decoupling |

### Modified files

| File | Changes |
|---|---|
| `scripts/config.py` | Add `session_file`, `end_session_file`, `ha_exclude_entities` |
| `scripts/audio.py` | Remove `import leds`, add `on_chunk`/`on_done` callbacks |
| `scripts/stt.py` | Replace hardcoded constants with `cfg.*` |
| `scripts/tts_generate.py` | Replace private `Config()` with `from config import cfg` |
| `scripts/handlers/ha_control.py` | Replace `os.environ` + raw JSON with `cfg.*` |
| `scripts/briefings.py` | Replace `os.environ` with `cfg.*` |
| `scripts/handlers/timer_handler.py` | Extend `Handler` base class |
| `scripts/responder.py` | Replace if/elif with dispatch table, re-export Response |
| `scripts/wake_converse.py` | Remove extracted code, pass LED callbacks, use cfg paths |
| `scripts/web/app.py` | Use `cfg.session_file`, `cfg.end_session_file` |
| `tests/test_responder.py` | Update imports, test dispatch table |
| `tests/conftest.py` | Add shared fixtures if needed |

---

## Phase 1: Foundation

### Task 1: Create handler_base.py with Response, Handler, and load_clips_from_index

**Files:**
- Create: `scripts/handler_base.py`
- Create: `tests/test_handler_base.py`

- [ ] **Step 1: Write tests for Response dataclass and Handler base class**

```python
# tests/test_handler_base.py
import pytest
from handler_base import Response, Handler, load_clips_from_index


class TestResponse:
    def test_required_fields(self):
        r = Response(text="hi", wav_path="/tmp/a.wav", method="real_clip", intent="GREETING")
        assert r.text == "hi"
        assert r.wav_path == "/tmp/a.wav"
        assert r.method == "real_clip"
        assert r.intent == "GREETING"

    def test_defaults(self):
        r = Response(text="hi", wav_path="/tmp/a.wav", method="real_clip", intent="GREETING")
        assert r.sub_key is None
        assert r.is_temp is False
        assert r.needs_thinking is False
        assert r.model is None

    def test_all_fields(self):
        r = Response(
            text="yo", wav_path="/tmp/b.wav", method="ai_fallback",
            intent="UNKNOWN", sub_key="job", is_temp=True,
            needs_thinking=True, model="claude-haiku-4-5-20251001",
        )
        assert r.sub_key == "job"
        assert r.is_temp is True
        assert r.needs_thinking is True
        assert r.model == "claude-haiku-4-5-20251001"


class TestHandler:
    def test_base_handler_raises(self):
        h = Handler()
        with pytest.raises(NotImplementedError):
            h.handle("hello", "GREETING")

    def test_default_intents_empty(self):
        h = Handler()
        assert h.intents == []


class TestLoadClipsFromIndex:
    def test_loads_key(self, tmp_path):
        import json
        index = {"thinking": ["clips/think1.wav", "clips/think2.wav"]}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index))
        clips = load_clips_from_index("thinking", str(index_path), str(tmp_path))
        assert len(clips) == 2
        assert all(str(tmp_path) in c for c in clips)

    def test_missing_key_returns_empty(self, tmp_path):
        import json
        index = {"other": ["a.wav"]}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index))
        clips = load_clips_from_index("thinking", str(index_path), str(tmp_path))
        assert clips == []

    def test_missing_file_returns_empty(self, tmp_path):
        clips = load_clips_from_index("thinking", str(tmp_path / "nope.json"), str(tmp_path))
        assert clips == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_handler_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handler_base'`

- [ ] **Step 3: Implement handler_base.py**

```python
# scripts/handler_base.py
"""Base classes and utilities for intent handlers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from logger import get_logger

log = get_logger("handler_base")


@dataclass
class Response:
    """Standard response object returned by all handlers."""

    text: str               # display text or clip basename
    wav_path: str           # WAV file to play
    method: str             # real_clip | pre_gen_tts | promoted_tts | handler_* | ai_fallback | error_fallback
    intent: str             # classified intent name
    sub_key: str | None = None
    is_temp: bool = False           # caller must os.unlink() after playback
    needs_thinking: bool = False    # True if response generated on-the-fly
    model: str | None = None        # AI model name if ai_fallback


class Handler:
    """Base class for intent handlers.

    Subclasses declare `intents` (list of intent strings they handle)
    and implement `handle()`.
    """

    intents: list[str] = []

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        """Return a Response, or None to fall through to AI fallback."""
        raise NotImplementedError


def load_clips_from_index(key: str, index_path: str, base_dir: str) -> list[str]:
    """Load clip paths from index.json by key.

    Returns a list of absolute WAV paths, or [] if the key or file is missing.
    Used by both thinking clips and timer alert clips.
    """
    try:
        with open(index_path, "r") as f:
            index = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log.warning("Could not load index %s: %s", index_path, exc)
        return []

    entries = index.get(key, [])
    if not entries:
        return []

    clips = []
    for entry in entries:
        full = os.path.join(base_dir, entry)
        if os.path.exists(full):
            clips.append(full)
    log.info("Loaded %d %s clip(s) (%d missing)", len(clips), key, len(entries) - len(clips))
    return clips
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_handler_base.py -v`
Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/handler_base.py tests/test_handler_base.py
git commit -m "feat: add handler_base with Response, Handler, and load_clips_from_index"
```

---

### Task 2: Add config attributes for IPC paths and HA exclude entities

**Files:**
- Modify: `scripts/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write tests for new config attributes**

Add to `tests/test_config.py`:

```python
def test_session_file_default(self):
    """cfg.session_file should point to .session_active.json in base dir."""
    assert cfg.session_file.endswith(".session_active.json")

def test_end_session_file_default(self):
    """cfg.end_session_file should point to .end_session in base dir."""
    assert cfg.end_session_file.endswith(".end_session")

def test_ha_exclude_entities_default(self):
    """cfg.ha_exclude_entities should be a list."""
    assert isinstance(cfg.ha_exclude_entities, list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_config.py -v -k "session_file or end_session or ha_exclude"`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'session_file'`

- [ ] **Step 3: Add attributes to Config class**

In `scripts/config.py`, add to the Config class `__init__` method, after loading `bender_config.json` overrides:

```python
# IPC paths
self.session_file: str = os.path.join(_BASE_DIR, ".session_active.json")
self.end_session_file: str = os.path.join(_BASE_DIR, ".end_session")

# HA exclude entities (loaded from bender_config.json)
self.ha_exclude_entities: list = self._data.get("ha_exclude_entities", [])
```

Where `self._data` is the parsed `bender_config.json` dict (check the actual variable name used in `__init__` — it may be `data` or `overrides`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_config.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/config.py tests/test_config.py
git commit -m "feat: add session_file, end_session_file, ha_exclude_entities to config"
```

---

## Phase 2: Decoupling

### Task 3: Decouple audio.py from leds

**Files:**
- Modify: `scripts/audio.py`
- Create: `tests/test_audio_callbacks.py`

- [ ] **Step 1: Write tests for callback-based play**

```python
# tests/test_audio_callbacks.py
"""Test audio.py LED decoupling — callbacks instead of hard leds import."""
import pytest
from unittest.mock import patch, MagicMock
import struct
import wave
import os


def _make_wav(path, n_frames=100, sample_rate=44100):
    """Create a minimal valid WAV file."""
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Write sine-ish data (just ascending values for non-zero RMS)
        data = struct.pack(f"<{n_frames}h", *[i * 100 for i in range(n_frames)])
        wf.writeframes(data)


class TestPlayCallbacks:
    @patch("audio._pa")
    def test_on_chunk_called_during_playback(self, mock_pa, tmp_path):
        """on_chunk should be called at least once during playback."""
        wav_path = tmp_path / "test.wav"
        _make_wav(wav_path)

        mock_stream = MagicMock()
        mock_pa.open.return_value = mock_stream

        import audio
        chunk_values = []
        audio.open_session()
        audio.play(str(wav_path), on_chunk=lambda v: chunk_values.append(v))
        audio.close_session()

        assert len(chunk_values) > 0
        assert all(0.0 <= v <= 1.0 for v in chunk_values)

    @patch("audio._pa")
    def test_on_done_called_after_playback(self, mock_pa, tmp_path):
        """on_done should be called once after playback finishes."""
        wav_path = tmp_path / "test.wav"
        _make_wav(wav_path)

        mock_stream = MagicMock()
        mock_pa.open.return_value = mock_stream

        import audio
        done_calls = []
        audio.open_session()
        audio.play(str(wav_path), on_done=lambda: done_calls.append(True))
        audio.close_session()

        assert done_calls == [True]

    @patch("audio._pa")
    def test_no_callbacks_no_error(self, mock_pa, tmp_path):
        """play() with no callbacks should work without errors."""
        wav_path = tmp_path / "test.wav"
        _make_wav(wav_path)

        mock_stream = MagicMock()
        mock_pa.open.return_value = mock_stream

        import audio
        audio.open_session()
        audio.play(str(wav_path))  # No callbacks — should not raise
        audio.close_session()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_audio_callbacks.py -v`
Expected: FAIL — either `leds` import fails on non-Pi, or `play()` doesn't accept `on_chunk`

- [ ] **Step 3: Modify audio.py — remove leds import, add callbacks**

In `scripts/audio.py`:

1. **Remove** `import leds` (line 22)
2. **Modify `play()` signature** — add `on_chunk` and `on_done` parameters:

```python
def play(wav_path: str,
         on_chunk: callable = None,
         on_done: callable = None) -> None:
```

3. **Replace LED calls in `play()`**:
   - Replace `leds.set_level(rms_to_ratio(rms(data, sw)))` with:
     ```python
     if on_chunk:
         on_chunk(rms_to_ratio(rms(data, sw)))
     ```
   - Replace `leds.all_off()` at end of play with:
     ```python
     if on_done:
         on_done()
     ```

4. **Modify `play_oneshot()` signature** — same callbacks:

```python
def play_oneshot(wav_path: str,
                 on_chunk: callable = None,
                 on_done: callable = None) -> None:
```

5. **Replace LED calls in `play_oneshot()`**:
   - Pass callbacks through to the internal play logic
   - `on_done` must be called **outside** the `with _lock:` block (matching current `leds.all_off()` placement)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_audio_callbacks.py -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Run existing audio-dependent tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/ -v -k "not test_audio_callbacks"`
Expected: all existing tests still PASS (no tests directly test `audio.play` with LED assertions)

- [ ] **Step 6: Commit**

```bash
git add scripts/audio.py tests/test_audio_callbacks.py
git commit -m "refactor: decouple audio.py from leds — use on_chunk/on_done callbacks"
```

---

### Task 4: Unify config access in stt.py

**Files:**
- Modify: `scripts/stt.py`

- [ ] **Step 1: Replace hardcoded constants with cfg references**

In `scripts/stt.py`:

1. Add `from config import cfg` to imports
2. Remove these module-level constants:
   ```python
   VAD_AGGRESSIVENESS = 2
   SILENCE_FRAMES = 50
   MAX_RECORD_S = 15
   WHISPER_MODEL = "tiny.en"
   ```
3. Keep hardware-fixed constants:
   ```python
   SAMPLE_RATE = 16000
   FRAME_MS = 30
   AUDIO_DEVICE = "hw:2,0"
   ```
4. Replace all references:
   - `VAD_AGGRESSIVENESS` → `cfg.vad_aggressiveness`
   - `SILENCE_FRAMES` → `cfg.silence_frames`
   - `MAX_RECORD_S` → `cfg.max_record_seconds`
   - `WHISPER_MODEL` → `cfg.whisper_model`

- [ ] **Step 2: Run existing tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add scripts/stt.py
git commit -m "refactor: stt.py uses cfg singleton instead of hardcoded constants"
```

---

### Task 5: Unify config access in tts_generate.py

**Files:**
- Modify: `scripts/tts_generate.py`

- [ ] **Step 1: Replace private Config() with shared cfg singleton**

In `scripts/tts_generate.py`:

1. Replace the try/except Config import block:
   ```python
   # Remove:
   try:
       from config import Config as _Config
       _cfg = _Config()
   except Exception:
       _cfg = None
   ```
   With:
   ```python
   from config import cfg
   ```
2. Replace all `_cfg.` references with `cfg.`
3. Replace any `_cfg is not None` guards — `cfg` is always available

- [ ] **Step 2: Run existing tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add scripts/tts_generate.py
git commit -m "refactor: tts_generate.py uses cfg singleton instead of private Config()"
```

---

### Task 6: Unify config access in ha_control.py

**Files:**
- Modify: `scripts/handlers/ha_control.py`

- [ ] **Step 1: Replace os.environ and raw JSON reads with cfg**

In `scripts/handlers/ha_control.py`:

1. Add `from config import cfg` to imports
2. Replace:
   ```python
   HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
   HA_TOKEN = os.environ.get("HA_TOKEN", "")
   ```
   With references to `cfg.ha_url` and `cfg.ha_token` at call sites
3. Replace `_load_exclude_entities()` — the raw `open(bender_config.json)` read — with:
   ```python
   def _load_exclude_entities() -> set:
       return set(cfg.ha_exclude_entities)
   ```
4. Update all references from `HA_URL` → `cfg.ha_url` and `HA_TOKEN` → `cfg.ha_token`

- [ ] **Step 2: Run HA control tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_ha_control.py -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add scripts/handlers/ha_control.py
git commit -m "refactor: ha_control.py uses cfg singleton for HA config and exclude entities"
```

---

### Task 7: Unify config access in briefings.py

**Files:**
- Modify: `scripts/briefings.py`

- [ ] **Step 1: Replace os.environ reads with cfg**

In `scripts/briefings.py`:

1. Ensure `from config import cfg` is imported (it may already be imported as `_cfg`)
2. Remove:
   ```python
   HA_URL_DEFAULT = "http://homeassistant.local:8123"
   HA_TOKEN_DEFAULT = ""
   HA_ENTITY_DEFAULT = "weather.forecast_home"
   ```
3. Replace all `os.environ.get("HA_URL", HA_URL_DEFAULT)` → `cfg.ha_url`
4. Replace all `os.environ.get("HA_TOKEN", HA_TOKEN_DEFAULT)` → `cfg.ha_token`
5. Replace all `os.environ.get("HA_WEATHER_ENTITY", HA_ENTITY_DEFAULT)` → `cfg.ha_weather_entity`

- [ ] **Step 2: Run existing tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add scripts/briefings.py
git commit -m "refactor: briefings.py uses cfg singleton for HA connection params"
```

---

### Task 8: Centralise IPC paths

**Files:**
- Modify: `scripts/wake_converse.py`
- Modify: `scripts/web/app.py`

- [ ] **Step 1: Replace local constants in wake_converse.py**

In `scripts/wake_converse.py`:

1. Remove:
   ```python
   _SESSION_FILE = os.path.join(_BASE_DIR, ".session_active.json")
   _END_SESSION_FILE = os.path.join(_BASE_DIR, ".end_session")
   ```
2. Add `from config import cfg` if not already imported
3. Replace all `_SESSION_FILE` → `cfg.session_file`
4. Replace all `_END_SESSION_FILE` → `cfg.end_session_file`

- [ ] **Step 2: Replace local constants in web/app.py**

In `scripts/web/app.py`:

1. Remove:
   ```python
   _SESSION_FILE = os.path.join(_BASE_DIR, ".session_active.json")
   _END_SESSION_FILE = os.path.join(_BASE_DIR, ".end_session")
   ```
2. Add `from config import cfg` if not already imported
3. Replace all `_SESSION_FILE` → `cfg.session_file`
4. Replace all `_END_SESSION_FILE` → `cfg.end_session_file`

- [ ] **Step 3: Run web session tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_web_session.py -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add scripts/wake_converse.py scripts/web/app.py
git commit -m "refactor: centralise IPC session file paths in cfg singleton"
```

---

## Phase 3: Handler Extraction

### Task 9: Create RealClipHandler

**Files:**
- Create: `scripts/handlers/clip_handler.py`
- Create: `tests/test_clip_handler.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_clip_handler.py
import json
import os
import pytest
from handlers.clip_handler import RealClipHandler


@pytest.fixture
def index_with_clips(tmp_path):
    """Create a minimal index.json with greeting and dismissal clips."""
    wav_dir = tmp_path / "speech" / "wav"
    wav_dir.mkdir(parents=True)
    # Create dummy WAV files
    for name in ["greet1.wav", "greet2.wav", "bye1.wav"]:
        (wav_dir / name).write_bytes(b"RIFF" + b"\x00" * 40)

    index = {
        "greeting": [f"speech/wav/{n}" for n in ["greet1.wav", "greet2.wav"]],
        "dismissal": ["speech/wav/bye1.wav"],
    }
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index))
    return str(index_path), str(tmp_path)


class TestRealClipHandler:
    def test_intents(self):
        h = RealClipHandler.__new__(RealClipHandler)
        assert "GREETING" in h.intents
        assert "DISMISSAL" in h.intents
        assert "AFFIRMATION" in h.intents
        assert "JOKE" in h.intents

    def test_handle_greeting(self, index_with_clips):
        index_path, base_dir = index_with_clips
        h = RealClipHandler(index_path=index_path, base_dir=base_dir)
        resp = h.handle("hello", "GREETING")
        assert resp is not None
        assert resp.method == "real_clip"
        assert resp.intent == "GREETING"
        assert resp.is_temp is False
        assert os.path.isfile(resp.wav_path)

    def test_handle_returns_none_for_missing_intent(self, index_with_clips):
        index_path, base_dir = index_with_clips
        h = RealClipHandler(index_path=index_path, base_dir=base_dir)
        resp = h.handle("weather please", "WEATHER")
        assert resp is None

    def test_handle_returns_none_when_no_clips(self, tmp_path):
        index = {"greeting": []}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index))
        h = RealClipHandler(index_path=str(index_path), base_dir=str(tmp_path))
        resp = h.handle("hello", "GREETING")
        assert resp is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_clip_handler.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement RealClipHandler**

```python
# scripts/handlers/clip_handler.py
"""Handler for original Bender WAV clips (real_clip responses)."""

from __future__ import annotations

import json
import os
import random

from handler_base import Handler, Response
from logger import get_logger

log = get_logger("clip_handler")


class RealClipHandler(Handler):
    """Plays original Bender speech clips from speech/wav/."""

    intents = ["GREETING", "AFFIRMATION", "DISMISSAL", "JOKE"]

    def __init__(self, index_path: str = None, base_dir: str = None):
        _base = base_dir or os.path.join(os.path.dirname(__file__), "..")
        self._base_dir = os.path.normpath(_base)
        _idx = index_path or os.path.join(self._base_dir, "speech", "responses", "index.json")
        self._index = self._load_index(_idx)

    def _load_index(self, path: str) -> dict:
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            log.warning("RealClipHandler: could not load index: %s", exc)
            return {}

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        key = intent.lower()
        clips = self._index.get(key, [])
        if not clips:
            return None

        rel_path = random.choice(clips)
        wav_path = os.path.join(self._base_dir, rel_path)

        if not os.path.isfile(wav_path):
            log.warning("RealClipHandler: missing WAV %s", wav_path)
            return None

        return Response(
            text=os.path.basename(wav_path),
            wav_path=wav_path,
            method="real_clip",
            intent=intent,
            sub_key=sub_key,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_clip_handler.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/handlers/clip_handler.py tests/test_clip_handler.py
git commit -m "feat: add RealClipHandler for original Bender WAV clips"
```

---

### Task 10: Create PreGenHandler

**Files:**
- Create: `scripts/handlers/pregen_handler.py`
- Create: `tests/test_pregen_handler.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_pregen_handler.py
import json
import os
import pytest
from handlers.pregen_handler import PreGenHandler


@pytest.fixture
def index_with_personal(tmp_path):
    """Create index with personal sub-keys."""
    resp_dir = tmp_path / "speech" / "responses"
    resp_dir.mkdir(parents=True)
    for name in ["job_1.wav", "age_1.wav"]:
        (resp_dir / name).write_bytes(b"RIFF" + b"\x00" * 40)

    index = {
        "personal": {
            "job": "speech/responses/job_1.wav",
            "age": "speech/responses/age_1.wav",
        }
    }
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index))
    return str(index_path), str(tmp_path)


class TestPreGenHandler:
    def test_intents(self):
        h = PreGenHandler.__new__(PreGenHandler)
        assert "PERSONAL" in h.intents

    def test_handle_personal_with_sub_key(self, index_with_personal):
        index_path, base_dir = index_with_personal
        h = PreGenHandler(index_path=index_path, base_dir=base_dir)
        resp = h.handle("what's your job", "PERSONAL", sub_key="job")
        assert resp is not None
        assert resp.method == "pre_gen_tts"
        assert resp.sub_key == "job"

    def test_handle_returns_none_for_missing_sub_key(self, index_with_personal):
        index_path, base_dir = index_with_personal
        h = PreGenHandler(index_path=index_path, base_dir=base_dir)
        resp = h.handle("do you like me", "PERSONAL", sub_key="like_me")
        assert resp is None

    def test_handle_returns_none_for_wrong_intent(self, index_with_personal):
        index_path, base_dir = index_with_personal
        h = PreGenHandler(index_path=index_path, base_dir=base_dir)
        resp = h.handle("hello", "GREETING")
        assert resp is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_pregen_handler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement PreGenHandler**

```python
# scripts/handlers/pregen_handler.py
"""Handler for pre-generated TTS responses (PERSONAL intents)."""

from __future__ import annotations

import json
import os
import random

from handler_base import Handler, Response
from logger import get_logger

log = get_logger("pregen_handler")


class PreGenHandler(Handler):
    """Plays pre-built TTS WAVs from speech/responses/ for PERSONAL intents."""

    intents = ["PERSONAL"]

    def __init__(self, index_path: str = None, base_dir: str = None):
        _base = base_dir or os.path.join(os.path.dirname(__file__), "..")
        self._base_dir = os.path.normpath(_base)
        _idx = index_path or os.path.join(self._base_dir, "speech", "responses", "index.json")
        self._index = self._load_index(_idx)

    def _load_index(self, path: str) -> dict:
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            log.warning("PreGenHandler: could not load index: %s", exc)
            return {}

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        personal = self._index.get("personal", {})
        if not sub_key or sub_key not in personal:
            return None

        entry = personal[sub_key]
        if not entry:
            return None

        # index.json stores personal values as strings (single path), not lists
        rel_path = entry if isinstance(entry, str) else random.choice(entry)
        wav_path = os.path.join(self._base_dir, rel_path)

        if not os.path.isfile(wav_path):
            log.warning("PreGenHandler: missing WAV %s", wav_path)
            return None

        return Response(
            text=os.path.basename(wav_path),
            wav_path=wav_path,
            method="pre_gen_tts",
            intent=intent,
            sub_key=sub_key,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_pregen_handler.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/handlers/pregen_handler.py tests/test_pregen_handler.py
git commit -m "feat: add PreGenHandler for pre-generated TTS responses"
```

---

### Task 11: Create PromotedHandler

**Files:**
- Create: `scripts/handlers/promoted_handler.py`
- Create: `tests/test_promoted_handler.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_promoted_handler.py
import json
import os
import pytest
from handlers.promoted_handler import PromotedHandler


@pytest.fixture
def promoted_base(tmp_path):
    """Create a base dir with a promoted WAV file."""
    resp_dir = tmp_path / "speech" / "responses"
    resp_dir.mkdir(parents=True)
    (resp_dir / "promo1.wav").write_bytes(b"RIFF" + b"\x00" * 40)
    return str(tmp_path)


class TestPromotedHandler:
    def test_intents(self):
        h = PromotedHandler.__new__(PromotedHandler)
        assert "PROMOTED" in h.intents

    def test_handle_with_sub_key_file(self, promoted_base):
        """sub_key from intent classifier holds the relative file path."""
        h = PromotedHandler(base_dir=promoted_base)
        resp = h.handle("what is your name", "PROMOTED",
                        sub_key="speech/responses/promo1.wav")
        assert resp is not None
        assert resp.method == "promoted_tts"

    def test_handle_returns_none_when_no_sub_key(self, promoted_base):
        h = PromotedHandler(base_dir=promoted_base)
        resp = h.handle("test", "PROMOTED", sub_key=None)
        assert resp is None

    def test_handle_returns_none_when_file_missing(self, promoted_base):
        h = PromotedHandler(base_dir=promoted_base)
        resp = h.handle("test", "PROMOTED", sub_key="nope.wav")
        assert resp is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_promoted_handler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement PromotedHandler**

```python
# scripts/handlers/promoted_handler.py
"""Handler for promoted AI responses (pre-built WAVs matched by pattern)."""

from __future__ import annotations

import json
import os
import re

from handler_base import Handler, Response
from logger import get_logger

log = get_logger("promoted_handler")


class PromotedHandler(Handler):
    """Plays promoted AI response WAVs using the file path from intent classifier.

    The intent classifier (intent.py) puts the promoted file path in sub_key
    when it matches a PROMOTED pattern. This handler simply resolves and plays it.
    """

    intents = ["PROMOTED"]

    def __init__(self, index_path: str = None, base_dir: str = None):
        _base = base_dir or os.path.join(os.path.dirname(__file__), "..")
        self._base_dir = os.path.normpath(_base)

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        if not sub_key:
            return None

        # sub_key holds the relative file path from intent.classify()
        wav_path = os.path.join(self._base_dir, sub_key)

        if not os.path.isfile(wav_path):
            log.warning("PromotedHandler: missing WAV %s", wav_path)
            return None

        return Response(
            text=os.path.basename(wav_path),
            wav_path=wav_path,
            method="promoted_tts",
            intent=intent,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_promoted_handler.py -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/handlers/promoted_handler.py tests/test_promoted_handler.py
git commit -m "feat: add PromotedHandler for promoted AI response WAVs"
```

---

### Task 12: Create WeatherHandler and NewsHandler

**Files:**
- Create: `scripts/handlers/weather_handler.py`
- Create: `scripts/handlers/news_handler.py`
- Create: `tests/test_weather_handler.py`
- Create: `tests/test_news_handler.py`

- [ ] **Step 1: Write weather handler tests**

```python
# tests/test_weather_handler.py
import pytest
from unittest.mock import patch
from handlers.weather_handler import WeatherHandler


class TestWeatherHandler:
    def test_intents(self):
        h = WeatherHandler()
        assert h.intents == ["WEATHER"]

    @patch("handlers.weather_handler.briefings")
    def test_handle_returns_response(self, mock_briefings):
        mock_briefings.get_weather_wav.return_value = "/tmp/weather.wav"
        h = WeatherHandler()
        resp = h.handle("what's the weather", "WEATHER")
        assert resp is not None
        assert resp.method == "handler_weather"
        assert resp.wav_path == "/tmp/weather.wav"
        assert resp.is_temp is False

    @patch("handlers.weather_handler.briefings")
    def test_handle_returns_none_on_failure(self, mock_briefings):
        mock_briefings.get_weather_wav.return_value = None
        h = WeatherHandler()
        resp = h.handle("what's the weather", "WEATHER")
        assert resp is None
```

- [ ] **Step 2: Write news handler tests**

```python
# tests/test_news_handler.py
import pytest
from unittest.mock import patch
from handlers.news_handler import NewsHandler


class TestNewsHandler:
    def test_intents(self):
        h = NewsHandler()
        assert h.intents == ["NEWS"]

    @patch("handlers.news_handler.briefings")
    def test_handle_returns_response(self, mock_briefings):
        mock_briefings.get_news_wav.return_value = "/tmp/news.wav"
        h = NewsHandler()
        resp = h.handle("what's the news", "NEWS")
        assert resp is not None
        assert resp.method == "handler_news"
        assert resp.wav_path == "/tmp/news.wav"

    @patch("handlers.news_handler.briefings")
    def test_handle_returns_none_on_failure(self, mock_briefings):
        mock_briefings.get_news_wav.return_value = None
        h = NewsHandler()
        resp = h.handle("what's the news", "NEWS")
        assert resp is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_weather_handler.py tests/test_news_handler.py -v`
Expected: FAIL

- [ ] **Step 4: Implement WeatherHandler**

```python
# scripts/handlers/weather_handler.py
"""Handler for weather briefing responses."""

from __future__ import annotations

import briefings
from config import cfg
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("weather_handler")


class WeatherHandler(Handler):
    """Returns cached weather briefing WAV."""

    intents = ["WEATHER"]

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        wav_path = briefings.get_weather_wav()
        if not wav_path:
            log.warning("WeatherHandler: no weather WAV available")
            return None

        return Response(
            text="weather briefing",
            wav_path=wav_path,
            method="handler_weather",
            intent=intent,
            sub_key=sub_key,
        )
```

- [ ] **Step 5: Implement NewsHandler**

```python
# scripts/handlers/news_handler.py
"""Handler for news briefing responses."""

from __future__ import annotations

import briefings
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("news_handler")


class NewsHandler(Handler):
    """Returns cached news briefing WAV."""

    intents = ["NEWS"]

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        wav_path = briefings.get_news_wav()
        if not wav_path:
            log.warning("NewsHandler: no news WAV available")
            return None

        return Response(
            text="news briefing",
            wav_path=wav_path,
            method="handler_news",
            intent=intent,
            sub_key=sub_key,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_weather_handler.py tests/test_news_handler.py -v`
Expected: all 6 tests PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/handlers/weather_handler.py scripts/handlers/news_handler.py tests/test_weather_handler.py tests/test_news_handler.py
git commit -m "feat: add WeatherHandler and NewsHandler for briefing responses"
```

---

### Task 13: Create HAHandler

**Files:**
- Create: `scripts/handlers/ha_handler.py`
- Create: `tests/test_ha_handler.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_ha_handler.py
import pytest
from unittest.mock import patch
from handlers.ha_handler import HAHandler


class TestHAHandler:
    def test_intents(self):
        h = HAHandler()
        assert h.intents == ["HA_CONTROL"]

    @patch("handlers.ha_handler.ha_control")
    def test_handle_returns_response(self, mock_ha):
        mock_ha.control.return_value = "/tmp/ha_confirm.wav"
        h = HAHandler()
        resp = h.handle("turn on the kitchen light", "HA_CONTROL")
        assert resp is not None
        assert resp.method == "handler_ha"
        assert resp.wav_path == "/tmp/ha_confirm.wav"

    @patch("handlers.ha_handler.ha_control")
    def test_handle_returns_none_on_failure(self, mock_ha):
        mock_ha.control.return_value = None
        h = HAHandler()
        resp = h.handle("turn on something", "HA_CONTROL")
        assert resp is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_ha_handler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement HAHandler**

```python
# scripts/handlers/ha_handler.py
"""Handler for Home Assistant device control."""

from __future__ import annotations

from handlers import ha_control
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("ha_handler")


class HAHandler(Handler):
    """Controls Home Assistant devices via REST API."""

    intents = ["HA_CONTROL"]

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        wav_path = ha_control.control(text)
        if not wav_path:
            log.warning("HAHandler: HA control returned no WAV")
            return None

        return Response(
            text=text,
            wav_path=wav_path,
            method="handler_ha",
            intent=intent,
            sub_key=sub_key,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_ha_handler.py -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/handlers/ha_handler.py tests/test_ha_handler.py
git commit -m "feat: add HAHandler for Home Assistant control"
```

---

### Task 14: Refactor TimerHandler to extend Handler

**Files:**
- Modify: `scripts/handlers/timer_handler.py`
- Modify: `tests/test_timer_handler.py`

- [ ] **Step 1: Add Handler-conformant test**

Add to `tests/test_timer_handler.py`:

```python
from handlers.timer_handler import TimerHandler


class TestTimerHandlerInterface:
    def test_intents(self):
        h = TimerHandler()
        assert "TIMER" in h.intents
        assert "TIMER_CANCEL" in h.intents
        assert "TIMER_STATUS" in h.intents

    @patch("handlers.timer_handler.tts_generate")
    @patch("handlers.timer_handler.timers")
    def test_handle_dispatches_timer_set(self, mock_timers, mock_tts):
        mock_timers.create_timer.return_value = {"label": "test", "seconds": 60}
        mock_tts.speak.return_value = "/tmp/timer.wav"
        h = TimerHandler()
        resp = h.handle("set a timer for one minute", "TIMER")
        assert resp is not None
        assert resp.method == "handler_timer"
        assert resp.is_temp is True
        assert resp.needs_thinking is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_timer_handler.py::TestTimerHandlerInterface -v`
Expected: FAIL — `TimerHandler` has no `intents` or `handle()`

- [ ] **Step 3: Refactor TimerHandler to extend Handler**

In `scripts/handlers/timer_handler.py`:

1. Add imports:
   ```python
   from handler_base import Handler, Response
   ```
2. Wrap existing functions in a `TimerHandler` class:
   ```python
   class TimerHandler(Handler):
       intents = ["TIMER", "TIMER_CANCEL", "TIMER_STATUS"]

       def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
           if intent == "TIMER":
               wav = handle_set(text)
           elif intent == "TIMER_CANCEL":
               wav = handle_cancel(text)
           elif intent == "TIMER_STATUS":
               wav = handle_status(text)
           else:
               return None

           if not wav:
               return None

           return Response(
               text=text,
               wav_path=wav,
               method="handler_timer",
               intent=intent,
               sub_key=sub_key,
               is_temp=True,
               needs_thinking=True,
           )
   ```
3. Keep existing `handle_set()`, `handle_cancel()`, `handle_status()` as module-level functions (the class delegates to them). This preserves backward compatibility with any direct callers.

- [ ] **Step 4: Run all timer tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_timer_handler.py -v`
Expected: all tests PASS (both old and new)

- [ ] **Step 5: Commit**

```bash
git add scripts/handlers/timer_handler.py tests/test_timer_handler.py
git commit -m "refactor: TimerHandler extends Handler base class with intent dispatch"
```

---

## Phase 4: Assembly

### Task 15: Extract TimerAlertRunner

**Files:**
- Create: `scripts/handlers/timer_alert.py`
- Create: `tests/test_timer_alert.py`
- Modify: `scripts/wake_converse.py`

- [ ] **Step 1: Write tests for TimerAlertRunner**

```python
# tests/test_timer_alert.py
import pytest
from handlers.timer_alert import TimerAlertRunner


class TestTimerAlertDismissPatterns:
    def test_stop_is_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss("stop") is True

    def test_thanks_is_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss("thanks") is True

    def test_random_text_not_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss("what time is it") is False

    def test_ok_is_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss("ok") is True

    def test_got_it_is_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss("got it") is True


class TestTimerAlertLoadClips:
    def test_load_alert_clips(self, tmp_path):
        import json
        index = {"timer_alerts": ["clips/alert1.wav", "clips/alert2.wav"]}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index))
        runner = TimerAlertRunner(index_path=str(index_path), base_dir=str(tmp_path))
        assert len(runner._alert_clips) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_timer_alert.py -v`
Expected: FAIL

- [ ] **Step 3: Implement TimerAlertRunner**

```python
# scripts/handlers/timer_alert.py
"""Timer alert interaction — plays alert sound, listens for dismissal."""

from __future__ import annotations

import os
import re
import time
from typing import Callable

from config import cfg
from handler_base import load_clips_from_index
from logger import log
import metrics


DISMISS_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\b(stop|enough|ok|okay|shut up|quiet|silence|dismiss)\b",
        r"\bthat'?s?\s*(enough|ok|fine)\b",
        r"\bplease stop\b",
        r"\byes\b",
        r"\bgot it\b",
        r"\bthank(s| you)\b",
    ]
]


class TimerAlertRunner:
    """Manages the alert interaction when a timer fires."""

    def __init__(self, index_path: str = None, base_dir: str = None):
        _base = base_dir or os.path.join(os.path.dirname(__file__), "..")
        self._base_dir = os.path.normpath(_base)
        _idx = index_path or os.path.join(self._base_dir, "speech", "responses", "index.json")
        self._alert_clips = load_clips_from_index("timer_alerts", _idx, self._base_dir)

    def _is_dismiss(self, text: str) -> bool:
        """Check if transcribed text matches a dismiss pattern."""
        if not text:
            return False
        return any(p.search(text) for p in DISMISS_PATTERNS)

    def run(self, fired_timers: list[dict],
            on_chunk: Callable | None = None,
            on_done: Callable | None = None,
            on_flash: Callable[[bool], None] | None = None) -> None:
        """Play alert in a loop, listen for voice/UI dismissal.

        This method manages its own audio session lifecycle.
        Extracted from wake_converse.run_timer_alert().

        Args:
            fired_timers: list of timer dicts from timers.check_fired().
            on_chunk: optional callback(float) for LED visualisation during playback.
            on_done: optional callback() when playback ends.
            on_flash: optional callback(bool) for LED alert flash toggle.
                      True = start flashing, False = stop flashing.
        """
        # Lazy imports to avoid circular deps and allow testing
        import audio
        import stt
        import timers as timers_mod
        import tts_generate
        import random

        labels = [t["label"] for t in fired_timers]
        label_str = ", ".join(labels) if labels else "timer"
        log.info("Timer alert: %s", label_str)
        metrics.count("timer_alert", labels=label_str)

        max_seconds = cfg.timer_alert_max_seconds
        start = time.time()
        dismissed = False

        audio.open_session()
        try:
            while time.time() - start < max_seconds:
                # Play alert clip
                if self._alert_clips:
                    clip = random.choice(self._alert_clips)
                    audio.play(clip, on_chunk=on_chunk, on_done=on_done)

                # Flash LEDs via callback (no direct leds import)
                if on_flash:
                    on_flash(True)

                # Listen for voice dismissal (short window)
                audio.close_session()
                text = stt.listen_and_transcribe(max_seconds=3)
                audio.open_session()

                if on_flash:
                    on_flash(False)

                if text and self._is_dismiss(text):
                    dismissed = True
                    break

                # Check web UI dismissal
                still_firing = timers_mod.check_fired()
                if not still_firing:
                    dismissed = True
                    break

            # Dismiss all and confirm
            timers_mod.dismiss_all_fired()

            if dismissed:
                confirm_text = f"Alright, {label_str} dismissed."
            else:
                confirm_text = f"Timer {label_str} timed out."

            wav = tts_generate.speak(confirm_text)
            if wav:
                audio.play(wav, on_chunk=on_chunk, on_done=on_done)
                os.unlink(wav)

        finally:
            if on_flash:
                on_flash(False)
            audio.close_session()
```

**Note to implementer:** The exact alert loop logic should be copied from the current `run_timer_alert()` in `wake_converse.py` (lines 152–220). The above is a structural template — match the existing behaviour exactly, including the play-pause cycle, LED flash toggle, and dismissal flow. Adapt variable names to use `self._alert_clips` and `self._is_dismiss()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_timer_alert.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/handlers/timer_alert.py tests/test_timer_alert.py
git commit -m "feat: extract TimerAlertRunner from wake_converse.py"
```

---

### Task 16: Refactor Responder to use dispatch table

**Files:**
- Modify: `scripts/responder.py`
- Modify: `tests/test_responder.py`

- [ ] **Step 1: Write dispatch table tests**

Add to `tests/test_responder.py`:

```python
class TestDispatchTable:
    def test_all_intents_registered(self):
        """Every known intent should have at least one handler."""
        from responder import Responder
        r = Responder()
        expected_intents = [
            "GREETING", "AFFIRMATION", "DISMISSAL", "JOKE",
            "PERSONAL", "PROMOTED", "WEATHER", "NEWS",
            "HA_CONTROL", "TIMER", "TIMER_CANCEL", "TIMER_STATUS",
        ]
        for intent in expected_intents:
            assert intent in r._dispatch, f"Missing handler for {intent}"

    def test_unknown_intent_falls_through(self):
        """UNKNOWN intent should not be in dispatch table."""
        from responder import Responder
        r = Responder()
        assert "UNKNOWN" not in r._dispatch
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_responder.py::TestDispatchTable -v`
Expected: FAIL — `Responder` has no `_dispatch`

- [ ] **Step 3: Refactor responder.py**

In `scripts/responder.py`:

1. **Add re-export** at the top:
   ```python
   from handler_base import Response, Handler  # noqa: F401 — re-export
   ```
   Remove the local `Response` dataclass definition.

2. **Replace `__init__`** — build dispatch table:
   ```python
   from handlers.clip_handler import RealClipHandler
   from handlers.pregen_handler import PreGenHandler
   from handlers.promoted_handler import PromotedHandler
   from handlers.weather_handler import WeatherHandler
   from handlers.news_handler import NewsHandler
   from handlers.ha_handler import HAHandler
   from handlers.timer_handler import TimerHandler

   class Responder:
       def __init__(self, index_path=None, base_dir=None):
           handlers = [
               RealClipHandler(index_path=index_path, base_dir=base_dir),
               PreGenHandler(index_path=index_path, base_dir=base_dir),
               PromotedHandler(index_path=index_path, base_dir=base_dir),
               WeatherHandler(),
               NewsHandler(),
               HAHandler(),
               TimerHandler(),
           ]
           self._dispatch: dict[str, list[Handler]] = {}
           for h in handlers:
               for intent_name in h.intents:
                   self._dispatch.setdefault(intent_name, []).append(h)
   ```

3. **Replace `get_response()`** with dispatch loop:
   ```python
   def get_response(self, text: str, ai=None) -> Response:
       from intent import classify
       intent_name, sub_key = classify(text)

       for handler in self._dispatch.get(intent_name, []):
           try:
               resp = handler.handle(text, intent_name, sub_key)
               if resp is not None:
                   return resp
           except Exception as exc:
               log.warning("Handler %s failed for %s: %s",
                           type(handler).__name__, intent_name, exc)

       return self._respond_ai(text, ai, intent_name, sub_key)
   ```

4. **Remove** all `_handle_*`, `_respond_handler`, `_respond_real_clip`, `_respond_pre_gen`, `_respond_promoted`, `_is_real_clip`, `_is_pre_gen`, `_handle_clip`, `_handle_promoted`, `_handle_weather`, `_handle_news`, `_handle_ha`, `pick_clip` methods.

5. **Keep** `_respond_ai()` and `_error_response()` methods.

- [ ] **Step 4: Update existing responder tests**

The following existing tests in `tests/test_responder.py` call removed methods and must be updated:
- Tests calling `r.pick_clip(...)` — delete these tests (clip selection is now tested in `test_clip_handler.py`)
- Tests calling `r._is_pre_gen(...)` — delete these tests (pre-gen detection is now internal to `PreGenHandler`)
- Tests importing `Response` from `responder` — these still work (re-exported), no change needed
- Tests calling `r.get_response(...)` — these should still work via the dispatch table, but update mocks to patch handler classes instead of internal methods

- [ ] **Step 5: Run all responder tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_responder.py -v`
Expected: all tests PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /c/ws/benderpi && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/responder.py tests/test_responder.py
git commit -m "refactor: replace responder if/elif chain with handler dispatch table"
```

---

### Task 17: Clean up wake_converse.py orchestrator

**Files:**
- Modify: `scripts/wake_converse.py`

- [ ] **Step 1: Remove extracted code and wire up new modules**

In `scripts/wake_converse.py`:

1. **Remove** `TIMER_DISMISS_PATTERNS` and `_is_timer_dismiss()` (lines 133–147)
2. **Remove** `run_timer_alert()` (lines 152–220)
3. **Remove** `_load_timer_alert_clips()` and `_timer_alert_clips` (lines 110–128)
4. **Replace** `_load_thinking_clips()` body with call to shared utility:
   ```python
   from handler_base import load_clips_from_index

   _thinking_clips = []

   def _load_thinking_clips():
       global _thinking_clips
       _idx = os.path.join(_BASE_DIR, "speech", "responses", "index.json")
       _thinking_clips = load_clips_from_index("thinking", _idx, _BASE_DIR)
   ```
5. **Add** timer alert runner import and instantiation:
   ```python
   from handlers.timer_alert import TimerAlertRunner
   _alert_runner = TimerAlertRunner()
   ```
6. **Replace** inline `run_timer_alert(fired)` call with:
   ```python
   _alert_runner.run(fired, on_chunk=leds.set_level, on_done=leds.all_off, on_flash=leds.set_alert_flash)
   ```
7. **Verify** `_SESSION_FILE` / `_END_SESSION_FILE` were already replaced with `cfg.session_file` / `cfg.end_session_file` in Task 8. If Task 8 was completed, no action needed here. If not, do it now.
8. **Replace greeting `pick_clip` / `_is_pre_gen` calls** (lines 271–275). These methods are removed from `Responder` in Task 16. Replace with `RealClipHandler`:
   ```python
   from handlers.clip_handler import RealClipHandler
   _greeting_handler = RealClipHandler()

   # In run_session(), replace:
   #   greeting_path = responder.pick_clip("GREETING")
   #   method = "pre_gen_tts" if responder._is_pre_gen(greeting_path) else "real_clip"
   # With:
   greeting_resp = _greeting_handler.handle("(wake word)", "GREETING")
   if greeting_resp:
       greeting_path = greeting_resp.wav_path
       method = greeting_resp.method
   ```
   The `_greeting_handler` can be instantiated at module level (same as `_alert_runner`).
9. **Pass LED callbacks** to all `audio.play()` calls in `run_session()`:
   ```python
   audio.play(response.wav_path, on_chunk=leds.set_level, on_done=leds.all_off)
   ```

- [ ] **Step 2: Run full test suite**

Run: `cd /c/ws/benderpi && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add scripts/wake_converse.py
git commit -m "refactor: clean up orchestrator — use handler registry and timer alert runner"
```

---

### Task 18: Final verification and cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run full test suite**

Run: `cd /c/ws/benderpi && python -m pytest tests/ -v --tb=short`
Expected: all tests PASS

- [ ] **Step 2: Verify no unused imports or dead code**

Check that `responder.py` no longer imports `briefings`, `ha_control`, or `tts_generate` directly (handlers own those imports now). Verify `audio.py` no longer imports `leds`. Verify `stt.py` no longer has hardcoded STT constants.

- [ ] **Step 3: Verify backward compatibility**

Check that `from responder import Response` still works (re-export from handler_base).

- [ ] **Step 4: Commit any final cleanup**

```bash
git add -A
git commit -m "chore: final cleanup after architecture refactor"
```

- [ ] **Step 5: Update HANDOVER.md**

Document the architecture refactor: handler registry pattern, new file locations, how to add a new handler (create class extending Handler, declare intents, implement handle(), add to Responder's handler list).

```bash
git add HANDOVER.md
git commit -m "docs: update HANDOVER.md with architecture refactor notes"
```
