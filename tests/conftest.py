"""Shared test fixtures for BenderPi tests."""
import os
import sys
import types

# Ensure scripts/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# python-dotenv is present on BenderPi but absent on some dev/CI machines.
# wake_converse imports it at module top, so stub it if missing so the whole
# suite is runnable off-device. Real dotenv (when installed) is left untouched.
if "dotenv" not in sys.modules:
    try:
        import dotenv  # noqa: F401
    except ImportError:
        _dotenv = types.ModuleType("dotenv")
        _dotenv.dotenv_values = lambda *a, **k: {}
        _dotenv.load_dotenv = lambda *a, **k: False
        sys.modules["dotenv"] = _dotenv


# --------------------------------------------------------------------------
# Hardware stubs
# --------------------------------------------------------------------------
# leds.py imports board/busio/neopixel_spi at module scope; those ship with
# adafruit-blinka and only exist in a --system-site-packages venv on the Pi.
# Anything that transitively imports leds (wake_converse, session, the whole
# web app) therefore died at *collection* on a dev box, which is most of why
# the suite had a permanent red wall. Stubbed centrally rather than per-file so
# a new test importing leds doesn't rediscover this.
#
# Real modules are left untouched when present, so the Pi still exercises the
# genuine article.
class _PermissiveModule(types.ModuleType):
    """Answers any attribute with a MagicMock.

    Enumerating pin names (board.SCK, board.MOSI, board.D10, ...) is a losing
    game — a stub that must be extended every time hardware code touches a new
    constant is just a slower version of the original problem.
    """

    def __getattr__(self, item):
        from unittest.mock import MagicMock

        value = MagicMock(name=f"{self.__name__}.{item}")
        setattr(self, item, value)
        return value


def _stub(name: str) -> None:
    if name in sys.modules:
        return
    try:
        __import__(name)          # real hardware libs win when present (on the Pi)
    except Exception:
        sys.modules[name] = _PermissiveModule(name)


for _hw in ("board", "busio", "neopixel_spi", "adafruit_blinka", "cv2"):
    _stub(_hw)

# pyaudio gets a CONCRETE stub, not a permissive one: audio.py does arithmetic
# and comparisons on what the API returns (e.g. `d.get("maxOutputChannels", 0)
# <= 0` in _list_devices), and a MagicMock raises TypeError against an int.
# An empty PyAudio() object also makes device enumeration come back empty,
# which is what the per-file stubs in test_audio_pure/test_mic_reader relied on.
if "pyaudio" not in sys.modules:
    try:
        import pyaudio  # noqa: F401
    except ImportError:
        _pa = types.ModuleType("pyaudio")
        _pa.paInt16 = 8
        _pa.paContinue = 0
        _pa.paComplete = 1
        _pa.PyAudio = lambda *a, **k: types.SimpleNamespace()
        sys.modules["pyaudio"] = _pa
