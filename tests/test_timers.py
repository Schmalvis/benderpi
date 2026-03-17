"""Tests for the timers module."""

import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timezone, timedelta
from unittest import mock

import pytest

# Add scripts/ to path so we can import timers
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


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


def test_create_timer_duration():
    """Create timer with duration → correct fires_at within 1s tolerance."""
    import timers

    before = datetime.now(timezone.utc)
    t = timers.create_timer("pasta", 600)
    after = datetime.now(timezone.utc)

    assert t["label"] == "pasta"
    assert t["type"] == "timer"
    assert t["duration_s"] == 600
    assert t["id"].startswith("t_")
    assert len(t["id"]) == 10  # t_ + 8 hex
    assert t["fired"] is False
    assert t["dismissed"] is False

    fires_at = datetime.fromisoformat(t["fires_at"])
    expected_min = before + timedelta(seconds=600)
    expected_max = after + timedelta(seconds=600)
    assert expected_min - timedelta(seconds=1) <= fires_at <= expected_max + timedelta(seconds=1)


def test_create_alarm_specific_time():
    """Create alarm with a specific fires_at time."""
    import timers

    target = datetime(2026, 3, 17, 14, 30, 0, tzinfo=timezone.utc)
    a = timers.create_alarm("meeting", target)

    assert a["label"] == "meeting"
    assert a["type"] == "alarm"
    assert a["id"].startswith("a_")
    assert len(a["id"]) == 10
    assert a["fires_at"] == target.isoformat()
    assert a["duration_s"] is None
    assert a["dismissed"] is False


def test_list_timers_returns_active():
    """list_timers returns active (not dismissed) timers with remaining_s."""
    import timers

    t1 = timers.create_timer("eggs", 300)
    t2 = timers.create_timer("tea", 120)
    timers.dismiss_timer(t1["id"])

    result = timers.list_timers()
    ids = [t["id"] for t in result]
    assert t2["id"] in ids
    assert t1["id"] not in ids

    # Check remaining_s is present
    for t in result:
        assert "remaining_s" in t


def test_cancel_timer():
    """Cancel removes timer from list entirely."""
    import timers

    t = timers.create_timer("noodles", 60)
    assert timers.cancel_timer(t["id"]) is True
    assert all(x["id"] != t["id"] for x in timers.list_timers())

    # Cancel non-existent returns False
    assert timers.cancel_timer("t_00000000") is False


def test_dismiss_timer():
    """Dismiss marks timer dismissed, excluded from list_timers."""
    import timers

    t = timers.create_timer("rice", 60)
    assert timers.dismiss_timer(t["id"]) is True
    assert all(x["id"] != t["id"] for x in timers.list_timers())

    # Dismiss non-existent returns False
    assert timers.dismiss_timer("t_00000000") is False


def test_check_fired():
    """check_fired returns only timers past their time and not dismissed."""
    import timers

    # Create a timer that fires in the past
    t1 = timers.create_timer("done", 0)  # fires immediately
    t2 = timers.create_timer("future", 9999)

    fired = timers.check_fired()
    fired_ids = [t["id"] for t in fired]
    assert t1["id"] in fired_ids
    assert t2["id"] not in fired_ids


def test_dismiss_all_fired():
    """dismiss_all_fired clears multiple fired timers."""
    import timers

    t1 = timers.create_timer("a", 0)
    t2 = timers.create_timer("b", 0)
    t3 = timers.create_timer("c", 9999)

    count = timers.dismiss_all_fired()
    assert count == 2

    # Fired timers gone from list
    remaining = timers.list_timers()
    ids = [t["id"] for t in remaining]
    assert t1["id"] not in ids
    assert t2["id"] not in ids
    assert t3["id"] in ids


def test_persistence(tmp_path, monkeypatch):
    """Create timer, clear cache, reload from file — timer still there."""
    import timers

    t = timers.create_timer("persist", 300)

    # Simulate fresh process by clearing cache
    with timers._lock:
        timers._cache = None

    result = timers.list_timers()
    ids = [x["id"] for x in result]
    assert t["id"] in ids


def test_multiple_concurrent_timers():
    """Multiple timers created from threads all persist."""
    import timers

    results = []

    def make_timer(i):
        t = timers.create_timer(f"thread-{i}", 100 + i)
        results.append(t)

    threads = [threading.Thread(target=make_timer, args=(i,)) for i in range(10)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert len(results) == 10
    listed = timers.list_timers()
    assert len(listed) == 10


def test_empty_state():
    """No file → empty list, no errors."""
    import timers

    assert timers.list_timers() == []
    assert timers.check_fired() == []
    assert timers.dismiss_all_fired() == 0
    assert timers.cancel_timer("t_deadbeef") is False
