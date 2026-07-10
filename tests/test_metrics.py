"""Tests for metrics collection module."""
import json
import time

def test_timer_records_duration(tmp_path):
    from metrics import MetricsWriter
    path = tmp_path / "metrics.jsonl"
    m = MetricsWriter(str(path))
    with m.timer("test_op"):
        time.sleep(0.05)  # 50ms — Windows sleep resolution is ~15ms
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["type"] == "timer"
    assert event["name"] == "test_op"
    assert event["duration_ms"] >= 5  # conservative: sleep(0.05) should give at least 5ms
    assert "ts" in event

def test_count_records_event(tmp_path):
    from metrics import MetricsWriter
    path = tmp_path / "metrics.jsonl"
    m = MetricsWriter(str(path))
    m.count("intent", intent="GREETING")
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["type"] == "count"
    assert event["name"] == "intent"
    assert event["intent"] == "GREETING"

def test_timer_with_tags(tmp_path):
    from metrics import MetricsWriter
    path = tmp_path / "metrics.jsonl"
    m = MetricsWriter(str(path))
    with m.timer("stt_transcribe", model="tiny.en"):
        pass
    event = json.loads(path.read_text().strip())
    assert event["model"] == "tiny.en"

def test_multiple_events(tmp_path):
    from metrics import MetricsWriter
    path = tmp_path / "metrics.jsonl"
    m = MetricsWriter(str(path))
    m.count("a")
    m.count("b")
    m.count("c")
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 3

def test_rotation_on_size_threshold(tmp_path):
    from metrics import MetricsWriter
    path = tmp_path / "metrics.jsonl"
    # Tiny threshold so a single write already trips rotation on the next call.
    m = MetricsWriter(str(path), max_bytes=10, backup_count=2)
    m.count("first")
    assert path.exists()
    assert not (tmp_path / "metrics.jsonl.1").exists()

    m.count("second")  # first write already exceeded 10 bytes -> rotates before this write
    assert (tmp_path / "metrics.jsonl.1").exists()
    backup_event = json.loads((tmp_path / "metrics.jsonl.1").read_text().strip())
    assert backup_event["name"] == "first"
    live_event = json.loads(path.read_text().strip())
    assert live_event["name"] == "second"

def test_rotation_keeps_only_backup_count(tmp_path):
    from metrics import MetricsWriter
    path = tmp_path / "metrics.jsonl"
    m = MetricsWriter(str(path), max_bytes=10, backup_count=2)
    for i in range(5):
        m.count(f"event{i}")
    assert path.exists()
    assert (tmp_path / "metrics.jsonl.1").exists()
    assert (tmp_path / "metrics.jsonl.2").exists()
    assert not (tmp_path / "metrics.jsonl.3").exists()

def test_rotation_disabled_with_zero_backup_count(tmp_path):
    from metrics import MetricsWriter
    path = tmp_path / "metrics.jsonl"
    m = MetricsWriter(str(path), max_bytes=10, backup_count=0)
    m.count("first")
    m.count("second")
    assert not (tmp_path / "metrics.jsonl.1").exists()
    # With backup_count=0, rotation just drops the old file -- only the
    # latest write should remain live.
    live_event = json.loads(path.read_text().strip())
    assert live_event["name"] == "second"
