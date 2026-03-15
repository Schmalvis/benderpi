"""Lightweight metrics collection for BenderPi.

Writes timer and counter events to logs/metrics.jsonl.
Aggregation happens offline via generate_status.py.

Usage:
    from metrics import metrics
    with metrics.timer("stt_transcribe"):
        result = transcribe(audio)
    metrics.count("intent", intent="GREETING")
"""

import json
import os
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PATH = os.path.join(_BASE_DIR, "logs", "metrics.jsonl")


class MetricsWriter:
    """Thread-safe metrics writer to a JSONL file."""

    def __init__(self, path: str = None):
        self._path = path or _DEFAULT_PATH
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def _write(self, event: dict):
        event["ts"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with open(self._path, "a") as f:
                f.write(json.dumps(event) + "\n")

    @contextmanager
    def timer(self, name: str, **tags):
        """Context manager that records elapsed time in ms."""
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = round((time.monotonic() - start) * 1000, 1)
            self._write({"type": "timer", "name": name, "duration_ms": elapsed_ms, **tags})

    def count(self, name: str, **tags):
        """Record a counter event."""
        self._write({"type": "count", "name": name, **tags})


# Singleton
metrics = MetricsWriter()
