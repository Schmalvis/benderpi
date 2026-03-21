# Abrupt Stop — Design Spec

**Date:** 2026-03-21
**Status:** Draft
**Scope:** Add instant audio interruption via UI stop button and voice DISMISSAL, with configurable session behaviour.

---

## Problem Statement

Two UX issues with stopping Bender:

1. **UI stop button is not abrupt** — Pressing stop creates an end-session file, but the main loop only checks it between turns. Bender finishes his current sentence (potentially several seconds) before stopping. The button disappears briefly then reappears, making it feel broken.

2. **No voice stop during listening** — When Bender finishes talking and is listening for the next command, saying "stop" triggers a DISMISSAL intent but still plays a farewell clip before ending. This adds unnecessary delay. Users expect instant silence like Alexa.

### Current Interrupt Latency

A stop request can take **up to 23+ seconds** to take effect:
- Up to 15s if STT is recording (blocks until silence or hard cap)
- Plus duration of current `audio.play()` (no abort mechanism)
- Plus farewell clip playback on DISMISSAL

### Root Cause

`audio.play()` is a blocking chunk loop with no abort flag. `stt.listen_and_transcribe()` blocks on `stream.read()`. The end-session file is only polled at the top of the conversation loop, between turns.

---

## Constraints

- WM8960 hardware constraint stays — mic and speaker cannot operate simultaneously
- Voice stop only works during the **listening phase** (after Bender finishes talking), not during speech. Detecting voice during playback would require a separate audio device.
- No new hardware required
- Existing DISMISSAL patterns ("stop", "shut up", "bye", "that's all") continue to work
- Must not break timer alert dismissal flow

---

## Design

### 1. Audio Abort Mechanism

Add a `threading.Event` to `audio.py` for instant playback interruption.

**New module-level state:**
```python
_abort = threading.Event()
```

**New public function:**
```python
def abort():
    """Signal all in-progress playback to stop immediately."""
    _abort.set()
```

**Modified `play()` chunk loop:**
```python
def play(wav_path, on_chunk=None, on_done=None):
    _abort.clear()  # Reset at start of each play call
    with _lock:
        ...
        while data:
            if _abort.is_set():
                log.info("Playback aborted: %s", wav_path)
                break
            _stream.write(data)
            if on_chunk:
                on_chunk(rms_to_ratio(rms(data, sw)))
            data = wf.readframes(CHUNK)
    # on_done still called (outside lock) — even on abort,
    # so LEDs get cleaned up
    if on_done:
        on_done()
```

**Abort granularity:** ~12ms per chunk (512 samples at 44100Hz). The user hears at most one more chunk after abort is signalled — effectively instant.

**`play_oneshot()` same treatment** — checks `_abort.is_set()` in its chunk loop too.

**New helper:**
```python
def was_aborted() -> bool:
    """Return True if the last play() call was aborted."""
    return _abort.is_set()
```

This lets callers distinguish between "playback finished naturally" and "playback was aborted" without passing state around.

### 2. UI Stop Button (Abrupt)

**Web endpoint change:**

Enhance `/api/actions/end-session` in `app.py` to also trigger audio abort. Since `audio.py` runs in the `bender-converse` service (a separate process from the web server), the abort signal cannot be a direct `threading.Event` cross-process call. Instead:

**IPC approach:** The end-session file already serves as cross-process IPC. Add a second signal file for "abort now":

```python
# config.py
self.abort_file: str = os.path.join(_BASE_DIR, ".abort_playback")
```

**Web endpoint:**
```python
@app.post("/api/actions/end-session")
async def end_session():
    # Write both files — end session AND abort playback
    with open(cfg.end_session_file, "w") as f:
        f.write("")
    with open(cfg.abort_file, "w") as f:
        f.write("")
    return {"status": "ok"}
```

**Orchestrator polling:**

`wake_converse.py` adds an abort file check in two places:

1. **In the `on_chunk` callback passed to `audio.play()`** — checked every ~12ms during playback:
```python
def _check_abort_on_chunk(level):
    leds.set_level(level)
    if os.path.exists(cfg.abort_file):
        audio.abort()

# In run_session():
audio.play(wav, on_chunk=_check_abort_on_chunk, on_done=leds.all_off)
```

2. **After each `audio.play()` returns** — check end-session file for session termination:
```python
audio.play(response.wav_path, on_chunk=_check_abort_on_chunk, on_done=leds.all_off)
if os.path.exists(cfg.end_session_file):
    # Clean up and end session immediately — no farewell clip
    _cleanup_abort_files()
    audio.close_session()
    return
```

**Cleanup helper:**
```python
def _cleanup_abort_files():
    for f in [cfg.end_session_file, cfg.abort_file]:
        try:
            os.unlink(f)
        except OSError:
            pass
```

**Result:** When the user presses the UI stop button:
1. Web creates both `.end_session` and `.abort_playback` files
2. Within ~12ms, the `on_chunk` callback sees `.abort_playback` and calls `audio.abort()`
3. `play()` breaks out of the chunk loop immediately
4. The orchestrator sees `.end_session` after play returns and ends the session with no farewell clip
5. Total latency: **< 50ms** from button press to silence

### 3. Voice Stop (Listening Phase)

DISMISSAL intent is already detected during the listening phase. The change is in how the orchestrator **responds** to it.

**Current behaviour (in `run_session()`):**
1. User says "stop" → STT transcribes → intent classified as DISMISSAL
2. `responder.get_response()` returns a farewell clip (e.g., "Bite my shiny metal ass, I'm outta here")
3. Farewell clip plays to completion
4. Session ends

**New behaviour:**
```python
response = responder.get_response(text, ai)

if response.intent == "DISMISSAL":
    if cfg.stop_ends_session:
        # Abrupt end — no farewell, immediate silence
        log.info("DISMISSAL: abrupt session end (stop_ends_session=True)")
        session_log.log_turn(text, "DISMISSAL", None, "abrupt_stop")
        session_log.session_end("dismissal_abrupt")
        audio.close_session()
        return
    else:
        # Soft stop — skip current response, keep session alive
        log.info("DISMISSAL: soft stop, session continues")
        session_log.log_turn(text, "DISMISSAL", None, "soft_stop")
        continue  # Back to listening
```

**Key difference:** When `stop_ends_session` is `True` (default), the farewell clip is **never played**. The session ends immediately after DISMISSAL is classified, before any audio plays.

When `stop_ends_session` is `False`, the DISMISSAL doesn't end the session — it just skips the response and goes back to listening. The user stays in conversation.

### 4. Configuration

Add to `config.py` Config class:
```python
self.stop_ends_session: bool = True
```

Add to `bender_config.json`:
```json
"stop_ends_session": true
```

**`True` (default):** DISMISSAL intent ends the session immediately with no farewell clip. UI stop button ends session immediately.

**`False`:** DISMISSAL intent skips current response but session continues. UI stop button still ends the session entirely (it always ends the session — this config only affects voice dismissal).

---

## Files Changed

| File | Changes |
|---|---|
| `scripts/audio.py` | Add `_abort` Event, `abort()`, `was_aborted()`. Check abort in `play()` and `play_oneshot()` chunk loops. |
| `scripts/config.py` | Add `abort_file` and `stop_ends_session` attributes |
| `bender_config.json` | Add `stop_ends_session: true` |
| `scripts/wake_converse.py` | Add `_check_abort_on_chunk()` callback, abort file polling after playback, DISMISSAL fast-path |
| `scripts/web/app.py` | End-session endpoint writes both end-session and abort files |
| `tests/test_audio_callbacks.py` | Add tests for abort mechanism |

---

## Testing Strategy

- **Audio abort:** Unit test — start `play()` in a thread, call `abort()` from main thread, verify play returns early and `was_aborted()` is True
- **UI stop:** Integration test — write abort file, verify `on_chunk` callback triggers `audio.abort()`
- **Voice DISMISSAL:** Unit test — mock responder returning DISMISSAL intent, verify session ends without playing farewell clip when `stop_ends_session=True`
- **Config toggle:** Test both `True` and `False` paths for DISMISSAL handling
- **Timer alert:** Verify timer alert dismissal flow still works (it uses its own `_is_dismiss()` pattern, separate from session DISMISSAL)

---

## Edge Cases

- **Abort during `play_oneshot()`** (puppet mode) — should work the same way, abort breaks chunk loop
- **Abort while no playback is happening** — `_abort.set()` is harmless; next `play()` clears it
- **Rapid abort/play cycles** — `play()` clears `_abort` at the start, so a stale abort doesn't affect the next clip
- **STT blocking** — The abort mechanism does NOT interrupt STT recording. If Bender is listening (mic open), the UI stop must wait for STT to complete (~1.5s silence timeout). This is acceptable because the user doesn't perceive delay when Bender is silent and listening.
- **Abort file cleanup** — Both `.abort_playback` and `.end_session` files are cleaned up by the orchestrator after processing. If the service crashes, stale files are cleaned up at startup.
