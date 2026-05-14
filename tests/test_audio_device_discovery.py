"""Offline tests for audio.find_input_device / find_output_device."""
import sys
import types

sys.path.insert(0, "scripts")


class FakePA:
    def __init__(self, devices):
        self._devices = devices

    def get_device_count(self):
        return len(self._devices)

    def get_device_info_by_index(self, i):
        return self._devices[i]


def _patch_pyaudio(monkeypatch):
    fake_pyaudio = types.SimpleNamespace(paInt16=8, PyAudio=lambda: FakePA([]))
    monkeypatch.setitem(sys.modules, "pyaudio", fake_pyaudio)


def test_find_input_prefers_named_hint(monkeypatch):
    _patch_pyaudio(monkeypatch)
    import audio
    pa = FakePA([
        {"index": 0, "name": "bcm2835 Headphones", "maxInputChannels": 0, "maxOutputChannels": 2},
        {"index": 1, "name": "seeed-2mic-voicecard", "maxInputChannels": 2, "maxOutputChannels": 2},
        {"index": 2, "name": "mic_shared", "maxInputChannels": 1, "maxOutputChannels": 0},
    ])
    assert audio.find_input_device(pa, ["mic_shared"]) == 2


def test_find_input_falls_back_to_seeed(monkeypatch):
    _patch_pyaudio(monkeypatch)
    import audio
    pa = FakePA([
        {"index": 0, "name": "bcm2835 Headphones", "maxInputChannels": 0, "maxOutputChannels": 2},
        {"index": 3, "name": "seeed-2mic-voicecard", "maxInputChannels": 2, "maxOutputChannels": 2},
    ])
    # No mic_shared — should fall back to seeed
    assert audio.find_input_device(pa, ["mic_shared"]) == 3


def test_find_input_returns_none_when_no_match(monkeypatch):
    _patch_pyaudio(monkeypatch)
    import audio
    pa = FakePA([{"index": 0, "name": "nothing useful", "maxInputChannels": 1, "maxOutputChannels": 0}])
    assert audio.find_input_device(pa, ["missing"]) is None


def test_find_output_skips_input_only_devices(monkeypatch):
    _patch_pyaudio(monkeypatch)
    import audio
    pa = FakePA([
        {"index": 0, "name": "seeed mic-only", "maxInputChannels": 2, "maxOutputChannels": 0},
        {"index": 1, "name": "seeed-2mic-voicecard", "maxInputChannels": 2, "maxOutputChannels": 2},
    ])
    assert audio.find_output_device(pa, ["seeed"]) == 1
