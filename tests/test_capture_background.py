"""Long-session background capture, and the false-wake rate it measures.

v0.3_r35 shipped claiming "0 false wakes per hour" from THREE MINUTES of
held-out conversation, then woke four times in a day during work calls. Three
minutes cannot measure a 0.4/hour rate. These tests pin the two things that
make the new measurement trustworthy: an interrupted chunk leaves nothing
behind, and the wake-event count uses the live settings and does not
double-count one noisy passage.
"""
import json
import os
import sys
import types
import wave
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.modules.setdefault("pyaudio", MagicMock())

import capture_background as cb


def _write_wav(path, seconds, amplitude=1000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(cb.RATE)
        n = int(cb.RATE * seconds)
        w.writeframes((np.ones(n) * amplitude).astype(np.int16).tobytes())


class _FakeScorer:
    """Scores a fixed sequence, so event counting can be checked exactly."""

    def __init__(self, scores, required=2, window=4, threshold=0.1):
        self.scores = np.array(scores)
        self.required, self.window, self.threshold = required, window, threshold
        self.name = "fake.onnx"

    scan = cb._Scorer.scan

    def _predict_stub(self):
        pass


class TestEventCounting:
    def _scan(self, scores, required=2, window=4):
        s = _FakeScorer(scores, required, window)
        # drive the real scan() with a stubbed per-frame model
        it = iter(scores)
        s.model = types.SimpleNamespace(predict=lambda frame: {"w": next(it, 0.0)})
        pcm = np.zeros(cb.FRAME * len(scores), dtype=np.int16)
        return cb._Scorer.scan(s, pcm)

    def test_a_single_hot_frame_is_not_a_wake(self):
        """The live loop needs a second frame over the line; so must this."""
        events, peak = self._scan([0.0, 0.9, 0.0, 0.0, 0.0, 0.0])
        assert events == []
        assert peak == pytest.approx(0.9)

    def test_two_frames_in_the_window_is_one_wake(self):
        events, _ = self._scan([0.0, 0.9, 0.5, 0.0, 0.0, 0.0])
        assert len(events) == 1
        assert events[0]["score"] == pytest.approx(0.9)

    def test_one_noisy_passage_is_not_counted_many_times(self):
        """A continuously hot minute must not read as dozens of false wakes --
        that would make any per-hour figure meaningless."""
        events, _ = self._scan([0.9] * 40)
        assert len(events) == 10        # 40 frames / 4-frame window

    def test_the_timestamp_locates_the_wake_in_the_chunk(self):
        """at_s marks the START of the window that triggered, not the hottest
        frame — so seeking there in the WAV lands just before the sound."""
        events, _ = self._scan([0.0] * 20 + [0.9, 0.9] + [0.0] * 10)
        assert events
        # hot frames at 20 and 21; the first 4-frame window holding both starts at 18
        assert events[0]["at_s"] == pytest.approx(18 * cb.FRAME / cb.RATE, abs=0.05)

    def test_stricter_smoothing_suppresses_a_short_burst(self):
        assert self._scan([0.0, 0.9, 0.9, 0.0], required=2, window=4)[0]
        assert self._scan([0.0, 0.9, 0.9, 0.0], required=4, window=6)[0] == []


class TestChunkSafety:
    def test_an_interrupted_chunk_leaves_no_file(self, tmp_path, monkeypatch):
        """A partial file with a full-length header silently overstates how
        much audio exists. That bug already cost a conversation set."""
        target = tmp_path / "000.wav"

        def boom(cmd, check=True):
            _write_wav(str(target) + ".part", 12)
            raise KeyboardInterrupt

        monkeypatch.setattr(cb.subprocess, "run", boom)
        assert cb._record(300, str(target)) is False
        assert not target.exists()
        assert not (tmp_path / "000.wav.part").exists()

    def test_a_completed_chunk_is_renamed_into_place(self, tmp_path, monkeypatch):
        target = tmp_path / "000.wav"
        monkeypatch.setattr(cb.subprocess, "run",
                            lambda cmd, check=True: _write_wav(str(target) + ".part", 2))
        assert cb._record(2, str(target)) is True
        assert target.exists() and not (tmp_path / "000.wav.part").exists()

    def test_a_failed_arecord_is_not_treated_as_success(self, tmp_path, monkeypatch):
        def fail(cmd, check=True):
            raise cb.subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(cb.subprocess, "run", fail)
        assert cb._record(2, str(tmp_path / "000.wav")) is False


class TestScoreOnly:
    def test_it_reports_a_rate_over_the_whole_recording(self, tmp_path, monkeypatch,
                                                        capsys):
        out = tmp_path / "background" / "work_calls"
        _write_wav(str(out / "000.wav"), 60)
        _write_wav(str(out / "001.wav"), 60)
        monkeypatch.setattr(cb, "OUT_ROOT", str(tmp_path / "background"))

        class _S:
            name, threshold, required, window = "m.onnx", 0.1, 2, 4

            def scan(self, pcm):
                return [{"at_s": 1.0, "score": 0.8}], 0.8

        monkeypatch.setattr(cb, "_Scorer", lambda path: _S())
        monkeypatch.setattr(sys, "argv",
                            ["capture_background.py", "--label", "work_calls",
                             "--score-only"])
        cb.main()
        text = capsys.readouterr().out
        assert "2 false wakes over 2.0 minutes" in text
        assert "60.00 per hour" in text
        rows = [json.loads(l) for l in
                open(out / "triggers.jsonl") if l.strip()]
        assert len(rows) == 2 and rows[0]["file"] == "000.wav"

    def test_nothing_recorded_is_an_error_not_a_zero_rate(self, tmp_path, monkeypatch):
        """Reporting "0 per hour" for an empty directory is how a false claim
        gets made."""
        out = tmp_path / "background" / "empty"
        out.mkdir(parents=True)
        monkeypatch.setattr(cb, "OUT_ROOT", str(tmp_path / "background"))
        monkeypatch.setattr(cb, "_Scorer", lambda path: MagicMock())
        monkeypatch.setattr(sys, "argv",
                            ["capture_background.py", "--label", "empty",
                             "--score-only"])
        with pytest.raises(SystemExit, match="nothing recorded"):
            cb.main()
