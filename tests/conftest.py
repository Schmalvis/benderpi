"""Shared test fixtures for BenderPi tests."""
import os
import sys

import pytest
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


# ---------------------------------------------------------------------------
# Module-state hygiene for wake_converse
#
# Several test files import wake_converse AFTER replacing config, audio,
# metrics and logger with fakes, which is the only way to import it off-device.
# They do `sys.modules.pop("wake_converse")` before importing, but nothing put
# it back, so the FAKE-config module stayed in sys.modules for the rest of the
# session. The next file to `import wake_converse` silently got that one.
#
# Live consequence, found 2026-10-01: with test_wake_loop_heartbeat.py running
# before test_oww_smoothing.py, the wake loop saw fake thresholds
# (wake_stall_seconds 0.2, mic_stall_max_reinits 0), declared a stall on the
# second frame and raised SystemExit -- five failures and one hang. Alphabetical
# order normally hid it, because "oww" sorts before "wake"; adding any test file
# that sorts earlier exposes it. It also means a random-order run could fail
# for reasons unrelated to the code under test.
#
# So: if a test leaves a wake_converse in sys.modules that was not there when
# it started, drop it. The next importer re-imports against real config.
@pytest.fixture(autouse=True)
def _drop_faked_wake_converse():
    had = "wake_converse" in sys.modules
    before = sys.modules.get("wake_converse")
    yield
    now = sys.modules.get("wake_converse")
    if now is not None and (not had or now is not before):
        sys.modules.pop("wake_converse", None)
