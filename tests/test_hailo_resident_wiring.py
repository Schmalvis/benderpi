"""Tests that stt.py and ai_local.py actually stop releasing the Hailo device
when cfg.hailo_resident is on — and still release it when it is off.

The hub itself is covered by test_hailo_hub.py. What is easy to get wrong is the
wiring: a stray stt.release() or release_chip() that still frees the device
silently reintroduces the ~8.4s LLM reload and ~2.5s Whisper reload this change
exists to remove, while every test still passes and the logs look normal. So
these assert the no-ops directly, in both modes.
"""
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "scripts")

# stt.py -> audio.py instantiates pyaudio at import; ai_local -> ai_response
# imports anthropic. Neither exists on a dev box. Same stubs as the existing
# test_stt_pure.py / test_ai_local.py.
sys.modules.setdefault("pyaudio", types.SimpleNamespace(
    paInt16=8, PyAudio=lambda: types.SimpleNamespace()))
sys.modules.setdefault("anthropic", MagicMock())
# Only stub webrtcvad when it genuinely isn't installed. sys.modules.setdefault
# checks whether the key is already *imported*, not whether the real package
# exists -- so if this module imported first it silently handed a fake VAD to
# every later test in the process (which is exactly what happened to
# test_capture_wake_samples.py, whose trimming needs a real one).
try:  # pragma: no cover - depends on the environment, not the code
    import webrtcvad  # noqa: F401
except ImportError:
    sys.modules.setdefault("webrtcvad", types.SimpleNamespace(
        Vad=lambda *a, **k: types.SimpleNamespace(is_speech=lambda *a: False)))

import ai_local
import hailo_hub
import stt
from config import cfg


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    hailo_hub._reset_for_tests()
    stt._backend = None
    stt._s2t = None
    stt._vdevice = None
    yield
    hailo_hub._reset_for_tests()
    stt._backend = None
    stt._s2t = None
    stt._vdevice = None


class _Sentinel:
    """Stands in for a Speech2Text handle; records if anyone released it."""

    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


# ---------------------------------------------------------------------------
# stt.release()
# ---------------------------------------------------------------------------

class TestSttRelease:
    def test_resident_release_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
        handle = _Sentinel()
        # State as _load_model() leaves it in resident mode: we hold a borrowed
        # handle and explicitly do NOT own a VDevice.
        stt._backend, stt._s2t, stt._vdevice = "hailo", handle, None

        stt.release()

        assert handle.released is False, "resident mode must not free the hub's model"
        assert stt._backend == "hailo", "backend must stay loaded for the next turn"
        assert stt._s2t is handle

    def test_legacy_release_still_frees_the_device(self, monkeypatch):
        monkeypatch.setattr(cfg, "hailo_resident", False, raising=False)
        handle, vdev = _Sentinel(), _Sentinel()
        stt._backend, stt._s2t, stt._vdevice = "hailo", handle, vdev
        monkeypatch.setattr(stt, "_RELEASE_SETTLE_S", 0)

        stt.release()

        assert handle.released is True
        assert vdev.released is True
        assert stt._backend is None, "legacy mode must force a reload next turn"

    def test_cpu_backend_release_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
        stt._backend = "cpu"
        stt.release()
        assert stt._backend == "cpu"


class TestSttRecovery:
    """Resident mode stops release() resetting _backend every turn, so a
    transient Hailo failure must not strand the process on CPU faster-whisper
    (measured median ~19.8s/utterance) until a restart."""

    def test_cpu_backend_upgrades_back_to_hailo(self, monkeypatch):
        monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
        monkeypatch.setattr(cfg, "hailo_stt_enabled", True, raising=False)
        handle = _Sentinel()
        monkeypatch.setattr(hailo_hub, "get_speech2text", lambda: handle)
        stt._backend = "cpu"

        stt._load_model()

        assert stt._backend == "hailo"
        assert stt._s2t is handle
        assert stt._vdevice is None

    def test_stays_on_cpu_while_hailo_still_down(self, monkeypatch):
        monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
        monkeypatch.setattr(cfg, "hailo_stt_enabled", True, raising=False)
        monkeypatch.setattr(hailo_hub, "get_speech2text", lambda: None)
        stt._backend = "cpu"

        stt._load_model()

        assert stt._backend == "cpu"

    def test_resident_hailo_backend_is_not_reloaded(self, monkeypatch):
        """The steady-state path must stay a pure early return."""
        monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
        calls = []
        monkeypatch.setattr(hailo_hub, "get_speech2text",
                            lambda: calls.append(1) or _Sentinel())
        handle = _Sentinel()
        stt._backend, stt._s2t, stt._vdevice = "hailo", handle, None

        stt._load_model()

        assert calls == [], "loaded backend must not re-query the hub"
        assert stt._s2t is handle


# ---------------------------------------------------------------------------
# _HailoLLMResponder
# ---------------------------------------------------------------------------

class TestHailoResponderWiring:
    def test_load_borrows_handle_from_hub(self, monkeypatch):
        monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
        fake_llm = object()
        monkeypatch.setattr(hailo_hub, "get_llm", lambda: fake_llm)

        r = ai_local._HailoLLMResponder()
        assert r._load() is True
        assert r._llm is fake_llm
        assert r._vdevice is None, "the hub owns the VDevice, not the responder"

    def test_load_fails_cleanly_when_hub_has_no_llm(self, monkeypatch):
        monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
        monkeypatch.setattr(hailo_hub, "get_llm", lambda: None)

        r = ai_local._HailoLLMResponder()
        assert r._load() is False
        assert r._available is False

    def test_release_chip_is_a_noop_in_resident_mode(self, monkeypatch):
        monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
        fake_llm = _Sentinel()

        r = ai_local._HailoLLMResponder()
        r._llm = fake_llm
        r._available = True

        r.release_chip(warm=False)   # session end() path
        r.release_chip(warm=True)    # per-turn path

        assert fake_llm.released is False
        assert r._llm is fake_llm
        assert r._available is True, "the model must stay loaded across turns"

    def test_release_chip_still_releases_in_legacy_mode(self, monkeypatch):
        monkeypatch.setattr(cfg, "hailo_resident", False, raising=False)
        llm, vdev = MagicMock(), MagicMock()

        r = ai_local._HailoLLMResponder()
        r._llm, r._vdevice, r._available = llm, vdev, True

        r.release_chip(warm=False)

        llm.release.assert_called_once()
        vdev.release.assert_called_once()
        assert r._llm is None

    def test_close_delegates_to_hub_in_resident_mode(self, monkeypatch):
        monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
        calls = []
        monkeypatch.setattr(hailo_hub, "close", lambda: calls.append("close"))

        r = ai_local._HailoLLMResponder()
        r._llm = _Sentinel()
        r.close()

        assert calls == ["close"]
        assert r._llm is None
        # The guard must be released, or a second close() would deadlock/skip.
        assert r._infer_lock.acquire(blocking=False) is True
        r._infer_lock.release()

    def test_close_skips_hub_while_inference_in_flight(self, monkeypatch):
        """Never release the device out from under a live generate call."""
        monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
        calls = []
        monkeypatch.setattr(hailo_hub, "close", lambda: calls.append("close"))

        r = ai_local._HailoLLMResponder()
        r._infer_lock.acquire()  # simulate a zombie mid-generate
        try:
            r.close()
        finally:
            r._infer_lock.release()

        assert calls == [], "hub must not be closed under active inference"


class TestResetHailo:
    def test_reset_is_a_noop_in_resident_mode(self, monkeypatch):
        """Clearing init state per turn would defeat the hub's retry cooldown."""
        monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
        responder = ai_local.LocalAIResponder()
        responder._hailo._available = False
        responder._hailo._last_failed_at = 123.0

        responder.reset_hailo()

        assert responder._hailo._available is False
        assert responder._hailo._last_failed_at == 123.0

    def test_reset_still_clears_state_in_legacy_mode(self, monkeypatch):
        monkeypatch.setattr(cfg, "hailo_resident", False, raising=False)
        responder = ai_local.LocalAIResponder()
        responder._hailo._available = False
        responder._hailo._last_failed_at = 123.0

        responder.reset_hailo()

        assert responder._hailo._available is None
        assert responder._hailo._last_failed_at is None
