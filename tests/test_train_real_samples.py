"""Seeding real-voice clips into the openWakeWord training pipeline.

How the mix ratio works, read from upstream openwakeword/train.py:
--generate_clips counts what is already in positive_train/ and generates only
`n_samples - n_current`, so copying real clips in BEFORE that stage both
injects them and reduces the synthetic count by the same number.
--augment_clips then globs those directories, so each copy is augmented
independently with RIR + background noise.

These tests pin the two things that would silently ruin a $2, 3-hour run:
the real share actually landing where it was asked to, and held-out clips
never reaching a training directory.

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

# modal is a deploy-time dependency, not in the test venv; the helpers under
# test are plain functions that never touch it.
sys.modules.setdefault("modal", MagicMock())

import train_hey_bender as th


CONV_SECONDS = 6  # fixture-only: real captures are 60s per file


def _write(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"RIFF")


@pytest.fixture
def volume(tmp_path):
    """A mounted samples volume: 80 training positives, 64 training negatives,
    20 held out, 16 held out, 10 watch-only, 15+5 ambient minutes."""
    root = tmp_path / "wake_samples"
    split = {"positive": {"train": [], "holdout": []},
             "hard_negative": {"train": [], "holdout": [], "watch": []},
             "ambient": {"background": [], "holdout": []},
             "conversation": {"train": [], "holdout": []}}

    def add(mode, group, n, bucket, start=1):
        for i in range(start, start + n):
            rel = f"data/wake_samples/{mode}/martin/{group}_{i:03d}.wav"
            _write(str(tmp_path / rel.replace("data/wake_samples/", "wake_samples/")))
            split[mode][bucket].append(rel)

    for c in ("close_normal", "mid_normal", "far_normal", "mid_quiet", "mid_loud",
              "mid_fast", "mid_slow", "off_axis", "with_background", "moving"):
        add("positive", c, 8, "train")
        add("positive", c, 2, "holdout", start=9)
    for p in ("hey_there", "hey_friend", "bender", "hey_bend", "hey_Brenda",
              "hey_vendor", "okay_then", "play_defender"):
        add("hard_negative", p, 8, "train")
        add("hard_negative", p, 2, "holdout", start=9)
    add("hard_negative", "hey_bender's", 10, "watch")
    for i in range(20):
        rel = f"data/wake_samples/ambient/{i:03d}.wav"
        _write(str(root / "ambient" / f"{i:03d}.wav"))
        split["ambient"]["background" if i < 15 else "holdout"].append(rel)

    # Conversational negatives: 10 files, 7 trained on, 3 held out. Six
    # seconds each, not the real sixty — this fixture is function-scoped and
    # 10 real minutes of PCM per test filled a 2 GB /tmp. The slicing logic
    # is length-independent.
    (root / "conversation").mkdir(parents=True, exist_ok=True)
    for i in range(10):
        path = root / "conversation" / f"{i:03d}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(np.zeros(16000 * CONV_SECONDS, dtype=np.int16).tobytes())
        rel = f"data/wake_samples/conversation/{i:03d}.wav"
        split["conversation"]["train" if i < 7 else "holdout"].append(rel)

    (root / "split.json").write_text(json.dumps(split))
    return str(root), split


class TestSplitLoading:
    def test_missing_split_refuses_to_train(self, tmp_path):
        with pytest.raises(RuntimeError, match="split.json"):
            th._load_split(str(tmp_path))

    def test_loads_the_frozen_split(self, volume):
        root, split = volume
        assert th._load_split(root)["positive"]["train"] == split["positive"]["train"]


@pytest.fixture
def dry_copy(monkeypatch):
    """Record copies instead of performing them.

    A real 20000-sample run writes 4000+ files; on tmpfs each 4-byte file
    still costs a 4 KB block, and running the ratio assertions for real filled
    a 2 GB /tmp. The behaviour under test is which clip is copied how many
    times and where -- not the bytes.
    """
    copies = []
    monkeypatch.setattr(th.__dict__.setdefault("shutil", __import__("shutil")),
                        "copyfile", lambda src, dst: copies.append((src, dst)))
    return copies


class TestSeeding:
    def test_real_share_matches_the_requested_fraction(self, volume, tmp_path, dry_copy):
        root, split = volume
        w = th._seed_real_clips(root, split, str(tmp_path / "pt"),
                                str(tmp_path / "nt"), 20000, 0.20, 25)
        # 80 training clips x 50 copies = 4000 of 20000 positives
        assert w["positive_copies"] == 50
        assert w["positive"] == 4000
        assert abs(w["positive"] / 20000 - 0.20) < 0.01
        assert len([d for _, d in dry_copy if "/pt/" in d]) == 4000

    def test_fraction_scales(self, volume, tmp_path, dry_copy):
        # copies-per-clip is a whole number, so the share lands near the ask,
        # not exactly on it: 20000*0.35/80 = 87.5 -> 88 copies -> 35.2%.
        root, split = volume
        for frac in (0.10, 0.35):
            dry_copy.clear()
            th._seed_real_clips(root, split, str(tmp_path / "pt"),
                                str(tmp_path / "nt"), 20000, frac, 25)
            got = len([d for _, d in dry_copy if "/pt/" in d])
            assert abs(got / 20000 - frac) < 0.01

    def test_negative_copies_are_independent_of_the_fraction(self, volume, tmp_path, dry_copy):
        root, split = volume
        th._seed_real_clips(root, split, str(tmp_path / "pt"),
                            str(tmp_path / "nt"), 20000, 0.20, 25)
        assert len([d for _, d in dry_copy if "/nt/" in d]) == 64 * 25

    def test_copies_are_distinctly_named(self, volume, tmp_path):
        """Real file IO here, deliberately: this is the one test that proves
        the copies actually land on disk. Kept small (n_samples=100) so it
        costs ~100 files, not 4000."""
        root, split = volume
        pos = str(tmp_path / "pt")
        th._seed_real_clips(root, split, pos, str(tmp_path / "nt"), 100, 0.10, 1)
        names = os.listdir(pos)
        assert len(names) == len(set(names))
        assert all(n.startswith("real_") for n in names)

    def test_at_least_one_copy_even_at_a_tiny_fraction(self, volume, tmp_path):
        root, split = volume
        pos = str(tmp_path / "pt")
        th._seed_real_clips(root, split, pos, str(tmp_path / "nt"), 100, 0.001, 1)
        assert len(os.listdir(pos)) == 80


class TestNoLeakage:
    def test_holdout_clips_never_reach_a_training_directory(self, volume, tmp_path, dry_copy):
        root, split = volume
        th._seed_real_clips(root, split, str(tmp_path / "pt"),
                            str(tmp_path / "nt"), 20000, 0.20, 25)
        held = {os.path.basename(p).replace(".wav", "")
                for p in split["positive"]["holdout"] + split["hard_negative"]["holdout"]}
        seeded = {os.path.basename(d).replace("real_", "").rsplit("_", 1)[0]
                  for _, d in dry_copy}
        assert not (held & seeded)

    def test_a_leaked_split_is_rejected(self, volume, tmp_path, dry_copy):
        root, split = volume
        split["positive"]["train"].append(split["positive"]["holdout"][0])
        with pytest.raises(AssertionError, match="leaked"):
            th._seed_real_clips(root, split, str(tmp_path / "pt"),
                                str(tmp_path / "nt"), 20000, 0.20, 25)

    def test_the_excluded_phrase_is_rejected_if_it_appears_in_negatives(self, volume, tmp_path, dry_copy):
        root, split = volume
        split["hard_negative"]["train"].append(split["hard_negative"]["watch"][0])
        with pytest.raises(AssertionError, match="excluded phrase"):
            th._seed_real_clips(root, split, str(tmp_path / "pt"),
                                str(tmp_path / "nt"), 20000, 0.20, 25)

    def test_a_missing_clip_fails_loudly(self, volume, tmp_path):
        root, split = volume
        os.remove(os.path.join(root, "positive", "martin", "close_normal_001.wav"))
        with pytest.raises(RuntimeError, match="missing"):
            th._seed_real_clips(root, split, str(tmp_path / "pt"),
                                str(tmp_path / "nt"), 20000, 0.20, 25)


class TestAmbientBackgrounds:
    def test_only_the_held_in_minutes_become_backgrounds(self, volume, tmp_path):
        root, split = volume
        d = th._ambient_background_dir(root, split, str(tmp_path / "work"))
        assert len(os.listdir(d)) == 15
        assert "015.wav" not in os.listdir(d), "held-out ambient must not be a background"

    def test_no_ambient_means_no_directory(self, volume, tmp_path):
        root, split = volume
        split["ambient"]["background"] = []
        assert th._ambient_background_dir(root, split, str(tmp_path / "w")) is None


class TestConfig:
    def test_room_ambient_is_weighted_above_generic_music(self, tmp_path, monkeypatch):
        cfg = {}

        class _Yaml:
            Loader = object

            @staticmethod
            def load(*a, **k):
                return {"background_paths": [], "background_paths_duplication_rate": []}

            @staticmethod
            def dump(obj, f):
                cfg.update(obj)

        monkeypatch.setitem(sys.modules, "yaml", _Yaml)
        oww = tmp_path / "oww" / "examples"
        oww.mkdir(parents=True)
        (oww / "custom_model.yml").write_text("{}")
        th._write_config(str(tmp_path), str(tmp_path / "oww"), "psg", "hey bender",
                         100, 10, 1000, "piper.pt", 1.0, 500, 2, 0.5,
                         room_ambient_dir="/room", room_ambient_weight=3)
        assert cfg["background_paths"][-1] == "/room"
        assert cfg["background_paths_duplication_rate"] == [1, 3]

    def test_without_real_samples_the_room_is_absent(self, tmp_path, monkeypatch):
        cfg = {}

        class _Yaml:
            Loader = object

            @staticmethod
            def load(*a, **k):
                return {}

            @staticmethod
            def dump(obj, f):
                cfg.update(obj)

        monkeypatch.setitem(sys.modules, "yaml", _Yaml)
        oww = tmp_path / "oww" / "examples"
        oww.mkdir(parents=True)
        (oww / "custom_model.yml").write_text("{}")
        th._write_config(str(tmp_path), str(tmp_path / "oww"), "psg", "hey bender",
                         100, 10, 1000, "piper.pt", 1.0, 500, 2, 0.5)
        assert len(cfg["background_paths"]) == 1
        assert cfg["background_paths_duplication_rate"] == [1]


class TestConversationalNegatives:
    def test_minutes_are_sliced_into_two_second_clips(self, volume, tmp_path):
        root, split = volume
        neg = str(tmp_path / "nt")
        n = th._seed_conversation_negatives(root, split, neg)
        assert n == 7 * (CONV_SECONDS // 2), "each file sliced into 2s clips"
        assert len(os.listdir(neg)) == n

    def test_clips_are_the_training_format(self, volume, tmp_path):
        root, split = volume
        neg = str(tmp_path / "nt")
        th._seed_conversation_negatives(root, split, neg)
        with wave.open(os.path.join(neg, sorted(os.listdir(neg))[0])) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
            assert w.getnframes() == 32000

    def test_holdout_minutes_are_never_sliced_in(self, volume, tmp_path):
        root, split = volume
        neg = str(tmp_path / "nt")
        th._seed_conversation_negatives(root, split, neg)
        # names are real_conv_<source minute>_<chunk>; compare the source
        # field, not a substring (chunk 007 of minute 000 is not minute 007)
        sources = {n.split("_")[2] for n in os.listdir(neg)}
        held = {os.path.splitext(os.path.basename(p))[0]
                for p in split["conversation"]["holdout"]}
        assert not (sources & held)
        assert sources == {f"{i:03d}" for i in range(7)}

    def test_no_conversation_captured_is_not_an_error(self, volume, tmp_path):
        root, split = volume
        split["conversation"]["train"] = []
        assert th._seed_conversation_negatives(root, split, str(tmp_path / "nt")) == 0
