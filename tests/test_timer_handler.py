"""Tests for timer handler."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest


@pytest.fixture(autouse=True)
def _isolated_timers(tmp_path, monkeypatch):
    """Each test gets its own timers.json in a temp directory."""
    import timers

    monkeypatch.setattr(timers, "_FILE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(timers, "_TMP_FILE", str(tmp_path / "timers.json.tmp"))
    # Reset in-memory cache
    with timers._lock:
        timers._cache = None
    yield
    # Reset cache again after test
    with timers._lock:
        timers._cache = None


def test_handle_set_creates_timer():
    with patch("tts_generate.speak", return_value="/tmp/test.wav"):
        from handlers.timer_handler import handle_set
        import timers

        wav = handle_set("set a timer for pasta for 10 minutes")
        assert wav == "/tmp/test.wav"
        active = timers.list_timers()
        assert len(active) >= 1
        assert any(t["label"] == "pasta" for t in active)


def test_handle_set_parse_fail():
    with patch("tts_generate.speak", return_value="/tmp/test.wav") as mock_speak:
        from handlers.timer_handler import handle_set

        wav = handle_set("set a timer for something")
        assert wav == "/tmp/test.wav"
        # Should have been called with a parse-fail response
        mock_speak.assert_called_once()


def test_handle_cancel():
    with patch("tts_generate.speak", return_value="/tmp/test.wav"):
        from handlers.timer_handler import handle_cancel
        import timers

        timers.create_timer("eggs", 300)
        wav = handle_cancel("cancel the eggs timer")
        assert wav == "/tmp/test.wav"
        assert len(timers.list_timers()) == 0


def test_handle_cancel_no_match():
    with patch("tts_generate.speak", return_value="/tmp/test.wav"):
        from handlers.timer_handler import handle_cancel

        # No timers exist — should still return a WAV (no-timer response)
        wav = handle_cancel("cancel the pasta timer")
        assert wav == "/tmp/test.wav"


def test_handle_cancel_falls_back_to_most_recent():
    """If label is 'timer' (generic), cancel the most recent active timer."""
    with patch("tts_generate.speak", return_value="/tmp/test.wav"):
        from handlers.timer_handler import handle_cancel
        import timers

        timers.create_timer("eggs", 300)
        wav = handle_cancel("cancel the timer")
        assert wav == "/tmp/test.wav"
        assert len(timers.list_timers()) == 0


def test_handle_status_no_timers():
    with patch("tts_generate.speak", return_value="/tmp/test.wav"):
        from handlers.timer_handler import handle_status

        wav = handle_status("what timers do I have")
        assert wav == "/tmp/test.wav"


def test_handle_status_one_timer():
    with patch("tts_generate.speak", return_value="/tmp/test.wav"):
        from handlers.timer_handler import handle_status
        import timers

        timers.create_timer("pasta", 300)
        wav = handle_status("what timers are running")
        assert wav == "/tmp/test.wav"


def test_handle_status_multiple_timers():
    with patch("tts_generate.speak", return_value="/tmp/test.wav"):
        from handlers.timer_handler import handle_status
        import timers

        timers.create_timer("pasta", 300)
        timers.create_timer("eggs", 120)
        wav = handle_status("how many timers")
        assert wav == "/tmp/test.wav"


def test_format_duration_seconds():
    from handlers.timer_handler import _format_duration
    assert _format_duration(1) == "1 second"
    assert _format_duration(30) == "30 seconds"


def test_format_duration_minutes():
    from handlers.timer_handler import _format_duration
    assert _format_duration(60) == "1 minute"
    assert _format_duration(300) == "5 minutes"


def test_format_duration_hours():
    from handlers.timer_handler import _format_duration
    assert _format_duration(3600) == "1 hour"
    assert _format_duration(7200) == "2 hours"
    assert _format_duration(5400) == "1 hour and 30 minutes"


def test_format_remaining_done():
    from handlers.timer_handler import _format_remaining
    assert _format_remaining(0) == "done"
    assert _format_remaining(-5) == "done"
