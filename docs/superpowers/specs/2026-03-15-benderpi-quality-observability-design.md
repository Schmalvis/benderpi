# BenderPi Quality, Observability & Modularity — Design Spec

**Date:** 2026-03-15
**Status:** Draft
**Scope:** Logging, metrics, session handover, audio/speed improvements, intent hardening, health watchdog, and modularity refactoring for future web UI support.

---

## 1. Goals

1. **Observability** — Replace all `print()` with structured, levelled logging. Add timing and outcome metrics. Make problems discoverable from logs even when nobody was watching.
2. **Session handover** — Auto-generate a project status report from logs/metrics. Maintain a committed handover context file. New Claude sessions start with full context.
3. **Audio & speed** — Improve STT reliability, reduce TTS latency, add "thinking" acknowledgements, tighten silence gaps. Prioritise local/fast responses, API as last resort.
4. **Intent accuracy** — Harden regex patterns, add misclassification logging, reorder priority, prepare for future local classifier.
5. **Health monitoring** — Automated anomaly detection with configurable thresholds, surfaced in status report.
6. **Modularity** — Decouple data logic from TTS wrapping, extract response chain, centralise config. Every module has a clean API usable by both the conversation loop and a future web UI.

### Non-goals (documented for future)

- Raspberry Pi AI HAT+ integration (separate analysis document)
- Local intent classifier (needs training data from improved logging)
- Whisper model upgrade beyond `tiny.en` (needs metrics baseline)
- OpenWakeWord migration
- Web UI (this design ensures the architecture supports it)

---

## 2. Bug Fixes & Dead Code Cleanup

### 2.1 Fix PyAudio crash risk in stt.py

**Problem:** `stt.py:55-56` creates a new `pyaudio.PyAudio()` instance per utterance and calls `pa.terminate()` after. This risks crashing PortAudio if it collides with the shared instance in `audio.py`. Contradicts the documented constraint in CLAUDE.md.

**Fix:** Import `audio.get_pa()` and use the shared instance. Remove the `pa.terminate()` call. The mic stream is still opened/closed per utterance (correct — must release `hw:2,0` for output).

### 2.2 Remove dead code

- **`scripts/handlers/ha_status.py`** — Never imported or called. `ha_control.py` handles its own TTS responses. Delete file.
- **`scripts/handlers/weather.py`** — Superseded by `briefings.py`. Delete file.
- **`ha_control.py:296-299`** — Duplicate `if action is None` check. Line 280 already handles this case and returns. Remove lines 296-299.

### 2.3 Fix thread safety in briefings.py

**Problem:** `_load_meta()` / `_save_meta()` are called from both the main thread (lazy refresh on request) and the `briefings-refresh` daemon thread (startup refresh) with no synchronisation.

**Fix:** Add a `threading.Lock` (`_meta_lock`) around all reads/writes to `briefings_meta.json`.

### 2.4 Fix temp file leak

**Problem:** In `wake_converse.py`, if `audio.play(wav)` raises after `tts_generate.speak()` returns a temp WAV path, the `os.unlink(wav)` on the next line never runs.

**Fix:** Wrap play+unlink in `try/finally` for all temp WAV code paths: HA control, AI fallback, error fallback, and the greeting/dismissal fallback TTS paths.

---

## 3. Centralised Configuration — `scripts/config.py`

### Purpose

Single source of truth for all tunables. Replaces scattered hardcoded constants and per-module `os.environ.get()` calls. A future web UI can read/write `bender_config.json` to adjust behaviour at runtime.

### Design

```python
# scripts/config.py
# Loads: .env (secrets) → bender_config.json (tunables) → env var overrides

class Config:
    # Audio
    sample_rate: int          # 44100
    silence_pre: float        # 0.02 (seconds)
    silence_post: float       # 0.08
    output_device: int        # 0

    # STT
    whisper_model: str        # "tiny.en"
    vad_aggressiveness: int   # 2
    silence_frames: int       # 50
    max_record_seconds: int   # 15

    # TTS
    piper_bin: str            # derived from project layout
    model_path: str           # derived from project layout

    # AI
    ai_model: str             # "claude-haiku-4-5-20251001"
    ai_max_tokens: int        # 150
    ai_max_history: int       # 6

    # Conversation
    silence_timeout: float    # 8.0
    thinking_sound: bool      # True

    # HA
    ha_url: str
    ha_token: str             # from .env only, never in config JSON
    ha_weather_entity: str

    # Briefings
    weather_ttl: int          # 1800 (seconds)
    news_ttl: int             # 7200

    # Logging
    log_level: str            # "INFO"
    log_level_file: str       # "DEBUG"

    # LED
    led_count: int            # 12
    led_brightness: float     # 0.8
    led_colour: tuple         # (255, 120, 0)
```

### Loading order

1. Hardcoded defaults (in `Config` class)
2. `bender_config.json` overrides (committed, runtime-editable)
3. `.env` overrides (secrets only — `HA_TOKEN`, `ANTHROPIC_API_KEY`, `PORCUPINE_ACCESS_KEY`)
4. Environment variable overrides (e.g. `BENDER_LOG_LEVEL`)

### bender_config.json

Committed to repo with sensible defaults. Contains only non-secret tunables. Format:

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
  "led_colour": [255, 120, 0]
}
```

### Migration

Every module replaces its local constants and `os.environ.get()` calls with imports from `config`:

```python
from config import cfg
# Before: WHISPER_MODEL = "tiny.en"
# After:  uses cfg.whisper_model
```

---

## 4. Structured Logging — `scripts/logger.py`

### Purpose

Replace all `print()` calls with structured, levelled logging. Provide persistent searchable logs for diagnostics, metrics collection, and the health watchdog.

### Design

```python
# scripts/logger.py
import logging
from logging.handlers import RotatingFileHandler

def get_logger(name: str) -> logging.Logger:
    """Return a child logger under 'bender' namespace.
    E.g. get_logger("stt") → logger named 'bender.stt'
    """
```

### Handlers

| Handler | Destination | Format | Level |
|---|---|---|---|
| Console (stdout) | journald | `%(asctime)s %(levelname)-5s [%(name)s] %(message)s` | `cfg.log_level` (default INFO) |
| Rotating file | `logs/bender.log` | Same | `cfg.log_level_file` (default DEBUG) |

Rotating file: max 5MB, keeps 3 backups.

### Log levels used

| Level | Usage |
|---|---|
| `DEBUG` | VAD frame counts, entity cache hits, Whisper raw output, config values at startup |
| `INFO` | Wake word detected, intent classified, clip played, session start/end, briefing refreshed |
| `WARNING` | Whisper hallucination filtered, HA fetch timeout (recovered), briefing cache stale, empty STT result with significant PCM data |
| `ERROR` | TTS subprocess failure, API error, stream open failure, unhandled exception in session |

### Migration

All `print()` and `print(f"Error: {e}")` calls across every script become appropriate `log.info()`, `log.warning()`, or `log.error()` calls. Standalone test blocks (`if __name__ == "__main__"`) may keep `print()` for interactive use.

---

## 5. Metrics & Timing — `scripts/metrics.py`

### Purpose

Lightweight timing and outcome tracking. Data source for the status report and health watchdog.

### Design

```python
# scripts/metrics.py

@contextmanager
def timer(name: str, **tags):
    """Context manager that records elapsed time.
    Usage: with metrics.timer("stt_transcribe"): ...
    """

def count(name: str, **tags):
    """Record a counter event.
    Usage: metrics.count("intent", intent="GREETING")
    """
```

### Storage

One JSON line per event in `logs/metrics.jsonl`:

```json
{"ts": "2026-03-15T10:30:01.234Z", "type": "timer", "name": "stt_transcribe", "duration_ms": 1823}
{"ts": "2026-03-15T10:30:01.234Z", "type": "timer", "name": "stt_record", "duration_ms": 4200}
{"ts": "2026-03-15T10:30:01.500Z", "type": "timer", "name": "tts_generate", "duration_ms": 1150}
{"ts": "2026-03-15T10:30:01.500Z", "type": "count", "name": "intent", "intent": "GREETING"}
{"ts": "2026-03-15T10:30:02.000Z", "type": "count", "name": "stt_empty", "pcm_bytes": 1200}
{"ts": "2026-03-15T10:30:02.000Z", "type": "count", "name": "api_call", "model": "claude-haiku-4-5-20251001"}
{"ts": "2026-03-15T10:30:02.000Z", "type": "count", "name": "error", "category": "ha_timeout"}
```

Rolling file: same rotation as bender.log (5MB x 3).

### Instrumentation points

| What | Metric type | Location |
|---|---|---|
| Recording duration | timer `stt_record` | `stt.py` |
| Transcription time | timer `stt_transcribe` | `stt.py` |
| TTS generation | timer `tts_generate` | `tts_generate.py` |
| API call | timer `ai_api_call` | `ai_response.py` |
| HA REST call | timer `ha_call` | `ha_control.py` |
| Audio playback | timer `audio_play` | `audio.py` |
| End-to-end response | timer `response_total` | `responder.py` |
| Briefing generation | timer `briefing_generate` | `briefings.py` |
| Intent classified | count `intent` | `responder.py` |
| STT empty return | count `stt_empty` | `stt.py` |
| Whisper hallucination filtered | count `stt_hallucination` | `stt.py` |
| API call made | count `api_call` | `ai_response.py` |
| Error by category | count `error` | various |
| Session start/end | count `session` | `wake_converse.py` |

### Performance

The timer is just two `time.monotonic()` calls and a JSON line write. No threads, no in-memory aggregation. Aggregation is done offline by `generate_status.py`.

---

## 6. Session Handover

### 6.1 Auto-generated status report — `scripts/generate_status.py` → `STATUS.md`

Reads `logs/metrics.jsonl`, `logs/bender.log`, and conversation JSONL files. Produces `STATUS.md` at project root.

**Triggered by:**
- `git_pull.sh` after a successful pull (so STATUS.md is current when a new Claude session starts)
- Manual: `venv/bin/python scripts/generate_status.py`

**Output structure:**

```markdown
# BenderPi Status Report
Generated: 2026-03-15T10:30:00Z

## Health
- Service uptime: 4d 12h (last restart: 2026-03-11)
- Errors (24h): 2 (HA timeout x1, TTS failure x1)
- Watchdog alerts: none

## Performance (7-day averages)
- STT latency: 1.8s (recording) + 0.9s (transcribe)
- TTS generation: 1.2s
- API fallback: 2.1s (call) + 1.2s (TTS)
- End-to-end (wake to response): 4.2s avg

## Usage (7 days)
- Sessions: 34 | Turns: 112
- Local responses: 89% | API fallback: 9% | Errors: 2%
- Top intents: GREETING 28, HA_CONTROL 22, JOKE 18, UNKNOWN 12

## Attention Needed
- (populated by watchdog — see section 8)

## Recent Errors (last 48h)
- (parsed from logs/bender.log, ERROR level only)

## Recent Changes
- (from git log --oneline -5)
```

`STATUS.md` is **gitignored** — it's auto-generated on the Pi and would cause merge noise if committed.

### 6.2 Maintained context file — `HANDOVER.md`

Committed to repo. Human/AI-maintained context for decisions, priorities, and architectural notes.

```markdown
# BenderPi Handover Context
Last updated: 2026-03-15

## Current Priorities
- (what to work on next and why)

## Recent Decisions
- (architectural choices, trade-offs, reasoning)

## Known Issues
- (things needing attention not captured elsewhere)

## Future Considerations
- (ideas parked for later)
```

**Convention (added to CLAUDE.md):** Each Claude session that makes changes should update `HANDOVER.md` before finishing. `STATUS.md` is read-only (auto-generated). `HANDOVER.md` is the place for context that metrics can't capture.

---

## 7. Modularity Refactoring

### 7.1 Decouple data logic from TTS — ha_control.py

Split `ha_control.control()` into two layers:

```python
def execute(user_text: str) -> dict:
    """Parse intent, call HA, return structured result.
    Returns: {
        "action": "on" | "off" | "set_temp",
        "entities": [{"entity_id": ..., "friendly_name": ..., "success": bool}],
        "room_display": "Kitchen",
        "temperature": 21.0 | None,
        "error": None | "no_match" | "no_action" | "ha_failed"
    }
    """

def control(user_text: str) -> str:
    """Execute + wrap result in Bender TTS. Returns temp WAV path.
    Existing API preserved — wake_converse.py unchanged.
    """
    result = execute(user_text)
    text = _result_to_speech(result)
    return tts_generate.speak(text)
```

A future web UI calls `execute()` directly and renders the result however it wants.

### 7.2 Decouple data logic from TTS — briefings.py

```python
def get_weather_text() -> str:
    """Fetch weather data, return Bender-style text. No TTS."""

def get_weather_wav() -> str:
    """Cached WAV version. Existing API preserved."""

def get_news_text() -> str:
    """Fetch news, return Bender-style text. No TTS."""

def get_news_wav() -> str:
    """Cached WAV version. Existing API preserved."""
```

### 7.3 Extract response chain — `scripts/responder.py`

Extract the response priority logic from `wake_converse.py:run_session()` into a standalone module.

```python
from dataclasses import dataclass

@dataclass
class Response:
    text: str               # response text (for logging, web UI display)
    wav_path: str           # path to WAV file
    method: str             # real_clip | pre_gen_tts | promoted_tts | handler_weather | handler_news | handler_ha | ai_fallback | error_fallback
    intent: str             # classified intent
    sub_key: str | None     # intent sub-key
    is_temp: bool           # True if wav_path is a temp file (caller must unlink)
    model: str | None       # AI model used, if any

def get_response(user_text: str, ai: AIResponder) -> Response:
    """Classify intent, resolve through priority chain, return Response.

    Priority:
      1. Real Bender clip (speech/wav/)
      2. Pre-generated TTS (speech/responses/<category>/)
      3. Promoted TTS (speech/responses/promoted/)
      4. Dynamic handler (briefings, HA control)
      5. AI fallback (Claude API + TTS)
    """
```

**wake_converse.py becomes a thin orchestrator:**

```python
while True:
    text = stt.listen_and_transcribe()
    if not text: ...  # silence timeout handling

    response = responder.get_response(text, ai)
    audio.play(response.wav_path)
    if response.is_temp:
        os.unlink(response.wav_path)
    log.log_turn(text, response.intent, response.sub_key,
                 response.method, response.text, response.model)

    if response.intent == "DISMISSAL":
        break
```

### 7.4 Audio oneshot mode — audio.py

Add a method for playing audio outside a conversation session (for future puppet mode):

```python
def play_oneshot(wav_path: str):
    """Open stream, play clip, close stream. Independent of session state.
    Thread-safe — blocks behind _lock if a session is active.
    """
```

---

## 8. Audio Quality & Response Speed

### 8.1 Whisper hallucination filter — stt.py

Add a blocklist of known Whisper `tiny.en` phantom outputs:

```python
WHISPER_HALLUCINATIONS = {
    "thank you", "thanks for watching", "subscribe",
    "like and subscribe", "thanks for listening",
    "you", "the", "i", "a",  # single-word noise
}
```

After transcription, if `text.lower().strip()` is in the blocklist, log a warning with the PCM byte count and return empty string. The metrics system records a `stt_hallucination` count event.

### 8.2 Persistent Piper subprocess — tts_generate.py

Replace subprocess-per-call with a long-running Piper process.

```python
class PiperProcess:
    """Manages a persistent Piper process using --json-input mode."""

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()

    def _ensure_running(self):
        """Start or restart the Piper process if needed."""

    def speak(self, text: str) -> str:
        """Send text to Piper, read WAV output, return temp file path.
        Falls back to subprocess-per-call if persistent process dies.
        """
```

Piper's `--json-input` mode reads JSON lines from stdin:
```json
{"text": "Bite my shiny metal ass!"}
```
And writes WAV data to the specified output path.

Expected improvement: ~0.5-1s faster per dynamic TTS call (eliminates model load cold start).

The existing `speak()` function signature is preserved — callers are unaffected.

### 8.3 Thinking acknowledgement sound

Pre-generate a set of short "thinking" clips via `prebuild_responses.py`:

```python
THINKING_SOUNDS = [
    "Hmm.",
    "Let me think.",
    "Hang on.",
    "One sec.",
]
```

Stored in `speech/responses/thinking/`. When `responder.get_response()` determines the response will require generation time (AI fallback, HA control, briefing cache miss), it plays a random thinking clip first via `audio.play()` before starting generation.

The thinking clip plays synchronously (it's ~0.5s), then generation proceeds. This fills the silence gap so the user knows Bender heard them.

Controlled by `cfg.thinking_sound` (default True).

### 8.4 Reduced silence gaps — audio.py

Current: 50ms pre-silence, 200ms post-silence.
New defaults: 20ms pre-silence, 80ms post-silence.

Values come from `cfg.silence_pre` and `cfg.silence_post` so they can be tuned without code changes. Logged at startup at DEBUG level.

### 8.5 Direct PCM to Whisper — stt.py

Remove the temp WAV file round-trip. `faster-whisper` accepts numpy arrays directly:

```python
def listen_and_transcribe() -> str:
    pcm = _record_utterance()
    if len(pcm) < FRAME_BYTES * 3:
        return ""
    audio_array = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = model.transcribe(audio_array, language="en", beam_size=1)
    return " ".join(s.text for s in segments).strip()
```

Saves ~10-20ms of disk I/O per utterance.

---

## 9. Intent Hardening

### 9.1 Fix false-positive patterns

Key changes:

| Pattern | Problem | Fix |
|---|---|---|
| `\bgood\b` (AFFIRMATION) | Matches "good restaurant", "good morning" | Require short utterance or standalone: `^(good|that's good|very good)$` or utterance <=4 words |
| `\bstop\b` (DISMISSAL) | Matches "don't stop", "bus stop" | `\b(stop it\|stop bender\|just stop)\b\|^stop$` |
| `\bok(ay)?\b` (AFFIRMATION) | Matches "what's ok to eat" | `^ok(ay)?$\|^ok(ay)? bender$` |
| `\b(do\|job\|...)` (PERSONAL/job) | `do` matches almost anything | Remove `do`, keep `job\|purpose\|programmed\|function\|what do you do` |
| `\b(home\|house)` (PERSONAL/where_live) | Matches "smart home", "house lights" | Add negative lookahead: `(?!.*\b(light\|lamp\|on\|off\|heating)\b)` |

### 9.2 Utterance-length heuristic

Simple intents (GREETING, AFFIRMATION, DISMISSAL) are almost always short utterances. Add a check:

```python
SIMPLE_INTENTS = {"GREETING", "AFFIRMATION", "DISMISSAL"}
word_count = len(t.split())

if intent in SIMPLE_INTENTS and word_count > 6:
    log.info(f"Long utterance matched simple intent {intent}, falling through to UNKNOWN")
    # Fall through to UNKNOWN
```

Threshold (6 words) configurable via `bender_config.json`.

### 9.3 Multi-match logging

For every classification, also check what other intents *would have* matched:

```python
all_matches = _check_all_intents(t)  # returns list of (intent, pattern)
if len(all_matches) > 1:
    log.warning(f"Multi-match: {all_matches} — resolved to {intent}")
metrics.count("intent_multi_match", resolved=intent, others=...)
```

This data is visible in logs and feeds the status report's "Attention Needed" section.

### 9.4 Priority reorder

New order (more specific first, catch-all last):

1. HA_CONTROL (most specific — room + action words)
2. WEATHER (specific topic keywords)
3. NEWS (specific topic keywords)
4. DISMISSAL (session-ending — important to catch)
5. JOKE (specific request patterns)
6. PERSONAL (sub-key patterns, moderately specific)
7. PROMOTED (custom patterns)
8. GREETING (vague, short-utterance)
9. AFFIRMATION (vague, short-utterance)
10. UNKNOWN (everything else)

---

## 10. Health Watchdog — `scripts/watchdog.py`

### Purpose

Analyse logs and metrics to detect anomalies. Output consumed by `generate_status.py` for the "Attention Needed" section.

### Design

Not a daemon. Runs as a function called by `generate_status.py`.

```python
def run_checks(config: dict) -> list[Alert]:
    """Run all health checks, return list of alerts."""

@dataclass
class Alert:
    severity: str   # "info" | "warning" | "error"
    check: str      # check name
    message: str    # human-readable description
    data: dict      # supporting metrics
```

### Checks and thresholds

| Check | Default threshold | Severity |
|---|---|---|
| STT empty rate | >10% over 24h | warning |
| API fallback rate | >20% of turns (7d) | warning |
| Error rate | >5% of turns (7d) | error |
| STT latency | >4s avg (24h) | warning |
| TTS latency | >3s avg (24h) | warning |
| API latency | >5s avg (24h) | warning |
| Repeated AI query | same query >= 3 times (7d) | info |
| Briefing staleness | weather >2h or news >6h | warning |
| Session length | avg <1.5 turns (7d) | warning |
| Log gap | no events for >24h | error |

### Configuration — `watchdog_config.json`

Committed to repo. All thresholds overridable:

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

---

## 11. File Inventory

### New files

| File | Purpose |
|---|---|
| `scripts/config.py` | Centralised configuration |
| `scripts/logger.py` | Structured logging module |
| `scripts/metrics.py` | Timing & counter metrics |
| `scripts/responder.py` | Response priority chain (extracted from wake_converse) |
| `scripts/watchdog.py` | Health anomaly detection |
| `scripts/generate_status.py` | Auto-generated STATUS.md |
| `bender_config.json` | Runtime-overridable config defaults (committed) |
| `watchdog_config.json` | Health check thresholds (committed) |
| `HANDOVER.md` | Maintained session handover context (committed) |

### Deleted files

| File | Reason |
|---|---|
| `scripts/handlers/ha_status.py` | Never called — ha_control.py handles TTS directly |
| `scripts/handlers/weather.py` | Superseded by briefings.py |

### Modified files

| File | Changes |
|---|---|
| `scripts/wake_converse.py` | Thin orchestrator using responder.py. Logging. Temp file safety. Thinking sound. |
| `scripts/audio.py` | Configurable silence gaps. play_oneshot(). Logging. |
| `scripts/stt.py` | Shared PyAudio. Direct PCM-to-Whisper. Hallucination filter. Logging + metrics. |
| `scripts/tts_generate.py` | Persistent Piper subprocess. Logging + metrics. |
| `scripts/ai_response.py` | Config-driven. Logging + metrics. |
| `scripts/intent.py` | Tighter patterns. Priority reorder. Length heuristic. Multi-match logging. |
| `scripts/briefings.py` | Thread-safe meta. Separated text/WAV APIs. Logging + metrics. |
| `scripts/ha_control.py` | execute() + control() split. Remove dead code. Logging + metrics. |
| `scripts/conversation_log.py` | Uses logger.py instead of raw file writes (JSONL format preserved). |
| `scripts/review_log.py` | Reads metrics.jsonl in addition to conversation JSONL. |
| `scripts/prebuild_responses.py` | Thinking sounds added. Logging. |
| `scripts/git_pull.sh` | Calls generate_status.py after successful pull. |
| `scripts/leds.py` | Logging (minimal — startup config only). |
| `CLAUDE.md` | Updated with HANDOVER.md convention, new file descriptions, logging instructions. |
| `.gitignore` | Add STATUS.md, logs/bender.log*, logs/metrics.jsonl* |

---

## 12. Architecture After Changes

```
                    ┌──────────────────────┐
                    │   wake_converse.py    │  thin orchestrator
                    │  (wake → listen →     │
                    │   respond → play →    │
                    │   log → loop)         │
                    └──────┬───────────────┘
                           │
              ┌────────────┼────────────────┐
              │            │                │
              v            v                v
        ┌──────────┐ ┌──────────┐    ┌──────────┐
        │  stt.py  │ │responder │    │ audio.py │
        │          │ │  .py     │    │          │
        │ listen   │ │ intent   │    │ play()   │
        │ + transcr│ │ → resolve│    │ oneshot()│
        └──────────┘ │ → respond│    │ session()│
                     └────┬─────┘    └──────────┘
                          │
           ┌──────┬───────┼───────┬──────────┐
           │      │       │       │          │
           v      v       v       v          v
        intent  clips  briefings  ha_ctrl  ai_resp
        .py     index  .py        .py      .py
                .json  text/wav   exec/    respond
                       split      ctrl     + tts
                                  split

        ┌──────────────────────────────────────┐
        │         shared infrastructure        │
        │  config.py │ logger.py │ metrics.py  │
        │  tts_generate.py │ leds.py           │
        └──────────────────────────────────────┘

        ┌──────────────────────────────────────┐
        │         offline tools                │
        │  generate_status.py │ watchdog.py    │
        │  review_log.py │ prebuild_responses  │
        └──────────────────────────────────────┘

Future web UI → imports: config, responder, tts_generate,
                         audio.play_oneshot, ha_control.execute,
                         briefings.*_text, log/metrics readers
```

Every module exposes a clean API. The conversation loop and a future web UI are both consumers. No module depends on the conversation loop existing.

---

## 13. Implementation Order

Incremental commits on `main`, each leaving the system functional:

1. Bug fixes & dead code cleanup (section 2)
2. `config.py` + `bender_config.json` (section 3)
3. `logger.py` + migrate all print() (section 4)
4. `metrics.py` + instrument all modules (section 5)
5. `responder.py` + refactor wake_converse.py (section 7.3)
6. ha_control execute/control split (section 7.1)
7. briefings text/wav split (section 7.2)
8. audio.py: configurable gaps + play_oneshot (sections 7.4, 8.4)
9. stt.py: shared PyAudio + direct PCM + hallucination filter (sections 2.1, 8.1, 8.5)
10. tts_generate.py: persistent Piper subprocess (section 8.2)
11. Thinking sounds (section 8.3)
12. Intent hardening (section 9)
13. `generate_status.py` + `watchdog.py` + `HANDOVER.md` (sections 6, 10)
14. `git_pull.sh` update + `.gitignore` + `CLAUDE.md` update
