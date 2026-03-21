import json
import sys
import os
import pytest

# Ensure scripts/ is on path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from handlers.timer_alert import TimerAlertRunner


class TestTimerAlertDismissPatterns:
    def test_stop_is_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss("stop") is True

    def test_thanks_is_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss("thanks") is True

    def test_random_text_not_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss("what time is it") is False

    def test_ok_is_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss("ok") is True

    def test_got_it_is_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss("got it") is True

    def test_empty_string_not_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss("") is False

    def test_none_not_dismiss(self):
        runner = TimerAlertRunner()
        assert runner._is_dismiss(None) is False


class TestTimerAlertLoadClips:
    def test_load_alert_clips(self, tmp_path):
        (tmp_path / "clips").mkdir()
        (tmp_path / "clips" / "alert1.wav").write_bytes(b"RIFF" + b"\x00" * 40)
        (tmp_path / "clips" / "alert2.wav").write_bytes(b"RIFF" + b"\x00" * 40)
        index = {"timer_alerts": ["clips/alert1.wav", "clips/alert2.wav"]}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index))
        runner = TimerAlertRunner(index_path=str(index_path), base_dir=str(tmp_path))
        assert len(runner._alert_clips) == 2
