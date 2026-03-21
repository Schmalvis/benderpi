# Architecture Refactor — Design Spec

**Date:** 2026-03-21
**Status:** Draft
**Scope:** Five interconnected improvements to make BenderPi's architecture modular, consistent, and extensible.

---

## Problem Statement

A comprehensive architecture review identified five issues that, while not bugs, limit the system's ability to grow cleanly:

1. **No handler registry** — adding an intent requires editing three files; `responder.py` has a growing if/elif chain
2. **Timer alert logic in orchestrator** — `wake_converse.py` contains a 70-line feature implementation that should be a handler module
3. **Config bypass** — four modules read config independently, making web UI config changes ineffective for STT, TTS, HA, and briefings
4. **Audio–LED coupling** — `audio.py` hard-imports `leds`, making audio untestable without LED hardware
5. **Duplicated IPC paths** — session file paths hardcoded in two files

These are tackled as a single refactor because they interact: the handler registry design determines how timer alert extraction looks, and config unification touches the same modules.

---

## Constraints

- No changes to external behaviour — Bender responds identically before and after
- No changes to the hardware audio constraint (WM8960 session model stays)
- Config keys must remain generic enough to support a future swap from Whisper/Piper to Raspberry Pi AI HAT+ NPU inference (inference engine change = config change + internal rewrite, not cross-module refactor)
- All existing tests must pass without modification (or be updated minimally to match new interfaces)

---

## 1. Handler Registry

### Current State

`responder.py` routes intents through a priority chain of if/elif branches in `get_response()` and `_respond_handler()`. Each intent type has bespoke methods (`_handle_weather`, `_handle_news`, `_respond_real_clip`, etc.). Adding a handler means editing `responder.py`.

### Design

#### New file: `scripts/handler_base.py`

```python
from dataclasses import dataclass

@dataclass
class Response:
    text: str
    wav_path: str
    method: str
    intent: str
    sub_key: str | None = None
    is_temp: bool = False
    needs_thinking: bool = False
    model: str | None = None

class Handler:
    """Base class for intent handlers."""
    intents: list[str] = []

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        """Return a Response, or None to fall through to AI fallback."""
        raise NotImplementedError
```

The `Response` dataclass moves from `responder.py` to `handler_base.py`. `responder.py` re-exports it for backward compatibility: `from handler_base import Response`.

#### Handler implementations

Each existing response path becomes a handler class. These live in the modules that already own the logic:

| Handler class | File | Intents claimed |
|---|---|---|
| `RealClipHandler` | `scripts/handlers/clip_handler.py` | `GREETING`, `AFFIRMATION`, `DISMISSAL`, `JOKE` |
| `PreGenHandler` | `scripts/handlers/pregen_handler.py` | `PERSONAL` |
| `PromotedHandler` | `scripts/handlers/promoted_handler.py` | `PROMOTED` |
| `WeatherHandler` | `scripts/handlers/weather_handler.py` | `WEATHER` |
| `NewsHandler` | `scripts/handlers/news_handler.py` | `NEWS` |
| `HAHandler` | `scripts/handlers/ha_handler.py` | `HA_CONTROL` |
| `TimerHandler` | `scripts/handlers/timer_handler.py` (existing, refactored) | `TIMER`, `TIMER_CANCEL`, `TIMER_STATUS` |

Each handler class:
- Extends `Handler`
- Declares `intents` as a class attribute
- Implements `handle(text, intent, sub_key) -> Response | None`
- Returns `None` to fall through to AI fallback (e.g., if a real clip lookup misses)

#### Responder changes

`Responder.__init__` builds a dispatch dict:

```python
from handlers.clip_handler import RealClipHandler
from handlers.pregen_handler import PreGenHandler
from handlers.promoted_handler import PromotedHandler
from handlers.weather_handler import WeatherHandler
from handlers.news_handler import NewsHandler
from handlers.ha_handler import HAHandler
from handlers.timer_handler import TimerHandler

class Responder:
    def __init__(self):
        handlers = [
            RealClipHandler(),
            PreGenHandler(),
            PromotedHandler(),
            WeatherHandler(),
            NewsHandler(),
            HAHandler(),
            TimerHandler(),
        ]
        self._dispatch: dict[str, list[Handler]] = {}
        for h in handlers:
            for intent in h.intents:
                self._dispatch.setdefault(intent, []).append(h)
```

Note: `_dispatch` maps intent → list of handlers, not a single handler. This supports the priority chain: for intents like `GREETING`, the `RealClipHandler` tries first; if it returns `None` (no matching clip), the next handler (or AI fallback) takes over.

`get_response()` becomes:

```python
def get_response(self, text: str, ai=None) -> Response:
    intent, sub_key = intent_classify(text)

    for handler in self._dispatch.get(intent, []):
        try:
            resp = handler.handle(text, intent, sub_key)
            if resp is not None:
                return resp
        except Exception as exc:
            logger.warning("handler %s failed: %s", type(handler).__name__, exc)

    return self._respond_ai(text, intent, sub_key, ai)
```

All existing `_handle_*`, `_respond_*`, `_is_real_clip`, `_is_pre_gen` methods are removed from `responder.py`. The file shrinks to: dispatch table setup, the `get_response` loop, and `_respond_ai`.

#### Handler priority within the same intent

Some intents could match multiple handlers (e.g., a `GREETING` could be a real clip OR a pre-gen TTS). Priority is determined by handler order in the `handlers` list passed to `__init__`. The list order is the priority chain:

1. `RealClipHandler` — original WAV clips (highest priority)
2. `PreGenHandler` — pre-built TTS
3. `PromotedHandler` — promoted AI responses
4. Domain handlers (weather, news, HA, timers)

Domain handlers claim unique intents so order among them doesn't matter.

---

## 2. Extract Timer Alert

### Current State

`wake_converse.py` contains `run_timer_alert()` (lines 152–220): a 70-line function managing alert playback, LED flash, voice/UI dismissal detection, and confirmation TTS. `TIMER_DISMISS_PATTERNS` (line 133) is also in the orchestrator.

### Design

#### New file: `scripts/handlers/timer_alert.py`

```python
class TimerAlertRunner:
    """Manages the alert interaction when a timer fires."""

    DISMISS_PATTERNS: list[re.Pattern]  # moved from wake_converse.py

    def run(self, timer_label: str, alert_clips: list[str],
            on_chunk: Callable | None = None,
            on_done: Callable | None = None) -> None:
        """
        Play alert in a loop, listen for voice/UI dismissal.
        Manages its own audio session lifecycle.
        """
        ...

    def _is_dismiss(self, text: str) -> bool:
        """Check transcribed text against dismiss patterns."""
        ...

    def _load_alert_clips(self) -> list[str]:
        """Load timer alert clips from index.json."""
        ...
```

Moves from `wake_converse.py`:
- `TIMER_DISMISS_PATTERNS` → `TimerAlertRunner.DISMISS_PATTERNS`
- `_is_timer_dismiss()` → `TimerAlertRunner._is_dismiss()`
- `run_timer_alert()` → `TimerAlertRunner.run()`
- `_load_timer_alert_clips()` → `TimerAlertRunner._load_alert_clips()`

Also extract `_load_thinking_clips()` into a shared helper (since it's near-identical to `_load_timer_alert_clips()`):

```python
# In handler_base.py or a utils module
def load_clips_from_index(key: str) -> list[str]:
    """Load clip paths from index.json by key."""
    ...
```

#### Orchestrator change

`wake_converse.py` replaces the inline block with:

```python
from handlers.timer_alert import TimerAlertRunner

_alert_runner = TimerAlertRunner()

# In main loop, where timer firing is detected:
if timers.get_firing():
    _alert_runner.run(label, on_chunk=leds.set_level, on_done=leds.all_off)
    continue
```

---

## 3. Unify Config Access

### Current State

Four modules bypass the `cfg` singleton from `config.py`:

| Module | What's bypassed |
|---|---|
| `stt.py` | Hardcodes `WHISPER_MODEL="tiny.en"`, `VAD_AGGRESSIVENESS=2`, `SILENCE_FRAMES=50`, `MAX_RECORD_S=15` |
| `tts_generate.py` | Creates own `Config()` instance via `_cfg = _Config()` |
| `ha_control.py` | Reads `HA_URL`/`HA_TOKEN` from `os.environ`; reads `bender_config.json` with raw `open()` |
| `briefings.py` | Reads `HA_URL`/`HA_TOKEN`/`HA_WEATHER_ENTITY` from `os.environ` |

### Design

#### `stt.py`

Replace hardcoded constants:

```python
# Before (dead config)
WHISPER_MODEL = "tiny.en"
VAD_AGGRESSIVENESS = 2
SILENCE_FRAMES = 50
MAX_RECORD_S = 15

# After
from config import cfg

# Hardware-fixed (not tunables, stay as constants)
SAMPLE_RATE = 16000
FRAME_MS = 30
AUDIO_DEVICE = "hw:2,0"
```

All references to the removed constants use `cfg.whisper_model`, `cfg.vad_aggressiveness`, `cfg.silence_frames`, `cfg.max_record_seconds`.

#### `tts_generate.py`

```python
# Before
try:
    from config import Config as _Config
    _cfg = _Config()
except Exception:
    _cfg = None

# After
from config import cfg
```

All `_cfg.` references become `cfg.`. Remove the try/except.

#### `ha_control.py`

```python
# Before
HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

# After
from config import cfg
# Use cfg.ha_url, cfg.ha_token
```

For exclude entities, add `ha_exclude_entities: list[str]` to `config.py`'s Config class (loaded from `bender_config.json`). Replace the raw `open()` + JSON parse in `_load_exclude_entities()` with `cfg.ha_exclude_entities`.

#### `briefings.py`

```python
# Before
HA_URL_DEFAULT = "http://homeassistant.local:8123"
HA_TOKEN_DEFAULT = ""
HA_ENTITY_DEFAULT = "weather.forecast_home"
# ... os.environ.get("HA_URL", HA_URL_DEFAULT) ...

# After
from config import cfg
# Use cfg.ha_url, cfg.ha_token, cfg.ha_weather_entity
```

Remove the `HA_*_DEFAULT` constants.

#### Config key naming

Keep config keys generic for future AI HAT+ migration:
- `whisper_model` → keep name (it's the STT model, and a future AI HAT+ config could reuse or replace it)
- `speech_rate` → keep name (TTS-engine-agnostic)
- `tts_model_path` → keep name (points to whatever model the current engine uses)

No Whisper-specific or Piper-specific key names are introduced. The existing names are already generic enough.

---

## 4. Decouple Audio from LEDs

### Current State

`audio.py` imports `leds` at module level and calls `leds.set_level()` on every audio chunk during `play()`, plus `leds.all_off()` after playback.

### Design

#### Remove hard import

```python
# Before
import leds

# After
# No leds import
```

#### Add callbacks to play functions

```python
def play(wav_path: str,
         on_chunk: Callable[[float], None] | None = None,
         on_done: Callable[[], None] | None = None) -> None:
    """Play a WAV file. Optional callbacks for chunk-level visualization."""
    ...
    # During playback loop, where leds.set_level was called:
    if on_chunk:
        on_chunk(rms_to_ratio(rms(data, sw)))
    ...
    # After playback, where leds.all_off was called:
    if on_done:
        on_done()


def play_oneshot(wav_path: str,
                 on_chunk: Callable[[float], None] | None = None,
                 on_done: Callable[[], None] | None = None) -> None:
    """Open session, play, close session. Optional LED callbacks."""
    ...
```

#### Orchestrator passes callbacks

In `wake_converse.py`:

```python
audio.play(wav, on_chunk=leds.set_level, on_done=leds.all_off)
```

In `web/app.py` (puppet mode):

```python
audio.play_oneshot(wav)  # No LED callbacks — or pass them if desired
```

#### `rms_to_ratio` stays in `audio.py`

The `rms_to_ratio()` helper converts raw audio data to a 0.0–1.0 float. It stays in `audio.py` since it's an audio concern. The callback receives the ratio, not raw data — the LED module doesn't need to know about audio internals.

---

## 5. Centralise IPC Paths

### Current State

Session file paths hardcoded identically in `wake_converse.py` (lines 56–57) and `web/app.py` (lines 30–31):

```python
_SESSION_FILE = os.path.join(_BASE_DIR, ".session_active.json")
_END_SESSION_FILE = os.path.join(_BASE_DIR, ".end_session")
```

### Design

Add to `config.py` Config class:

```python
session_file: str = os.path.join(_BASE_DIR, ".session_active.json")
end_session_file: str = os.path.join(_BASE_DIR, ".end_session")
```

Both `wake_converse.py` and `web/app.py` replace local constants with `cfg.session_file` and `cfg.end_session_file`.

---

## Files Changed Summary

### New files
| File | Purpose |
|---|---|
| `scripts/handler_base.py` | `Handler` base class + `Response` dataclass + `load_clips_from_index()` utility |
| `scripts/handlers/clip_handler.py` | `RealClipHandler` — original WAV clip responses |
| `scripts/handlers/pregen_handler.py` | `PreGenHandler` — pre-built TTS responses |
| `scripts/handlers/promoted_handler.py` | `PromotedHandler` — promoted AI responses |
| `scripts/handlers/weather_handler.py` | `WeatherHandler` — weather briefing |
| `scripts/handlers/news_handler.py` | `NewsHandler` — news briefing |
| `scripts/handlers/ha_handler.py` | `HAHandler` — Home Assistant control |
| `scripts/handlers/timer_alert.py` | `TimerAlertRunner` — timer alert interaction |

### Modified files
| File | Changes |
|---|---|
| `scripts/responder.py` | Remove all `_handle_*`/`_respond_*`/`_is_*` methods. Add dispatch dict. Simplify `get_response()` to loop. Re-export `Response` from `handler_base`. |
| `scripts/handlers/timer_handler.py` | Refactor to extend `Handler` base class. |
| `scripts/wake_converse.py` | Remove `run_timer_alert`, `TIMER_DISMISS_PATTERNS`, `_load_timer_alert_clips`, `_load_thinking_clips`. Replace with `TimerAlertRunner` call. Pass LED callbacks to `audio.play()`. |
| `scripts/audio.py` | Remove `import leds`. Add `on_chunk`/`on_done` callbacks to `play()`/`play_oneshot()`. |
| `scripts/stt.py` | Replace hardcoded constants with `cfg.*`. |
| `scripts/tts_generate.py` | Replace private `Config()` with `from config import cfg`. |
| `scripts/handlers/ha_control.py` | Replace `os.environ` reads and raw JSON with `cfg.*`. |
| `scripts/briefings.py` | Replace `os.environ` reads with `cfg.*`. |
| `scripts/config.py` | Add `session_file`, `end_session_file`, `ha_exclude_entities` attributes. |
| `scripts/web/app.py` | Replace local `_SESSION_FILE`/`_END_SESSION_FILE` with `cfg.*`. |

### Unchanged files
| File | Why unchanged |
|---|---|
| `scripts/intent.py` | Intent classification stays as-is — handlers claim intents, not the other way around |
| `scripts/leds.py` | No changes — it's just no longer hard-imported by `audio.py` |
| `scripts/conversation_log.py` | No interface changes |
| `scripts/metrics.py` | No interface changes |
| `scripts/logger.py` | No interface changes |
| `scripts/timers.py` | No interface changes |
| `scripts/time_parser.py` | No interface changes |

---

## Testing Strategy

- All existing tests must pass (update imports where `Response` moved)
- Add unit tests for `Responder` dispatch: verify each intent routes to the correct handler
- Add unit test for `audio.play()` with mock `on_chunk` callback (verifies LED decoupling)
- Add unit test for `TimerAlertRunner._is_dismiss()` (moved from orchestrator, should be tested standalone)
- Handler tests: each handler gets a test that calls `handle()` and verifies the `Response` shape

---

## Migration Notes

- `Response` dataclass re-exported from `responder.py` for backward compatibility — any external code importing `from responder import Response` continues to work
- Config key names kept generic for future AI HAT+ migration — no Whisper-specific or Piper-specific names introduced
- `bender_config.json` gains `ha_exclude_entities` key (defaults to current hardcoded list if absent)
