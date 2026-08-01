"""Tests for the capture-level STT gates in listen_and_transcribe().

Why these exist at all: `_transcribe_cpu`'s own docstring notes that its
per-segment confidence gates are "the ONLY defence on the Hailo path, which
returns text with no confidence signals". The device runs Hailo STT, so on the
live box everything below the phrase blocklist was ungated — and on 2026-08-01
session 5cfc5cd8 answered two captures of room noise ("So, So", "I'm going to
do the") as though they were questions.

These gates work on the *audio*, before transcription, so they apply equally to
both backends.

The balance being struck: over-gating (a Bender that ignores you) is worse than
the occasional reply to a noise, so "stop"/"bye" surviving is asserted here
explicitly.
"""
import sys, os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import stt


@pytest.fixture
def capture(monkeypatch):
    """Drive listen_and_transcribe with a scripted capture, no hardware."""
    calls = {"transcribed": False, "metrics": []}

    monkeypatch.setattr(stt, "_load_model", lambda: None)
    monkeypatch.setattr(stt.metrics, "count",
                        lambda name, **kw: calls["metrics"].append((name, kw)))

    def _transcribe(_arr):
        calls["transcribed"] = True
        return "transcribed text"
    monkeypatch.setattr(stt, "_transcribe_array", _transcribe)
    monkeypatch.setattr(stt, "_filter_hallucination", lambda t, source="": t)

    def _install(reason, voiced_ms, voiced_rms=4000, seconds=3.0):
        pcm = b"\x01\x00" * int(stt.SAMPLE_RATE * seconds)
        monkeypatch.setattr(stt, "_record_utterance", lambda: (
            pcm, reason, {"voiced_ms": voiced_ms, "voiced_rms": voiced_rms}))
        return calls

    _install.calls = calls
    return _install


def _gates(calls):
    return [kw.get("gate") for name, kw in calls["metrics"] if name == "stt_rejected"]


class TestSilenceOnlyCapture:
    def test_no_speech_capture_is_not_transcribed(self, capture):
        """The pre-existing defect: a silence-only capture runs the full
        max_record_seconds, so the length check passes and Whisper is asked to
        transcribe pure silence — the classic hallucination generator."""
        calls = capture("no_speech", voiced_ms=0)
        assert stt.listen_and_transcribe() == ""
        assert calls["transcribed"] is False
        assert "no_speech" in _gates(calls)


class TestMinSpeechMs:
    def test_transient_is_rejected(self, capture, monkeypatch):
        monkeypatch.setattr(stt.cfg, "stt_min_speech_ms", 250, raising=False)
        calls = capture("silence", voiced_ms=90)
        assert stt.listen_and_transcribe() == ""
        assert calls["transcribed"] is False
        assert "min_speech_ms" in _gates(calls)

    def test_short_command_survives(self, capture, monkeypatch):
        """'stop' and 'bye' end sessions; gating them out would strand the user
        in a conversation they can't leave."""
        monkeypatch.setattr(stt.cfg, "stt_min_speech_ms", 250, raising=False)
        calls = capture("silence", voiced_ms=390)
        assert stt.listen_and_transcribe() == "transcribed text"
        assert calls["transcribed"] is True

    def test_gate_disabled_when_zero(self, capture, monkeypatch):
        monkeypatch.setattr(stt.cfg, "stt_min_speech_ms", 0, raising=False)
        calls = capture("silence", voiced_ms=30)
        assert stt.listen_and_transcribe() == "transcribed text"
        assert _gates(calls) == []


class TestMinSpeechRms:
    def test_disabled_by_default(self, capture, monkeypatch):
        """Ships off: the right floor is room-specific and guessing it deaf-ens
        the device for quiet speakers."""
        monkeypatch.setattr(stt.cfg, "stt_min_speech_ms", 250, raising=False)
        monkeypatch.setattr(stt.cfg, "stt_min_speech_rms", 0, raising=False)
        calls = capture("silence", voiced_ms=900, voiced_rms=50)
        assert stt.listen_and_transcribe() == "transcribed text"

    def test_rejects_quiet_capture_when_enabled(self, capture, monkeypatch):
        monkeypatch.setattr(stt.cfg, "stt_min_speech_ms", 250, raising=False)
        monkeypatch.setattr(stt.cfg, "stt_min_speech_rms", 500, raising=False)
        calls = capture("silence", voiced_ms=900, voiced_rms=120)
        assert stt.listen_and_transcribe() == ""
        assert "min_speech_rms" in _gates(calls)


class TestInstrumentation:
    def test_every_capture_is_measured(self, capture, monkeypatch):
        """These two numbers are the only evidence available for tuning the
        gates on the Hailo path, so they must be emitted even when nothing is
        rejected — otherwise the only data is about captures we already threw
        away."""
        monkeypatch.setattr(stt.cfg, "stt_min_speech_ms", 250, raising=False)
        calls = capture("silence", voiced_ms=900, voiced_rms=4200)
        stt.listen_and_transcribe()
        cap = [kw for name, kw in calls["metrics"] if name == "stt_capture"]
        assert len(cap) == 1
        assert cap[0]["voiced_ms"] == 900
        assert cap[0]["voiced_rms"] == 4200

    def test_rejected_captures_are_measured_too(self, capture, monkeypatch):
        monkeypatch.setattr(stt.cfg, "stt_min_speech_ms", 250, raising=False)
        calls = capture("silence", voiced_ms=60)
        stt.listen_and_transcribe()
        assert any(name == "stt_capture" for name, _ in calls["metrics"])


class TestRecordUtteranceStats:
    def test_returns_voiced_stats(self, monkeypatch):
        """Only VAD-positive frames count toward the stats — otherwise trailing
        silence would inflate voiced_ms and defeat the gate."""
        import numpy as np

        frames = [b"\x00\x10" * 240] * 4 + [b"\x00\x00" * 240] * 30
        supply = list(frames)

        class _Reader:
            def __init__(self, *a, **kw): pass
            def read(self, timeout): return supply.pop(0) if supply else b""
            def stop(self): pass

        class _Vad:
            def __init__(self, *a): self.n = 0
            def is_speech(self, data, rate):
                self.n += 1
                return self.n <= 4

        monkeypatch.setattr(stt.audio_mod, "MicReader", _Reader)
        monkeypatch.setattr(stt.audio_mod, "get_pa", lambda: type(
            "PA", (), {"open": lambda self, **kw: type(
                "S", (), {"stop_stream": lambda s: None,
                          "close": lambda s: None})()})())
        monkeypatch.setattr(stt.audio_mod, "get_input_device_index", lambda: 2)
        monkeypatch.setattr(stt.webrtcvad, "Vad", _Vad)
        monkeypatch.setattr(stt.cfg, "post_play_flush_ms", 0, raising=False)
        monkeypatch.setattr(stt.cfg, "silence_frames", 5, raising=False)
        monkeypatch.setattr(stt.cfg, "max_record_seconds", 30, raising=False)

        pcm, reason, cap = stt._record_utterance()
        assert reason == "silence"
        assert cap["voiced_ms"] == 4 * stt.FRAME_MS
        assert cap["voiced_rms"] > 0
