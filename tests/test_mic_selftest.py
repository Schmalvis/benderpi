"""Tests for audio.mic_selftest()'s verdict logic.

The startup self-test is the signal the 6-day silent-mic incident exists to
provide, so what it calls a failure matters. From 2026-07-30 it reported
"FAILED (read-rate too slow)" on every single service start — with frames=33
and healthy RMS, i.e. a perfectly working mic — because Hailo residency made
the concurrent warm-up load two HEFs (~12.7s) and starve the reading thread.

A check that cries wolf every boot trains people to ignore it. So: a slow wall
clock with every frame present and real signal is now an advisory (ok=True,
slow=True), not a failure. Genuine faults — no frames, all-zero frames, a
wedged read — are unchanged.
"""
import sys
import time
import types

import numpy as np
import pytest

sys.path.insert(0, "scripts")

import audio


def _frame(amplitude: int = 1500, n: int = 480) -> bytes:
    """One 30ms 16kHz mono frame of non-silent audio."""
    return (np.ones(n, dtype=np.int16) * amplitude).tobytes()


def _install_fake_mic(monkeypatch, *, frames, read_delay=0.0):
    """Point mic_selftest at a scripted reader instead of hardware."""
    fake_pa = types.SimpleNamespace(
        open=lambda **kw: types.SimpleNamespace(
            stop_stream=lambda: None, close=lambda: None),
        get_device_info_by_index=lambda i: {"name": "seeed-2mic-voicecard"},
    )
    monkeypatch.setattr(audio, "get_pa", lambda: fake_pa)
    monkeypatch.setattr(audio, "get_input_device_index", lambda: 2)

    supply = list(frames)

    class _FakeReader:
        def __init__(self, *a, **kw):
            pass

        def read(self, timeout):
            if read_delay:
                time.sleep(read_delay)
            return supply.pop(0) if supply else b""

        def stop(self):
            pass

    monkeypatch.setattr(audio, "MicReader", _FakeReader)


class TestHealthy:
    def test_good_mic_passes(self, monkeypatch):
        _install_fake_mic(monkeypatch, frames=[_frame()] * 2)
        r = audio.mic_selftest(duration_s=0.06)
        assert r["ok"] is True
        assert r["slow"] is False
        assert r["reason"] == "ok"
        assert r["frames"] == 2
        assert r["max_rms"] > 0


class TestSlowButWorking:
    def test_slow_reads_are_advisory_not_failure(self, monkeypatch):
        """The exact shape seen on-device: every frame arrived, signal was
        healthy, only the wall clock was bad."""
        _install_fake_mic(monkeypatch, frames=[_frame()] * 2, read_delay=0.12)
        r = audio.mic_selftest(duration_s=0.06)
        assert r["ok"] is True, "a working mic must not be reported as failed"
        assert r["slow"] is True
        assert "slow_reads" in r["reason"]
        assert r["frames"] == 2
        assert r["max_rms"] > 0

    def test_real_faults_win_over_slowness(self, monkeypatch):
        """Slow AND silent must report the silence — the actionable problem —
        rather than blaming host load."""
        silent = np.zeros(480, dtype=np.int16).tobytes()
        _install_fake_mic(monkeypatch, frames=[silent] * 2, read_delay=0.12)
        r = audio.mic_selftest(duration_s=0.06)
        assert r["ok"] is False
        assert "all-zero" in r["reason"]


class TestGenuineFailures:
    def test_no_frames_fails(self, monkeypatch):
        _install_fake_mic(monkeypatch, frames=[])
        r = audio.mic_selftest(duration_s=0.06)
        assert r["ok"] is False
        assert r["reason"] == "no frames read"
        assert r["frames"] == 0

    def test_all_zero_frames_fails(self, monkeypatch):
        silent = np.zeros(480, dtype=np.int16).tobytes()
        _install_fake_mic(monkeypatch, frames=[silent] * 2)
        r = audio.mic_selftest(duration_s=0.06)
        assert r["ok"] is False
        assert "all-zero" in r["reason"]

    def test_stalled_read_fails(self, monkeypatch):
        _install_fake_mic(monkeypatch, frames=[_frame()])

        class _Stalling:
            def __init__(self, *a, **kw):
                pass

            def read(self, timeout):
                raise audio.MicStallError("no frame in 10.0s")

            def stop(self):
                pass

        monkeypatch.setattr(audio, "MicReader", _Stalling)
        r = audio.mic_selftest(duration_s=0.06)
        assert r["ok"] is False
        assert "stalled" in r["reason"]

    def test_never_raises_on_open_failure(self, monkeypatch):
        """Startup must never be blocked by the self-test."""
        def _boom(**kw):
            raise OSError("device busy")

        monkeypatch.setattr(audio, "get_pa", lambda: types.SimpleNamespace(
            open=_boom, get_device_info_by_index=lambda i: {"name": "x"}))
        monkeypatch.setattr(audio, "get_input_device_index", lambda: 2)
        r = audio.mic_selftest(duration_s=0.06)
        assert r["ok"] is False
        assert "error" in r["reason"]


class TestStartupOrdering:
    def test_selftest_runs_before_model_warmup(self):
        """Ordering is the actual fix: measuring the mic while two HEFs load
        produced a false 'read-rate too slow' on every boot. Guard it in
        source order so a future edit doesn't silently undo it."""
        src = (__import__("pathlib").Path("scripts/wake_converse.py")).read_text()
        i_selftest = src.index("audio.mic_selftest()")
        i_warm = src.index("name=\"stt-warmup\"")
        assert i_selftest < i_warm, (
            "mic_selftest() must run before the model warm-up thread starts")
