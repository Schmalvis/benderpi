"""Held-out evaluation of a wake-word model.

This is the script that decides whether a retrain shipped or not, so its
arithmetic has to be right when nobody is checking it by hand. The cases
below are the ones that would quietly produce a wrong verdict: peak-score
recall instead of the live N-of-M gate, a noisy minute counted as thirty
false wakes, gates read in the wrong direction, and a corrupt mic stream
mistaken for a model that does not generalise.

Plan: docs/superpowers/plans/2026-09-24-wake-word-retrain-v0.2.md
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

import eval_wake_model as ev


@pytest.fixture(autouse=True)
def smoothing(monkeypatch):
    """The live loop's gate: 2 of any 4 consecutive frames."""
    monkeypatch.setattr(ev.cfg, "oww_frames_required", 2, raising=False)
    monkeypatch.setattr(ev.cfg, "oww_window", 4, raising=False)


class TestWouldFire:
    def test_one_hot_frame_is_not_a_wake(self):
        """v0.1's peak score flatters it: the loop needs a SECOND frame over
        the line, and that one sits materially lower."""
        assert ev.would_fire(np.array([0.0, 0.9, 0.0, 0.0, 0.0]), 0.35) is False

    def test_two_frames_inside_the_window_fire(self):
        assert ev.would_fire(np.array([0.0, 0.9, 0.5, 0.0, 0.0]), 0.35) is True

    def test_two_frames_outside_the_window_do_not(self):
        assert ev.would_fire(np.array([0.9, 0.0, 0.0, 0.0, 0.0, 0.9]), 0.35) is False

    def test_threshold_is_inclusive(self):
        assert ev.would_fire(np.array([0.35, 0.35]), 0.35) is True

    def test_empty_scores_never_fire(self):
        assert ev.would_fire(np.zeros(0), 0.1) is False

    def test_a_clip_shorter_than_the_window_is_still_judged(self):
        """range(len - window + 1) was empty for a 3-frame array, so short
        clips could never fire and scored as silent misses."""
        assert ev.would_fire(np.array([0.9, 0.9, 0.0]), 0.35) is True
        assert ev.would_fire(np.array([0.9, 0.0, 0.0]), 0.35) is False
        assert ev.count_fires(np.array([0.9, 0.9]), 0.35) == 1


class TestCountFires:
    def test_a_long_hot_run_is_one_event_not_thirty(self):
        """A noisy minute must not report as dozens of false wakes; a real
        session starts once and then returns to listening."""
        assert ev.count_fires(np.ones(40), 0.35) == 10  # 40 frames / 4-frame window

    def test_separate_bursts_count_separately(self):
        s = np.zeros(40)
        s[2:4] = 0.9
        s[30:32] = 0.9
        assert ev.count_fires(s, 0.35) == 2

    def test_quiet_audio_counts_nothing(self):
        assert ev.count_fires(np.full(100, 0.002), 0.1) == 0


class TestGates:
    def _result(self, normal=(11, 12), other=(11, 14), hard=(1, 28), amb_fires=0):
        """normal/other are (fired, n) for the ordinary-speech conditions and
        for everything else; recall is their sum."""
        def clips(fired, n, group):
            return [{"path": f"{group}_{i}", "group": group, "peak": 0.9,
                     "fires": {0.35: i < fired}} for i in range(n)]
        pos = clips(*normal, "mid_normal") + clips(*other, "mid_slow")
        return {
            "model": "cand.onnx",
            "positive": pos,
            "hard_negative": clips(hard[0], hard[1], "hey_vendor"),
            "watch": [],
            "ambient": {"seconds": 300.0, "peak": 0.01, "fires": {0.35: amb_fires}},
            "conversation": {"seconds": 180.0, "peak": 0.01, "fires": {0.35: 0}},
            "synthetic": 0.97,
        }

    def test_a_passing_candidate_ships(self):
        g = ev.gate_results(self._result())
        assert g["ship"] is True
        assert all(g["passed"].values())

    def test_low_recall_blocks_the_ship(self):
        g = ev.gate_results(self._result(normal=(5, 12), other=(5, 14)))
        assert g["passed"]["recall"] is False
        assert g["ship"] is False

    def test_good_overall_recall_cannot_hide_bad_normal_speech(self):
        """v0.1's failure shape exactly: 0/60 on ordinary speech while scoring
        0.868 on a drawn-out 'heeey benderrr'."""
        # 22/26 overall = 85% (passes), but 2/6 ordinary speech = 33% (fails)
        g = ev.gate_results(self._result(normal=(2, 6), other=(20, 20)))
        assert g["passed"]["recall"] is True
        assert g["passed"]["recall_normal"] is False
        assert g["ship"] is False

    def test_false_wakes_block_the_ship(self):
        g = ev.gate_results(self._result(hard=(9, 28)))
        assert g["passed"]["hard_negative_rate"] is False

    def test_a_single_ambient_false_wake_blocks_the_ship(self):
        g = ev.gate_results(self._result(amb_fires=1))
        assert g["passed"]["ambient_per_hour"] is False

    def test_rates_are_computed_per_hour_not_per_file(self):
        r = self._result(amb_fires=1)
        g = ev.gate_results(r)
        assert abs(g["values"]["ambient_per_hour"] - 12.0) < 0.01  # 1 in 300s

    def test_gate_thresholds_match_the_plan(self):
        assert ev.SHIP_GATES == {"recall": 0.80, "recall_normal": 0.75,
                                 "hard_negative_rate": 0.10, "ambient_per_hour": 0.0}
        assert ev.SHIP_THRESHOLD == 0.35


class TestScoringClips:
    @pytest.fixture
    def rig(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ev, "BASE_DIR", str(tmp_path))

        def wav(rel, seconds=2.0):
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(ev.RATE)
                w.writeframes(np.zeros(int(ev.RATE * seconds), dtype=np.int16).tobytes())
            return rel

        return tmp_path, wav

    def test_group_comes_from_the_filename(self):
        assert ev._group("data/wake_samples/positive/martin/mid_normal_007.wav") \
            == "mid_normal"

    def test_clip_rows_carry_group_peak_and_decisions(self, rig):
        tmp, wav = rig
        rels = [wav("p/mid_normal_001.wav"), wav("p/mid_slow_002.wav")]
        scorer = types.SimpleNamespace(
            frame_scores=lambda pcm: np.array([0.9, 0.9, 0.0]))
        rows = ev._clip_results(scorer, rels, (0.35, 0.95))
        assert [r["group"] for r in rows] == ["mid_normal", "mid_slow"]
        assert rows[0]["peak"] == pytest.approx(0.9)
        assert rows[0]["fires"][0.35] is True
        assert rows[0]["fires"][0.95] is False

    def test_a_missing_clip_is_skipped_not_scored_as_a_miss(self, rig):
        """Counting an absent file as a miss would understate recall and send
        a good model back for another $2 run."""
        tmp, wav = rig
        rows = ev._clip_results(types.SimpleNamespace(frame_scores=lambda p: np.ones(4)),
                                [wav("p/mid_normal_001.wav"), "p/gone_002.wav"], (0.35,))
        assert len(rows) == 1

    def test_continuous_audio_reports_real_duration(self, rig):
        tmp, wav = rig
        rels = [wav("a/000.wav", seconds=60), wav("a/001.wav", seconds=37.6)]
        out = ev._continuous_results(
            types.SimpleNamespace(frame_scores=lambda p: np.zeros(10)), rels, (0.35,))
        assert out["seconds"] == pytest.approx(97.6, abs=0.1)
        assert out["fires"][0.35] == 0


class TestMicGuard:
    def test_corrupt_stream_stops_the_run(self, monkeypatch, tmp_path):
        """A corrupt XVF3800 feed scores ~0.001 on everything and is
        indistinguishable from a model that does not generalise."""
        monkeypatch.setattr(ev, "SPLIT_PATH", str(tmp_path / "split.json"))
        (tmp_path / "split.json").write_text(json.dumps(
            {"positive": {"holdout": []}, "hard_negative": {"holdout": [], "watch": []},
             "ambient": {"holdout": []}, "conversation": {"holdout": []}}))
        monkeypatch.setattr(ev, "_mic_zero_fraction", lambda *a, **k: 0.92)
        monkeypatch.setattr(ev.cfg, "mic_zero_frac_max", 0.5, raising=False)
        monkeypatch.setattr(sys, "argv", ["eval_wake_model.py"])
        with pytest.raises(SystemExit) as exc:
            ev.main()
        assert exc.value.code == 2

    def test_missing_split_is_refused(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ev, "SPLIT_PATH", str(tmp_path / "nope.json"))
        monkeypatch.setattr(sys, "argv", ["eval_wake_model.py"])
        with pytest.raises(SystemExit, match="split_wake_samples"):
            ev.main()


class TestSyntheticControl:
    """Piper is stochastic: measured on-device 2026-09-28, five renders of
    "hey bender" scored 0.966, 0.957, 0.071, 0.964, 0.071 on v0.1. A per-run
    render therefore swung the control between 0.97 and 0.13 and read as a
    broken export, so the control is a cached file chosen once."""

    def test_an_existing_control_file_is_reused_not_re_rendered(self, tmp_path, monkeypatch):
        import wave
        path = tmp_path / "synthetic_control.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(ev.RATE)
            w.writeframes(np.arange(1000, dtype=np.int16).tobytes())
        monkeypatch.setattr(ev, "SYNTHETIC_CONTROL", str(path))
        monkeypatch.setattr(ev.subprocess, "run",
                            lambda *a, **k: pytest.fail("must not render again"))
        out = ev.render_synthetic()
        assert len(out) == 1000

    def test_a_missing_control_and_no_piper_is_not_fatal(self, tmp_path, monkeypatch):
        """No Piper binary on a dev clone: the harness still runs, the control
        column just says n/a."""
        monkeypatch.setattr(ev, "SYNTHETIC_CONTROL", str(tmp_path / "none.wav"))
        monkeypatch.setattr(ev, "BASE_DIR", str(tmp_path))
        assert ev.render_synthetic() is None

    def test_scoring_without_a_control_returns_the_n_a_sentinel(self):
        assert ev._synthetic_score(object(), None) == -1.0
