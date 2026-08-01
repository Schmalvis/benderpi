"""Tests for scripts/wake_score.py — the wake-word diagnostic.

Its whole value is being a faithful mirror of the live wake loop: same framing,
same N-of-M gate. A diagnostic that quietly disagrees with the thing it's
diagnosing is worse than none, so the mirror is what's pinned here.

Written 2026-08-01, after `oww_threshold` 0.35 was found to never fire for a
real human voice (0.23-0.27) despite scoring 0.97 on synthetic speech. The
journal's per-60s peak score could show that something was wrong but not what;
this tool is what separated "model too strict" from "audio path broken".
"""
import sys, os

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import wake_score


class TestFramingMatchesWakeLoop:
    def test_frame_size_matches_wake_converse(self):
        """openWakeWord is stateful over a rolling buffer; a different frame
        size would produce scores that aren't comparable to the live ones."""
        src = open(os.path.join(os.path.dirname(__file__),
                                '..', 'scripts', 'wake_converse.py')).read()
        assert "OWW_FRAME_SIZE = 1280" in src
        assert wake_score.FRAME == 1280

    def test_rate_matches_wake_loop(self):
        assert wake_score.RATE == 16000


class TestWouldFire:
    """Mirrors wake_converse: fire when `oww_frames_required` of any
    `oww_window` consecutive frames land at or above the threshold."""

    def test_single_high_frame_does_not_fire(self, monkeypatch):
        monkeypatch.setattr('config.cfg.oww_frames_required', 2)
        monkeypatch.setattr('config.cfg.oww_window', 4)
        scores = np.array([0.0, 0.9, 0.0, 0.0, 0.0, 0.0])
        fires, req, win = wake_score.would_fire(scores, 0.35)
        assert fires is False
        assert (req, win) == (2, 4)

    def test_two_frames_in_window_fires(self, monkeypatch):
        monkeypatch.setattr('config.cfg.oww_frames_required', 2)
        monkeypatch.setattr('config.cfg.oww_window', 4)
        scores = np.array([0.0, 0.9, 0.8, 0.0, 0.0, 0.0])
        assert wake_score.would_fire(scores, 0.35)[0] is True

    def test_two_frames_spread_beyond_window_does_not_fire(self, monkeypatch):
        monkeypatch.setattr('config.cfg.oww_frames_required', 2)
        monkeypatch.setattr('config.cfg.oww_window', 4)
        scores = np.array([0.9, 0.0, 0.0, 0.0, 0.0, 0.9])
        assert wake_score.would_fire(scores, 0.35)[0] is False

    def test_threshold_is_inclusive(self, monkeypatch):
        """wake_converse uses `>=`; a score exactly on the line counts."""
        monkeypatch.setattr('config.cfg.oww_frames_required', 2)
        monkeypatch.setattr('config.cfg.oww_window', 4)
        scores = np.array([0.35, 0.35, 0.0, 0.0])
        assert wake_score.would_fire(scores, 0.35)[0] is True

    def test_real_voice_scores_miss_old_threshold_but_clear_new_one(self, monkeypatch):
        """The actual 2026-08-01 finding, as a regression guard: measured
        live-voice peaks of ~0.23-0.27 must fire at 0.1 and not at 0.35."""
        monkeypatch.setattr('config.cfg.oww_frames_required', 2)
        monkeypatch.setattr('config.cfg.oww_window', 4)
        scores = np.array([0.02, 0.18, 0.27, 0.23, 0.05])
        assert wake_score.would_fire(scores, 0.35)[0] is False
        assert wake_score.would_fire(scores, 0.10)[0] is True

    def test_background_noise_does_not_fire_at_shipped_threshold(self, monkeypatch):
        """34h of measured non-wake audio never exceeded 0.023, so the shipped
        0.1 threshold must leave real headroom over it."""
        monkeypatch.setattr('config.cfg.oww_frames_required', 2)
        monkeypatch.setattr('config.cfg.oww_window', 4)
        noise = np.array([0.001, 0.023, 0.002, 0.018, 0.001, 0.009])
        assert wake_score.would_fire(noise, 0.10)[0] is False

    def test_empty_scores_do_not_fire(self, monkeypatch):
        monkeypatch.setattr('config.cfg.oww_frames_required', 2)
        monkeypatch.setattr('config.cfg.oww_window', 4)
        assert wake_score.would_fire(np.array([]), 0.10)[0] is False

    def test_required_is_clamped_to_window(self, monkeypatch):
        """A hand-edited config asking for more frames than the window holds
        must not become unfireable — wake_converse clamps, so this must too."""
        monkeypatch.setattr('config.cfg.oww_frames_required', 9)
        monkeypatch.setattr('config.cfg.oww_window', 3)
        fires, req, win = wake_score.would_fire(np.array([0.9, 0.9, 0.9]), 0.35)
        assert (req, win) == (3, 3)
        assert fires is True


class TestReport:
    def test_flags_when_nothing_loud_reached_the_mic(self, monkeypatch, capsys):
        """Two diagnostic runs on 2026-08-01 recorded an empty room and looked
        exactly like a failing model. Silence must be called out, not scored."""
        monkeypatch.setattr('config.cfg.oww_threshold', 0.1)
        scores = np.array([0.001] * 10)
        levels = np.array([120] * 10)
        wake_score.report(scores, levels)
        out = capsys.readouterr().out
        assert "nothing loud enough" in out

    def test_reports_score_on_speech_frames(self, monkeypatch, capsys):
        monkeypatch.setattr('config.cfg.oww_threshold', 0.1)
        scores = np.array([0.001, 0.42, 0.38, 0.001])
        levels = np.array([100, 9000, 8500, 90])
        wake_score.report(scores, levels)
        out = capsys.readouterr().out
        assert "speech-level audio: 2" in out
        assert "0.4200" in out

    def test_empty_input_is_an_error_not_a_verdict(self, monkeypatch):
        monkeypatch.setattr('config.cfg.oww_threshold', 0.1)
        with pytest.raises(SystemExit):
            wake_score.report(np.array([]), np.array([]))
