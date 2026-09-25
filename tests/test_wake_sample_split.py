"""The frozen train/holdout split of the captured wake-word samples.

The held-out clips ARE the v0.2 experiment: v0.1 scored 0.97 on synthetic
audio and woke on 6 of 100 real utterances, so a model measured on clips it
trained on tells you nothing. These tests pin the properties that make the
split trustworthy: every condition on both sides, nothing held out that is
also trained on, "hey bender's" out of the negatives entirely, and ambient
holdout minutes never reused as augmentation backgrounds.

Plan: docs/superpowers/plans/2026-09-24-wake-word-retrain-v0.2.md
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import split_wake_samples as sws

CONDITIONS = ["close_normal", "mid_normal", "far_normal", "mid_quiet", "mid_loud",
              "mid_fast", "mid_slow", "off_axis", "with_background", "moving"]
PHRASES = ["hey_there", "hey_friend", "bender", "hey_bend", "hey_Brenda",
           "hey_vendor", "okay_then", "hey_bender's", "play_defender"]


@pytest.fixture
def captured(tmp_path, monkeypatch):
    """A faithful copy of the real capture layout: 100 positives, 90 hard
    negatives, 20 ambient minutes."""
    root = tmp_path / "data" / "wake_samples"
    for c in CONDITIONS:
        d = root / "positive" / "martin"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, 11):
            (d / f"{c}_{i:03d}.wav").write_bytes(b"RIFF")
    for p in PHRASES:
        d = root / "hard_negative" / "martin"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, 11):
            (d / f"{p}_{i:03d}.wav").write_bytes(b"RIFF")
    (root / "ambient").mkdir(parents=True, exist_ok=True)
    for i in range(20):
        (root / "ambient" / f"{i:03d}.wav").write_bytes(b"RIFF")
    (root / "conversation").mkdir(parents=True, exist_ok=True)
    for i in range(10):
        (root / "conversation" / f"{i:03d}.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(sws, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(sws, "OUT_ROOT", str(root))
    monkeypatch.setattr(sws, "SPLIT_PATH", str(root / "split.json"))
    return root


class TestSplitShape:
    def test_holds_out_two_per_condition(self, captured):
        s = sws.build_split()
        assert len(s["positive"]["holdout"]) == 20
        assert len(s["positive"]["train"]) == 80
        groups = {sws._group(p) for p in s["positive"]["holdout"]}
        assert groups == set(CONDITIONS), "every condition must appear in the holdout"

    def test_hard_negatives_exclude_the_wake_phrase_variant(self, captured):
        s = sws.build_split()
        trained = {sws._group(p) for p in s["hard_negative"]["train"]}
        assert "hey_bender's" not in trained
        assert len(s["hard_negative"]["watch"]) == 10
        # 8 phrases x 8 trained, 8 phrases x 2 held out
        assert len(s["hard_negative"]["train"]) == 64
        assert len(s["hard_negative"]["holdout"]) == 16

    def test_ambient_splits_15_background_5_holdout(self, captured):
        s = sws.build_split()
        assert len(s["ambient"]["background"]) == 15
        assert len(s["ambient"]["holdout"]) == 5
        assert not set(s["ambient"]["background"]) & set(s["ambient"]["holdout"])

    def test_holdout_ambient_is_contiguous_and_last(self, captured):
        s = sws.build_split()
        assert [os.path.basename(p) for p in s["ambient"]["holdout"]] == \
            [f"{i:03d}.wav" for i in range(15, 20)]


class TestNoLeakage:
    def test_train_and_holdout_are_disjoint(self, captured):
        s = sws.build_split()
        for mode in ("positive", "hard_negative"):
            assert not set(s[mode]["train"]) & set(s[mode]["holdout"])

    def test_every_clip_is_accounted_for(self, captured):
        s = sws.build_split()
        pos = set(s["positive"]["train"]) | set(s["positive"]["holdout"])
        assert len(pos) == 100
        neg = (set(s["hard_negative"]["train"]) | set(s["hard_negative"]["holdout"])
               | set(s["hard_negative"]["watch"]))
        assert len(neg) == 90

    def test_watch_clips_are_not_trained_on(self, captured):
        s = sws.build_split()
        assert not set(s["hard_negative"]["watch"]) & set(s["hard_negative"]["train"])


class TestDeterminism:
    def test_same_seed_gives_the_same_split(self, captured):
        assert sws.build_split() == sws.build_split()

    def test_a_different_seed_moves_clips(self, captured):
        a = sws.build_split()
        b = sws.build_split(seed=999)
        assert a["positive"]["holdout"] != b["positive"]["holdout"]

    def test_existing_split_is_not_silently_rewritten(self, captured, monkeypatch, capsys):
        sws.SPLIT_PATH and json.dump(sws.build_split(), open(sws.SPLIT_PATH, "w"))
        before = open(sws.SPLIT_PATH).read()
        monkeypatch.setattr(sys, "argv", ["split_wake_samples.py"])
        sws.main()
        assert open(sws.SPLIT_PATH).read() == before
        assert "--force" in capsys.readouterr().out


class TestGuards:
    def test_never_holds_out_a_whole_group(self, captured):
        # a condition with only 2 clips must still leave one for training
        d = captured / "positive" / "martin"
        for f in list(d.glob("moving_*.wav"))[2:]:
            f.unlink()
        s = sws.build_split()
        moving = [p for p in s["positive"]["train"] if sws._group(p) == "moving"]
        assert len(moving) >= 1

    def test_ambient_holdout_larger_than_capture_is_refused(self, captured):
        with pytest.raises(SystemExit):
            sws.build_split(ambient_minutes=50)


class TestConversation:
    """Close-range speech aimed at another person is the false-wake case that
    ambient room sound does not contain (v0.1: 0 frames over threshold in 20
    ambient minutes, yet 22/90 false wakes on near-miss phrases)."""

    def test_holds_out_the_last_three_minutes(self, captured):
        s = sws.build_split()
        assert len(s["conversation"]["holdout"]) == 3
        assert len(s["conversation"]["train"]) == 7
        assert [os.path.basename(p) for p in s["conversation"]["holdout"]] == \
            ["007.wav", "008.wav", "009.wav"]

    def test_train_and_holdout_are_disjoint(self, captured):
        s = sws.build_split()
        assert not set(s["conversation"]["train"]) & set(s["conversation"]["holdout"])

    def test_absent_conversation_is_not_an_error(self, captured):
        """Round 1 captured none; a split built then must still be valid."""
        for f in (captured / "conversation").glob("*.wav"):
            f.unlink()
        s = sws.build_split()
        assert s["conversation"] == {"train": [], "holdout": []}
        assert len(s["positive"]["train"]) == 80
