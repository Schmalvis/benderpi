"""Tests for hailo_hub — the process-lifetime owner of the Hailo VDevice.

The point of the hub is that Whisper and Qwen stay resident instead of being
released and reloaded around every STT/LLM step, so these tests lock down the
properties that make that safe:

  * models load once and are handed back from cache on subsequent calls
  * a failed init does not retry on every turn (cooldown), and does not take
    the *other* model down with it
  * teardown releases models before the VDevice they were created on, once
  * the whole thing is inert when cfg.hailo_resident is false

No Hailo hardware or SDK is required: hailo_hub imports hailo_platform lazily
inside its getters, so fake modules injected into sys.modules are enough.
"""
import sys
import types

import pytest

sys.path.insert(0, "scripts")

import hailo_hub
from config import cfg


# ---------------------------------------------------------------------------
# Fake Hailo SDK
# ---------------------------------------------------------------------------

def _install_fake_sdk(monkeypatch, order, *, s2t_exc=None, llm_exc=None):
    """Inject a fake hailo_platform whose objects append to ``order`` on
    release, so teardown ordering is directly assertable."""

    class FakeVDevice:
        created = 0

        def __init__(self, params):
            FakeVDevice.created += 1
            self.params = params

        @staticmethod
        def create_params():
            return types.SimpleNamespace(group_id=None)

        def release(self):
            order.append("vdevice.release")

    class FakeSpeech2Text:
        def __init__(self, vdevice, hef):
            if s2t_exc:
                raise s2t_exc
            self.vdevice, self.hef = vdevice, hef

        def release(self):
            order.append("s2t.release")

    class FakeLLM:
        def __init__(self, vdevice, hef):
            if llm_exc:
                raise llm_exc
            self.vdevice, self.hef = vdevice, hef

        def clear_context(self):
            order.append("llm.clear_context")

        def release(self):
            order.append("llm.release")

    platform = types.ModuleType("hailo_platform")
    platform.VDevice = FakeVDevice
    genai = types.ModuleType("hailo_platform.genai")
    genai.Speech2Text = FakeSpeech2Text
    genai.LLM = FakeLLM
    platform.genai = genai

    monkeypatch.setitem(sys.modules, "hailo_platform", platform)
    monkeypatch.setitem(sys.modules, "hailo_platform.genai", genai)
    # HEFs do not exist on a dev box; the hub checks before loading.
    monkeypatch.setattr(hailo_hub.os.path, "exists", lambda p: True)
    return FakeVDevice


@pytest.fixture(autouse=True)
def _clean_hub(monkeypatch):
    """Every test starts from a hub that has never touched hardware."""
    hailo_hub._reset_for_tests()
    monkeypatch.setattr(cfg, "hailo_resident", True, raising=False)
    yield
    hailo_hub._reset_for_tests()


# ---------------------------------------------------------------------------
# Residency
# ---------------------------------------------------------------------------

class TestResidency:
    def test_models_share_one_vdevice(self, monkeypatch):
        order = []
        FakeVDevice = _install_fake_sdk(monkeypatch, order)

        s2t = hailo_hub.get_speech2text()
        llm = hailo_hub.get_llm()

        assert s2t is not None and llm is not None
        assert FakeVDevice.created == 1, "both models must share one VDevice"
        assert s2t.vdevice is llm.vdevice

    def test_repeat_calls_return_cached_handles(self, monkeypatch):
        """The whole win: the second call is a lookup, not an 8.4s HEF reload."""
        order = []
        FakeVDevice = _install_fake_sdk(monkeypatch, order)
        FakeVDevice.created = 0

        first_llm = hailo_hub.get_llm()
        for _ in range(5):
            assert hailo_hub.get_llm() is first_llm
        for _ in range(5):
            assert hailo_hub.get_speech2text() is hailo_hub.get_speech2text()

        assert FakeVDevice.created == 1

    def test_group_id_falls_back_to_shared(self, monkeypatch):
        """hailo_apps is absent off-device; the literal must still be used."""
        order = []
        _install_fake_sdk(monkeypatch, order)
        monkeypatch.setitem(sys.modules, "hailo_apps", None)

        llm = hailo_hub.get_llm()
        assert llm.vdevice.params.group_id == "SHARED"


# ---------------------------------------------------------------------------
# Failure isolation and cooldown
# ---------------------------------------------------------------------------

class TestFailureHandling:
    def test_llm_failure_does_not_break_stt(self, monkeypatch):
        """A dead LLM must degrade to Ollama/cloud, not to CPU STT as well."""
        order = []
        _install_fake_sdk(monkeypatch, order, llm_exc=RuntimeError("KV-Cache busy"))

        assert hailo_hub.get_llm() is None
        assert hailo_hub.get_speech2text() is not None

    def test_stt_failure_does_not_break_llm(self, monkeypatch):
        order = []
        _install_fake_sdk(monkeypatch, order, s2t_exc=RuntimeError("no hef"))

        assert hailo_hub.get_speech2text() is None
        assert hailo_hub.get_llm() is not None

    def test_failed_init_does_not_retry_every_call(self, monkeypatch):
        """Without the cooldown a broken accelerator costs a failed ~8s init on
        every turn instead of once a minute."""
        order = []
        attempts = []

        class Exploding:
            def __init__(self, vdevice, hef):
                attempts.append(1)
                raise RuntimeError("boom")

        _install_fake_sdk(monkeypatch, order)
        sys.modules["hailo_platform.genai"].LLM = Exploding

        for _ in range(5):
            assert hailo_hub.get_llm() is None
        assert len(attempts) == 1, "cooldown should suppress repeat init attempts"

    def test_retry_after_cooldown_expires(self, monkeypatch):
        order = []
        attempts = []

        class Exploding:
            def __init__(self, vdevice, hef):
                attempts.append(1)
                raise RuntimeError("boom")

        _install_fake_sdk(monkeypatch, order)
        sys.modules["hailo_platform.genai"].LLM = Exploding

        assert hailo_hub.get_llm() is None
        # Pretend the cooldown elapsed.
        hailo_hub._llm_failed_at -= (hailo_hub._INIT_RETRY_COOLDOWN + 1)
        assert hailo_hub.get_llm() is None
        assert len(attempts) == 2

    def test_missing_hef_returns_none(self, monkeypatch):
        order = []
        _install_fake_sdk(monkeypatch, order)
        monkeypatch.setattr(hailo_hub.os.path, "exists", lambda p: False)

        assert hailo_hub.get_llm() is None
        assert hailo_hub.get_speech2text() is None


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

class TestClose:
    def test_releases_models_before_vdevice(self, monkeypatch):
        order = []
        _install_fake_sdk(monkeypatch, order)
        hailo_hub.get_speech2text()
        hailo_hub.get_llm()

        hailo_hub.close()

        assert order.index("llm.release") < order.index("vdevice.release")
        assert order.index("s2t.release") < order.index("vdevice.release")
        assert "llm.clear_context" in order

    def test_close_is_idempotent(self, monkeypatch):
        """close() is reachable from atexit twice (hub hook + ai_local.close)."""
        order = []
        _install_fake_sdk(monkeypatch, order)
        hailo_hub.get_llm()

        hailo_hub.close()
        hailo_hub.close()
        hailo_hub.close()

        assert order.count("vdevice.release") == 1
        assert order.count("llm.release") == 1

    def test_getters_return_none_after_close(self, monkeypatch):
        order = []
        _install_fake_sdk(monkeypatch, order)
        hailo_hub.get_llm()
        hailo_hub.close()

        assert hailo_hub.get_llm() is None
        assert hailo_hub.get_speech2text() is None

    def test_close_survives_release_errors(self, monkeypatch):
        """One object failing to release must not strand the others."""
        order = []
        _install_fake_sdk(monkeypatch, order)
        llm = hailo_hub.get_llm()
        hailo_hub.get_speech2text()

        def boom():
            raise RuntimeError("release failed")

        llm.release = boom

        hailo_hub.close()  # must not raise
        assert "vdevice.release" in order
        assert "s2t.release" in order


# ---------------------------------------------------------------------------
# Disabled mode
# ---------------------------------------------------------------------------

class TestDisabled:
    def test_getters_inert_when_disabled(self, monkeypatch):
        order = []
        FakeVDevice = _install_fake_sdk(monkeypatch, order)
        FakeVDevice.created = 0
        monkeypatch.setattr(cfg, "hailo_resident", False, raising=False)

        assert hailo_hub.enabled() is False
        assert hailo_hub.get_llm() is None
        assert hailo_hub.get_speech2text() is None
        assert FakeVDevice.created == 0, "disabled hub must not touch hardware"

    def test_warm_up_inert_when_disabled(self, monkeypatch):
        order = []
        FakeVDevice = _install_fake_sdk(monkeypatch, order)
        FakeVDevice.created = 0
        monkeypatch.setattr(cfg, "hailo_resident", False, raising=False)

        hailo_hub.warm_up()
        assert FakeVDevice.created == 0


class TestWarmUp:
    def test_warm_up_loads_both(self, monkeypatch):
        order = []
        _install_fake_sdk(monkeypatch, order)
        hailo_hub.warm_up()
        assert hailo_hub.status()["llm"] is True
        assert hailo_hub.status()["speech2text"] is True

    def test_warm_up_can_skip_llm(self, monkeypatch):
        """cloud_only must not hold the KV-Cache for a model it never uses."""
        order = []
        _install_fake_sdk(monkeypatch, order)
        hailo_hub.warm_up(llm=False)
        assert hailo_hub.status()["speech2text"] is True
        assert hailo_hub.status()["llm"] is False
