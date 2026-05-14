"""Offline test that wait_for_wakeword raises on stall (zero-length reads)."""
import sys
import time
import types
import pytest

sys.path.insert(0, "scripts")


class StallingStream:
    def __init__(self):
        self.reads = 0

    def read(self, n, exception_on_overflow=False):
        self.reads += 1
        return b""  # always zero-length — simulates wedged USB

    def stop_stream(self):
        pass

    def close(self):
        pass


# Build a minimal fake for all modules wake_converse imports at load time
def _make_fake_module(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _patch_all_deps(monkeypatch):
    """Patch every heavy import wake_converse touches at module level."""
    fake_porcupine_obj = types.SimpleNamespace(
        sample_rate=16000,
        frame_length=512,
        process=lambda *a: -1,
        delete=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "pvporcupine",
        _make_fake_module("pvporcupine",
            create=lambda **kw: fake_porcupine_obj))

    fake_pa_instance = types.SimpleNamespace(
        open=lambda **kw: StallingStream(),
    )
    fake_pyaudio = _make_fake_module("pyaudio",
        paInt16=8,
        PyAudio=lambda: fake_pa_instance,
    )
    monkeypatch.setitem(sys.modules, "pyaudio", fake_pyaudio)

    for mod in ("leds", "stt", "tts_generate", "vision", "briefings",
                "ai_response", "ai_local", "conversation_log",
                "responder", "handlers.clip_handler", "handlers.timer_alert",
                "handler_base", "timers"):
        monkeypatch.setitem(sys.modules, mod,
            _make_fake_module(mod,
                AIResponder=lambda: None,
                LocalAIResponder=lambda: None,
                SessionLogger=lambda: None,
                Responder=lambda: None,
                RealClipHandler=lambda: None,
                TimerAlertRunner=lambda: None,
                load_clips_from_index=lambda *a: [],
                ResponseStream=object,
                get_logger=lambda n: __import__("logging").getLogger(n),
            ))

    # audio needs get_pa and get_input_device_index
    fake_audio = _make_fake_module("audio",
        get_pa=lambda: fake_pa_instance,
        get_input_device_index=lambda: None,
        open_session=lambda: None,
        close_session=lambda: None,
        play=lambda *a, **kw: None,
        abort=lambda: None,
        was_aborted=lambda: False,
    )
    monkeypatch.setitem(sys.modules, "audio", fake_audio)

    fake_logger = _make_fake_module("logger",
        get_logger=lambda n: __import__("logging").getLogger(n))
    monkeypatch.setitem(sys.modules, "logger", fake_logger)

    fake_metrics_obj = types.SimpleNamespace(
        count=lambda *a, **kw: None,
        _write=lambda *a, **kw: None,
    )
    fake_metrics = _make_fake_module("metrics", metrics=fake_metrics_obj)
    monkeypatch.setitem(sys.modules, "metrics", fake_metrics)

    fake_cfg = types.SimpleNamespace(
        wake_stall_seconds=0.2,
        wake_heartbeat_frames=1000,
        ai_backend="cloud_only",
        session_file="/tmp/.bender_test_session",
        end_session_file="/tmp/.bender_test_end",
        abort_file="/tmp/.bender_test_abort",
        dismissal_ends_session=True,
        silent_wakeword=False,
        led_listening_enabled=False,
        vlm_timeout=1.0,
        thinking_sound=False,
        simple_intent_max_words=6,
    )
    fake_config = _make_fake_module("config", cfg=fake_cfg)
    monkeypatch.setitem(sys.modules, "config", fake_config)

    monkeypatch.setitem(sys.modules, "dotenv",
        _make_fake_module("dotenv", dotenv_values=lambda *a, **kw: {}))

    return fake_audio, fake_cfg, fake_pa_instance


def test_wake_loop_raises_after_stall(monkeypatch):
    fake_audio, fake_cfg, fake_pa_instance = _patch_all_deps(monkeypatch)

    # Remove cached module if already imported in a previous test run
    sys.modules.pop("wake_converse", None)

    import wake_converse

    # Ensure the module-level cfg and audio references use our fakes
    monkeypatch.setattr(wake_converse, "cfg", fake_cfg, raising=False)
    monkeypatch.setattr(wake_converse, "audio", fake_audio, raising=False)
    monkeypatch.setattr(wake_converse, "metrics", fake_audio.__dict__.get("metrics",
        types.SimpleNamespace(count=lambda *a, **kw: None, _write=lambda *a, **kw: None)),
        raising=False)

    monkeypatch.setenv("PORCUPINE_ACCESS_KEY", "fake-key-for-testing")

    with pytest.raises(RuntimeError, match="stalled"):
        wake_converse.wait_for_wakeword()
