# Abrupt Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add instant audio interruption via UI stop button and voice DISMISSAL, with configurable session behaviour.

**Architecture:** A `threading.Event` in `audio.py` provides sub-20ms playback abort. Cross-process IPC uses an abort file alongside the existing end-session file. The orchestrator checks the abort file in a throttled `on_chunk` callback during playback. Voice DISMISSAL behaviour is configurable via `cfg.dismissal_ends_session`.

**Tech Stack:** Python 3.13, threading.Event, pytest, FastAPI

**Spec:** `docs/superpowers/specs/2026-03-21-abrupt-stop-design.md`

---

## File Structure

### Modified files

| File | Changes |
|---|---|
| `scripts/audio.py` | Add `_abort` Event, `abort()`, `was_aborted()`. Check abort in `play()` and `play_oneshot()` chunk loops. |
| `scripts/config.py` | Add `abort_file` and `dismissal_ends_session` attributes |
| `bender_config.json` | Add `"dismissal_ends_session": true` |
| `scripts/wake_converse.py` | Add `_check_abort_on_chunk()`, abort file check after playback, DISMISSAL fast-path, cleanup stale files at startup |
| `scripts/web/app.py` | End-session endpoint writes both end-session and abort files |
| `tests/test_audio_callbacks.py` | Add tests for abort mechanism |

---

## Task 1: Add abort mechanism to audio.py

**Files:**
- Modify: `scripts/audio.py`
- Modify: `tests/test_audio_callbacks.py`

- [ ] **Step 1: Write tests for abort mechanism**

Add to `tests/test_audio_callbacks.py`:

```python
class TestAbort:
    def test_abort_stops_playback_early(self, audio_mod):
        """Calling abort() during play() should stop playback before all chunks are read."""
        wav = _make_wav(num_frames=44100 * 2)  # 2 seconds of audio
        try:
            chunks_played = []

            def _on_chunk(v):
                chunks_played.append(v)
                if len(chunks_played) == 3:
                    audio_mod.abort()

            audio_mod.play(wav, on_chunk=_on_chunk)
            # Should have stopped well before all ~172 chunks (44100*2/512)
            assert len(chunks_played) < 20, f"Expected early stop, got {len(chunks_played)} chunks"
            assert audio_mod.was_aborted() is True
        finally:
            os.unlink(wav)

    def test_was_aborted_false_on_normal_play(self, audio_mod):
        """was_aborted() returns False after normal playback."""
        wav = _make_wav()
        try:
            audio_mod.play(wav)
            assert audio_mod.was_aborted() is False
        finally:
            os.unlink(wav)

    def test_on_done_called_even_on_abort(self, audio_mod):
        """on_done must be called even when playback is aborted (for LED cleanup)."""
        wav = _make_wav(num_frames=44100 * 2)
        try:
            done_calls = []

            def _on_chunk(v):
                audio_mod.abort()

            audio_mod.play(wav, on_chunk=_on_chunk, on_done=lambda: done_calls.append(1))
            assert done_calls == [1]
        finally:
            os.unlink(wav)

    def test_abort_clears_on_next_play(self, audio_mod):
        """A stale abort from a previous play() should not affect the next one."""
        wav = _make_wav(num_frames=44100 * 2)
        try:
            # First play — abort immediately
            audio_mod.play(wav, on_chunk=lambda v: audio_mod.abort())
            assert audio_mod.was_aborted() is True

            # Second play — should play normally
            chunks = []
            audio_mod.play(wav, on_chunk=chunks.append)
            assert audio_mod.was_aborted() is False
            assert len(chunks) > 10  # played through
        finally:
            os.unlink(wav)

    def test_abort_on_play_oneshot(self, audio_mod):
        """abort() works on play_oneshot() too."""
        wav = _make_wav(num_frames=44100 * 2)
        try:
            chunks = []

            def _on_chunk(v):
                chunks.append(v)
                if len(chunks) == 3:
                    audio_mod.abort()

            audio_mod.play_oneshot(wav, on_chunk=_on_chunk)
            assert len(chunks) < 20
            assert audio_mod.was_aborted() is True
        finally:
            os.unlink(wav)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_audio_callbacks.py::TestAbort -v`
Expected: FAIL — `AttributeError: module 'audio' has no attribute 'abort'`

- [ ] **Step 3: Implement abort mechanism in audio.py**

In `scripts/audio.py`, add after the existing `_lock = threading.Lock()` line:

```python
_abort = threading.Event()
```

Add two new public functions after `get_pa()`:

```python
def abort():
    """Signal all in-progress playback to stop immediately."""
    _abort.set()


def was_aborted() -> bool:
    """Return True if the last play() call was aborted."""
    return _abort.is_set()
```

Modify `play()` — add `_abort.clear()` inside the `_lock` block (before the silence write), and add the abort check in the chunk loop:

```python
def play(wav_path: str, on_chunk=None, on_done=None):
    with metrics.timer("audio_play"):
        with _lock:
            _abort.clear()
            if _stream is None or not _stream.is_active():
                open_session()

            _stream.write(_silence(SILENCE_PRE))

            with wave.open(wav_path, 'rb') as wf:
                sw = wf.getsampwidth()
                data = wf.readframes(CHUNK)
                while data:
                    if _abort.is_set():
                        log.info("Playback aborted: %s", wav_path)
                        break
                    _stream.write(data)
                    if on_chunk:
                        on_chunk(rms_to_ratio(rms(data, sw)))
                    data = wf.readframes(CHUNK)

            if not _abort.is_set():
                _stream.write(_silence(SILENCE_POST))

    if on_done:
        on_done()
```

Key changes vs current code:
1. `_abort.clear()` inside `_lock` (prevents race)
2. `if _abort.is_set(): break` in chunk loop
3. Only write SILENCE_POST if not aborted (don't pad silence after abort)

Modify `play_oneshot()` — same abort check in its chunk loop:

```python
def play_oneshot(wav_path: str, on_chunk=None, on_done=None):
    with _lock:
        _abort.clear()
        was_open = _stream is not None
        if was_open:
            try:
                was_open = _stream.is_active()
            except Exception:
                was_open = False

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
                    if _abort.is_set():
                        log.info("Playback aborted (oneshot): %s", wav_path)
                        break
                    stream.write(data)
                    if on_chunk:
                        on_chunk(rms_to_ratio(rms(data, sw)))
                    data = wf.readframes(CHUNK)
            if not _abort.is_set():
                stream.write(_silence(SILENCE_POST))
        finally:
            if not was_open:
                stream.stop_stream()
                stream.close()
    if on_done:
        on_done()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_audio_callbacks.py -v`
Expected: all tests PASS (existing + new abort tests)

- [ ] **Step 5: Run full test suite**

Run: `cd /c/ws/benderpi && python -m pytest tests/ -v`
Expected: all 258+ tests PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/audio.py tests/test_audio_callbacks.py
git commit -m "feat: add abort mechanism to audio.py — threading.Event for instant playback stop"
```

---

## Task 2: Add config attributes for abort file and dismissal behaviour

**Files:**
- Modify: `scripts/config.py`
- Modify: `bender_config.json`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write tests for new config attributes**

Add to `tests/test_config.py`:

```python
def test_abort_file_default(self):
    """cfg.abort_file should point to .abort_playback in base dir."""
    assert cfg.abort_file.endswith(".abort_playback")

def test_dismissal_ends_session_default(self):
    """cfg.dismissal_ends_session should default to True."""
    assert cfg.dismissal_ends_session is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_config.py -v -k "abort_file or dismissal_ends"`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Add attributes to config.py**

In `scripts/config.py`, in the Config class, add to class-level defaults (after `silent_wakeword`):

```python
    # Stop behaviour
    dismissal_ends_session: bool = True  # True: DISMISSAL ends session immediately; False: skips response, session continues
```

In `__init__`, after the `self.end_session_file` line, add:

```python
        self.abort_file: str = os.path.join(_BASE_DIR, ".abort_playback")
```

- [ ] **Step 4: Add to bender_config.json**

Add `"dismissal_ends_session": true` to `bender_config.json`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_config.py -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/config.py bender_config.json tests/test_config.py
git commit -m "feat: add abort_file and dismissal_ends_session config attributes"
```

---

## Task 3: Update web endpoint to write abort file

**Files:**
- Modify: `scripts/web/app.py`
- Modify: `tests/test_web_session.py` (if exists, add test)

- [ ] **Step 1: Read the existing end-session endpoint test**

Check if `tests/test_web_session.py` exists. If so, read it to understand the test pattern.

- [ ] **Step 2: Write test for abort file creation**

Add a test that verifies the end-session endpoint creates both files:

```python
def test_end_session_creates_abort_file(self, ...):
    """POST /api/actions/end-session should create both end_session and abort files."""
    # Create a session file first so the endpoint doesn't return no_session
    ...
    response = client.post("/api/actions/end-session", ...)
    assert response.status_code == 200
    assert os.path.exists(cfg.abort_file)
    assert os.path.exists(cfg.end_session_file)
```

Follow the existing test patterns in the file for auth headers, client fixture, etc.

- [ ] **Step 3: Modify end-session endpoint in app.py**

In `scripts/web/app.py`, find the `end_session()` function (currently at line 128). Change it to also create the abort file:

```python
@app.post("/api/actions/end-session", dependencies=[Depends(require_pin)])
async def end_session():
    if not os.path.exists(cfg.session_file):
        return {"status": "no_session"}
    with open(cfg.end_session_file, "w") as f:
        f.write("")
    with open(cfg.abort_file, "w") as f:
        f.write("")
    return {"status": "ok"}
```

- [ ] **Step 4: Run tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_web_session.py -v` (or the relevant web test file)
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/web/app.py tests/test_web_session.py
git commit -m "feat: end-session endpoint writes abort file for instant playback stop"
```

---

## Task 4: Update orchestrator with abort-aware playback and DISMISSAL fast-path

**Files:**
- Modify: `scripts/wake_converse.py`

This is the largest task — three changes in the orchestrator.

- [ ] **Step 1: Add `_cleanup_abort_files()` helper**

Add after `_remove_session_file()` (around line 78):

```python
def _cleanup_abort_files():
    """Remove abort and end-session IPC files."""
    for f in [cfg.end_session_file, cfg.abort_file]:
        try:
            os.unlink(f)
        except OSError:
            pass
```

- [ ] **Step 2: Add throttled `_check_abort_on_chunk()` callback**

Add after the `_greeting_handler` definition (around line 104):

```python
_last_abort_check = 0.0

def _check_abort_on_chunk(level):
    """LED callback + throttled abort file check (~10 Hz)."""
    global _last_abort_check
    leds.set_level(level)
    now = time.monotonic()
    if now - _last_abort_check > 0.1:
        _last_abort_check = now
        if os.path.exists(cfg.abort_file):
            audio.abort()
```

- [ ] **Step 3: Replace the remote end-session handler at the top of the while loop**

In `run_session()`, replace the existing block at line 176-192:

```python
        # Check for remote end-session request
        if os.path.exists(cfg.end_session_file):
            try:
                os.unlink(cfg.end_session_file)
            except OSError:
                pass
            log.info("Session ended by remote request")
            if not (cfg.silent_wakeword and cfg.led_listening_enabled):
                dismiss_resp = _greeting_handler.handle("(remote end)", "DISMISSAL")
                if dismiss_resp:
                    leds.set_talking()
                    audio.play(dismiss_resp.wav_path, on_chunk=leds.set_level, on_done=leds.all_off)
            leds.all_off()
            session_log.session_end("remote_end")
            metrics.count("session", event="end", turns=session_log.turn, reason="remote_end")
            _remove_session_file()
            audio.close_session()
            return
```

With abrupt exit (no farewell clip):

```python
        # Check for remote end-session request
        if os.path.exists(cfg.end_session_file):
            _cleanup_abort_files()
            log.info("Remote end-session: abrupt exit")
            leds.all_off()
            session_log.session_end("remote_abrupt")
            metrics.count("session", event="end", turns=session_log.turn, reason="remote_abrupt")
            _remove_session_file()
            audio.close_session()
            return
```

- [ ] **Step 4: Replace all `on_chunk=leds.set_level` calls with `on_chunk=_check_abort_on_chunk`**

In `run_session()`, replace every occurrence of `on_chunk=leds.set_level` with `on_chunk=_check_abort_on_chunk`. There are 3 occurrences:
1. Greeting playback (~line 158)
2. Thinking sound playback (~line 221)
3. Response playback (~line 224)

Also replace the TTS fallback greeting path (~line 166).

- [ ] **Step 5: Add abort file check after each audio.play() returns**

After the main response `audio.play()` call (currently ~line 224), add:

```python
        # Play response
        audio.play(response.wav_path, on_chunk=_check_abort_on_chunk, on_done=leds.all_off)

        # Check for abort during playback (UI stop button pressed)
        if audio.was_aborted() or os.path.exists(cfg.end_session_file):
            _cleanup_abort_files()
            log.info("Session aborted during playback")
            if response.is_temp:
                try:
                    os.unlink(response.wav_path)
                except OSError:
                    pass
            leds.all_off()
            session_log.session_end("aborted")
            metrics.count("session", event="end", turns=session_log.turn, reason="aborted")
            _remove_session_file()
            audio.close_session()
            return

        if response.is_temp:
            ...
```

- [ ] **Step 6: Add DISMISSAL fast-path**

Replace the current DISMISSAL handler (currently ~line 235):

```python
        if response.intent == "DISMISSAL":
            leds.all_off()
            session_log.session_end("dismissal")
            metrics.count("session", event="end", turns=session_log.turn, reason="dismissal")
            _remove_session_file()
            audio.close_session()
            return
```

With configurable behaviour:

```python
        if response.intent == "DISMISSAL":
            if cfg.dismissal_ends_session:
                # Abrupt end — session already played the response above,
                # but with the new architecture we want to skip even that.
                # Since responder already returned a response and we already
                # played it, this just prevents continuing the loop.
                leds.all_off()
                session_log.session_end("dismissal")
                metrics.count("session", event="end", turns=session_log.turn, reason="dismissal")
                _remove_session_file()
                audio.close_session()
                return
            else:
                # Soft stop — session continues, just log the dismissal attempt
                log.info("DISMISSAL: soft stop, session continues")
                session_log.log_turn(text, "DISMISSAL", response.sub_key,
                                "soft_stop", response.text)
                last_heard = time.time()
                continue
```

**Wait** — for the `dismissal_ends_session=True` case, the spec says to skip the farewell clip entirely. But the current flow plays the response BEFORE checking the intent. To truly skip the farewell, the DISMISSAL check must happen BEFORE `audio.play()`:

```python
        response = responder.get_response(text, ai)

        # DISMISSAL fast-path — skip farewell clip if configured
        if response.intent == "DISMISSAL" and cfg.dismissal_ends_session:
            log.info("DISMISSAL: abrupt session end")
            if response.is_temp:
                try:
                    os.unlink(response.wav_path)
                except OSError:
                    pass
            leds.all_off()
            session_log.log_turn(text, "DISMISSAL", response.sub_key,
                            "abrupt_stop", response.text)
            session_log.session_end("dismissal_abrupt")
            metrics.count("session", event="end", turns=session_log.turn, reason="dismissal_abrupt")
            _remove_session_file()
            audio.close_session()
            return

        # Switch to talking LEDs
        leds.set_talking()
        ...
```

This goes right after `responder.get_response()` and BEFORE `leds.set_talking()` and `audio.play()`.

For `dismissal_ends_session=False`, the soft-stop logic replaces the existing DISMISSAL block after playback.

- [ ] **Step 7: Clean up stale abort files at startup**

In `main()`, before the main loop, add:

```python
    # Clean up stale IPC files from previous crashes
    _cleanup_abort_files()
    _remove_session_file()
```

- [ ] **Step 8: Run full test suite**

Run: `cd /c/ws/benderpi && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 9: Commit**

```bash
git add scripts/wake_converse.py
git commit -m "feat: abrupt stop — abort-aware playback, DISMISSAL fast-path, cleanup on startup"
```

---

## Verification Checklist

After all tasks are complete:

- [ ] `python -m pytest tests/ -v` — all tests pass
- [ ] `audio.abort()` stops playback within one chunk (~12ms)
- [ ] `audio.was_aborted()` correctly reflects abort state
- [ ] `_abort.clear()` is called inside `_lock` in both `play()` and `play_oneshot()`
- [ ] End-session endpoint creates both `.end_session` and `.abort_playback` files
- [ ] `_check_abort_on_chunk` is throttled to ~100ms between filesystem checks
- [ ] DISMISSAL with `dismissal_ends_session=True` skips farewell clip entirely
- [ ] DISMISSAL with `dismissal_ends_session=False` continues session
- [ ] Stale abort files are cleaned up at startup
- [ ] Timer alert flow is unaffected (uses separate dismissal patterns)
