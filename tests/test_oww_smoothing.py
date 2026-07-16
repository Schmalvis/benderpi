"""Tests for N-of-M temporal smoothing, RMS input sentinel, and predict()
resilience in wait_for_wakeword()."""
import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import pytest
import numpy as np
from unittest.mock import MagicMock

# Reuse the same import-chain stubbing strategy as test_oww_detection.py so
# wake_converse imports for real on machines without the Pi's heavy deps.


def _stub(name, **attrs):
    if name in sys.modules:
        return
    try:
        __import__(name)
    except ImportError:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m


_stub("dotenv", dotenv_values=lambda *a, **k: {})
_stub("pyaudio", paInt16=8, PyAudio=lambda: None)
if "openwakeword" not in sys.modules:
    try:
        __import__("openwakeword")
    except ImportError:
        _oww = types.ModuleType("openwakeword")
        _oww_model = types.ModuleType("openwakeword.model")
        _oww_model.Model = object
        _oww.model = _oww_model
        sys.modules["openwakeword"] = _oww
        sys.modules["openwakeword.model"] = _oww_model

for _name in (
    "tts_generate", "stt", "briefings", "leds", "vision",
    "ai_response", "ai_local", "conversation_log", "responder",
    "session",
):
    _stub(
        _name,
        warm_up=lambda *a, **k: None,
        refresh_all=lambda *a, **k: None,
        AIResponder=object,
        LocalAIResponder=object,
        SessionLogger=object,
        Responder=object,
        ConversationSession=object,
        FutureVisionProvider=object,
    )
_stub("handlers.timer_alert", TimerAlertRunner=object)


# Helpers build frames with a known stddev/RMS so we can exercise the dead-feed
# sentinel deterministically. The sentinel escalates on *stddev*, so a "healthy"
# frame must VARY (a constant-value frame — even a loud one — is a stuck feed).
def _varying_pcm(n_samples: int, amp: float, channels: int = 1) -> bytes:
    """A sine-shaped frame: stddev ≈ RMS ≈ amp/√2 (mean ≈ 0)."""
    mono = (np.sin(np.arange(n_samples) * 0.19) * amp).astype(np.int16)
    frame = np.repeat(mono, channels) if channels > 1 else mono
    return frame.tobytes()


# Healthy live mic: loud AND varying (high stddev) — sentinel stays quiet.
def _loud_pcm(n_samples: int, channels: int = 1) -> bytes:
    return _varying_pcm(n_samples, 5000.0, channels)


# Genuinely dead feed: all zeros → stddev 0 → escalates.
def _silent_pcm(n_samples: int, channels: int = 1) -> bytes:
    return np.zeros(n_samples * channels, dtype=np.int16).tobytes()


# Stuck-at-DC feed: loud but constant → RMS high, stddev 0 → escalates
# (the failure mode the old RMS-floor sentinel could NOT catch).
def _dc_pcm(n_samples: int, channels: int = 1) -> bytes:
    return (np.ones(n_samples * channels, dtype=np.int16) * 5000).tobytes()


# Quiet room: low-amplitude but varying → RMS ~25 (< 30 advisory floor) but
# stddev ~25 (> 5 std floor) → must NOT escalate (the 2026-07-14 false positive).
def _quiet_varying_pcm(n_samples: int, channels: int = 1) -> bytes:
    return _varying_pcm(n_samples, 35.0, channels)


def _wire_mic(monkeypatch, read_fn):
    import audio
    mock_stream = MagicMock()
    mock_stream.read = read_fn
    mock_pa = MagicMock()
    mock_pa.get_device_info_by_index.return_value = {'name': 'default_mic'}
    mock_pa.open.return_value = mock_stream
    monkeypatch.setattr(audio, 'get_pa', lambda: mock_pa)
    monkeypatch.setattr(audio, 'get_input_device_index', lambda: 0)


def _base_config(monkeypatch, **overrides):
    defaults = {
        'oww_threshold': 0.35,
        'oww_frames_required': 2,
        'oww_window': 4,
        'wake_stall_seconds': 30,
        'wake_heartbeat_frames': 250,
        'wake_std_floor': 5.0,
        'wake_silence_alarm_s': 120.0,
        'wake_rms_floor': 30.0,
        'wake_degraded_warn_s': 600.0,
        'wake_score_log_interval_s': 60.0,
        'mic_read_timeout_s': 10.0,
        'mic_stall_max_reinits': 1,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        monkeypatch.setattr(f'config.cfg.{k}', v)


# The blocking read runs on a MicReader daemon thread, so a bail raised inside
# fake_read never reaches the main loop. Bail instead from predict() (called on
# the main thread) using KeyboardInterrupt — a BaseException, so the loop's
# `except Exception` around predict() does NOT swallow it.
def _predict_scorer(scores, bail_after):
    """Return a predict() that yields `scores[i]` per call, then raises
    KeyboardInterrupt once call count exceeds bail_after (to terminate a
    non-firing test deterministically)."""
    calls = [0]
    def predict(arr):
        calls[0] += 1
        if calls[0] > bail_after:
            raise KeyboardInterrupt
        score = scores[calls[0] - 1] if calls[0] - 1 < len(scores) else 0.1
        return {'w': score}
    predict.calls = calls
    return predict


def test_single_spike_does_not_fire(monkeypatch):
    """A single frame over threshold must NOT trigger with 2-of-4 smoothing."""
    from wake_converse import wait_for_wakeword

    def fake_read(n, exception_on_overflow=False):
        return _loud_pcm(n)
    _wire_mic(monkeypatch, fake_read)
    _base_config(monkeypatch)

    # Exactly one frame (the 3rd) over threshold; all others below.
    mock_model = MagicMock()
    mock_model.predict = _predict_scorer([0.1, 0.1, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1],
                                         bail_after=8)

    with pytest.raises(KeyboardInterrupt):
        wait_for_wakeword(_oww_model=mock_model)


def test_two_of_four_fires(monkeypatch):
    """Two frames over threshold within the window DOES trigger."""
    from wake_converse import wait_for_wakeword

    def fake_read(n, exception_on_overflow=False):
        return _loud_pcm(n)
    _wire_mic(monkeypatch, fake_read)
    _base_config(monkeypatch)

    calls = [0]
    mock_model = MagicMock()
    def fake_predict(arr):
        calls[0] += 1
        # Two hits inside a 4-frame window (frames 2 and 3).
        return {'w': 0.9 if calls[0] in (2, 3) else 0.1}
    mock_model.predict = fake_predict

    wait_for_wakeword(_oww_model=mock_model)  # returns => fired
    assert calls[0] == 3


def test_spaced_hits_outside_window_do_not_fire(monkeypatch):
    """Two hits separated by more than the window must NOT accumulate."""
    from wake_converse import wait_for_wakeword

    def fake_read(n, exception_on_overflow=False):
        return _loud_pcm(n)
    _wire_mic(monkeypatch, fake_read)
    _base_config(monkeypatch, oww_window=3, oww_frames_required=2)

    # Hits on frame 1 and frame 6 — never both inside a 3-frame window.
    mock_model = MagicMock()
    mock_model.predict = _predict_scorer(
        [0.9, 0.1, 0.1, 0.1, 0.1, 0.9, 0.1, 0.1, 0.1, 0.1], bail_after=10)

    with pytest.raises(KeyboardInterrupt):
        wait_for_wakeword(_oww_model=mock_model)


def test_frames_required_one_disables_smoothing(monkeypatch):
    """oww_frames_required=1 restores pure per-frame threshold behaviour."""
    from wake_converse import wait_for_wakeword

    def fake_read(n, exception_on_overflow=False):
        return _loud_pcm(n)
    _wire_mic(monkeypatch, fake_read)
    _base_config(monkeypatch, oww_frames_required=1)

    calls = [0]
    mock_model = MagicMock()
    def fake_predict(arr):
        calls[0] += 1
        return {'w': 0.9 if calls[0] == 1 else 0.1}
    mock_model.predict = fake_predict

    wait_for_wakeword(_oww_model=mock_model)  # first hit fires immediately
    assert calls[0] == 1


def test_predict_exception_does_not_kill_loop(monkeypatch):
    """A predict() exception on a frame is swallowed; the loop continues."""
    from wake_converse import wait_for_wakeword

    def fake_read(n, exception_on_overflow=False):
        return _loud_pcm(n)
    _wire_mic(monkeypatch, fake_read)
    _base_config(monkeypatch, oww_frames_required=1)

    calls = [0]
    mock_model = MagicMock()
    def fake_predict(arr):
        calls[0] += 1
        if calls[0] <= 2:
            raise ValueError("bad frame")
        return {'w': 0.9}  # third frame fires
    mock_model.predict = fake_predict

    wait_for_wakeword(_oww_model=mock_model)  # survives the exceptions, fires
    assert calls[0] == 3


def test_std_sentinel_escalates_on_dead_mic(monkeypatch):
    """Silent (zero-stddev) frames past the alarm window escalate to the stall
    path. With mic_stall_max_reinits=0 the escalation calls sys.exit(1) after
    emitting the wake_mic_silent metric — assert on SystemExit as proof."""
    from wake_converse import wait_for_wakeword

    # Fake monotonic clock so the alarm window elapses deterministically.
    t = [0.0]
    monkeypatch.setattr('wake_converse.time.monotonic', lambda: t[0])
    monkeypatch.setattr('wake_converse.time.sleep', lambda *_: None)

    silent_calls = [0]
    import metrics as metrics_mod
    orig_count = metrics_mod.metrics.count
    def spy_count(name, **tags):
        if name == "wake_mic_silent":
            silent_calls[0] += 1
        return orig_count(name, **tags)
    monkeypatch.setattr(metrics_mod.metrics, 'count', spy_count)

    def fake_read(n, exception_on_overflow=False):
        t[0] += 1.0  # each frame advances 1s of virtual time
        return _silent_pcm(n)
    _wire_mic(monkeypatch, fake_read)
    # Short alarm window; zero reinit budget => escalation is sys.exit(1).
    _base_config(monkeypatch, wake_silence_alarm_s=5.0, mic_stall_max_reinits=0)

    mock_model = MagicMock()
    mock_model.predict.return_value = {'w': 0.0}

    with pytest.raises(SystemExit):
        wait_for_wakeword(_oww_model=mock_model)
    assert silent_calls[0] >= 1  # wake_mic_silent metric emitted


def test_stuck_dc_feed_escalates(monkeypatch):
    """A loud-but-constant (stuck-at-DC) feed has high RMS yet zero stddev — the
    old RMS-floor sentinel would have missed it; the stddev sentinel catches it."""
    from wake_converse import wait_for_wakeword

    t = [0.0]
    monkeypatch.setattr('wake_converse.time.monotonic', lambda: t[0])
    monkeypatch.setattr('wake_converse.time.sleep', lambda *_: None)

    silent_calls = [0]
    import metrics as metrics_mod
    orig_count = metrics_mod.metrics.count
    def spy_count(name, **tags):
        if name == "wake_mic_silent":
            silent_calls[0] += 1
        return orig_count(name, **tags)
    monkeypatch.setattr(metrics_mod.metrics, 'count', spy_count)

    def fake_read(n, exception_on_overflow=False):
        t[0] += 1.0
        return _dc_pcm(n)  # RMS 5000, stddev 0
    _wire_mic(monkeypatch, fake_read)
    _base_config(monkeypatch, wake_silence_alarm_s=5.0, mic_stall_max_reinits=0)

    mock_model = MagicMock()
    mock_model.predict.return_value = {'w': 0.0}

    with pytest.raises(SystemExit):
        wait_for_wakeword(_oww_model=mock_model)
    assert silent_calls[0] >= 1


def test_quiet_room_does_not_escalate(monkeypatch):
    """A quiet-but-varying feed (RMS ~25 < 30 advisory floor, but stddev ~25 >
    5 std floor) must NOT escalate — this is the 2026-07-14 false positive. It
    should emit the log-only wake_mic_degraded metric and keep running."""
    from wake_converse import wait_for_wakeword

    t = [0.0]
    monkeypatch.setattr('wake_converse.time.monotonic', lambda: t[0])

    degraded_calls = [0]
    import metrics as metrics_mod
    orig_count = metrics_mod.metrics.count
    def spy_count(name, **tags):
        if name == "wake_mic_degraded":
            degraded_calls[0] += 1
        return orig_count(name, **tags)
    monkeypatch.setattr(metrics_mod.metrics, 'count', spy_count)

    def fake_read(n, exception_on_overflow=False):
        return _quiet_varying_pcm(n)
    _wire_mic(monkeypatch, fake_read)
    # Advisory warning after 5s of low RMS; std floor still cleared by variance.
    _base_config(monkeypatch, wake_silence_alarm_s=5.0, wake_degraded_warn_s=5.0)

    calls = [0]
    mock_model = MagicMock()
    def fake_predict(arr):
        calls[0] += 1
        t[0] += 10.0  # advance past both windows between frames
        if calls[0] > 50:
            raise KeyboardInterrupt
        return {'w': 0.1}  # never fires the wake word
    mock_model.predict = fake_predict

    # A quiet room must never escalate (no SystemExit/RuntimeError); the loop
    # runs until the KeyboardInterrupt bail, having logged the advisory warning.
    with pytest.raises(KeyboardInterrupt):
        wait_for_wakeword(_oww_model=mock_model)
    assert degraded_calls[0] >= 1  # advisory warning emitted, but no restart


def test_healthy_signal_never_triggers_sentinel(monkeypatch):
    """Loud varying frames keep the stddev sentinel fresh so it never fires even
    over a long virtual run. Time is advanced in predict() (main thread) so each
    processed frame resets the sentinel before the alarm window elapses."""
    from wake_converse import wait_for_wakeword

    t = [0.0]
    monkeypatch.setattr('wake_converse.time.monotonic', lambda: t[0])

    def fake_read(n, exception_on_overflow=False):
        return _loud_pcm(n)
    _wire_mic(monkeypatch, fake_read)
    _base_config(monkeypatch, wake_silence_alarm_s=5.0)

    calls = [0]
    mock_model = MagicMock()
    def fake_predict(arr):
        calls[0] += 1
        t[0] += 10.0  # advance past the alarm window between frames
        if calls[0] > 50:
            raise KeyboardInterrupt
        return {'w': 0.1}  # never fires the wake word
    mock_model.predict = fake_predict

    # Stddev was already checked (and reset) before predict advances the clock,
    # so a healthy varying frame keeps last_std_ok current. Wrong behaviour would
    # surface as a RuntimeError/SystemExit instead of KeyboardInterrupt.
    with pytest.raises(KeyboardInterrupt):
        wait_for_wakeword(_oww_model=mock_model)
