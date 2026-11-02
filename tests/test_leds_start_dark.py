"""bender-converse sends the mouth an off frame at start and at exit.

The strip keeps its power-on colour until it gets data. On 2026-11-02 it sat
solid blue from the 07:00 boot because no session ran all morning.
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import wake_converse  # noqa: E402


def test_start_sends_off_and_exit_sends_it_again(monkeypatch):
    off = MagicMock()
    registered = []
    monkeypatch.setattr(wake_converse.leds, "all_off", off)
    monkeypatch.setattr(wake_converse.atexit, "register",
                        lambda fn, *a: registered.append((fn, a)))
    wake_converse._leds_dark()
    assert off.call_count == 1
    assert len(registered) == 1
    fn, args = registered[0]
    fn(*args)
    assert off.call_count == 2


def test_a_dead_strip_does_not_stop_the_service(monkeypatch):
    registered = []
    monkeypatch.setattr(wake_converse.leds, "all_off",
                        MagicMock(side_effect=OSError("spi gone")))
    monkeypatch.setattr(wake_converse.atexit, "register",
                        lambda fn, *a: registered.append((fn, a)))
    wake_converse._leds_dark()  # must not raise
    fn, args = registered[0]
    fn(*args)  # nor at exit


def test_main_does_it_first_and_always_exits_through_python_on_sigterm(
        monkeypatch):
    # Run main() until the first call after the LED and SIGTERM set-up. In
    # cloud_only mode the SIGTERM handler used to be missing, so a stop killed
    # the process with no atexit and the mouth kept its last level.
    calls = []

    class _Stop(Exception):
        pass

    monkeypatch.setattr(wake_converse, "_leds_dark", lambda: calls.append("leds"))
    monkeypatch.setattr(wake_converse.signal, "signal",
                        lambda sig, fn: calls.append(("signal", sig)))

    def _validate():
        calls.append("validate")
        raise _Stop

    monkeypatch.setattr(wake_converse.cfg, "validate", _validate)
    monkeypatch.setattr(wake_converse.cfg, "ai_backend", "cloud_only")
    try:
        wake_converse.main()
    except _Stop:
        pass
    assert calls == ["leds", ("signal", wake_converse.signal.SIGTERM), "validate"]
