"""Tests for openWakeWord-based wait_for_wakeword()."""
import sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import pytest
import numpy as np
from unittest.mock import MagicMock, patch

# wake_converse imports a chain of heavy first-party modules (stt, leds,
# vision, ...) and system-site-packages (pyaudio, webrtcvad, scipy, ...) that
# are present on BenderPi but absent on dev/CI machines. Stub only what the
# import chain needs so the modules the tests DO exercise (audio, config,
# wake_converse, wait_for_wakeword) import for real.


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


# Third-party libs audio.py / wake_converse's import chain pull in
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

# Heavy first-party modules wake_converse imports but these tests never call.
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


def _pcm_bytes(n_samples: int, channels: int = 1) -> bytes:
    return np.zeros(n_samples * channels, dtype=np.int16).tobytes()


def test_wait_for_wakeword_returns_on_high_score(monkeypatch):
    """Returns as soon as model score reaches threshold."""
    import audio
    from wake_converse import wait_for_wakeword, OWW_FRAME_SIZE

    call_num = [0]
    mock_stream = MagicMock()
    def fake_read(n, exception_on_overflow=False):
        call_num[0] += 1
        return _pcm_bytes(n)
    mock_stream.read = fake_read

    mock_pa = MagicMock()
    mock_pa.get_device_info_by_index.return_value = {'name': 'default_mic'}
    mock_pa.open.return_value = mock_stream

    # Low score for first 2 frames, threshold-crossing on 3rd
    predict_calls = [0]
    mock_model = MagicMock()
    def fake_predict(arr):
        predict_calls[0] += 1
        score = 0.9 if predict_calls[0] >= 3 else 0.1
        return {'hey_jarvis_v0.1': score}
    mock_model.predict = fake_predict

    monkeypatch.setattr(audio, 'get_pa', lambda: mock_pa)
    monkeypatch.setattr(audio, 'get_input_device_index', lambda: 0)
    monkeypatch.setattr('config.cfg.oww_threshold', 0.5)
    monkeypatch.setattr('config.cfg.wake_stall_seconds', 30)
    monkeypatch.setattr('config.cfg.wake_heartbeat_frames', 250)

    wait_for_wakeword(_oww_model=mock_model)

    assert predict_calls[0] >= 3
    assert call_num[0] >= 3


def test_wait_for_wakeword_keeps_looping_below_threshold(monkeypatch):
    """Does not return while all scores remain below threshold."""
    import audio
    from wake_converse import wait_for_wakeword

    frames_read = [0]
    mock_stream = MagicMock()
    def fake_read(n, exception_on_overflow=False):
        frames_read[0] += 1
        if frames_read[0] > 10:
            raise KeyboardInterrupt
        return _pcm_bytes(n)
    mock_stream.read = fake_read

    mock_pa = MagicMock()
    mock_pa.get_device_info_by_index.return_value = {'name': 'default_mic'}
    mock_pa.open.return_value = mock_stream

    mock_model = MagicMock()
    mock_model.predict.return_value = {'hey_jarvis_v0.1': 0.1}  # always below 0.5

    monkeypatch.setattr(audio, 'get_pa', lambda: mock_pa)
    monkeypatch.setattr(audio, 'get_input_device_index', lambda: 0)
    monkeypatch.setattr('config.cfg.oww_threshold', 0.5)
    monkeypatch.setattr('config.cfg.wake_stall_seconds', 30)
    monkeypatch.setattr('config.cfg.wake_heartbeat_frames', 250)

    with pytest.raises(KeyboardInterrupt):
        wait_for_wakeword(_oww_model=mock_model)

    assert frames_read[0] > 5


def test_wait_for_wakeword_stereo_downmix(monkeypatch):
    """Stereo stream (xvf_dsnoop device) is downmixed to mono before predict."""
    import audio
    from wake_converse import wait_for_wakeword, OWW_FRAME_SIZE

    captured_arrays = []
    mock_stream = MagicMock()
    call_count = [0]
    def fake_read(n, exception_on_overflow=False):
        call_count[0] += 1
        return _pcm_bytes(n, channels=2)  # stereo
    mock_stream.read = fake_read

    mock_pa = MagicMock()
    mock_pa.get_device_info_by_index.return_value = {'name': 'xvf_dsnoop_something'}
    mock_pa.open.return_value = mock_stream

    mock_model = MagicMock()
    def fake_predict(arr):
        captured_arrays.append(arr.copy())
        return {'hey_jarvis_v0.1': 0.9}  # trigger on first frame
    mock_model.predict = fake_predict

    monkeypatch.setattr(audio, 'get_pa', lambda: mock_pa)
    monkeypatch.setattr(audio, 'get_input_device_index', lambda: 0)
    monkeypatch.setattr('config.cfg.oww_threshold', 0.5)
    monkeypatch.setattr('config.cfg.wake_stall_seconds', 30)
    monkeypatch.setattr('config.cfg.wake_heartbeat_frames', 250)

    wait_for_wakeword(_oww_model=mock_model)

    assert len(captured_arrays) >= 1
    # Stereo 1280-frame read = 2560 samples; after downmix → 1280 samples
    assert len(captured_arrays[0]) == OWW_FRAME_SIZE
