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
