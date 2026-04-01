# Streaming TTS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Play the first sentence of every TTS response as soon as Piper finishes it (~300ms), rather than waiting for the full concatenated WAV.

**Architecture:** `tts_generate.speak_streaming()` and `audio.play_stream()` already exist and are used by the main conversation path. This plan adds `audio.play_stream_oneshot()` for out-of-session callers, fixes a `GeneratorExit` cleanup bug in `speak_streaming`, and updates three remaining call sites that still use the old `speak()` + `play_oneshot()`/`play()` pattern.

**Tech Stack:** Python 3.13, PyAudio, Piper TTS (persistent process pool), pytest

---

## File Map

| File | Change |
|---|---|
| `scripts/tts_generate.py` | Fix `speak_streaming` — `except Exception` → `except BaseException` for `GeneratorExit` cleanup |
| `scripts/audio.py` | Add `play_stream_oneshot(wav_iter, on_chunk, on_done)` |
| `scripts/web/app.py` | `vision_analyse` bg task: `speak()`+`play_oneshot()` → `speak_streaming()`+`play_stream_oneshot()` |
| `scripts/wake_converse.py` | Greeting fallback + passive vision: `speak()`+`play()` → streaming equivalents |
| `tests/test_tts_streaming.py` | New file — tests for `speak_streaming` generator cleanup |
| `tests/test_audio_callbacks.py` | Add `play_stream_oneshot` tests |

---

## Task 1: Fix speak_streaming GeneratorExit cleanup

**Files:**
- Modify: `scripts/tts_generate.py` (line ~316)
- Create: `tests/test_tts_streaming.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tts_streaming.py`:

```python
"""Tests for tts_generate streaming generator cleanup."""
import os
import sys
import tempfile
import types
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test_speak_streaming_cleans_up_on_early_close(tmp_path, monkeypatch):
    """When generator is closed before all sentences are consumed, completed
    WAV files for unyielded sentences are deleted."""
    import importlib
    import tts_generate as tts

    created = []

    def fake_speak_single(text):
        f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=str(tmp_path))
        f.close()
        created.append(f.name)
        time.sleep(0.01)   # give other threads a moment to also complete
        return f.name

    monkeypatch.setattr(tts, "_speak_single", fake_speak_single)

    gen = tts.speak_streaming("Hello world. How are you today. Goodbye then.")

    # Consume only the first WAV
    first = next(gen)
    os.unlink(first)   # caller unlinks yielded paths

    # Close generator without consuming the rest
    gen.close()

    # Allow thread pool to settle
    time.sleep(0.1)

    # All files that were generated (but not yielded) must have been cleaned up
    leaks = [p for p in created[1:] if os.path.exists(p)]
    assert leaks == [], f"Leaked temp WAVs after generator close: {leaks}"


def test_speak_streaming_single_sentence_no_leak(tmp_path, monkeypatch):
    """Single-sentence path (no thread pool) yields one path and completes cleanly."""
    import tts_generate as tts

    paths = []

    def fake_speak_single(text):
        f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=str(tmp_path))
        f.close()
        paths.append(f.name)
        return f.name

    monkeypatch.setattr(tts, "_speak_single", fake_speak_single)

    result = list(tts.speak_streaming("Just one sentence."))
    assert len(result) == 1
    os.unlink(result[0])
    assert len(paths) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/pi/bender && python -m pytest tests/test_tts_streaming.py -v 2>&1 | tail -15
```

Expected: `test_speak_streaming_cleans_up_on_early_close FAILED` — the leak assertion fails because `except Exception` doesn't catch `GeneratorExit`.

`test_speak_streaming_single_sentence_no_leak` may pass already — that's fine.

- [ ] **Step 3: Fix speak_streaming in tts_generate.py**

In `scripts/tts_generate.py`, find `speak_streaming` (around line 300). The `try/except` block inside the `with ThreadPoolExecutor` block currently reads:

```python
        try:
            for future in futures:
                yield future.result()  # preserves sentence order; blocks only until each is ready
        except Exception:
            # Clean up temp files from any futures that already completed
            for f in futures:
                if f.done() and not f.cancelled():
                    try:
                        result = f.result()
                        os.unlink(result)
                    except Exception:
                        pass
            raise
```

Change `except Exception:` to `except BaseException:`:

```python
        try:
            for future in futures:
                yield future.result()  # preserves sentence order; blocks only until each is ready
        except BaseException:
            # Clean up temp files from any futures that already completed.
            # Using BaseException (not Exception) ensures GeneratorExit is caught too,
            # so temp files are cleaned up when the caller abandons the generator early.
            for f in futures:
                if f.done() and not f.cancelled():
                    try:
                        result = f.result()
                        os.unlink(result)
                    except Exception:
                        pass
            raise
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/pi/bender && python -m pytest tests/test_tts_streaming.py -v 2>&1 | tail -10
```

Expected: both tests `PASSED`

- [ ] **Step 5: Commit**

```bash
cd /home/pi/bender && git add scripts/tts_generate.py tests/test_tts_streaming.py && git commit -m "fix: catch GeneratorExit in speak_streaming to clean up unyielded WAV files"
```

---

## Task 2: Add play_stream_oneshot to audio.py

**Files:**
- Modify: `scripts/audio.py` (add after `play_oneshot`, around line 218)
- Modify: `tests/test_audio_callbacks.py` (add tests)

- [ ] **Step 1: Write the failing tests**

In `tests/test_audio_callbacks.py`, add these tests after the existing ones:

```python
# ---------------------------------------------------------------------------
# play_stream_oneshot tests
# ---------------------------------------------------------------------------

def _make_wav_bytes(n_frames: int = 1024) -> bytes:
    """Return minimal WAV file bytes (44100Hz mono int16)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00" * n_frames * 2)
    return buf.getvalue()


def _write_wav(path: str, n_frames: int = 1024):
    with open(path, "wb") as f:
        f.write(_make_wav_bytes(n_frames))


def test_play_stream_oneshot_plays_all_clips_and_unlinks(tmp_path):
    """play_stream_oneshot plays each WAV and unlinks it."""
    import audio

    paths = []
    for i in range(3):
        p = str(tmp_path / f"clip{i}.wav")
        _write_wav(p)
        paths.append(p)

    mock_stream = MagicMock()
    mock_stream.is_active.return_value = False
    mock_pa = MagicMock()
    mock_pa.open.return_value = mock_stream

    with patch.object(audio, "_pa", mock_pa), \
         patch.object(audio, "_stream", None):
        audio.play_stream_oneshot(iter(paths))

    # All files should be unlinked
    for p in paths:
        assert not os.path.exists(p), f"WAV not unlinked: {p}"

    # Stream opened and closed (oneshot — no active session)
    mock_pa.open.assert_called_once()
    mock_stream.stop_stream.assert_called_once()
    mock_stream.close.assert_called_once()


def test_play_stream_oneshot_calls_on_done_once(tmp_path):
    """on_done is called exactly once after all clips play."""
    import audio

    paths = [str(tmp_path / f"c{i}.wav") for i in range(2)]
    for p in paths:
        _write_wav(p)

    done_calls = []
    mock_stream = MagicMock()
    mock_stream.is_active.return_value = False
    mock_pa = MagicMock()
    mock_pa.open.return_value = mock_stream

    with patch.object(audio, "_pa", mock_pa), \
         patch.object(audio, "_stream", None):
        audio.play_stream_oneshot(iter(paths), on_done=lambda: done_calls.append(1))

    assert done_calls == [1], f"on_done called {len(done_calls)} times, expected 1"


def test_play_stream_oneshot_abort_unlinks_remaining(tmp_path):
    """Abort mid-stream: current and remaining WAVs are unlinked."""
    import audio

    paths = [str(tmp_path / f"c{i}.wav") for i in range(3)]
    for p in paths:
        _write_wav(p)

    clip_count = [0]
    original_write = None

    def abort_on_second_clip(data):
        """Trigger abort when second clip starts."""
        clip_count[0] += 1
        if clip_count[0] == 2:
            audio.abort()

    mock_stream = MagicMock()
    mock_stream.is_active.return_value = False
    mock_pa = MagicMock()
    mock_pa.open.return_value = mock_stream

    with patch.object(audio, "_pa", mock_pa), \
         patch.object(audio, "_stream", None):
        audio.play_stream_oneshot(iter(paths), on_chunk=lambda _: None)
        audio.abort()
        audio.play_stream_oneshot(iter(paths))

    # After abort, all files should be gone
    for p in paths:
        assert not os.path.exists(p), f"WAV not unlinked after abort: {p}"


def test_play_stream_oneshot_empty_iterator(tmp_path):
    """Empty iterator: completes cleanly, on_done still called."""
    import audio

    done = []
    mock_stream = MagicMock()
    mock_stream.is_active.return_value = False
    mock_pa = MagicMock()
    mock_pa.open.return_value = mock_stream

    with patch.object(audio, "_pa", mock_pa), \
         patch.object(audio, "_stream", None):
        audio.play_stream_oneshot(iter([]), on_done=lambda: done.append(1))

    assert done == [1]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/pi/bender && python -m pytest tests/test_audio_callbacks.py -k "stream_oneshot" -v 2>&1 | tail -15
```

Expected: `FAILED — AttributeError: module 'audio' has no attribute 'play_stream_oneshot'`

- [ ] **Step 3: Implement play_stream_oneshot in audio.py**

In `scripts/audio.py`, add after the `play_oneshot` function (after line 218):

```python
def play_stream_oneshot(wav_iter, on_chunk=None, on_done=None):
    """Open stream, play WAV clips from an iterator back-to-back, close stream.
    For use outside a session (camera responses, passive vision). Thread-safe.
    Unlinks each WAV after playing. Closes the generator on abort to trigger
    cleanup of unconsumed futures in speak_streaming.

    Args:
        wav_iter:  Iterator yielding WAV file paths (e.g. speak_streaming()).
        on_chunk:  Optional callback(amplitude: float) per audio chunk, [0.0, 1.0].
        on_done:   Optional callback called once after all clips finish (or abort).
    """
    gen = iter(wav_iter)
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
            for wav_path in gen:
                if _abort.is_set():
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
                    if hasattr(gen, "close"):
                        gen.close()  # triggers BaseException cleanup in speak_streaming
                    break
                try:
                    with wave.open(wav_path, "rb") as wf:
                        sw = wf.getsampwidth()
                        data = wf.readframes(CHUNK)
                        while data:
                            if _abort.is_set():
                                break
                            stream.write(data)
                            if on_chunk:
                                on_chunk(rms_to_ratio(rms(data, sw)))
                            data = wf.readframes(CHUNK)
                finally:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
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

```bash
cd /home/pi/bender && python -m pytest tests/test_audio_callbacks.py -v 2>&1 | tail -20
```

Expected: All tests `PASSED` (no regressions to existing audio tests)

- [ ] **Step 5: Commit**

```bash
cd /home/pi/bender && git add scripts/audio.py tests/test_audio_callbacks.py && git commit -m "feat: add play_stream_oneshot for out-of-session streaming TTS playback"
```

---

## Task 3: Update call sites

**Files:**
- Modify: `scripts/web/app.py`
- Modify: `scripts/wake_converse.py`

- [ ] **Step 1: Update vision_analyse background task in app.py**

In `scripts/web/app.py`, find the `_speak_in_background` function inside `vision_analyse`. Replace:

```python
    def _speak_in_background(t: str):
        try:
            wav = _tts.speak(t)
            try:
                leds.set_talking()
                audio.play_oneshot(wav, on_chunk=leds.set_level, on_done=leds.all_off)
            finally:
                import os as _os
                if _os.path.exists(wav):
                    _os.unlink(wav)
        except Exception as exc:
            log.warning("vision_analyse TTS/audio failed: %s", exc)
```

With:

```python
    def _speak_in_background(t: str):
        try:
            leds.set_talking()
            audio.play_stream_oneshot(
                _tts.speak_streaming(t),
                on_chunk=leds.set_level,
                on_done=leds.all_off,
            )
        except Exception as exc:
            log.warning("vision_analyse TTS/audio failed: %s", exc)
```

- [ ] **Step 2: Update greeting fallback in wake_converse.py**

In `scripts/wake_converse.py`, find the greeting fallback (around line 201). Replace:

```python
            text = "Yo. What do you want?"
            wav = tts_generate.speak(text)
            try:
                leds.set_talking()
                audio.play(wav, on_chunk=_check_abort_on_chunk, on_done=leds.all_off)
            finally:
                os.unlink(wav)
```

With:

```python
            text = "Yo. What do you want?"
            leds.set_talking()
            audio.play_stream(
                tts_generate.speak_streaming(text),
                on_chunk=_check_abort_on_chunk,
                on_done=leds.all_off,
            )
```

- [ ] **Step 3: Update passive vision comment in wake_converse.py**

In `scripts/wake_converse.py`, find the passive vision watcher (around line 420). Replace:

```python
            comment = f"Just so you know, I can see {description} in the room."
            wav = tts_generate.speak(comment)
            try:
                leds.set_talking()
                audio.play(wav, on_done=leds.all_off)
            finally:
                if os.path.exists(wav):
                    os.unlink(wav)
```

With:

```python
            comment = f"Just so you know, I can see {description} in the room."
            leds.set_talking()
            audio.play_stream_oneshot(
                tts_generate.speak_streaming(comment),
                on_done=leds.all_off,
            )
```

- [ ] **Step 4: Validate syntax**

```bash
python3 -m py_compile /home/pi/bender/scripts/web/app.py && echo "app.py OK"
python3 -m py_compile /home/pi/bender/scripts/wake_converse.py && echo "wake_converse.py OK"
```

Expected: both print `OK`

- [ ] **Step 5: Run full test suite**

```bash
cd /home/pi/bender && python -m pytest tests/ --tb=short --ignore=tests/test_web_puppet.py --ignore=tests/test_web_session.py --ignore=tests/test_web_timers.py --ignore=tests/test_web_vision.py -q 2>&1 | tail -10
```

Expected: All pass

- [ ] **Step 6: Restart service and smoke test**

```bash
sudo systemctl restart bender-web.service
sleep 3
systemctl is-active bender-web.service
```

Expected: `active`

Trigger "Ask Bender" from the UI. Expected: first sentence plays within ~400ms of the response being generated, subsequent sentences play back-to-back without gaps.

Check logs:

```bash
journalctl -u bender-web.service -f | grep -i "tts\|vision\|audio"
```

- [ ] **Step 7: Commit**

```bash
cd /home/pi/bender && git add scripts/web/app.py scripts/wake_converse.py && git commit -m "feat: use speak_streaming + play_stream_oneshot at all remaining TTS call sites"
```
