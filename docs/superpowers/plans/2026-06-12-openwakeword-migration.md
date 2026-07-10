# OpenWakeWord Migration Implementation Plan

> **Status: Authoritative.** This is the task-by-task plan that was actually
> executed to remove Porcupine and land openWakeWord (Phase 1, `hey_jarvis`
> interim model). It supersedes the earlier scoping doc
> `docs/superpowers/plans/2026-05-22-openwakeword-migration.md`. The custom
> "hey bender" model that later replaced `hey_jarvis` as the committed default
> is tracked separately in
> `docs/superpowers/plans/2026-06-12-hey-bender-wake-word.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Picovoice Porcupine (API key, proprietary, free tier ends June 30 2026) with openWakeWord (free, offline, ONNX) as the wake word detector for "Hey Bender".

**Architecture:** Phase 1 uses the bundled `hey_jarvis` pre-trained model as a stand-in while the custom "hey bender" model is trained offline. The `wait_for_wakeword()` function in `wake_converse.py` is the only site that changes — everything else (STT, conversation loop, audio) is untouched. openWakeWord requires 1280-sample frames (vs Porcupine's ~512); numpy replaces struct for PCM conversion.

**Tech Stack:** `openwakeword==0.4.0`, `numpy` (already installed), PyAudio (unchanged), ONNX runtime (installed as openwakeword dependency).

**Deadline:** 2026-06-30 — Picovoice free tier disabled.

---

## Context You Need to Read

Before starting, read these files:
- `scripts/wake_converse.py` — full file, especially `wait_for_wakeword()` (~line 90) and the imports at the top
- `scripts/config.py` — to understand how cfg fields are declared (dataclass-style)
- `bender_config.json` — to see the JSON key naming convention
- `requirements.txt` — to see current deps and formatting

**Critical facts from compat testing (already verified):**
- `Model(wakeword_model_paths=[path])` — note `wakeword_model_paths`, NOT `wakeword_models`
- `model.predict(np_int16_array)` returns `{model_name: float_score}` (e.g. `{'hey_jarvis_v0.1': 0.73}`)
- Bundled models are at: `venv/lib/python3.13/site-packages/openwakeword/resources/models/`
- Available: `alexa_v0.1.onnx`, `hey_jarvis_v0.1.onnx`, `hey_marvin_v0.1.onnx`, `hey_mycroft_v0.1.onnx`
- A harmless CUDA warning appears on import — ignore it, falls back to CPU
- openwakeword is already installed in the Pi venv from the compat test

---

## File Map

| File | Change |
|---|---|
| `scripts/wake_converse.py` | Remove pvporcupine import + KEYWORD_PATH; add numpy import + OWW constants; replace `wait_for_wakeword()` body; add `_load_oww_model()` helper |
| `scripts/config.py` | Add `oww_model_path: str` and `oww_threshold: float` fields |
| `bender_config.json` | Add `oww_model_path` and `oww_threshold` keys |
| `requirements.txt` | Add `openwakeword`; remove `pvporcupine==4.0.2`, `pvrecorder==1.2.7` (in Task 3) |
| `.env.example` | Remove `PORCUPINE_ACCESS_KEY` line |
| `CLAUDE.md` | Update env vars table and wake word detection description |
| `tests/test_oww_detection.py` | New: unit tests for `wait_for_wakeword()` with mock model |

---

## Task 1: Config fields + add openWakeWord to requirements

**Files:**
- Modify: `scripts/config.py`
- Modify: `bender_config.json`
- Modify: `requirements.txt`
- Modify: `.env.example`

**DO NOT remove pvporcupine from requirements.txt yet** — it's still imported in wake_converse.py at this point. Removing it now would break the service on the Pi. It gets removed in Task 3 after the import is gone.

- [ ] **Step 1.1 — Write the failing test**

Create `tests/test_oww_config.py`:

```python
"""Tests for OWW config fields."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_oww_config_fields_exist():
    from config import cfg
    assert hasattr(cfg, 'oww_model_path'), "cfg missing oww_model_path"
    assert hasattr(cfg, 'oww_threshold'), "cfg missing oww_threshold"
    assert isinstance(cfg.oww_model_path, str)
    assert isinstance(cfg.oww_threshold, float)
    assert 0.0 < cfg.oww_threshold <= 1.0


def test_oww_threshold_default_is_reasonable():
    from config import cfg
    assert cfg.oww_threshold == 0.5
```

- [ ] **Step 1.2 — Run to verify failure**

```bash
cd /home/pi/projects/benderpi
venv/bin/python -m pytest tests/test_oww_config.py -v 2>&1 | head -20
```

Expected: `AttributeError: 'Config' object has no attribute 'oww_model_path'` (or similar)

- [ ] **Step 1.3 — Add fields to config.py**

Read `scripts/config.py` first. Find where other path-style fields and float fields are declared in the Config dataclass. Add these two fields in a logical location (e.g. after the `local_llm_*` fields or in a new "Wake word" block):

```python
    oww_model_path: str = "models/hey_jarvis.onnx"
    oww_threshold: float = 0.5
```

- [ ] **Step 1.4 — Add keys to bender_config.json**

Read `bender_config.json` first. Add these keys in the JSON object (respect existing key ordering/grouping):

```json
"oww_model_path": "models/hey_jarvis.onnx",
"oww_threshold": 0.5
```

- [ ] **Step 1.5 — Add openwakeword to requirements.txt**

Read `requirements.txt` first. Add `openwakeword` in the appropriate section (AI/ML deps). Keep `pvporcupine==4.0.2` and `pvrecorder==1.2.7` for now.

- [ ] **Step 1.6 — Remove PORCUPINE_ACCESS_KEY from .env.example**

Read `.env.example`. Find and remove the `PORCUPINE_ACCESS_KEY` line. If there's a comment block explaining it, remove that too.

- [ ] **Step 1.7 — Run tests**

```bash
cd /home/pi/projects/benderpi
venv/bin/python -m pytest tests/test_oww_config.py -v
```

Expected: 2/2 PASS.

- [ ] **Step 1.8 — Commit**

```bash
git add scripts/config.py bender_config.json requirements.txt .env.example tests/test_oww_config.py
git commit -m "feat(config): add oww_model_path and oww_threshold; add openwakeword to requirements"
```

---

## Task 2: Replace wait_for_wakeword() with openWakeWord

**Files:**
- Modify: `scripts/wake_converse.py`
- Create: `tests/test_oww_detection.py`

The current function uses:
- `pvporcupine.create(...)` → creates the detector
- `porcupine.frame_length` (~512) → frame size
- `struct.unpack_from("h" * N, pcm)` → PCM conversion
- `porcupine.process(pcm_unpacked) >= 0` → detection
- `porcupine.delete()` in finally

The new function uses:
- `Model(wakeword_model_paths=[path])` → creates the detector (injected via `_oww_model` param for testing)
- `OWW_FRAME_SIZE = 1280` → constant
- `np.frombuffer(pcm, dtype=np.int16)` → PCM conversion (no struct needed)
- `max(model.predict(pcm_np).values()) >= cfg.oww_threshold` → detection

- [ ] **Step 2.1 — Remove pvporcupine import and KEYWORD_PATH from wake_converse.py**

Read `scripts/wake_converse.py`. Make these changes to the TOP of the file only (not the function body yet):

1. Remove: `import pvporcupine`
2. Remove: `import struct`  
3. Add after the other imports: `import numpy as np`
4. Remove: `KEYWORD_PATH = os.path.join(SCRIPT_DIR, "hey-bender.ppn")`
5. Add after the constants section: `OWW_FRAME_SIZE = 1280`

Leave `wait_for_wakeword()` body UNCHANGED for now — it will reference undefined names (`pvporcupine`, `porcupine`, `struct`) which is intentional for the red-test step.

- [ ] **Step 2.2 — Write the failing tests**

Create `tests/test_oww_detection.py`:

```python
"""Tests for openWakeWord-based wait_for_wakeword()."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import pytest
import numpy as np
from unittest.mock import MagicMock, patch


def _pcm_bytes(n_samples: int, channels: int = 1) -> bytes:
    return np.zeros(n_samples * channels, dtype=np.int16).tobytes()


def test_wait_for_wakeword_returns_on_high_score(monkeypatch):
    """Returns as soon as model score reaches threshold."""
    import audio
    from wake_converse import wait_for_wakeword, OWW_FRAME_SIZE

    call_num = [0]
    mock_stream = MagicMock()
    def fake_read(n, exception_on_overflow=False):
        call_num[0] += 1
        return _pcm_bytes(n)
    mock_stream.read = fake_read

    mock_pa = MagicMock()
    mock_pa.get_device_info_by_index.return_value = {'name': 'default_mic'}
    mock_pa.open.return_value = mock_stream

    # Low score for first 2 frames, threshold-crossing on 3rd
    predict_calls = [0]
    mock_model = MagicMock()
    def fake_predict(arr):
        predict_calls[0] += 1
        score = 0.9 if predict_calls[0] >= 3 else 0.1
        return {'hey_jarvis_v0.1': score}
    mock_model.predict = fake_predict

    monkeypatch.setattr(audio, 'get_pa', lambda: mock_pa)
    monkeypatch.setattr(audio, 'get_input_device_index', lambda: 0)
    monkeypatch.setattr('config.cfg.oww_threshold', 0.5)
    monkeypatch.setattr('config.cfg.wake_stall_seconds', 30)
    monkeypatch.setattr('config.cfg.wake_heartbeat_frames', 250)

    wait_for_wakeword(_oww_model=mock_model)

    assert predict_calls[0] >= 3
    assert call_num[0] >= 3


def test_wait_for_wakeword_keeps_looping_below_threshold(monkeypatch):
    """Does not return while all scores remain below threshold."""
    import audio
    from wake_converse import wait_for_wakeword

    frames_read = [0]
    mock_stream = MagicMock()
    def fake_read(n, exception_on_overflow=False):
        frames_read[0] += 1
        if frames_read[0] > 10:
            raise KeyboardInterrupt
        return _pcm_bytes(n)
    mock_stream.read = fake_read

    mock_pa = MagicMock()
    mock_pa.get_device_info_by_index.return_value = {'name': 'default_mic'}
    mock_pa.open.return_value = mock_stream

    mock_model = MagicMock()
    mock_model.predict.return_value = {'hey_jarvis_v0.1': 0.1}  # always below 0.5

    monkeypatch.setattr(audio, 'get_pa', lambda: mock_pa)
    monkeypatch.setattr(audio, 'get_input_device_index', lambda: 0)
    monkeypatch.setattr('config.cfg.oww_threshold', 0.5)
    monkeypatch.setattr('config.cfg.wake_stall_seconds', 30)
    monkeypatch.setattr('config.cfg.wake_heartbeat_frames', 250)

    with pytest.raises(KeyboardInterrupt):
        wait_for_wakeword(_oww_model=mock_model)

    assert frames_read[0] > 5


def test_wait_for_wakeword_stereo_downmix(monkeypatch):
    """Stereo stream (xvf_dsnoop device) is downmixed to mono before predict."""
    import audio
    from wake_converse import wait_for_wakeword, OWW_FRAME_SIZE

    captured_arrays = []
    mock_stream = MagicMock()
    call_count = [0]
    def fake_read(n, exception_on_overflow=False):
        call_count[0] += 1
        return _pcm_bytes(n, channels=2)  # stereo
    mock_stream.read = fake_read

    mock_pa = MagicMock()
    mock_pa.get_device_info_by_index.return_value = {'name': 'xvf_dsnoop_something'}
    mock_pa.open.return_value = mock_stream

    mock_model = MagicMock()
    def fake_predict(arr):
        captured_arrays.append(arr.copy())
        return {'hey_jarvis_v0.1': 0.9}  # trigger on first frame
    mock_model.predict = fake_predict

    monkeypatch.setattr(audio, 'get_pa', lambda: mock_pa)
    monkeypatch.setattr(audio, 'get_input_device_index', lambda: 0)
    monkeypatch.setattr('config.cfg.oww_threshold', 0.5)
    monkeypatch.setattr('config.cfg.wake_stall_seconds', 30)
    monkeypatch.setattr('config.cfg.wake_heartbeat_frames', 250)

    wait_for_wakeword(_oww_model=mock_model)

    assert len(captured_arrays) >= 1
    # Stereo 1280-frame read = 2560 bytes; after downmix → 1280 samples
    assert len(captured_arrays[0]) == OWW_FRAME_SIZE
```

- [ ] **Step 2.3 — Run to verify failure**

```bash
cd /home/pi/projects/benderpi
venv/bin/python -m pytest tests/test_oww_detection.py -v 2>&1 | head -30
```

Expected: `NameError: name 'pvporcupine' is not defined` (or similar — old function body uses removed names)

- [ ] **Step 2.4 — Replace wait_for_wakeword() body in wake_converse.py**

Find the entire `wait_for_wakeword()` function. Replace it with:

```python
def _load_oww_model(model_path: str):
    from openwakeword.model import Model
    return Model(wakeword_model_paths=[model_path])


def wait_for_wakeword(_oww_model=None):
    oww_model = _oww_model or _load_oww_model(
        os.path.join(BASE_DIR, cfg.oww_model_path)
    )
    pa = audio.get_pa()
    input_device_index = audio.get_input_device_index()
    device_name = (
        pa.get_device_info_by_index(input_device_index)["name"]
        if input_device_index is not None else ""
    )
    capture_channels = 2 if "xvf_dsnoop" in device_name else 1
    stream = pa.open(
        rate=16000,
        channels=capture_channels,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=OWW_FRAME_SIZE,
        input_device_index=input_device_index,
    )
    log.info("Listening for wake word... (mic: %s, ch=%d, model: %s, threshold: %.2f)",
             device_name or "default", capture_channels,
             os.path.basename(cfg.oww_model_path), cfg.oww_threshold)

    stall_s = float(cfg.wake_stall_seconds)
    hb_every = int(cfg.wake_heartbeat_frames)
    last_read_ts = time.monotonic()
    frames_since_hb = 0

    try:
        while True:
            pcm = stream.read(OWW_FRAME_SIZE, exception_on_overflow=False)
            now = time.monotonic()
            if not pcm or len(pcm) == 0:
                if now - last_read_ts > stall_s:
                    log.error("Wake loop stalled: %.1fs since last PCM frame", now - last_read_ts)
                    raise RuntimeError("wake loop stalled")
                continue
            last_read_ts = now
            frames_since_hb += 1
            if frames_since_hb >= hb_every:
                metrics.count("wake_loop_heartbeat")
                try:
                    from systemd import daemon as _sd_daemon
                    _sd_daemon.notify("WATCHDOG=1")
                except Exception:
                    pass
                frames_since_hb = 0

            pcm_np = np.frombuffer(pcm, dtype=np.int16)
            if capture_channels == 2:
                pcm_np = pcm_np[::2]  # left channel only (stereo downmix)
            prediction = oww_model.predict(pcm_np)
            if prediction and max(prediction.values()) >= cfg.oww_threshold:
                log.info("Wake word detected (score: %.3f)", max(prediction.values()))
                return
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
```

- [ ] **Step 2.5 — Run tests**

```bash
cd /home/pi/projects/benderpi
venv/bin/python -m pytest tests/test_oww_detection.py tests/test_oww_config.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 2.6 — Set up hey_jarvis model on this machine**

The default `oww_model_path` is `models/hey_jarvis.onnx` (relative to the project root). The `models/` directory is gitignored. Copy the bundled model there:

```bash
cp venv/lib/python3.13/site-packages/openwakeword/resources/models/hey_jarvis_v0.1.onnx models/hey_jarvis.onnx
ls -lh models/hey_jarvis.onnx
```

Expected: file ~1MB exists at `models/hey_jarvis.onnx`.

- [ ] **Step 2.7 — Commit**

```bash
git add scripts/wake_converse.py tests/test_oww_detection.py
git commit -m "feat(wake): replace Porcupine with openWakeWord (hey_jarvis interim model)

Switches wake word detection from pvporcupine to openwakeword 0.4.0.
Uses hey_jarvis pre-trained model as interim while custom 'hey bender'
model is trained. Frame size 512→1280, struct→numpy for PCM conversion.
Porcupine key and .ppn file no longer required."
```

---

## Task 3: Remove Porcupine + update docs

**Files:**
- Modify: `requirements.txt` — remove pvporcupine + pvrecorder
- Modify: `CLAUDE.md` — update env vars table and wake word section
- Test: verify no pvporcupine references remain in Python files

At this point pvporcupine is no longer imported anywhere in the codebase, so it's safe to remove from requirements.

- [ ] **Step 3.1 — Write the verification test**

Add to `tests/test_oww_config.py`:

```python
def test_no_pvporcupine_imports_in_source():
    """Ensure no Python source file in scripts/ imports pvporcupine."""
    import glob
    scripts = glob.glob('scripts/**/*.py', recursive=True)
    for path in scripts:
        with open(path) as f:
            content = f.read()
        assert 'pvporcupine' not in content, \
            f"Found pvporcupine import in {path}"
        assert 'PORCUPINE_ACCESS_KEY' not in content, \
            f"Found PORCUPINE_ACCESS_KEY in {path}"
```

- [ ] **Step 3.2 — Run to verify it currently passes**

```bash
cd /home/pi/projects/benderpi
venv/bin/python -m pytest tests/test_oww_config.py::test_no_pvporcupine_imports_in_source -v
```

Expected: PASS (wake_converse.py no longer imports pvporcupine after Task 2).

- [ ] **Step 3.3 — Remove pvporcupine + pvrecorder from requirements.txt**

Read `requirements.txt`. Remove the lines:
```
pvporcupine==4.0.2
pvrecorder==1.2.7
```

- [ ] **Step 3.4 — Update CLAUDE.md**

Read `CLAUDE.md`. Make these changes:

1. In the **Environment Variables** table, remove the `PORCUPINE_ACCESS_KEY` row entirely.

2. Find the wake word section and update `KEYWORD_PATH` references — remove them. Update the description to mention openWakeWord instead of Porcupine.

3. In the **Conversation Architecture** section, update the comment `STT (faster-whisper)` flow to note OWW replaces Porcupine.

4. In **Known Issues / Quirks**, add:
   ```
   - **openWakeWord model** — `models/hey_jarvis.onnx` is gitignored; copy from `venv/lib/python3.13/site-packages/openwakeword/resources/models/hey_jarvis_v0.1.onnx` after fresh venv setup
   - **hey_jarvis is interim** — not "hey bender"; custom model training is a future task using openWakeWord's synthetic TTS pipeline
   ```

- [ ] **Step 3.5 — Run full test suite**

```bash
cd /home/pi/projects/benderpi
venv/bin/python -m pytest tests/test_oww_config.py tests/test_oww_detection.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 3.6 — Commit**

```bash
git add requirements.txt CLAUDE.md tests/test_oww_config.py
git commit -m "chore: remove pvporcupine/pvrecorder from deps; update docs for OWW"
```

---

## On-Pi Verification

These steps run on BenderPi after `git pull` syncs the changes (auto-deploy runs every 5 min).

- [ ] **V1 — Install openwakeword on Pi (already done in compat test)**

```bash
ssh pi@192.168.68.132 "cd /home/pi/bender && venv/bin/pip install openwakeword 2>&1 | tail -5"
```

Expected: `Successfully installed ... openwakeword-0.4.0 ...` (or "already satisfied")

- [ ] **V2 — Copy model to models/**

```bash
ssh pi@192.168.68.132 "cp /home/pi/bender/venv/lib/python3.13/site-packages/openwakeword/resources/models/hey_jarvis_v0.1.onnx /home/pi/bender/models/hey_jarvis.onnx && ls -lh /home/pi/bender/models/hey_jarvis.onnx"
```

- [ ] **V3 — Restart service and test live detection**

```bash
ssh pi@192.168.68.132 "sudo systemctl restart bender-converse && sleep 3 && sudo journalctl -u bender-converse -n 30 --no-pager"
```

Expected in journal: `Listening for wake word... (mic: ..., ch=1, model: hey_jarvis.onnx, threshold: 0.50)`

Say "Hey Jarvis" (or any wake phrase — hey_jarvis responds to "Hey Jarvis"). Confirm detection fires.

- [ ] **V4 — Confirm no Porcupine key errors**

```bash
ssh pi@192.168.68.132 "sudo journalctl -u bender-converse -n 50 --no-pager | grep -i porcupine"
```

Expected: no output (no Porcupine references in logs).

---

## Self-Review

**Spec coverage:**
- ✅ Remove pvporcupine — Task 2 removes import, Task 3 removes from requirements
- ✅ openWakeWord ONNX detection loop — Task 2
- ✅ Frame size 1280, numpy PCM conversion — Task 2
- ✅ Config: oww_model_path + oww_threshold — Task 1
- ✅ .env.example: remove PORCUPINE_ACCESS_KEY — Task 1
- ✅ CLAUDE.md update — Task 3
- ✅ hey_jarvis as interim pre-trained model — Task 2 (model copy)
- ✅ Stall detection + heartbeat preserved — Task 2 (same logic, new frame size)
- ✅ Stereo downmix preserved for xvf_dsnoop devices — Task 2

**Note — custom "hey bender" model is NOT in this plan.** Training a custom model requires a separate process (generate synthetic audio via TTS, run training pipeline on a faster machine). That's out of scope for the deadline fix. The interim `hey_jarvis` model meets the June 30 deadline.
