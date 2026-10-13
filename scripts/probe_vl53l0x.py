#!/usr/bin/env python3
"""Probe a VL53L0X time-of-flight sensor. Standalone, read-only, no config.

WHY
---
Halloween mode opens the mic with no wake word, so any nearby speech starts a
session. Measured 2026-10-10 on 5.25 hours of recorded speech near the array:
88.6 sessions per hour, and Bender in a session 79% of the time. An RMS floor
cannot gate it -- the same measurement found `voiced_rms` does not track
distance on the XVF3800 (close_normal median 342, seated_far median 1432).

A distance sensor gates it properly. The IMX708 cannot: it has no on-sensor
accelerator, and a doorstep on 31 October is dark.

This script answers three questions before any of that is built:
  1. Is the sensor on the bus at all?
  2. What does it read with nobody there, and with a person at the door?
  3. Does it see a person at the 2m the doorstep needs?

USAGE (on the device -- does NOT stop bender-converse)
    venv/bin/pip install adafruit-circuitpython-vl53l0x
    venv/bin/python scripts/probe_vl53l0x.py --xshut 17            # live readings
    venv/bin/python scripts/probe_vl53l0x.py --xshut 17 --scan     # list the bus
    venv/bin/python scripts/probe_vl53l0x.py --xshut 17 --baseline # RUN THIS FIRST
    venv/bin/python scripts/probe_vl53l0x.py --xshut 17 --seconds 60 --histogram

Pass --xshut whenever XSHUT is wired to a GPIO. The pin is active low with no
pull-up, so an undriven GPIO holds the sensor in reset and it looks absent.

WIRING (VL53L0X -> Pi 5 40-pin header)
    VIN   -> 3V3   (pin 1)       SDA   -> GPIO 2 / SDA (pin 3)
    GND   -> GND   (pin 6)       SCL   -> GPIO 3 / SCL (pin 5)
    XSHUT -> GPIO 17 (pin 11)    GPIO1 -> nothing (interrupt, unused)
A ReSpeaker 2-Mic HAT with a Grove I2C connector carries the same bus, which
avoids soldering to an occupied header. Bus 1 currently holds only 0x1a (the
WM8960 codec), so the sensor's default 0x29 does not clash.

MOUNTING
--------
Flush at the surface, with an open aperture and NO window over it. The sensor
times its own IR pulse back, so it cannot tell Bender's eye socket from a
child: recess it in the head cavity and it reads the cavity. That latches the
presence gate OPEN forever while looking exactly like a working sensor. The
left eye's blank pupil square mirrors the camera in the right eye and puts the
sensor flush. Run --baseline after mounting, before anything depends on it.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time

# Nothing here imports bender modules. The probe has to work on a device where
# the service is wedged, so it must not depend on project config or logging.


def lock_bus(i2c, timeout_s: float = 2.5):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if i2c.try_lock():
            return True
        time.sleep(0.05)
    return False


def release_xshut(gpio: int):
    """Drive the breakout's active-low XSHUT pin high, then wait for boot.

    XSHUT carries no pull-up on this breakout. A Raspberry Pi GPIO left as an
    input rests on its own internal pull-down, so the pin sits low and holds
    the sensor in reset. A bus scan then returns only 0x1a, the codec, and
    the sensor looks absent while being wired correctly.

    MEASURED on-device 2026-10-13 with the sensor on GPIO 17: the sensor
    answers at 0x29 two milliseconds after the release, 5 of 5 times, at
    every delay from 2 ms to 400 ms. 50 ms is a 25x margin.

    Returns the held line. The caller MUST keep the reference. Dropping it
    releases the pin, and the sensor falls back into reset.
    """
    import board
    import digitalio
    line = digitalio.DigitalInOut(getattr(board, f"D{gpio}"))
    line.switch_to_output(value=True)
    time.sleep(0.05)
    return line


def scan(i2c) -> list:
    if not lock_bus(i2c):
        raise SystemExit("could not lock the I2C bus (another process holds it)")
    try:
        return sorted(i2c.scan())
    finally:
        i2c.unlock()


def baseline_check(sensor, args) -> int:
    """Measure what the sensor reads with nothing in front of it.

    This is the check that matters for a sensor mounted inside Bender's head.
    The VL53L0X times its own IR pulse back, so it cannot tell an eye socket
    from a child. A recessed mounting returns a short, STABLE reading with
    nobody there. The gate then latches open forever while looking exactly
    like a working sensor -- the worst failure available, because it is silent.

    A good mounting reads out of range here. A bad one reads a steady number.
    """
    print()
    print("BASELINE / CROSSTALK CHECK")
    print("Clear the field in front of Bender. Nothing within 3 metres.")
    print("Point him at open space, not at a wall.")
    print()
    input("Press Enter when the field is clear... ")

    reads, in_range, errors = [], 0, 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.seconds:
        try:
            mm = sensor.range
        except Exception as exc:
            errors += 1
            print(f"  read error: {type(exc).__name__}: {exc}")
            time.sleep(args.interval)
            continue
        reads.append(mm)
        if 0 < mm < 8000:
            in_range += 1
        time.sleep(args.interval)

    total = len(reads)
    print()
    print(f"samples {total}   errors {errors}")
    if not total:
        print("FAIL: no readings at all.")
        return 1

    near = [m for m in reads if 0 < m < 8000]
    pct = 100.0 * in_range / total
    print(f"readings that came back IN RANGE with an empty field: "
          f"{in_range}/{total} ({pct:.0f}%)")

    if not near:
        print()
        print("PASS. The sensor sees nothing when nothing is there.")
        print("Leave presence_min_mm at 0.")
        return 0

    lo, hi = min(near), max(near)
    med = int(statistics.median(near))
    spread = hi - lo
    print(f"their range: min {lo} mm, median {med} mm, max {hi} mm "
          f"(spread {spread} mm)")
    print()

    if pct >= 50 and spread <= 200:
        print("FAIL: the sensor is looking at Bender, not at the doorstep.")
        print(f"  A steady {med} mm with an empty field is the enclosure.")
        print("  Fix the MOUNTING first. Move the sensor flush to the surface,")
        print("  widen the aperture, and remove any window over it.")
        print(f"  If you cannot, set presence_min_mm above {hi} and re-run.")
        print("  Only do that if a person never stands that close.")
        return 1

    print("MARGINAL: some stray readings, but not a steady one.")
    print(f"  Set presence_min_mm to {hi} to discard them.")
    print("  Then re-run this check and confirm it passes.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true",
                    help="list I2C addresses and exit")
    ap.add_argument("--xshut", type=int, default=0,
                    help="GPIO driving the breakout's active-low XSHUT pin. "
                         "Without this the pin rests on the Pi's internal "
                         "pull-down and the sensor stays in reset. "
                         "This build wires it to GPIO 17.")
    ap.add_argument("--seconds", type=float, default=20.0,
                    help="how long to sample (default 20)")
    ap.add_argument("--interval", type=float, default=0.2,
                    help="seconds between reads (default 0.2)")
    ap.add_argument("--address", type=lambda s: int(s, 0), default=0x29,
                    help="I2C address (default 0x29)")
    ap.add_argument("--budget-us", type=int, default=0,
                    help="measurement timing budget in microseconds; "
                         "higher is slower but reaches further (try 200000)")
    ap.add_argument("--histogram", action="store_true",
                    help="print a distance histogram at the end")
    ap.add_argument("--baseline", action="store_true",
                    help="CROSSTALK CHECK: sample with NOTHING in front of the "
                         "sensor and report whether the enclosure is visible "
                         "to it. Run this first, before any live test.")
    args = ap.parse_args()

    try:
        import board
        import busio
    except Exception as exc:
        print(f"FAIL: cannot import Blinka (board/busio): {exc}")
        print("      The venv needs --system-site-packages. See CLAUDE.md.")
        return 2

    if args.xshut:
        try:
            # Held for the life of the process. See release_xshut().
            _xshut_held = release_xshut(args.xshut)          # noqa: F841
        except Exception as exc:
            print(f"FAIL: cannot drive XSHUT on GPIO {args.xshut}: {exc}")
            return 2
        print(f"XSHUT released on GPIO {args.xshut}")

    i2c = busio.I2C(board.SCL, board.SDA)
    addrs = scan(i2c)
    print(f"I2C bus addresses: {[hex(a) for a in addrs]}")

    if args.scan:
        print(f"0x29 (VL53L0X default) present: {0x29 in addrs}")
        return 0

    if args.address not in addrs:
        print(f"FAIL: nothing at {hex(args.address)}.")
        print("      Check VIN/GND/SDA/SCL. 0x1a is the WM8960 codec, not the sensor.")
        if not args.xshut:
            print("      XSHUT may be holding it in reset. Retry with --xshut 17.")
        return 1

    try:
        import adafruit_vl53l0x
    except ImportError:
        print("FAIL: adafruit_vl53l0x is not installed. On the device:")
        print("      venv/bin/pip install adafruit-circuitpython-vl53l0x")
        return 2

    sensor = adafruit_vl53l0x.VL53L0X(i2c, address=args.address)
    if args.budget_us:
        sensor.measurement_timing_budget = args.budget_us
    print(f"sensor ready at {hex(args.address)}, "
          f"timing budget {sensor.measurement_timing_budget} us")
    if args.baseline:
        return baseline_check(sensor, args)

    print()
    print("Walk towards the sensor, then away. Ctrl-C stops early.")
    print(f"{'t_s':>6} {'mm':>7}  note")

    reads, errors, t0 = [], 0, time.monotonic()
    try:
        while time.monotonic() - t0 < args.seconds:
            t = time.monotonic() - t0
            try:
                mm = sensor.range
            except Exception as exc:
                errors += 1
                print(f"{t:>6.1f} {'ERR':>7}  {type(exc).__name__}: {exc}")
                time.sleep(args.interval)
                continue
            # The driver reports a large value when nothing reflects in range.
            # Treat it as "absent", never as a near reading.
            note = "out of range" if mm >= 8000 or mm == 0 else ""
            if not note:
                reads.append(mm)
            print(f"{t:>6.1f} {mm:>7}  {note}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped")

    print()
    print(f"in-range reads: {len(reads)}   errors: {errors}")
    if reads:
        print(f"min {min(reads)} mm   median {int(statistics.median(reads))} mm   "
              f"max {max(reads)} mm")
        print()
        print("What to check:")
        print(f"  * Nobody there  -> expect 'out of range', not a small number.")
        print(f"  * You at the door -> expect a stable reading under 2000 mm.")
        print(f"  * Max in-range reading seen: {max(reads)} mm. The VL53L0X is")
        print(f"    specified to 2000 mm in good conditions. A dark costume")
        print(f"    reflects less, so the real reach will be shorter.")
    if args.histogram and reads:
        print()
        buckets = {}
        for mm in reads:
            buckets[mm // 200 * 200] = buckets.get(mm // 200 * 200, 0) + 1
        for lo in sorted(buckets):
            print(f"{lo:>5}-{lo+199:<5} {'#' * min(buckets[lo], 60)} {buckets[lo]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
