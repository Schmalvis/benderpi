"""Tests for the wake-word sample capture tool.

The trimming is the part that can silently poison a training run: a clip that
is the wrong length, or padded silence where a missed utterance should have
been dropped, teaches the model the wrong thing and you don't find out until
after a Modal run and a deploy.
"""
import sys, os

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import capture_wake_samples as cap


def _speechy(seconds: float, rate: int = 16000) -> np.ndarray:
    """Broadband noise — VAD reads it as speech."""
    rng = np.random.default_rng(0)
    return (rng.normal(0, 4000, int(rate * seconds))
            .clip(-32000, 32000).astype(np.int16))


def _silence(seconds: float, rate: int = 16000) -> np.ndarray:
    return np.zeros(int(rate * seconds), dtype=np.int16)


class TestTrimToVoiced:
    def test_output_is_exactly_clip_length(self):
        pcm = np.concatenate([_silence(0.5), _speechy(0.8), _silence(1.2)])
        out = cap.trim_to_voiced(pcm)
        assert out is not None
        assert len(out) == int(cap.RATE * cap.CLIP_S)
        assert out.dtype == np.int16

    def test_short_utterance_is_centred_not_left_aligned(self):
        """openWakeWord sees a rolling window; an utterance jammed against the
        clip edge trains it on a truncated phrase."""
        pcm = np.concatenate([_silence(0.2), _speechy(0.4), _silence(2.0)])
        out = cap.trim_to_voiced(pcm)
        energy = np.abs(out.astype(np.int32))
        third = len(out) // 3
        assert energy[third:2 * third].sum() > energy[:third].sum()

    def test_long_utterance_is_cropped_to_length(self):
        pcm = _speechy(5.0)
        out = cap.trim_to_voiced(pcm)
        assert len(out) == int(cap.RATE * cap.CLIP_S)

    def test_silence_returns_none(self):
        """A missed utterance must be dropped. Padding it into a silent
        'positive' would teach the model that the wake word sounds like
        nothing -- exactly the failure mode we're trying to fix."""
        assert cap.trim_to_voiced(_silence(3.0)) is None

    def test_speech_is_preserved_not_gated_away(self):
        pcm = np.concatenate([_silence(0.5), _speechy(0.8), _silence(1.2)])
        out = cap.trim_to_voiced(pcm)
        assert np.abs(out).max() > 1000

    def test_custom_clip_length_honoured(self):
        pcm = np.concatenate([_silence(0.3), _speechy(0.6), _silence(1.0)])
        out = cap.trim_to_voiced(pcm, clip_s=1.5)
        assert len(out) == int(cap.RATE * 1.5)


class TestConditions:
    def test_conditions_cover_the_generalisation_envelope(self):
        """v0.1 failed by generalisation, not sample count, so the capture set
        has to span distance, level, rate, angle and background."""
        labels = " ".join(label for label, _ in cap.POSITIVE_CONDITIONS)
        for axis in ("close", "far", "quiet", "loud", "fast", "slow",
                     "off_axis", "background", "moving"):
            assert axis in labels, f"no condition covers {axis}"

    def test_every_condition_has_an_instruction(self):
        for label, hint in cap.POSITIVE_CONDITIONS:
            assert hint.strip(), f"{label} has no instruction for the speaker"

    def test_hard_negatives_are_phonetically_close(self):
        """Adding real positives lifts recall and false positives together;
        these are what hold the FP rate down."""
        joined = " ".join(cap.HARD_NEGATIVE_PHRASES).lower()
        assert "hey" in joined
        assert "bender" in joined
        assert len(cap.HARD_NEGATIVE_PHRASES) >= 5


class TestOutputFormat:
    def test_written_wav_matches_training_input_format(self, tmp_path):
        """openWakeWord training expects 16kHz mono 16-bit."""
        import wave
        out = str(tmp_path / "x" / "clip.wav")
        cap._write(out, _speechy(2.0))
        with wave.open(out) as w:
            assert w.getframerate() == 16000
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2

    def test_captures_are_gitignored(self):
        """These are recordings of the household, and the repo is public."""
        ignored = open(os.path.join(os.path.dirname(__file__),
                                    '..', '.gitignore')).read()
        assert "data/wake_samples/" in ignored
