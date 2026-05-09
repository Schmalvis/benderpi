# BenderPi Diagnosis Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three live bugs that left bender-converse dead for 3.5 weeks: Ollama cold-start timeouts, Hailo LLM KV-Cache not being released on exit, and noisy camera-conflict error logs.

**Architecture:** Three independent fixes to `scripts/ai_local.py`, `scripts/vision.py`, and `bender_config.json`. A fourth step wires a SIGTERM/atexit handler into `scripts/wake_converse.py` to ensure Hailo resources are released cleanly on shutdown. Each task produces a commit; all changes reach the Pi via the existing auto-deploy git pull timer.

**Tech Stack:** Python 3.13, Hailo HailoRT GenAI SDK (`hailo_platform`), Ollama REST API, Picamera2, systemd

---

## Diagnosis recap (for context)

| # | Issue | Root cause |
|---|-------|------------|
| 1 | Every UNKNOWN intent escalates to cloud | `local_llm_timeout` is 3s in repo (10s on Pi). Ollama cold-start takes ~3.8s to load the runner; first request always times out. |
| 2 | Hailo LLM permanently unavailable within a session | `_HailoLLMResponder._load()` caches `_available = False` forever on first failure. If the KV-Cache is occupied at startup (leftover from a previous SIGTERM without cleanup), Hailo never retries. |
| 3 | Hailo KV-Cache stays locked across restarts | `_HailoLLMResponder` holds `_vdevice` and `_llm` but has no `close()`. When systemd sends SIGTERM, Python may not release these before the process dies, leaving the hardware KV-Cache locked for the next run. |
| 4 | Camera-busy errors fill the log with tracebacks | `vision.py:analyse_scene()` uses `log.exception()` for all failures. Cross-process camera contention (bender-web holding the device during MJPEG streaming) is a normal operating condition, not an error, but it generates a full stack trace at ERROR level every time. |

**bender-converse has been stopped since 14 April.** Task 4 ends with a restart step.

---

## File map

| File | Change |
|------|--------|
| `bender_config.json` | `local_llm_timeout`: 3 → 25 |
| `scripts/ai_local.py` | Add `warm_up()`, `close()`, and retry cooldown to Hailo init |
| `scripts/vision.py` | Distinguish camera-busy from real errors; log busy at WARNING |
| `scripts/wake_converse.py` | SIGTERM/atexit handler to call `ai_local.close()` |

---

## Task 1: Fix Ollama timeout + add model warm-up

**Files:**
- Modify: `bender_config.json`
- Modify: `scripts/ai_local.py` (add `warm_up()` to `_OllamaResponder` and `LocalAIResponder`)

**Context:** `local_llm_timeout` is currently 3 in the repo. On the Pi it was 10 (someone set it via web UI). Either way it's too low — Ollama logs show the runner taking 3.80s just to start, then the model needs to generate. The warm-up fires a minimal request at service startup so the model is already loaded in memory when the first real request arrives.

- [ ] **Step 1: Update timeout in config**

In `bender_config.json`, change line `"local_llm_timeout": 3` to:

```json
"local_llm_timeout": 25,
```

- [ ] **Step 2: Write the failing test for warm_up**

Create `tests/test_ai_local.py` (or open the existing one) and add:

```python
"""Tests for ai_local module."""
import sys
import types
import time
from unittest.mock import patch, MagicMock

# Stub out hardware deps so ai_local imports cleanly off-device
sys.modules.setdefault("hailo_platform", types.ModuleType("hailo_platform"))
sys.modules.setdefault("hailo_platform.genai", types.ModuleType("hailo_platform.genai"))
sys.modules.setdefault("hailo_apps", types.ModuleType("hailo_apps"))
sys.modules.setdefault("hailo_apps.python", types.ModuleType("hailo_apps.python"))
sys.modules.setdefault("hailo_apps.python.core", types.ModuleType("hailo_apps.python.core"))
sys.modules.setdefault("hailo_apps.python.core.common", types.ModuleType("hailo_apps.python.core.common"))
defines_stub = types.ModuleType("hailo_apps.python.core.common.defines")
defines_stub.SHARED_VDEVICE_GROUP_ID = 0
sys.modules["hailo_apps.python.core.common.defines"] = defines_stub

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test_warm_up_fires_chat_request():
    """warm_up() should POST to /api/chat with num_predict=1."""
    from ai_local import LocalAIResponder
    responder = LocalAIResponder()
    with patch("ai_local.requests.post") as mock_post:
        mock_post.return_value.raise_for_status = MagicMock()
        responder.warm_up()
    mock_post.assert_called_once()
    call_json = mock_post.call_args.kwargs["json"]
    assert call_json["options"]["num_predict"] == 1


def test_warm_up_swallows_exception():
    """warm_up() must not raise even if Ollama is down."""
    from ai_local import LocalAIResponder
    responder = LocalAIResponder()
    with patch("ai_local.requests.post", side_effect=Exception("connection refused")):
        responder.warm_up()  # should not raise
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /home/pi/projects/benderpi
venv/bin/python -m pytest tests/test_ai_local.py::test_warm_up_fires_chat_request tests/test_ai_local.py::test_warm_up_swallows_exception -v
```

Expected: `AttributeError: 'LocalAIResponder' object has no attribute 'warm_up'`

- [ ] **Step 4: Implement `warm_up()` in `scripts/ai_local.py`**

Add to `_OllamaResponder` class (after `clear_history`, before the class ends):

```python
def warm_up(self) -> None:
    """Pre-load the Ollama model so first real request doesn't cold-start."""
    try:
        requests.post(
            f"{cfg.local_llm_url}/api/chat",
            json={
                "model": cfg.local_llm_model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "options": {"num_predict": 1},
            },
            timeout=30,
        )
        log.info("Ollama model pre-loaded (%s)", cfg.local_llm_model)
    except Exception as e:
        log.warning("Ollama warm-up failed (non-fatal): %s", e)
```

Add to `LocalAIResponder` class (after `clear_history`):

```python
def warm_up(self) -> None:
    """Pre-load Ollama model in background at startup."""
    self._ollama.warm_up()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
venv/bin/python -m pytest tests/test_ai_local.py::test_warm_up_fires_chat_request tests/test_ai_local.py::test_warm_up_swallows_exception -v
```

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add bender_config.json scripts/ai_local.py tests/test_ai_local.py
git commit -m "fix: raise Ollama timeout to 25s, add model warm-up on startup"
```

---

## Task 2: Fix Hailo LLM — retry cooldown + clean shutdown

**Files:**
- Modify: `scripts/ai_local.py` (retry cooldown in `_load()`, `close()` on `_HailoLLMResponder` and `LocalAIResponder`)
- Modify: `scripts/wake_converse.py` (SIGTERM/atexit handler)

**Context:** `_HailoLLMResponder._load()` permanently caches `_available = False` on first failure. If the Hailo KV-Cache was locked by a previous run (from an unclean shutdown), Hailo LLM is dead for the life of the next process — it never retries. Fix: allow retry after 60s cooldown. Also add `close()` so systemd's SIGTERM triggers proper Hailo device release, breaking the cycle.

- [ ] **Step 1: Write failing tests for retry cooldown and close()**

Add to `tests/test_ai_local.py`:

```python
def test_hailo_load_retries_after_cooldown():
    """_HailoLLMResponder should retry init after _HAILO_RETRY_COOLDOWN seconds."""
    from ai_local import _HailoLLMResponder, _HAILO_RETRY_COOLDOWN
    h = _HailoLLMResponder()
    # Simulate a prior failure
    h._available = False
    h._last_failed_at = 0.0  # far in the past
    # Should reset _available to None and attempt _load again
    with patch("os.path.exists", return_value=False):
        result = h._load()
    # HEF not found → still False, but it retried (no permanent block)
    assert result is False
    assert h._last_failed_at is not None
    assert h._last_failed_at > 0.0  # timestamp was refreshed


def test_hailo_load_skips_retry_during_cooldown():
    """_HailoLLMResponder should not retry within the cooldown window."""
    from ai_local import _HailoLLMResponder
    import time
    h = _HailoLLMResponder()
    h._available = False
    h._last_failed_at = time.monotonic()  # just now
    with patch("os.path.exists", return_value=True):
        result = h._load()
    assert result is False  # didn't retry


def test_local_ai_responder_has_close():
    """LocalAIResponder must expose a close() method."""
    from ai_local import LocalAIResponder
    r = LocalAIResponder()
    assert callable(getattr(r, "close", None))
    r.close()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
venv/bin/python -m pytest tests/test_ai_local.py::test_hailo_load_retries_after_cooldown tests/test_ai_local.py::test_hailo_load_skips_retry_during_cooldown tests/test_ai_local.py::test_local_ai_responder_has_close -v
```

Expected: FAIL — `_HAILO_RETRY_COOLDOWN` not defined, `close()` not defined.

- [ ] **Step 3: Implement retry cooldown and close() in `scripts/ai_local.py`**

At the top of the file, after the existing imports, add:

```python
import time
```

After `_HAILO_HEF = ...`, add:

```python
_HAILO_RETRY_COOLDOWN = 60  # seconds before retrying after init failure
```

Replace the entire `_HailoLLMResponder.__init__` with:

```python
def __init__(self):
    self._vdevice = None
    self._llm = None
    self._available = None  # None = not yet attempted
    self._last_failed_at: float | None = None
    self.history: list[dict] = []
    self._scene_context: str = ""
```

Replace the entire `_HailoLLMResponder._load` method with:

```python
def _load(self) -> bool:
    if self._available is True:
        return True
    if self._available is False:
        elapsed = time.monotonic() - (self._last_failed_at or 0.0)
        if elapsed < _HAILO_RETRY_COOLDOWN:
            return False
        log.info("Hailo LLM init cooldown elapsed — retrying")
        self._available = None

    if not os.path.exists(_HAILO_HEF):
        log.warning("Hailo LLM HEF not found: %s", _HAILO_HEF)
        self._available = False
        self._last_failed_at = time.monotonic()
        return False
    try:
        from hailo_platform import VDevice
        from hailo_platform.genai import LLM
        from hailo_apps.python.core.common.defines import SHARED_VDEVICE_GROUP_ID
        params = VDevice.create_params()
        params.group_id = SHARED_VDEVICE_GROUP_ID
        self._vdevice = VDevice(params)
        self._llm = LLM(self._vdevice, _HAILO_HEF)
        self._available = True
        log.info("Hailo LLM ready: Qwen2.5-1.5B on Hailo-10H")
    except Exception as e:
        log.warning("Hailo LLM init failed (%s) — will use Ollama fallback", e)
        self._available = False
        self._last_failed_at = time.monotonic()
    return self._available
```

Add `close()` method to `_HailoLLMResponder` (after `clear_history`):

```python
def close(self) -> None:
    """Release Hailo VDevice and LLM, freeing the on-chip KV-Cache."""
    if self._llm is not None:
        try:
            self._llm.clear_context()
        except Exception:
            pass
        self._llm = None
    if self._vdevice is not None:
        try:
            del self._vdevice
        except Exception:
            pass
        self._vdevice = None
    self._available = None
    self._last_failed_at = None
    log.info("Hailo LLM closed and KV-Cache released")
```

Add `close()` to `LocalAIResponder` (after `clear_history`):

```python
def close(self) -> None:
    """Release all hardware resources. Call on shutdown."""
    self._hailo.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
venv/bin/python -m pytest tests/test_ai_local.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Wire SIGTERM/atexit handler in `scripts/wake_converse.py`**

Add to the import section of `wake_converse.py` (near the other stdlib imports):

```python
import atexit
import signal
```

Find where `LocalAIResponder` is instantiated (search for `LocalAIResponder()` in `wake_converse.py`). Directly after that instantiation, add:

```python
atexit.register(ai_local_responder.close)
signal.signal(signal.SIGTERM, lambda *_: (ai_local_responder.close(), sys.exit(0)))
```

(Replace `ai_local_responder` with whatever variable name the file uses for the `LocalAIResponder` instance.)

- [ ] **Step 6: Commit**

```bash
git add scripts/ai_local.py scripts/wake_converse.py tests/test_ai_local.py
git commit -m "fix: Hailo LLM retry cooldown after KV-Cache failure; release on SIGTERM"
```

---

## Task 3: Reduce camera-conflict log noise

**Files:**
- Modify: `scripts/vision.py`

**Context:** `analyse_scene()` calls `log.exception()` for all failures, which dumps a full stack trace at ERROR level. When bender-web holds the camera during an MJPEG stream, the OS raises `RuntimeError: Failed to acquire camera: Device or resource busy`. This is a normal cross-process contention condition — it's handled gracefully (bender-converse proceeds without vision context) — but it floods the log with frightening tracebacks. Fix: detect the "busy" case and log it at WARNING with a one-liner.

- [ ] **Step 1: Write the failing test**

Create `tests/test_vision.py`:

```python
"""Tests for vision.py — scene analysis."""
import sys, os, types
from unittest.mock import patch, MagicMock

# Stub hardware deps
for mod in ["picamera2", "cv2"]:
    sys.modules.setdefault(mod, types.ModuleType(mod))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def _make_busy_error():
    return RuntimeError("Failed to acquire camera: Device or resource busy")


def test_camera_busy_logs_warning_not_error(caplog):
    """Camera-busy RuntimeError should log at WARNING, not ERROR with traceback."""
    import logging
    import vision
    with patch("vision.camera.acquire_camera", side_effect=_make_busy_error()):
        with caplog.at_level(logging.DEBUG, logger="bender.vision"):
            result = vision.analyse_scene()

    assert result.is_empty()
    warning_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
    error_msgs   = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("busy" in m.message.lower() for m in warning_msgs), "Expected a WARNING about camera being busy"
    assert len(error_msgs) == 0, "Should not log at ERROR for camera-busy condition"


def test_real_error_still_logs_exception(caplog):
    """Non-busy errors should still be logged at ERROR with traceback."""
    import logging
    import vision
    with patch("vision.camera.acquire_camera", side_effect=RuntimeError("unexpected sensor failure")):
        with caplog.at_level(logging.DEBUG, logger="bender.vision"):
            result = vision.analyse_scene()

    assert result.is_empty()
    error_msgs = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_msgs) > 0, "Unexpected errors should still be logged at ERROR"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
venv/bin/python -m pytest tests/test_vision.py -v
```

Expected: `test_camera_busy_logs_warning_not_error` FAILS — currently `log.exception()` fires for both cases.

- [ ] **Step 3: Fix `scripts/vision.py`**

Replace the `analyse_scene` function:

```python
def analyse_scene() -> SceneDescription:
    """Capture a frame and describe it via VLM. Returns SceneDescription."""
    try:
        camera.acquire_camera()
        frame = camera.capture_frame()
        description = vlm.describe_scene(frame, prompt=cfg.vlm_prompt)
        return SceneDescription(description=description, captured_at=datetime.now())
    except RuntimeError as e:
        if "busy" in str(e).lower():
            log.warning("Camera busy (held by another process) — vision skipped this session")
        else:
            log.exception("analyse_scene failed (capture)")
        return SceneDescription()
    except Exception:
        log.exception("analyse_scene failed")
        return SceneDescription()
    finally:
        camera.release_camera()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
venv/bin/python -m pytest tests/test_vision.py -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/vision.py tests/test_vision.py
git commit -m "fix: log camera-busy as WARNING not ERROR in analyse_scene"
```

---

## Task 4: Call Ollama warm-up at startup + restart service

**Files:**
- Modify: `scripts/wake_converse.py` (call `warm_up()` in background thread)

**Context:** The warm-up method was added in Task 1 but not called yet. It needs to fire at service startup in a background thread so it doesn't block the wake-word listener. After this, restart the service on the Pi.

- [ ] **Step 1: Find where LocalAIResponder is instantiated**

```bash
grep -n "LocalAIResponder" /home/pi/projects/benderpi/scripts/wake_converse.py
```

Note the variable name and line number.

- [ ] **Step 2: Add background warm-up call**

Directly after the `LocalAIResponder()` instantiation (and the `atexit`/SIGTERM lines from Task 2), add:

```python
threading.Thread(target=ai_local_responder.warm_up, daemon=True, name="ollama-warmup").start()
```

(Replace `ai_local_responder` with the actual variable name.)

- [ ] **Step 3: Commit**

```bash
git add scripts/wake_converse.py
git commit -m "feat: warm up Ollama model in background at service startup"
```

- [ ] **Step 4: Push and wait for auto-deploy**

```bash
git push origin main
```

The bender-git-pull timer fires every 5 minutes. Check deploy:

```bash
ssh pi@192.168.68.132 'sudo journalctl -u bender-git-pull.service -n 10 --no-pager'
```

Expected: recent `git pull` with the new commits.

- [ ] **Step 5: Restart bender-converse on Pi**

```bash
ssh pi@192.168.68.132 'sudo systemctl start bender-converse'
```

- [ ] **Step 6: Verify clean startup**

```bash
ssh pi@192.168.68.132 'sudo journalctl -u bender-converse -n 50 --no-pager'
```

Expected log lines:
- `Ollama model pre-loaded (qwen2.5:1.5b)` — warm-up succeeded
- No `Hailo LLM init failed` errors (or if present, retry will kick in after 60s)
- `Listening for 'Hey Bender'...` — service is live

- [ ] **Step 7: Trigger a test conversation and confirm Ollama responds**

Say "Hey Bender" then ask an open question. Check logs for:

```bash
ssh pi@192.168.68.132 'sudo journalctl -u bender-converse -f'
```

Expected: `ai_local` log shows Ollama responding within timeout; no `escalating to cloud` on the first turn.

---

## Self-review

**Spec coverage:**
- ✅ Ollama timeout raised (Task 1, config)
- ✅ Ollama cold-start solved (Task 1 warm-up + Task 4 startup call)
- ✅ Hailo KV-Cache permanent-failure caching removed (Task 2 retry cooldown)
- ✅ Hailo resources released on SIGTERM (Task 2 close + signal handler)
- ✅ Camera-busy log noise fixed (Task 3)
- ✅ Service restarted and verified (Task 4)

**Placeholder scan:** None found.

**Type consistency:**
- `warm_up()` added to `_OllamaResponder` → delegated through `LocalAIResponder.warm_up()` → called in `wake_converse.py`. Consistent.
- `close()` added to `_HailoLLMResponder` → delegated through `LocalAIResponder.close()` → registered via `atexit` and SIGTERM. Consistent.
- `_HAILO_RETRY_COOLDOWN` defined at module level → referenced in `_load()`. Consistent.
