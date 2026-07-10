"""Unit tests for leds.set_alert_flash()'s start/stop concurrency guard.

set_alert_flash() used to gate _alert_loop thread creation on a bare
`_alert_active` bool + an unlocked `is_alive()` check: rapid on/off/on
toggling could race two check-then-start calls into starting two
concurrent `_alert_loop` threads fighting over the same SPI/NeoPixel bus.
The fix adds a lock around start/stop plus a stop Event that off() joins
on, so a new loop can never start while the previous one is still
exiting. These tests exercise that invariant without real LED hardware.
"""
import sys
import threading
import types

import pytest

sys.path.insert(0, "scripts")

# leds.py imports board/busio/neopixel_spi (hardware SPI libs) at module
# level — stub them so `import leds` succeeds off-device.
_fake_board = types.SimpleNamespace(SCK=object(), MOSI=object())
_fake_busio = types.SimpleNamespace(SPI=lambda *a, **k: types.SimpleNamespace())


class _FakeNeoPixel:
    """Records fill()/show() calls; not thread-safe by design, same as the
    real neopixel_spi.NeoPixel_SPI, so overlapping loops would corrupt
    `.calls` interleaving if the concurrency guard failed."""

    def __init__(self, *a, **k):
        self.calls = []
        self._lock = threading.Lock()

    def fill(self, colour):
        with self._lock:
            self.calls.append(("fill", colour))

    def show(self):
        with self._lock:
            self.calls.append(("show", None))


_fake_neopixel_spi = types.SimpleNamespace(NeoPixel_SPI=_FakeNeoPixel)

sys.modules.setdefault("board", _fake_board)
sys.modules.setdefault("busio", _fake_busio)
sys.modules.setdefault("neopixel_spi", _fake_neopixel_spi)

import leds  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_alert_state():
    """Ensure each test starts with the flasher fully stopped."""
    leds.set_alert_flash(False)
    yield
    leds.set_alert_flash(False)


class TestSetAlertFlash:
    def test_on_starts_a_live_thread(self):
        leds.set_alert_flash(True)
        assert leds._alert_thread is not None
        assert leds._alert_thread.is_alive()

    def test_off_stops_and_clears_the_thread(self):
        leds.set_alert_flash(True)
        leds.set_alert_flash(False)
        assert leds._alert_thread is None

    def test_off_blocks_until_loop_actually_exits(self):
        leds.set_alert_flash(True)
        thread_ref = leds._alert_thread
        leds.set_alert_flash(False)
        # join() inside set_alert_flash(False) must have already returned —
        # the thread must be dead by the time off() gives control back.
        assert not thread_ref.is_alive()

    def test_second_on_call_while_running_is_idempotent(self):
        leds.set_alert_flash(True)
        first_thread = leds._alert_thread
        leds.set_alert_flash(True)
        assert leds._alert_thread is first_thread

    def test_rapid_off_on_never_leaves_two_live_threads(self):
        # Regression test for the race: toggle on/off repeatedly and verify
        # exactly one _alert_loop thread is tracked and alive after each
        # on(), and none after each off() — never two live loops at once.
        seen_threads = []  # strong refs — id() can be recycled once GC'd
        for _ in range(20):
            leds.set_alert_flash(True)
            assert leds._alert_thread.is_alive()
            seen_threads.append(leds._alert_thread)
            leds.set_alert_flash(False)
            assert leds._alert_thread is None
        # Each on()/off() cycle joined its thread before returning, so a
        # fresh thread object was created each time (no thread reuse/leak),
        # and none of them are still alive afterwards.
        assert len({t for t in seen_threads}) == 20
        assert all(not t.is_alive() for t in seen_threads)

    def test_off_when_never_started_is_a_noop(self):
        # No prior on() call — off() must not raise.
        leds.set_alert_flash(False)
        assert leds._alert_thread is None
