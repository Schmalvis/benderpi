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
        labels = " ".join(item[0] for item in cap.POSITIVE_CONDITIONS)
        for axis in ("close", "far", "quiet", "loud", "fast", "slow",
                     "off_axis", "background", "moving",
                     # round 2: every round-1 clip was the phrase ALONE
                     "embedded", "another_room", "seated_far"):
            assert axis in labels, f"no condition covers {axis}"

    def test_every_condition_has_an_instruction(self):
        for item in cap.POSITIVE_CONDITIONS:
            label, hint, _ = cap._spec(item)
            assert hint.strip(), f"{label} has no instruction for the speaker"

    def test_a_longer_window_is_used_where_the_phrase_is_embedded(self):
        """A sentence does not fit the 3s default; the clip is centre-cropped
        to 2s, so the window has to be long enough to hold the lead-in and
        tail as well as the phrase."""
        specs = {item[0]: cap._spec(item)[2] for item in cap.POSITIVE_CONDITIONS}
        assert specs["embedded"] > cap.RECORD_S
        assert specs["mid_normal"] == cap.RECORD_S

    def test_negatives_press_on_the_measured_failure(self):
        """v0.1 false-wakes cluster on the '-ender' ending (hey vendor 7/10,
        hey bend 5/10), not on 'hey' — round 2 targets that."""
        joined = " ".join(cap.HARD_NEGATIVE_PHRASES).lower()
        for p in ("blender", "gender", "surrender", "lavender", "remember"):
            assert p in joined, f"missing -ender negative: {p}"

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


class TestResume:
    """Capture is ~45 minutes of a person's time and is explicitly meant to be
    done across several sittings. Progress therefore lives on disk, not in the
    process. Before this, a second run restarted at condition 1 and overwrote
    the clips from the first."""

    @pytest.fixture
    def out_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cap, "OUT_ROOT", str(tmp_path))
        return tmp_path

    def _make(self, root, mode, speaker, label, n):
        d = root / mode / speaker
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, n + 1):
            (d / f"{label}_{i:03d}.wav").write_bytes(b"")

    def test_counts_existing_clips(self, out_root):
        self._make(out_root, "positive", "martin", "close_normal", 4)
        assert cap.existing_clips("positive", "martin", "close_normal") == 4

    def test_zero_when_nothing_recorded(self, out_root):
        assert cap.existing_clips("positive", "martin", "close_normal") == 0

    def test_labels_do_not_bleed_into_each_other(self, out_root):
        """'mid_normal' and 'mid_normal_extra' must not share a count."""
        self._make(out_root, "positive", "martin", "mid_normal", 3)
        self._make(out_root, "positive", "martin", "mid_quiet", 5)
        assert cap.existing_clips("positive", "martin", "mid_normal") == 3
        assert cap.existing_clips("positive", "martin", "mid_quiet") == 5

    def test_speakers_are_counted_separately(self, out_root):
        self._make(out_root, "positive", "martin", "close_normal", 4)
        assert cap.existing_clips("positive", "other", "close_normal") == 0

    def test_plan_reports_progress(self, out_root):
        self._make(out_root, "positive", "martin", "close_normal", 10)
        self._make(out_root, "positive", "martin", "mid_normal", 3)
        rows, done, total = _plan_for(out_root, per_condition=10)
        assert total == 10 * len(cap.POSITIVE_CONDITIONS)
        assert done == 13

    def test_overshoot_does_not_inflate_progress(self, out_root):
        """A condition redone by hand shouldn't report >100% complete."""
        self._make(out_root, "positive", "martin", "close_normal", 25)
        _, done, _ = _plan_for(out_root, per_condition=10)
        assert done == 10

    @pytest.mark.parametrize("mode", ["ambient", "conversation"])
    def test_continuous_modes_resume_from_file_count(self, out_root, mode):
        d = out_root / mode
        d.mkdir(parents=True)
        for i in range(7):
            (d / f"{i:03d}.wav").write_bytes(b"")
        assert cap.continuous_done(mode) == 7

    @pytest.mark.parametrize("mode", ["ambient", "conversation"])
    def test_continuous_modes_zero_when_absent(self, out_root, mode):
        assert cap.continuous_done(mode) == 0

    def test_conversation_is_separate_from_ambient(self, out_root):
        """Room sound and close-range speech are different negatives; mixing
        them would hide which one the model actually struggles with."""
        (out_root / "ambient").mkdir(parents=True)
        (out_root / "ambient" / "000.wav").write_bytes(b"")
        assert cap.continuous_done("conversation") == 0
        assert set(cap.CONTINUOUS_MODES) == {"ambient", "conversation"}


def _plan_for(root, per_condition):
    return cap._plan(cap.POSITIVE_CONDITIONS, "positive", "martin", per_condition)


class TestFirstRunAndWrongHost:
    """2026-09-22: the first clip on a fresh device failed because the scratch
    file's folder did not exist; the same run on the dev clone failed with
    "Unknown PCM mic_shared" only after the service had been stopped."""

    def test_record_creates_the_output_folder(self, tmp_path, monkeypatch):
        target = tmp_path / "wake_samples" / ".scratch.wav"
        seen = {}

        def fake_run(cmd, check=True, **kw):
            seen["dir_existed"] = target.parent.is_dir()
            cap._write(str(target), np.zeros(cap.RATE, dtype=np.int16))

        monkeypatch.setattr(cap.subprocess, "run", fake_run)
        cap._record(1.0, str(target))
        assert seen["dir_existed"]

    def test_mic_available_matches_exact_pcm_name(self, monkeypatch):
        listing = "null\n    Discard all samples\nmic_shared\ndefault\n"
        monkeypatch.setattr(cap.subprocess, "run",
                            lambda *a, **k: type("R", (), {"stdout": listing})())
        assert cap._mic_available("mic_shared") is True
        assert cap._mic_available("mic") is False

    def test_missing_mic_exits_before_stopping_the_service(self, monkeypatch):
        stopped = []
        monkeypatch.setattr(cap, "_mic_available", lambda d: False)
        monkeypatch.setattr(cap, "_set_service", lambda a: stopped.append(a) or True)
        monkeypatch.setattr(cap, "_service_active", lambda: True)
        monkeypatch.setattr(cap.sys, "argv", ["capture_wake_samples.py", "--speaker", "martin"])
        with pytest.raises(SystemExit) as exc:
            cap.main()
        assert "BenderPi" in str(exc.value)
        assert stopped == []


class TestManifestTypes:
    """2026-09-22: openWakeWord scores are numpy float32; the first real clip
    was saved and then json.dumps crashed the manifest write."""

    def test_scorer_returns_a_plain_float(self):
        s = cap._Scorer.__new__(cap._Scorer)
        s.model = type("M", (), {"predict": lambda self, fr: {"hey": np.float32(0.42)}})()
        out = s.peak(np.zeros(1280 * 4, dtype=np.int16))
        assert type(out) is float
        assert abs(out - 0.42) < 1e-6

    def test_log_accepts_numpy_scalars(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setattr(cap, "OUT_ROOT", str(tmp_path))
        cap._log({"current_model_score": np.float32(0.25), "peak_level": np.int16(900)})
        row = json.loads((tmp_path / "manifest.jsonl").read_text())
        assert row == {"current_model_score": 0.25, "peak_level": 900}
