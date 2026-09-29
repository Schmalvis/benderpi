"""The livekit wake-word trainer: clip seeding and the engine-name guard.

livekit-wakeword is NOT a drop-in for openWakeWord. Measured on-device
2026-09-29: the same model scoring the same audio reads 0.965 through
openWakeWord's streaming front-end and 0.005 through livekit's stateless one.
So a livekit model must be scored by livekit's engine, and eval_wake_model.py
picks the engine from the filename — which makes the filename load-bearing,
and worth a test.
"""
import json
import os
import sys
import wave
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.modules.setdefault("pyaudio", MagicMock())

import train_wakeword_livekit_hf as lk
import eval_wake_model as ev


class TestEngineSelection:
    @pytest.mark.parametrize("name", [
        "hey_bender_livekit_v1.onnx", "lk_hey_bender.onnx", "HEY_BENDER_LiveKit.onnx",
    ])
    def test_livekit_models_are_detected_by_name(self, name):
        assert ev.engine_for(name) == "livekit"

    @pytest.mark.parametrize("name", [
        "hey_bender_v0.1.onnx", "hey_bender_v0.3_r35.onnx", "hey_jarvis.onnx",
    ])
    def test_openwakeword_is_the_default(self, name):
        assert ev.engine_for(name) == "openwakeword"

    def test_the_trainer_refuses_an_ambiguous_output_name(self, monkeypatch):
        """A name without "livekit" would be scored by the wrong engine and
        report ~0.005 — a silent wrong answer, not an error."""
        monkeypatch.setattr(sys, "argv",
                            ["t", "--output-name", "hey_bender_v2.onnx"])
        monkeypatch.setenv("HF_TOKEN", "x")
        with pytest.raises(SystemExit, match="must contain 'livekit'"):
            lk.main()

    def test_the_default_output_name_is_unambiguous(self):
        assert "livekit" in lk.OUTPUT_ONNX_NAME.lower()


class TestNegativePhrases:
    def test_the_measured_false_wakes_are_all_covered(self):
        """These are not guesses: v0.1 false-wakes on "hey vendor" 7/10 and
        "hey bend" 5/10, and the household recorded the rest."""
        joined = " ".join(lk.NEGATIVE_PHRASES).lower()
        for p in ("hey vendor", "hey bend", "blender", "gender", "surrender",
                  "lavender", "remember", "bender"):
            assert p in joined, p

    def test_the_wake_phrase_itself_is_not_a_negative(self):
        assert "hey bender" not in [p.lower() for p in lk.NEGATIVE_PHRASES]


@pytest.fixture
def volume(tmp_path):
    root = tmp_path / "wake_samples"
    split = {"positive": {"train": [], "holdout": []},
             "hard_negative": {"train": [], "holdout": [], "watch": []},
             "ambient": {"background": [], "holdout": []},
             "conversation": {"train": [], "holdout": []}}

    def wav(rel, seconds=2.0):
        path = root / rel.replace("data/wake_samples/", "")
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(np.zeros(int(16000 * seconds), dtype=np.int16).tobytes())

    for i in range(8):
        rel = f"data/wake_samples/positive/martin/mid_normal_{i:03d}.wav"
        wav(rel)
        split["positive"]["train" if i < 6 else "holdout"].append(rel)
    for i in range(6):
        rel = f"data/wake_samples/hard_negative/martin/hey_vendor_{i:03d}.wav"
        wav(rel)
        split["hard_negative"]["train" if i < 4 else "holdout"].append(rel)
    for i in range(2):
        rel = f"data/wake_samples/hard_negative/martin/hey_bender's_{i:03d}.wav"
        wav(rel)
        split["hard_negative"]["watch"].append(rel)
    for i in range(4):
        rel = f"data/wake_samples/ambient/{i:03d}.wav"
        wav(rel, seconds=1.0)
        split["ambient"]["background" if i < 3 else "holdout"].append(rel)
    for i in range(3):
        rel = f"data/wake_samples/conversation/{i:03d}.wav"
        wav(rel, seconds=6.0)
        split["conversation"]["train" if i < 2 else "holdout"].append(rel)
    (root / "split.json").write_text(json.dumps(split))
    return str(root), split


class TestSeeding:
    def test_clips_land_in_livekits_generated_directories(self, volume, tmp_path):
        root, split = volume
        out = str(tmp_path / "output")
        w = lk._seed_real_clips(root, split, out, "hey_bender_livekit", 5, 3)
        assert w["positive"] == 6 * 5
        assert w["negative"] == 4 * 3
        pos = os.listdir(os.path.join(out, "hey_bender_livekit", "positive_train"))
        assert len(pos) == 30 and all(n.startswith("real_") for n in pos)

    def test_holdout_clips_never_reach_training(self, volume, tmp_path):
        root, split = volume
        out = str(tmp_path / "output")
        lk._seed_real_clips(root, split, out, "m", 2, 2)
        seeded = set()
        for sub in ("positive_train", "negative_train"):
            seeded |= {n.replace("real_", "").rsplit("_", 1)[0]
                       for n in os.listdir(os.path.join(out, "m", sub))}
        held = {os.path.basename(p).replace(".wav", "")
                for p in split["positive"]["holdout"] + split["hard_negative"]["holdout"]}
        assert not (seeded & held)

    def test_a_leaked_split_is_rejected(self, volume, tmp_path):
        root, split = volume
        split["positive"]["train"].append(split["positive"]["holdout"][0])
        with pytest.raises(AssertionError, match="leaked"):
            lk._seed_real_clips(root, split, str(tmp_path / "o"), "m", 2, 2)

    def test_the_watch_phrase_is_rejected_from_negatives(self, volume, tmp_path):
        root, split = volume
        split["hard_negative"]["train"].append(split["hard_negative"]["watch"][0])
        with pytest.raises(AssertionError, match="excluded phrase"):
            lk._seed_real_clips(root, split, str(tmp_path / "o"), "m", 2, 2)

    def test_conversation_minutes_are_sliced_into_negatives(self, volume, tmp_path):
        root, split = volume
        out = str(tmp_path / "output")
        n = lk._seed_conversation_negatives(root, split, out, "m")
        assert n == 2 * 3          # 2 trained minutes of 6s, 2s per clip
        names = os.listdir(os.path.join(out, "m", "negative_train"))
        assert all(n_.startswith("real_conv_") for n_ in names)

    def test_only_held_in_ambient_becomes_background(self, volume, tmp_path):
        root, split = volume
        d = lk._room_backgrounds(root, split, str(tmp_path / "w"))
        assert len(os.listdir(d)) == 3
        assert "003.wav" not in os.listdir(d)
