"""Tests for tts_generate.py crash resilience (Group 13) and the content-hash
disk cache. Uses fake Piper processes throughout — no real piper binary or
scipy required, so this suite runs the same off-device as on the Pi.
"""
import json
import os
import queue as _queue
import sys
import tempfile
import time
import wave

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def _write_minimal_wav(path, nframes=100, rate=44100):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * nframes)


class _FakeStdin:
    """Stand-in for Popen.stdin. on_write(payload) can simulate Piper
    actually producing the output file, or raise BrokenPipeError."""

    def __init__(self, on_write=None, raise_broken_pipe=False):
        self._on_write = on_write
        self._raise = raise_broken_pipe

    def write(self, payload):
        if self._raise:
            raise BrokenPipeError()
        if self._on_write:
            self._on_write(payload)

    def flush(self):
        pass


class _FakeProc:
    """Stand-in for subprocess.Popen used by _PiperProcess in tests."""

    def __init__(self, crash_after=None, stdin=None):
        self._t0 = time.monotonic()
        self._crash_after = crash_after
        self.stdin = stdin or _FakeStdin()
        self.returncode = None
        self._killed = False

    def poll(self):
        if self._killed:
            self.returncode = -9
            return self.returncode
        if self._crash_after is not None and time.monotonic() - self._t0 >= self._crash_after:
            self.returncode = 1
            return self.returncode
        return None

    def kill(self):
        self._killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


# ---------------------------------------------------------------------------
# _PiperProcess.synthesize() crash / timeout resilience
# ---------------------------------------------------------------------------


def test_synthesize_fails_fast_on_mid_inference_crash(monkeypatch):
    """A Piper process that dies mid-write must fail in well under the 10s
    deadline, restart itself in place, and leave no dangling temp WAV."""
    import tts_generate as tts

    created_paths = []
    orig_mkstemp = tts.tempfile.mkstemp

    def spy_mkstemp(*a, **kw):
        fd, path = orig_mkstemp(*a, **kw)
        created_paths.append(path)
        return fd, path

    monkeypatch.setattr(tts.tempfile, "mkstemp", spy_mkstemp)

    starts = []

    def fake_start(self):
        proc = _FakeProc(crash_after=0.05)
        self._proc = proc
        starts.append(proc)

    monkeypatch.setattr(tts._PiperProcess, "_start", fake_start)

    proc_wrapper = tts._PiperProcess()
    assert len(starts) == 1

    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="crashed"):
        proc_wrapper.synthesize("hello world")
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"crash detection took {elapsed:.2f}s — should fail fast, not burn the 10s deadline"
    assert len(starts) == 2, "process should be restarted in place after the crash"
    assert not os.path.exists(created_paths[-1]), "no dangling temp WAV after a mid-inference crash"


def test_synthesize_happy_path_still_works(monkeypatch):
    """Sanity check the poll()-in-loop change doesn't break normal synthesis."""
    import tts_generate as tts

    def on_write(payload):
        req = json.loads(payload.decode())
        with open(req["output_file"], "wb") as f:
            f.write(b"\x00" * 100)

    def fake_start(self):
        self._proc = _FakeProc(stdin=_FakeStdin(on_write=on_write))

    monkeypatch.setattr(tts._PiperProcess, "_start", fake_start)
    proc = tts._PiperProcess()
    result = proc.synthesize("hi")
    try:
        assert os.path.exists(result)
    finally:
        tts._safe_unlink(result)


def test_synthesize_raises_after_one_restart_on_persistent_broken_pipe(monkeypatch):
    """A repeatedly-broken pipe (e.g. OOM killer takes the freshly-restarted
    process too) must raise after a single retry, not restart-loop forever."""
    import tts_generate as tts

    restart_count = {"n": 0}

    def fake_start(self):
        restart_count["n"] += 1
        self._proc = _FakeProc(stdin=_FakeStdin(raise_broken_pipe=True))

    monkeypatch.setattr(tts._PiperProcess, "_start", fake_start)
    proc = tts._PiperProcess()
    assert restart_count["n"] == 1

    with pytest.raises(RuntimeError, match="broken"):
        proc.synthesize("hello")

    assert restart_count["n"] == 2, "exactly one restart attempt, then raise"


def test_synthesize_timeout_restarts_and_raises(monkeypatch):
    """A process that never produces output (wedged, but not exited) must
    time out, get killed + restarted, and raise TimeoutError."""
    import tts_generate as tts

    monkeypatch.setattr(tts, "_PIPER_SYNTH_TIMEOUT_S", 0.05)

    starts = []

    def fake_start(self):
        proc = _FakeProc()  # never writes the file, never exits on its own
        self._proc = proc
        starts.append(proc)

    monkeypatch.setattr(tts._PiperProcess, "_start", fake_start)
    proc_wrapper = tts._PiperProcess()

    with pytest.raises(TimeoutError):
        proc_wrapper.synthesize("hello")

    assert len(starts) == 2, "wedged process should be killed and replaced"
    assert starts[0]._killed, "the wedged process must be killed, not leaked"


# ---------------------------------------------------------------------------
# PiperPool borrow timeout (pool starvation)
# ---------------------------------------------------------------------------


def test_pool_synthesize_raises_on_starvation(monkeypatch):
    """If every process is checked out, a caller must not block forever."""
    import tts_generate as tts

    counted = []
    monkeypatch.setattr(tts.metrics, "count", lambda name, **tags: counted.append(name))

    pool = tts.PiperPool.__new__(tts.PiperPool)
    pool._q = _queue.Queue()  # empty — nothing to borrow

    with pytest.raises(TimeoutError):
        pool.synthesize("hello", timeout=0.05)

    assert "tts_pool_starved" in counted


# ---------------------------------------------------------------------------
# Content-hash disk cache
# ---------------------------------------------------------------------------


def test_cache_miss_before_any_write(tmp_path, monkeypatch):
    import tts_generate as tts

    monkeypatch.setattr(tts.cfg, "tts_cache_dir", str(tmp_path))
    key = tts._cache_key("nobody home")
    assert tts._cache_get(key) is None


def test_cache_hit_returns_distinct_copy_not_the_cache_file(tmp_path, monkeypatch):
    import tts_generate as tts

    monkeypatch.setattr(tts.cfg, "tts_cache_dir", str(tmp_path))
    monkeypatch.setattr(tts.cfg, "tts_cache_max_mb", 100)

    key = tts._cache_key("hello there")
    src = tmp_path / "src.wav"
    _write_minimal_wav(str(src))
    tts._cache_put(key, str(src))

    cache_file = tts._cache_path(key)
    assert os.path.exists(cache_file)

    hit1 = tts._cache_get(key)
    hit2 = tts._cache_get(key)
    try:
        assert hit1 is not None and hit2 is not None
        assert hit1 != hit2, "each hit must be a fresh temp copy"
        assert hit1 != cache_file and hit2 != cache_file, "must never hand back the cache file itself"
        assert os.path.exists(cache_file), "reading a cache entry must not consume it"
    finally:
        for p in (hit1, hit2):
            if p and os.path.exists(p):
                os.unlink(p)


def test_cache_key_changes_with_voice_params(monkeypatch):
    """Cache key must be sensitive to speech_rate / noise scale, or a Martin
    speech-rate tweak in the web UI would keep serving stale-sounding audio."""
    import tts_generate as tts

    monkeypatch.setattr(tts.cfg, "speech_rate", 1.0)
    monkeypatch.setattr(tts.cfg, "tts_noise_scale", 0.9)
    monkeypatch.setattr(tts.cfg, "tts_noise_scale_w", 1.2)
    k1 = tts._cache_key("same text")

    monkeypatch.setattr(tts.cfg, "speech_rate", 1.3)
    k2 = tts._cache_key("same text")

    assert k1 != k2


def test_cache_key_changes_with_text(monkeypatch):
    import tts_generate as tts

    k1 = tts._cache_key("good news everyone")
    k2 = tts._cache_key("bad news everyone")
    assert k1 != k2


def test_speak_single_hits_cache_on_second_call(tmp_path, monkeypatch):
    """End-to-end through _speak_single: second call for identical text +
    params must not re-invoke the Piper pool."""
    import tts_generate as tts

    monkeypatch.setattr(tts.cfg, "tts_cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(tts.cfg, "tts_cache_max_mb", 100)

    synth_calls = []

    class FakePool:
        def synthesize(self, text):
            synth_calls.append(text)
            f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=str(tmp_path))
            f.close()
            return f.name

    monkeypatch.setattr(tts, "_get_piper_pool", lambda: FakePool())
    monkeypatch.setattr(tts, "_resample_and_pad", lambda in_path, out_path: _write_minimal_wav(out_path))

    p1 = tts._speak_single("Bite my shiny metal ass")
    assert len(synth_calls) == 1
    os.unlink(p1)

    p2 = tts._speak_single("Bite my shiny metal ass")
    assert len(synth_calls) == 1, "second call should be served from cache"
    os.unlink(p2)


def test_prune_cache_bounds_total_size_oldest_first(tmp_path, monkeypatch):
    import tts_generate as tts

    monkeypatch.setattr(tts.cfg, "tts_cache_dir", str(tmp_path))
    monkeypatch.setattr(tts.cfg, "tts_cache_max_mb", 0.01)  # ~10.24KB cap

    now = time.time()
    for i in range(5):
        p = tmp_path / f"{i}.wav"
        p.write_bytes(b"\x00" * 4096)  # 4KB each, 20KB total — well over the cap
        mtime = now - (5 - i) * 10  # 0.wav oldest, 4.wav newest
        os.utime(str(p), (mtime, mtime))

    tts._prune_cache()

    remaining = sorted(os.listdir(str(tmp_path)))
    total = sum(os.path.getsize(os.path.join(str(tmp_path), f)) for f in remaining)
    assert total <= 0.01 * 1024 * 1024
    assert "4.wav" in remaining, "newest entry should survive"
    assert "0.wav" not in remaining, "oldest entry should be pruned first"


def test_prune_cache_noop_under_cap(tmp_path, monkeypatch):
    import tts_generate as tts

    monkeypatch.setattr(tts.cfg, "tts_cache_dir", str(tmp_path))
    monkeypatch.setattr(tts.cfg, "tts_cache_max_mb", 100)

    p = tmp_path / "only.wav"
    p.write_bytes(b"\x00" * 1024)

    tts._prune_cache()
    assert os.path.exists(str(p))
