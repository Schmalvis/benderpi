"""The live-conversation CPU STT fallback must use a small, fast model.

This path only runs when Hailo STT is already down, so the comparison is not
"tiny.en is less accurate than base.en" — it is "tiny.en works and base.en makes
the assistant look dead". Measured on the live 4GB Pi across ~4.5 months:

    base.en (mic)  n=825  median 27,297ms  p90 75,256ms  max 108,724ms
    tiny.en (mic)  n=156  median    845ms  p90  4,583ms
    base.en (file) n=12   median  2,429ms   <- the web-upload path, still fine

Two separate keys because those two rows want different answers. A 27s
transcription also breaks the session silence-timeout logic in wake_converse.py,
which anchors on when recording *started* precisely because this path can stall.
"""
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "scripts")

sys.modules.setdefault("pyaudio", types.SimpleNamespace(
    paInt16=8, PyAudio=lambda: types.SimpleNamespace()))
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

import stt
from config import Config, cfg


class TestDefaults:
    def test_whisper_fallback_default_is_small(self, tmp_path):
        c = Config(config_path=str(tmp_path / "nonexistent.json"))
        assert c.whisper_fallback_model == "tiny.en"

    def test_file_path_model_stays_accurate(self, tmp_path):
        """The web-upload path is not latency-critical (2.4s measured), so it
        keeps the more accurate model."""
        c = Config(config_path=str(tmp_path / "nonexistent.json"))
        assert c.whisper_model == "base.en"

    def test_keys_are_independent(self, tmp_path):
        import json
        p = tmp_path / "bender_config.json"
        p.write_text(json.dumps({"whisper_fallback_model": "base.en"}))
        c = Config(config_path=str(p))
        assert c.whisper_fallback_model == "base.en"
        assert c.whisper_model == "base.en"


class TestFallbackSelection:
    def test_cpu_fallback_uses_the_fallback_model(self, monkeypatch):
        monkeypatch.setattr(cfg, "whisper_model", "base.en", raising=False)
        monkeypatch.setattr(cfg, "whisper_fallback_model", "tiny.en", raising=False)
        assert stt._cpu_fallback_model_name() == "tiny.en"

    def test_falls_back_to_whisper_model_if_key_missing(self, monkeypatch):
        """An old bender_config.json predating the split must still boot."""
        monkeypatch.setattr(cfg, "whisper_model", "base.en", raising=False)
        monkeypatch.setattr(cfg, "whisper_fallback_model", None, raising=False)
        assert stt._cpu_fallback_model_name() == "base.en"

    def test_metric_reports_the_model_that_actually_ran(self, monkeypatch):
        """If the metric tag lied, CPU-fallback timings would be attributed to
        the wrong model — which is how this went unnoticed for months."""
        monkeypatch.setattr(cfg, "whisper_model", "base.en", raising=False)
        monkeypatch.setattr(cfg, "whisper_fallback_model", "tiny.en", raising=False)
        monkeypatch.setattr(stt, "_backend", "cpu")
        assert stt._active_model_name() == "tiny.en"

    def test_metric_reports_hailo_when_resident(self, monkeypatch):
        monkeypatch.setattr(stt, "_backend", "hailo")
        assert stt._active_model_name() == "whisper-small-hailo"


class TestLoadWiring:
    def test_load_model_constructs_the_fallback_model(self, monkeypatch):
        """End-to-end: the CPU branch must hand the small model to
        faster-whisper, not whisper_model."""
        monkeypatch.setattr(cfg, "whisper_model", "base.en", raising=False)
        monkeypatch.setattr(cfg, "whisper_fallback_model", "tiny.en", raising=False)
        monkeypatch.setattr(cfg, "hailo_stt_enabled", False, raising=False)
        monkeypatch.setattr(stt, "_backend", None)
        monkeypatch.setattr(stt, "_cpu_model", None)

        built = []
        fake = types.ModuleType("faster_whisper")
        fake.WhisperModel = lambda name, **kw: built.append(name) or MagicMock()
        monkeypatch.setitem(sys.modules, "faster_whisper", fake)

        stt._load_model()

        assert built == ["tiny.en"]
        assert stt._backend == "cpu"
