"""Tests for metrics collection module."""
import os
import sys
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


class TestTheSuiteDoesNotWriteToTheRepoLogs:
    """A test run must not raise a watchdog alert against a healthy device.

    The suite imports the real `metrics` singleton, so a test that triggers
    `metrics.count("wake_loop_stall_reinit")` used to append to the repo's own
    logs/metrics.jsonl. watchdog.py reads that file and
    `mic_stall_reinit_threshold` is 3, so two suite runs produced "USB mic may
    be flapping/wedging" and an error-severity "mic likely needs a physical
    reseat" for a device with zero stalls. Measured 2026-10-14: 24 of each in
    24h on the dev clone, 0 on the device.
    """

    @staticmethod
    def _repo_logs():
        import metrics as metrics_mod
        return os.path.realpath(os.path.join(metrics_mod._BASE_DIR, "logs"))

    def test_the_singleton_writes_outside_the_repo(self):
        from metrics import metrics
        live = os.path.realpath(metrics._path)
        assert not live.startswith(self._repo_logs() + os.sep), (
            f"the suite's metrics singleton writes to {live}, inside the repo's "
            "logs directory; conftest.py must set BENDER_LOG_DIR before import"
        )

    def test_the_logger_writes_outside_the_repo(self):
        import logger as logger_mod
        live = os.path.realpath(logger_mod._LOG_FILE)
        assert not live.startswith(self._repo_logs() + os.sep), (
            f"the suite's log file is {live}, inside the repo's logs directory"
        )

    def test_a_real_count_lands_in_the_redirected_file(self):
        from metrics import metrics
        marker = "suite_isolation_probe"
        metrics.count(marker)
        with open(metrics._path) as f:
            assert marker in f.read()

    def test_the_override_is_what_redirects_it(self, tmp_path):
        """The mechanism, not just the current state.

        Runs in a SUBPROCESS on purpose. `importlib.reload(metrics)` rebinds
        the module's `metrics` singleton to a new object, while every module
        that did `from metrics import metrics` keeps the old one -- so an
        in-process reload silently breaks
        test_presence.py::TestMetricsSingletonIsImported, which asserts the two
        are the same object. A subprocess cannot reach this session's imports.
        """
        import subprocess
        target = tmp_path / "elsewhere"
        code = (
            "import metrics, sys; "
            "sys.stdout.write(metrics._DEFAULT_PATH)"
        )
        env = {**os.environ, "BENDER_LOG_DIR": str(target),
               "PYTHONPATH": os.path.join(
                   os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "scripts")}
        out = subprocess.run([sys.executable, "-c", code], env=env,
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert out.stdout == str(target / "metrics.jsonl")
