# Streaming TTS — Design Spec

**Date:** 2026-04-01
**Status:** Approved
**Scope:** Eliminate the full-response-wait for TTS by playing sentence 1 as soon as Piper finishes it, while sentences 2+ synthesise concurrently.

---

## Background

Piper TTS runs on CPU (~300–500ms per sentence on BenderPi). The current pattern generates the full response as a single concatenated WAV before playback begins. A 3-sentence reply takes ~1s before the first word plays.

`tts_generate.speak_streaming()` and `audio.play_stream()` already exist and are used by the main conversation path. Three call sites still use the old pattern:

| Call site | File | Context |
|---|---|---|
| Camera response | `scripts/web/app.py` — `vision_analyse` bg task | Outside session |
| Greeting synthesis | `scripts/wake_converse.py` line ~201 | Inside open session |
| Passive vision comment | `scripts/wake_converse.py` line ~420 | Daemon thread, outside session |

---

## Architecture

### New function: `audio.play_stream_oneshot(wav_iter, on_chunk=None, on_done=None)`

For use outside a session (camera, passive vision). Mirrors `play_oneshot` lifecycle but accepts a WAV iterator:

- Acquires `_lock` for the full duration (never shares a session stream)
- Opens a fresh PyAudio stream if none is active; uses the existing one if it is
- Plays each WAV in sequence as the iterator yields them; unlinks each after playing
- Closes the stream after the last clip only if it opened it
- On abort: calls `wav_iter.close()` to trigger generator cleanup, then drains and unlinks any remaining yielded paths
- Calls `on_done` exactly once after the iterator is exhausted (or aborted)

### Fix: `tts_generate.speak_streaming()` generator cleanup

The existing `except Exception` block does not catch `GeneratorExit` (a `BaseException`). When a caller abandons the generator mid-iteration, WAV files for completed-but-unyielded futures leak.

Change:
```python
except Exception:  # current
```
to:
```python
except BaseException:  # catches GeneratorExit too
```

This ensures temp files are cleaned up whether the generator raises, is closed, or is garbage collected.

### Call site changes

**`scripts/web/app.py` — `vision_analyse` background task:**

Replace:
```python
wav = _tts.speak(t)
try:
    leds.set_talking()
    audio.play_oneshot(wav, on_chunk=leds.set_level, on_done=leds.all_off)
finally:
    if os.path.exists(wav):
        os.unlink(wav)
```
With:
```python
leds.set_talking()
audio.play_stream_oneshot(
    _tts.speak_streaming(t),
    on_chunk=leds.set_level,
    on_done=leds.all_off,
)
```
(`play_stream_oneshot` handles WAV cleanup internally.)

**`scripts/wake_converse.py` line ~201 — greeting inside session:**

Replace:
```python
wav = tts_generate.speak(text)
audio.play(wav, on_chunk=_check_abort_on_chunk, on_done=leds.all_off)
os.unlink(wav)
```
With:
```python
audio.play_stream(
    tts_generate.speak_streaming(text),
    on_chunk=_check_abort_on_chunk,
    on_done=leds.all_off,
)
```

**`scripts/wake_converse.py` line ~420 — passive vision comment (outside session):**

Replace:
```python
wav = tts_generate.speak(comment)
audio.play(wav, on_done=leds.all_off)
if os.path.exists(wav):
    os.unlink(wav)
```
With:
```python
audio.play_stream_oneshot(
    tts_generate.speak_streaming(comment),
    on_done=leds.all_off,
)
```

---

## File Map

| File | Change |
|---|---|
| `scripts/audio.py` | Add `play_stream_oneshot()` |
| `scripts/tts_generate.py` | Fix `speak_streaming()` — `except Exception` → `except BaseException` |
| `scripts/web/app.py` | Update `vision_analyse` background task |
| `scripts/wake_converse.py` | Update greeting and passive vision call sites |

---

## Testing

- Unit tests for `play_stream_oneshot`: mock WAV iterator, verify playback order, cleanup, and abort behaviour
- Verify `speak_streaming` generator cleanup: confirm temp files are unlinked when generator is closed mid-iteration
- Regression: existing `play_stream` and `play_oneshot` tests must still pass
- Integration: camera endpoint background task calls `play_stream_oneshot`, not `play_oneshot`

---

## Known Constraints

- All WAVs from `speak_streaming` are 44100Hz mono int16 (enforced by `_resample_and_pad`). `play_stream_oneshot` reuses the same stream across clips without re-opening — this is safe given consistent format.
- `on_done` fires once, after the last clip. No LED callbacks fire between sentences.
- Single-sentence responses: `speak_streaming` yields one WAV — behaviour is identical to the current `play_oneshot` path.
