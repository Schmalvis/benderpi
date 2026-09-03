"""STT capture onset gate, speech-onset timeout, and flush control.

Batch 2 / commit 1 of docs/superpowers/plans/2026-09-03-batch2-session-quality.md.

Live evidence (2026-08-04): eight rejected 120ms captures in nine seconds. One
VAD-positive 30ms frame started a capture, 750ms of silence ended it, the 250ms
gate rejected it, and the loop re-entered — reopening the stream and flushing
210ms of whatever the user had started saying. A silent window ran the full
15s max_record_seconds, so idle tails were 15–30s and background chatter was
transcribed. These tests pin the three changes that fix that.
"""
import sys
import os
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import stt

VOICED = b"\x00\x10" * 240
SILENT = b"\x00\x00" * 240


class _Reader:
    """Scripted MicReader: hands out frames in order, then empty frames."""

    def __init__(self, frames, clock=None):
        self.supply = list(frames)
        self.reads = 0
        self.clock = clock

    def __call__(self, *a, **kw):  # MicReader(stream, frame_frames, timeout, name=...)
        return self

    def read(self, timeout):
        self.reads += 1
        if self.clock is not None:
            self.clock.advance(stt.FRAME_MS / 1000.0)
        return self.supply.pop(0) if self.supply else SILENT

    def stop(self):
        pass


class _Vad:
    """VAD that reports speech for VOICED frames only."""

    def __init__(self, *a):
        pass

    def is_speech(self, data, rate):
        return data == VOICED


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def advance(self, dt):
        self.t += dt

    def monotonic(self):
        return self.t


@pytest.fixture
def rig(monkeypatch):
    """Install fakes and return a function that runs one capture."""
    monkeypatch.setattr(stt.audio_mod, "get_pa", lambda: type(
        "PA", (), {"open": lambda self, **kw: type(
            "S", (), {"stop_stream": lambda s: None,
                      "close": lambda s: None})()})())
    monkeypatch.setattr(stt.audio_mod, "get_input_device_index", lambda: 2)
    monkeypatch.setattr(stt.webrtcvad, "Vad", _Vad)
    monkeypatch.setattr(stt.cfg, "post_play_flush_ms", 210, raising=False)
    monkeypatch.setattr(stt.cfg, "silence_frames", 5, raising=False)
    monkeypatch.setattr(stt.cfg, "max_record_seconds", 15, raising=False)
    monkeypatch.setattr(stt.cfg, "stt_onset_frames", 3, raising=False)
    monkeypatch.setattr(stt.cfg, "stt_speech_onset_timeout_s", 6.0, raising=False)

    def run(frames, flush=True, **cfg_over):
        for k, v in cfg_over.items():
            monkeypatch.setattr(stt.cfg, k, v, raising=False)
        clock = _Clock()
        reader = _Reader(frames, clock)
        monkeypatch.setattr(stt.audio_mod, "MicReader", reader)
        monkeypatch.setattr(stt.time, "monotonic", clock.monotonic)
        pcm, reason, cap = stt._record_utterance(flush=flush)
        return pcm, reason, cap, reader

    return run


class TestOnsetGate:
    def test_single_frame_transient_does_not_start_capture(self, rig):
        # one voiced frame then silence: the old code started here and ran
        # 750ms of silence before returning a 30ms "utterance".
        pcm, reason, cap, _ = rig([VOICED] + [SILENT] * 40, flush=False)
        assert reason == "no_speech"
        assert cap["voiced_ms"] == 0

    def test_two_frame_transient_does_not_start_capture(self, rig):
        pcm, reason, cap, _ = rig([VOICED, VOICED] + [SILENT] * 40, flush=False)
        assert reason == "no_speech"
        assert cap["voiced_ms"] == 0

    def test_three_consecutive_frames_start_and_all_count(self, rig):
        pcm, reason, cap, _ = rig([VOICED] * 3 + [SILENT] * 10, flush=False)
        assert reason == "silence"
        # the onset frames themselves are speech and count toward the gate
        assert cap["voiced_ms"] == 3 * stt.FRAME_MS

    def test_broken_run_resets_the_counter(self, rig):
        # V V s V V V: the first two do not carry over across the silent frame
        frames = [VOICED, VOICED, SILENT, VOICED, VOICED, VOICED] + [SILENT] * 10
        pcm, reason, cap, _ = rig(frames, flush=False)
        assert reason == "silence"
        assert cap["voiced_ms"] == 3 * stt.FRAME_MS

    def test_onset_frames_one_restores_old_behaviour(self, rig):
        pcm, reason, cap, _ = rig([VOICED] + [SILENT] * 10, flush=False,
                                  stt_onset_frames=1)
        assert reason == "silence"
        assert cap["voiced_ms"] == stt.FRAME_MS


class TestSpeechOnsetTimeout:
    def test_silence_ends_at_onset_timeout_not_record_cap(self, rig):
        pcm, reason, cap, reader = rig([], flush=False)  # endless silence
        assert reason == "no_speech"
        # ~6s of 30ms frames, not 15s
        assert reader.reads == pytest.approx(6.0 / (stt.FRAME_MS / 1000.0), abs=3)

    def test_speech_after_onset_timeout_is_missed_by_design(self, rig):
        # voice arriving at 7s never starts: the window gave up at 6s
        frames = [SILENT] * 240 + [VOICED] * 10
        pcm, reason, cap, _ = rig(frames, flush=False)
        assert reason == "no_speech"

    def test_zero_disables_onset_timeout(self, rig):
        pcm, reason, cap, reader = rig([], flush=False, stt_speech_onset_timeout_s=0)
        assert reason == "no_speech"
        assert reader.reads > 15.0 / (stt.FRAME_MS / 1000.0) - 3

    def test_onset_timeout_does_not_cut_speech_in_progress(self, rig):
        # speech starts at 5.5s and runs past 6s: capture continues to silence
        frames = [SILENT] * 183 + [VOICED] * 40 + [SILENT] * 10
        pcm, reason, cap, _ = rig(frames, flush=False)
        assert reason == "silence"
        assert cap["voiced_ms"] == 40 * stt.FRAME_MS


class TestFlushControl:
    def test_flush_true_discards_post_play_frames(self, rig):
        # 210ms flush = 7 frames of VOICED get eaten before VAD sees anything
        frames = [VOICED] * 7 + [SILENT] * 40
        pcm, reason, cap, reader = rig(frames, flush=True)
        assert reason == "no_speech"

    def test_flush_false_keeps_the_opening_words(self, rig):
        frames = [VOICED] * 7 + [SILENT] * 10
        pcm, reason, cap, _ = rig(frames, flush=False)
        assert reason == "silence"
        assert cap["voiced_ms"] == 7 * stt.FRAME_MS

    def test_listen_and_transcribe_forwards_after_playback(self, monkeypatch):
        seen = {}

        def fake_record(flush=True):
            seen["flush"] = flush
            return b"", "no_speech", {"voiced_ms": 0, "voiced_rms": 0}

        monkeypatch.setattr(stt, "_load_model", lambda: None)
        monkeypatch.setattr(stt, "_record_utterance", fake_record)
        monkeypatch.setattr(stt.metrics, "count", lambda *a, **k: None)
        stt.listen_and_transcribe(after_playback=False)
        assert seen["flush"] is False
        stt.listen_and_transcribe()
        assert seen["flush"] is True


class TestWakeLoopWiring:
    """The session loop in wake_converse.main() is not unit-testable (it owns
    hardware), so pin the two wiring facts by reading the source."""

    def _src(self):
        path = os.path.join(os.path.dirname(__file__), "..", "scripts", "wake_converse.py")
        with open(path) as f:
            return f.read()

    def test_idle_clock_restarts_after_handle_turn(self):
        src = self._src()
        turn = src.index("result = session.handle_turn(text)")
        stamp = src.index("last_heard = time.monotonic()", turn)
        assert stamp - turn < 400, "last_heard must be stamped right after handle_turn"
        assert "last_heard = time.time()" not in src

    def test_flush_only_after_playback(self):
        src = self._src()
        assert "listen_and_transcribe(after_playback=played_since_capture)" in src
        assert src.count("played_since_capture = True") == 2   # session start + after a turn
        assert "played_since_capture = False" in src
