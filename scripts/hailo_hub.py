"""Process-lifetime owner of the Hailo-10H VDevice and its resident models.

Background
----------
Until 2026-07-30 the conversation loop released and re-acquired the Hailo device
between every STT and LLM step: ``stt.release()`` after each transcription, then
``_HailoLLMResponder._load()`` re-loading the Qwen HEF, then ``release_chip()``
after inference, then Whisper re-loading on the next turn. That choreography
existed because we believed Whisper and Qwen could not be resident at the same
time.

They can. Hailo's own reference app (``hailo_apps/python/gen_ai_apps/
voice_assistant/voice_assistant.py``) builds **one** VDevice in the
``SHARED`` group and constructs both ``Speech2Text`` and ``LLM`` on it in
``__init__``, holding both for the lifetime of the process; its ``close()``
releases the LLM and leaves the VDevice to process cleanup ("it's shared").
An on-device spike reproduced this on BenderPi: Whisper-Small + Qwen2.5-1.5B
resident together, interleaved STT -> LLM -> STT inference stable, and STT
latency with the LLM resident identical to STT alone (0.75s, no eviction).

The real constraint is narrower than "one model at a time": the on-chip
**KV-Cache is a singleton**. Whisper does not use it; the LLM and the VLM each
need exclusive hold of it, so ``LLM`` and ``VLM`` are mutually exclusive while
loaded. The resource model is therefore::

    Whisper + (LLM XOR VLM), all resident, for the life of the process.

The reload tax this removes is measured, not estimated: ``ai_hailo_load``
median 8.4s on *every* AI turn, plus a ~2.5s Whisper reload per post-AI turn
that was previously invisible (it ran untimed inside ``listen_and_transcribe()``
before the mic even opened, so it also delayed the start of recording).

Rollback
--------
Everything here is gated on ``cfg.hailo_resident`` (default ``True``). Setting
it ``false`` in ``bender_config.json`` restores the previous per-turn
release/reload behaviour without a code change -- the legacy paths in
``stt.py`` and ``ai_local.py`` are still present and still tested. The one
property this design has not yet proven is multi-week stability of a
permanently-held VDevice, so the flag stays until a soak says otherwise.

Proof it is working
-------------------
``ai_hailo_load`` should appear **once per process** instead of once per AI
turn, and ``ai_hailo_stt_load`` once per process. Both are visible in
STATUS.md with no new instrumentation.

Thread safety
-------------
``stt.warm_up()`` runs on a startup thread while inference runs on session
threads, so construction is serialised by a module-level ``RLock``. The lock
guards *construction and teardown only* -- concurrent inference on the returned
handles is the caller's problem (``_HailoLLMResponder._infer_lock`` handles it
for the LLM). STT and LLM initialisation fail independently: a broken LLM must
never take Whisper down with it.
"""

import atexit
import os
import threading
import time

from config import cfg
from logger import get_logger
from metrics import metrics

log = get_logger("hailo_hub")

WHISPER_HEF = "/usr/local/hailo/resources/models/hailo10h/Whisper-Small.hef"
LLM_HEF     = "/usr/local/hailo/resources/models/hailo10h/Qwen2.5-1.5B-Instruct.hef"

# Seconds before retrying a model that failed to initialise. Matches the legacy
# _HAILO_RETRY_COOLDOWN in ai_local.py: without it, a genuinely dead accelerator
# would cost an init attempt (~8s) on every single turn.
_INIT_RETRY_COOLDOWN = 60.0

_lock = threading.RLock()

_vdevice = None
_s2t     = None
_llm     = None

_s2t_failed_at: float | None = None
_llm_failed_at: float | None = None

_closed          = False
_atexit_hooked   = False


def enabled() -> bool:
    """True when models should be held resident for the process lifetime."""
    return bool(getattr(cfg, "hailo_resident", True))


def _cooling(failed_at: float | None) -> bool:
    """True if a prior init failure is still within its retry cooldown."""
    if failed_at is None:
        return False
    return (time.monotonic() - failed_at) < _INIT_RETRY_COOLDOWN


def _shared_group_id() -> str:
    """The VDevice group id shared by every Hailo consumer in this process.

    Prefers the vendor constant so we track it if Hailo ever changes the value;
    falls back to the literal it has always been.
    """
    try:
        from hailo_apps.python.core.common.defines import SHARED_VDEVICE_GROUP_ID
        return SHARED_VDEVICE_GROUP_ID
    except Exception:
        return "SHARED"


def _get_vdevice():
    """Create (once) and return the shared VDevice. Caller must hold ``_lock``.

    Constructed without entering a context manager, matching Hailo's reference
    apps: they use the object directly and tear down via ``.release()``, never
    ``__enter__``/``__exit__``. That avoids the ``__exit__`` + ``__del__``
    double-release path blamed for the HAILO_INVALID_OPERATION(6) crash on
    2026-05-19.
    """
    global _vdevice
    if _vdevice is None:
        from hailo_platform import VDevice
        params = VDevice.create_params()
        params.group_id = _shared_group_id()
        _vdevice = VDevice(params)
        log.info("Hailo VDevice created (group_id=%s), resident for process lifetime",
                 params.group_id)
        _hook_atexit()
    return _vdevice


def _hook_atexit() -> None:
    """Register close() once, so the device is torn down even if the process
    owns no LocalAIResponder (e.g. ai_backend='cloud_only' with Hailo STT on).
    close() is idempotent, so an additional explicit call is harmless."""
    global _atexit_hooked
    if not _atexit_hooked:
        atexit.register(close)
        _atexit_hooked = True


def get_speech2text():
    """Return the resident Speech2Text handle, or None if unavailable.

    None means "use the CPU fallback" -- it is never an error to the caller.
    """
    global _s2t, _s2t_failed_at
    if not enabled():
        return None
    with _lock:
        if _closed:
            return None
        if _s2t is not None:
            return _s2t
        if _cooling(_s2t_failed_at):
            return None
        if not os.path.exists(WHISPER_HEF):
            log.warning("Whisper HEF not found: %s", WHISPER_HEF)
            _s2t_failed_at = time.monotonic()
            return None
        try:
            from hailo_platform.genai import Speech2Text
            # Timed so the Whisper load stops being invisible: previously this
            # ran untimed on every post-AI turn and silently delayed recording.
            with metrics.timer("ai_hailo_stt_load"):
                _s2t = Speech2Text(_get_vdevice(), WHISPER_HEF)
            _s2t_failed_at = None
            log.info("Hailo Speech2Text resident (Whisper-Small)")
            return _s2t
        except Exception as e:
            log.warning("Hailo Speech2Text init failed (%s) — CPU STT fallback", e)
            _s2t_failed_at = time.monotonic()
            metrics.count("hailo_init_failed", model="speech2text", error=str(e)[:120])
            return None


def get_llm():
    """Return the resident LLM handle, or None if unavailable.

    None means "fall back to Ollama/cloud" -- not an error to the caller.
    """
    global _llm, _llm_failed_at
    if not enabled():
        return None
    with _lock:
        if _closed:
            return None
        if _llm is not None:
            return _llm
        if _cooling(_llm_failed_at):
            return None
        if not os.path.exists(LLM_HEF):
            log.warning("Hailo LLM HEF not found: %s", LLM_HEF)
            _llm_failed_at = time.monotonic()
            return None
        try:
            from hailo_platform.genai import LLM
            # Same metric name as the legacy per-turn load on purpose: after
            # this lands it should fire ONCE per process instead of once per
            # AI turn. That delta is the proof the change worked, and it shows
            # up in STATUS.md with no new instrumentation.
            with metrics.timer("ai_hailo_load"):
                _llm = LLM(_get_vdevice(), LLM_HEF)
            _llm_failed_at = None
            log.info("Hailo LLM resident (Qwen2.5-1.5B-Instruct)")
            return _llm
        except Exception as e:
            log.warning("Hailo LLM init failed (%s) — Ollama/cloud fallback", e)
            _llm_failed_at = time.monotonic()
            metrics.count("hailo_init_failed", model="llm", error=str(e)[:120])
            return None


def warm_up(*, llm: bool = True) -> None:
    """Pre-load the resident models at startup. Never raises.

    Failures are already handled: each getter records its own retry cooldown and
    returns None, so a Hailo that is missing or broken at boot degrades to CPU
    STT and Ollama/cloud inference exactly as it would have on the first turn.
    Runs on a background thread so it cannot delay systemd READY=1.

    ``llm=False`` for cloud-only deployments, where loading Qwen would hold the
    KV-Cache for a model that is never asked to generate anything.
    """
    if not enabled():
        return
    get_speech2text()
    if llm:
        get_llm()


def status() -> dict:
    """Snapshot for diagnostics/health surfaces. Never raises."""
    with _lock:
        return {
            "enabled": enabled(),
            "closed": _closed,
            "vdevice": _vdevice is not None,
            "speech2text": _s2t is not None,
            "llm": _llm is not None,
        }


def close() -> None:
    """Release resident models and the VDevice. Idempotent; safe at exit.

    Teardown order mirrors the reference apps: models before the VDevice they
    were created on, each guarded independently so one failure cannot strand
    the rest. Callers that may be racing live inference (``ai_local.close()``)
    are responsible for their own in-flight guard before calling this.
    """
    global _vdevice, _s2t, _llm, _closed
    with _lock:
        if _closed:
            return
        _closed = True
        llm_ref, s2t_ref, vdev_ref = _llm, _s2t, _vdevice
        _llm = _s2t = _vdevice = None

    if llm_ref is not None:
        try:
            llm_ref.clear_context()
        except Exception as e:
            log.debug("LLM clear_context error at close: %s", e)
        try:
            llm_ref.release()
        except Exception as e:
            log.debug("LLM release error at close: %s", e)
    if s2t_ref is not None:
        try:
            s2t_ref.release()
        except Exception as e:
            log.debug("Speech2Text release error at close: %s", e)
    if vdev_ref is not None:
        try:
            vdev_ref.release()
        except Exception as e:
            log.debug("VDevice release error at close: %s", e)

    if any(r is not None for r in (llm_ref, s2t_ref, vdev_ref)):
        log.info("Hailo hub closed — models and VDevice released")


def _reset_for_tests() -> None:
    """Test-only: forget all state without touching hardware."""
    global _vdevice, _s2t, _llm, _s2t_failed_at, _llm_failed_at, _closed
    with _lock:
        _vdevice = _s2t = _llm = None
        _s2t_failed_at = _llm_failed_at = None
        _closed = False
