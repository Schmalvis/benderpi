"""Presence gate for Halloween open-mic mode — VL53L0X time-of-flight sensor.

WHY THIS EXISTS
---------------
Halloween mode drops the wake word: `wake_converse._halloween_loop` treats any
speech clearing the capture gates as the start of a turn. That is right for a
doorstep and wrong for everything else nearby.

Measured 2026-10-10, replaying 5.25 hours of recorded speech near the array
through the real capture gates (`scripts/stt.py`):

    sessions started      465
    per hour             88.6
    share of time busy    79%

A false trigger does not just make noise. It makes Bender deaf: a spurious
session runs a greeting, one reply and two empty 6s captures before it times
out, so the child who actually arrived is ignored for 22-45 seconds.

`stt_min_speech_rms` cannot gate this, although the Halloween plan assumed it
could. The same measurement found `voiced_rms` does not track distance on the
XVF3800 array -- `close_normal` has the LOWEST median of any condition (342)
and `seated_far` reads four times louder (1432). A level floor rejects the
child at the door before it rejects the voice across the room: at 1200 it cuts
57% of far-field speech and 56% of near-field speech too.

So the gate has to measure distance, not loudness. The camera cannot do it: the
board is a Camera Module 3 (IMX708) with no on-sensor accelerator, and a
doorstep on 31 October is dark.

DESIGN RULES
------------
1. **Fail open.** A missing, unwired or broken sensor must never make Bender
   deaf on the night. `available()` goes False and the caller keeps its old
   behaviour. The owner's stated worst failure is silence, so a chatty Bender
   beats a silent one.
2. **Never block the watchdog.** The service is `Type=notify` with
   `WatchdogSec=120`. `wait_for_presence()` takes a bounded timeout and returns,
   so the caller's loop keeps calling `_note_progress()`.
3. **Level, not edge.** A child who stands and talks for three turns re-enters
   the trigger loop each time, so the gate reports "someone is there", not
   "someone just arrived". Hysteresis stops boundary flapping.
4. **Confirm reads.** One stray reading must not open the mic, the same reason
   `oww_frames_required` exists for the wake model.
5. **Distrust short readings.** The sensor cannot tell Bender's own eye socket
   from a child -- it times its own IR pulse back, so any enclosure surface in
   the ~25-degree cone reads as a near target. That failure latches the gate
   OPEN while looking exactly like a working sensor, so `presence_min_mm`
   discards it. Mount with an open aperture and no window: a cover needs
   crosstalk calibration the Adafruit driver does not expose.

PUBLIC API
----------
available()                  -> bool   sensor usable right now
distance_mm()                -> int|None  last good reading, None if absent
present()                    -> bool   someone within `presence_max_mm`
wait_for_presence(timeout_s) -> bool   poll until present, or timeout
close()                      -> None   release the sensor (atexit)
"""
from __future__ import annotations

import atexit
import threading
import time

from config import cfg
from logger import get_logger
# The SINGLETON, not the module. `import metrics` binds the module, whose
# `count` lives on the MetricsWriter class, so every `metrics.count(...)` here
# raised AttributeError. It crashed `_open_locked()` on the first real run
# against the sensor, 2026-10-13, and the fail-open rule does not cover it: the
# error propagates out of `available()` and takes `_halloween_loop` down. Every
# other module in scripts/ already imports it this way. See
# TestMetricsSingletonIsImported, which checks this without monkeypatching.
from metrics import metrics

log = get_logger("presence")

# The driver reports a large value, or 0, when nothing reflects inside the
# ranging window. Treat either as "nobody there", never as a near reading.
_OUT_OF_RANGE_MM = 8000

# Consecutive read failures before the sensor is declared dead for this
# process. Deliberately small: a wedged I2C device should stop being polled,
# not be retried forever in the trigger loop.
_MAX_ERRORS = 5

# Seconds to wait after XSHUT is released before talking to the sensor.
# MEASURED on-device 2026-10-13: the sensor answers at 0x29 two milliseconds
# after the release, 5 of 5 times. 50 ms is a 25x margin, paid once per open.
_XSHUT_BOOT_S = 0.05

_sensor = None
_lock = threading.Lock()
_dead = False            # no XSHUT, or resets exhausted: restart to recover
_errors = 0
_last_mm = None
_present = False         # hysteresis state
_confirm_run = 0         # consecutive reads agreeing with the pending change
_warned_unavailable = False
_resets = 0              # XSHUT power-cycles used this process
_xshut = None            # digitalio.DigitalInOut, lazily created
_warned_bad_band = False  # hysteresis band inverted; warn once, not per read


def _cfg(key: str, default):
    """`cfg` value for `key`, with None treated as "not set".

    `getattr(cfg, key, default)` returns None when the attribute EXISTS and
    holds None, because the default only applies to a MISSING attribute. And
    `config.py`'s `_override_type_ok` accepts None for every key by design, so
    a `"presence_confirm_reads": null` in bender_config.json reaches this
    module intact. The Config page writes exactly that if someone clears the
    field: a Svelte `type="number"` input binds an empty box to null.

    `int(None)` then raises TypeError out of `present()`. **Fail-open does not
    cover that.** Fail-open catches exceptions from the sensor, inside
    `distance_mm()`. A TypeError here propagates past it and takes
    `_halloween_loop` down -- the same shape as the `import metrics` crash.
    """
    value = getattr(cfg, key, None)
    return default if value is None else value


def _enabled() -> bool:
    return bool(_cfg("presence_sensor_enabled", False))


def _xshut_line():
    """The XSHUT output, or None when the pin is not wired or unusable.

    Active low: drive it low to shut the sensor down, release it high to boot.
    """
    global _xshut
    pin_no = int(_cfg("presence_xshut_gpio", 0))
    if not pin_no:
        return None
    if _xshut is not None:
        return _xshut
    try:
        import board
        import digitalio
        line = digitalio.DigitalInOut(getattr(board, f"D{pin_no}"))
        line.switch_to_output(value=True)      # released = sensor running
        _xshut = line
    except Exception as exc:
        log.warning("XSHUT on GPIO %d is unusable: %s", pin_no, exc)
        metrics.count("presence_xshut_unavailable", gpio=pin_no,
                      error=type(exc).__name__)
        return None
    return _xshut


def _pulse_xshut(line) -> None:
    """Power-cycle the sensor: drive XSHUT low, then release it high.

    Driving an already-high pin high is not a reset. That distinction cost a
    whole afternoon on 2026-10-13 and it is the reason this helper exists.

    MEASURED on-device that day. The sensor wedged during a probe run: it
    answered its address, returned a stale 8190 for three reads, then raised
    `OSError [Errno 121]` (a bus NACK) and after that timed out on every read,
    35 in a row, with no recovery. Three further probe processes saw the exact
    same failure at the exact same read, because:

      - `digitalio` leaves the line in its last state when the process exits.
        `pinctrl get 17` read `op dh pd | hi` with nothing running.
      - So every new process found XSHUT already high and drove it high again.
      - The wedged sensor was never reset, and looked identically broken
        forever.

    A low pulse is the only way back. The sensor carries no other reset.

    `_XSHUT_BOOT_S` is 50 ms. Measured boot after the release is 2 ms, 5 of 5
    at every delay from 2 ms to 400 ms, so that is a 25x margin. The same wait
    serves as the low time: the datasheet asks for a minimum of 100 us.
    """
    line.value = False              # active low: sensor off
    time.sleep(_XSHUT_BOOT_S)
    line.value = True               # release: sensor boots at 0x29
    time.sleep(_XSHUT_BOOT_S)


def _io_timeout_s(budget_us: int) -> float:
    """Seconds to wait for one measurement before declaring the read failed.

    The Adafruit driver polls the sensor's status registers in FIVE places and
    bounds none of them unless `io_timeout_s` is set. It defaults to 0, which
    means off. So a sensor that stops asserting its interrupt hangs the caller
    for good, in uninterruptible I2C transfers, with no log line and no error.

    MEASURED on-device 2026-10-13, minutes after the sensor was mounted behind
    the eye: `probe_vl53l0x.py --baseline` sat in `read_range()`'s poll loop
    for over 3 minutes in process state D (`wchan: i2c_dw_xfer`). It printed
    nothing. It would never have returned.

    A hang is worse here than an error. `_halloween_loop` would block forever,
    stop calling `_note_progress()`, miss the `WatchdogSec=120` ping, and
    systemd would kill and restart the service. The fail-open rule only covers
    exceptions, so an unbounded poll defeats it entirely.

    Three measurement budgets plus 200 ms. The budget IS the measurement time,
    so one budget is the expected wait, and three leaves room for a retry
    inside the driver. The 0.5 s floor covers the constructor's own reference
    calibration, which runs BEFORE `measurement_timing_budget` is applied and
    so uses the driver's ~33 ms default.
    """
    return max(0.5, 3.0 * (budget_us / 1e6) + 0.2)


def _reset_via_xshut() -> bool:
    """Power-cycle the sensor through XSHUT and re-open it.

    Mirrors the XVF3800 REBOOT recovery: a wedged device gets cycled in place
    rather than waiting for a human. Returns True when the sensor answers
    again. Caller must NOT hold _lock.
    """
    global _sensor, _dead, _errors, _resets
    line = _xshut_line()
    if line is None:
        return False
    limit = int(_cfg("presence_max_resets", 3))
    if _resets >= limit:
        log.error("Presence sensor has used all %d XSHUT resets. "
                  "Leaving the gate open for the rest of this process.", limit)
        metrics.count("presence_resets_exhausted", resets=_resets)
        return False

    _resets += 1
    log.warning("Resetting the presence sensor over XSHUT (attempt %d of %d)",
                _resets, limit)
    metrics.count("presence_sensor_reset", attempt=_resets)
    try:
        _pulse_xshut(line)
    except Exception as exc:
        log.error("XSHUT toggle failed: %s", exc)
        metrics.count("presence_xshut_error", error=type(exc).__name__)
        return False

    with _lock:
        _sensor = None
        _dead = False               # give _open_locked a clean run
        _errors = 0
        _sensor = _open_locked()
        recovered = _sensor is not None
        if not recovered:
            _dead = True
    if recovered:
        log.info("Presence sensor recovered after an XSHUT reset")
        metrics.count("presence_sensor_recovered", attempt=_resets)
    else:
        metrics.count("presence_sensor_reset_failed", attempt=_resets)
    return recovered


def _open_locked():
    """Instantiate the sensor. Caller must hold _lock. Returns None on failure."""
    global _dead
    address = int(_cfg("presence_i2c_address", 0x29))

    # XSHUT first, before any I2C traffic. It is active low, and the breakout
    # carries no pull-up on it, so a GPIO left as an input rests on its own
    # internal pull-down and holds the sensor in reset. MEASURED on-device
    # 2026-10-13 with the sensor wired to GPIO 17: a bus scan returned only
    # 0x1a, the codec. With the pin driven high the same scan returned 0x29,
    # and the identification registers read 0xEE / 0xAA / 0x10.
    #
    # This used to happen only inside _reset_via_xshut(), which needs
    # _MAX_ERRORS consecutive READ failures to run. No read ever started,
    # because the failed open set _dead and _get_sensor() then returned None
    # for the life of the process. So a correctly wired sensor stayed dead.
    #
    # The pin must be PULSED, not just driven high. A sensor left wedged by a
    # previous process keeps XSHUT high across the process exit, so driving it
    # high changes nothing and the sensor stays wedged for ever. See
    # _pulse_xshut() for the on-device measurement that proved it.
    _line = _xshut_line()
    if _line is not None:
        try:
            _pulse_xshut(_line)
        except Exception as exc:
            log.warning("XSHUT pulse failed on open: %s", exc)
            metrics.count("presence_xshut_error", error=type(exc).__name__)
    try:
        # Lazy imports: the module must import on a dev clone with no Blinka,
        # and tests inject a fake sensor instead.
        import board
        import busio
        import adafruit_vl53l0x
    except Exception as exc:
        log.warning("Presence sensor unavailable (no Blinka/driver): %s", exc)
        metrics.count("presence_sensor_unavailable", reason="import",
                      error=type(exc).__name__)
        _dead = True
        return None

    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        budget = int(_cfg("presence_timing_budget_us", 200000))
        # io_timeout_s must be passed to the constructor, not set after it:
        # the constructor's own calibration uses two of the five unbounded
        # poll loops. See _io_timeout_s().
        sensor = adafruit_vl53l0x.VL53L0X(
            i2c, address=address, io_timeout_s=_io_timeout_s(budget))
        # The signal rate limit is the minimum return rate the sensor accepts
        # as a real echo. Lowering it accepts weaker echoes, so it reaches
        # further off a poor reflector. A person in clothing is a poor
        # reflector: the 2000 mm in the datasheet is for a white target.
        #
        # MEASURED on-device 2026-10-13, sensor mounted behind the left eye,
        # walking in from 3 m and back out. All runs 0 errors:
        #
        #   0.25 MCPS (driver default)  reliable to  641 mm, max  872 mm
        #   0.0938 MCPS                 reliable to 1196 mm, max 1267 mm
        #
        # So the default reaches barely past arm's reach, and this nearly
        # doubles it. Crosstalk did NOT come back at the lower limit: the
        # empty-field baseline read 0 of 135 in range. That had to be checked,
        # because a weaker echo threshold can start accepting the enclosure's
        # own reflection, which latches the gate OPEN for ever.
        #
        # Set to 0 or null to leave the driver default alone.
        #
        # ST's long-range profile (AN4545) also changes the VCSEL pulse period
        # to 18/14 from 14/10. The Adafruit driver does not expose that, so
        # this is as far as the public API reaches.
        rate = float(_cfg("presence_signal_rate_mcps", 0) or 0)
        if rate > 0:
            sensor.signal_rate_limit = rate
        if budget:
            # Shorter is better here, and that was not obvious. A longer budget
            # is the usual advice for reach, but measured on this build the
            # 200 ms budget reached 1267 mm against 1233 mm at 500 ms, and read
            # every 0.40 s against 0.70 s. So 500 ms bought nothing but delay:
            # at `presence_confirm_reads` of 2 the gate armed in 1.40 s instead
            # of 0.80 s. The signal rate limit above does the reaching.
            sensor.measurement_timing_budget = budget
    except Exception as exc:
        log.error("Presence sensor at %s failed to initialise: %s",
                  hex(address), exc)
        metrics.count("presence_sensor_unavailable", reason="init",
                      error=type(exc).__name__)
        _dead = True
        return None

    log.info("Presence sensor ready at %s (budget %s us, io timeout %.2f s, "
             "signal rate %s MCPS, gate <= %d mm)",
             hex(address), getattr(sensor, "measurement_timing_budget", "?"),
             _io_timeout_s(budget),
             getattr(sensor, "signal_rate_limit", "?"),
             int(_cfg("presence_max_mm", 2000)))
    metrics.count("presence_sensor_ready", address=hex(address))
    return sensor


def _get_sensor():
    """Return the sensor, opening it on first use. None when unusable."""
    global _sensor
    if _dead or not _enabled():
        return None
    with _lock:
        if _sensor is None and not _dead:
            _sensor = _open_locked()
        return _sensor


def available() -> bool:
    """True when the sensor is enabled, present and not declared dead."""
    global _warned_unavailable
    if not _enabled():
        return False
    ok = _get_sensor() is not None
    if not ok and not _warned_unavailable:
        _warned_unavailable = True
        log.warning("Presence gate is configured but the sensor is unusable. "
                    "Falling back to the open mic with no distance gate.")
    return ok


def distance_mm() -> int | None:
    """One reading in millimetres. None when nothing is in range, or on error.

    Marks the sensor dead after `_MAX_ERRORS` consecutive failures, so a wedged
    device stops being polled instead of being retried every loop pass.
    """
    global _errors, _dead, _last_mm
    sensor = _get_sensor()
    if sensor is None:
        return None
    try:
        mm = int(sensor.range)
    except Exception as exc:
        _errors += 1
        metrics.count("presence_read_error", error=type(exc).__name__,
                      consecutive=_errors)
        if _errors >= _MAX_ERRORS:
            _dead = True
            log.error("Presence sensor failed %d reads in a row (%s).",
                      _errors, exc)
            metrics.count("presence_sensor_dead", error=type(exc).__name__)
            # XSHUT, when wired, power-cycles it in place rather than waiting
            # for a human -- the same move as the XVF3800 REBOOT recovery.
            _reset_via_xshut()
        return None
    _errors = 0
    if mm <= 0 or mm >= _OUT_OF_RANGE_MM:
        _last_mm = None
        return None
    # Crosstalk floor. The sensor times its own IR pulse back, so it cannot
    # tell Bender's eye socket from a child. A recessed mounting returns a
    # short, stable reading with nobody there, and that would latch the gate
    # OPEN forever while looking exactly like a working sensor. Set from
    # `probe_vl53l0x.py --baseline`.
    floor = int(_cfg("presence_min_mm", 0))
    if floor and mm <= floor:
        metrics.count("presence_below_floor", mm=mm, floor=floor)
        _last_mm = None
        return None
    _last_mm = mm
    return mm


def present() -> bool:
    """True when someone is inside the gate, with hysteresis and confirmation.

    Entering needs `presence_confirm_reads` readings at or under
    `presence_max_mm`. Leaving needs the same number beyond
    `presence_clear_mm` (or out of range), so a person at the boundary does not
    flap the gate open and shut.
    """
    global _present, _confirm_run, _warned_bad_band
    near_mm  = int(_cfg("presence_max_mm", 2000))
    clear_mm = int(_cfg("presence_clear_mm", near_mm + 400))
    needed   = max(1, int(_cfg("presence_confirm_reads", 2)))

    if clear_mm <= near_mm:
        # Hysteresis only works one way round, and the release distance must sit
        # ABOVE the gate distance. `config_schema.py` refuses a save that breaks
        # that, but it cannot judge a value it was never sent: clearing the gate
        # distance box on the Config page sends null, `presence_max_mm` falls
        # back to the 2000 mm code default here, and the shipped
        # `presence_clear_mm` of 1400 then sits UNDER it. A child at 1800 mm
        # would be inside the gate and past the release band at the same time,
        # so the mic would flap open and shut on every read.
        #
        # Collapsing the band is the safe degenerate case: the gate opens at or
        # under `near_mm` and closes above it, and `presence_confirm_reads`
        # still absorbs a reading sitting exactly on the line.
        if not _warned_bad_band:
            _warned_bad_band = True
            log.warning("presence_clear_mm (%d) is not above presence_max_mm "
                        "(%d); collapsing the hysteresis band to %d mm",
                        clear_mm, near_mm, near_mm)
            metrics.count("presence_bad_band", near_mm=near_mm,
                          clear_mm=clear_mm)
        clear_mm = near_mm

    mm = distance_mm()
    if _present:
        # Out of range counts as gone: nothing reflected inside the window.
        leaving = mm is None or mm > clear_mm
        _confirm_run = _confirm_run + 1 if leaving else 0
        if leaving and _confirm_run >= needed:
            _present = False
            _confirm_run = 0
            metrics.count("presence_left", mm=mm if mm is not None else -1)
    else:
        arriving = mm is not None and mm <= near_mm
        _confirm_run = _confirm_run + 1 if arriving else 0
        if arriving and _confirm_run >= needed:
            _present = True
            _confirm_run = 0
            metrics.count("presence_arrived", mm=mm)
    return _present


def wait_for_presence(timeout_s: float) -> bool:
    """Poll until someone is inside the gate, or until `timeout_s` elapses.

    Bounded on purpose. The caller loops, so it keeps feeding the systemd
    watchdog and keeps serving puppet say-requests between passes.

    Returns True when the gate is open. Returns True immediately when the
    sensor is unusable, so a broken sensor cannot make Bender deaf.
    """
    if not available():
        return True                      # fail open -- see DESIGN RULES 1
    poll = max(0.02, float(_cfg("presence_poll_s", 0.1)))
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        if present():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


def close() -> None:
    """Release the sensor and the XSHUT line. Idempotent. Registered atexit."""
    global _sensor, _xshut
    line, _xshut = _xshut, None
    if line is not None:
        try:
            line.deinit()
        except Exception:
            pass
    with _lock:
        sensor = _sensor
        _sensor = None
    if sensor is None:
        return
    # The Adafruit driver holds no OS handle of its own: it talks through the
    # shared Blinka I2C bus, which `camera.py`-style teardown does not own.
    # Dropping the reference is the whole teardown.
    log.info("Presence sensor released")


atexit.register(close)


def _reset_for_tests() -> None:
    """Clear module state. Tests only."""
    global _sensor, _dead, _errors, _last_mm, _present, _confirm_run
    global _warned_unavailable, _resets, _xshut, _warned_bad_band
    _warned_bad_band = False
    _resets = 0
    _xshut = None
    _sensor = None
    _dead = False
    _errors = 0
    _last_mm = None
    _present = False
    _confirm_run = 0
    _warned_unavailable = False
