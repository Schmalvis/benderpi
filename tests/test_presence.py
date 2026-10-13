"""Tests for the VL53L0X presence gate (scripts/presence.py).

The gate sits in front of the Halloween open mic. Its most important property
is not accuracy -- it is failing OPEN. A missing, unwired or broken sensor must
leave `_halloween_loop` exactly as it was, because the owner's stated worst
failure is a silent Bender, not a chatty one. Half of these tests are about
that path.
"""
import sys
import types

import pytest

import presence


class FakeSensor:
    """Stands in for adafruit_vl53l0x.VL53L0X.

    `ranges` is consumed one reading per `.range` access. An element that is an
    exception class or instance is raised instead of returned, so a read
    failure can be scripted.
    """

    def __init__(self, ranges):
        self.ranges = list(ranges)
        self.reads = 0
        self.measurement_timing_budget = 33000
        self.signal_rate_limit = 0.25        # the driver default

    @property
    def range(self):
        self.reads += 1
        if not self.ranges:
            return 8190                      # nothing in range
        value = self.ranges.pop(0)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, type) and issubclass(value, BaseException):
            raise value("scripted read failure")
        return value


@pytest.fixture
def gate(monkeypatch):
    """Reset module state, enable the gate, record metrics, inject a sensor."""
    presence._reset_for_tests()
    counts = []

    fake_metrics = types.SimpleNamespace(
        count=lambda name, **tags: counts.append((name, tags)),
    )
    monkeypatch.setattr(presence, "metrics", fake_metrics)

    for key, value in (
        ("presence_sensor_enabled", True),
        ("presence_max_mm", 2000),
        ("presence_clear_mm", 2400),
        ("presence_confirm_reads", 2),
        ("presence_poll_s", 0.001),
        ("presence_min_mm", 0),
    ):
        monkeypatch.setattr(presence.cfg, key, value, raising=False)

    def install(ranges):
        sensor = FakeSensor(ranges)
        presence._sensor = sensor
        return sensor

    yield types.SimpleNamespace(install=install, counts=counts)
    presence._reset_for_tests()


# ---------------------------------------------------------------------------
# Failing open -- the property that protects Halloween night
# ---------------------------------------------------------------------------

def test_disabled_gate_is_unavailable_and_opens(monkeypatch):
    presence._reset_for_tests()
    monkeypatch.setattr(presence.cfg, "presence_sensor_enabled", False,
                        raising=False)
    assert presence.available() is False
    # The whole point: a gate that is off must not block the mic.
    assert presence.wait_for_presence(0.0) is True
    presence._reset_for_tests()


def test_missing_driver_fails_open(monkeypatch, gate):
    # No sensor injected, and no adafruit_vl53l0x on a dev box.
    monkeypatch.setitem(sys.modules, "adafruit_vl53l0x", None)
    monkeypatch.delitem(sys.modules, "adafruit_vl53l0x")
    assert presence.available() is False
    assert presence.wait_for_presence(0.0) is True
    assert any(n == "presence_sensor_unavailable" for n, _ in gate.counts)


def test_repeated_read_errors_mark_the_sensor_dead_and_open(gate):
    gate.install([OSError] * 10)
    for _ in range(presence._MAX_ERRORS):
        assert presence.distance_mm() is None
    assert presence._dead is True
    assert presence.available() is False
    # Dead sensor must not block the mic.
    assert presence.wait_for_presence(0.0) is True
    assert any(n == "presence_sensor_dead" for n, _ in gate.counts)


def test_a_single_read_error_does_not_kill_the_gate(gate):
    gate.install([OSError, 900, 900])
    assert presence.distance_mm() is None      # the error
    assert presence._dead is False
    assert presence.distance_mm() == 900       # recovered
    assert presence._errors == 0


# ---------------------------------------------------------------------------
# Absence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reading", [0, 8000, 8190, 20000])
def test_out_of_range_readings_count_as_absent(gate, reading):
    gate.install([reading])
    assert presence.distance_mm() is None


def test_absent_sensor_keeps_the_gate_shut(gate):
    gate.install([8190] * 10)
    assert presence.present() is False
    assert presence.present() is False


# ---------------------------------------------------------------------------
# Confirmation and hysteresis
# ---------------------------------------------------------------------------

def test_arrival_needs_two_agreeing_reads(gate):
    gate.install([1000, 1000])
    assert presence.present() is False      # one read is not enough
    assert presence.present() is True       # second read confirms


def test_one_stray_near_read_does_not_open_the_gate(gate):
    # A single spurious reading between out-of-range reads must not trigger,
    # the same reason oww_frames_required exists for the wake model.
    gate.install([8190, 1000, 8190, 8190])
    for _ in range(4):
        assert presence.present() is False


def test_gate_holds_inside_the_hysteresis_band(gate):
    gate.install([1000, 1000, 2200, 2200, 2300])
    assert presence.present() is False
    assert presence.present() is True
    # 2200 and 2300 are past presence_max_mm (2000) but inside
    # presence_clear_mm (2400), so the gate must stay open.
    assert presence.present() is True
    assert presence.present() is True
    assert presence.present() is True


def test_gate_closes_past_the_clear_distance(gate):
    gate.install([1000, 1000, 2500, 2500])
    presence.present()
    assert presence.present() is True
    assert presence.present() is True       # one far read is not enough
    assert presence.present() is False      # second confirms they left


def test_going_out_of_range_closes_the_gate(gate):
    gate.install([1000, 1000, 8190, 8190])
    presence.present()
    assert presence.present() is True
    assert presence.present() is True
    assert presence.present() is False


def test_arrival_and_departure_are_counted(gate):
    gate.install([1000, 1000, 8190, 8190])
    for _ in range(4):
        presence.present()
    names = [n for n, _ in gate.counts]
    assert "presence_arrived" in names
    assert "presence_left" in names


def test_confirm_reads_of_one_triggers_immediately(gate, monkeypatch):
    monkeypatch.setattr(presence.cfg, "presence_confirm_reads", 1,
                        raising=False)
    gate.install([1000])
    assert presence.present() is True


# ---------------------------------------------------------------------------
# wait_for_presence
# ---------------------------------------------------------------------------

def test_wait_returns_true_once_confirmed(gate):
    gate.install([8190, 1000, 1000])
    assert presence.wait_for_presence(5.0) is True


def test_wait_times_out_when_nobody_is_there(gate):
    gate.install([8190] * 200)
    assert presence.wait_for_presence(0.02) is False


def test_wait_is_bounded_so_the_watchdog_keeps_being_fed(gate):
    # The service is Type=notify with WatchdogSec=120. A gate that blocked
    # indefinitely would be killed by systemd, so the wait must return.
    import time
    gate.install([8190] * 500)
    t0 = time.monotonic()
    assert presence.wait_for_presence(0.05) is False
    assert time.monotonic() - t0 < 2.0


# ---------------------------------------------------------------------------
# Crosstalk floor -- Bender's own head
# ---------------------------------------------------------------------------

class TestCrosstalkFloor:
    """The sensor times its own IR pulse back, so it cannot tell Bender's eye
    socket from a child. A recessed mounting reads the socket as a near target.

    That failure LATCHES THE GATE OPEN forever while looking exactly like a
    working sensor, which makes it worse than the sensor simply dying. Dying
    fails open too, but it says so.
    """

    def test_the_floor_is_off_by_default(self):
        import config
        assert config.Config().presence_min_mm == 0

    def test_readings_at_or_under_the_floor_are_discarded(self, gate, monkeypatch):
        monkeypatch.setattr(presence.cfg, "presence_min_mm", 150, raising=False)
        gate.install([150, 149, 80])
        assert presence.distance_mm() is None     # exactly on the floor
        assert presence.distance_mm() is None
        assert presence.distance_mm() is None

    def test_readings_above_the_floor_pass(self, gate, monkeypatch):
        monkeypatch.setattr(presence.cfg, "presence_min_mm", 150, raising=False)
        gate.install([151, 900])
        assert presence.distance_mm() == 151
        assert presence.distance_mm() == 900

    def test_an_enclosure_reflection_cannot_latch_the_gate_open(self, gate,
                                                                monkeypatch):
        """The whole point. A steady short reading from inside the head must
        not read as a child standing at the door."""
        monkeypatch.setattr(presence.cfg, "presence_min_mm", 150, raising=False)
        gate.install([120] * 20)                  # the eye socket, not a child
        for _ in range(10):
            assert presence.present() is False

    def test_without_the_floor_an_enclosure_reflection_does_latch(self, gate):
        """Proves the floor is load-bearing, not decoration. With it off, the
        same readings open the gate and keep it open."""
        gate.install([120] * 20)
        presence.present()
        assert presence.present() is True
        assert presence.present() is True

    def test_the_floor_is_counted_so_a_bad_mounting_is_visible(self, gate,
                                                               monkeypatch):
        monkeypatch.setattr(presence.cfg, "presence_min_mm", 150, raising=False)
        gate.install([120])
        presence.distance_mm()
        assert any(n == "presence_below_floor" for n, _ in gate.counts)

    def test_a_child_at_the_door_is_not_discarded_by_a_sane_floor(self, gate,
                                                                  monkeypatch):
        """Guard against over-gating: the floor must reject the enclosure and
        nothing else. Nobody stands 150mm from Bender's face."""
        monkeypatch.setattr(presence.cfg, "presence_min_mm", 150, raising=False)
        gate.install([600, 600])
        assert presence.present() is False
        assert presence.present() is True


# ---------------------------------------------------------------------------
# XSHUT recovery
# ---------------------------------------------------------------------------

class FakeLine:
    """Stands in for digitalio.DigitalInOut on the XSHUT pin."""

    def __init__(self):
        self.value = True
        self.history = []
        self.deinited = False

    def __setattr__(self, name, value):
        if name == "value" and "history" in self.__dict__:
            self.history.append(value)
        object.__setattr__(self, name, value)

    def deinit(self):
        self.deinited = True


class TestXshutRecovery:
    """The breakout exposes an active-low shutdown pin. Wiring it lets a wedged
    sensor be power-cycled in place, the same move `xvf3800_reboot_on_corrupt`
    makes for the mic array instead of waiting for a human.

    Without it the sensor stays dead until the process restarts -- survivable,
    because the gate fails open, but it means an evening with no gate.
    """

    @pytest.fixture
    def wired(self, gate, monkeypatch):
        monkeypatch.setattr(presence.cfg, "presence_xshut_gpio", 17,
                            raising=False)
        monkeypatch.setattr(presence.cfg, "presence_max_resets", 3,
                            raising=False)
        line = FakeLine()
        monkeypatch.setattr(presence, "_xshut_line", lambda: line)
        return types.SimpleNamespace(gate=gate, line=line)

    def test_no_pin_configured_means_no_reset(self, gate, monkeypatch):
        monkeypatch.setattr(presence.cfg, "presence_xshut_gpio", 0,
                            raising=False)
        assert presence._xshut_line() is None
        assert presence._reset_via_xshut() is False

    def test_a_wedged_sensor_is_power_cycled(self, wired, monkeypatch):
        healthy = FakeSensor([900] * 10)
        monkeypatch.setattr(presence, "_open_locked", lambda: healthy)
        wired.gate.install([OSError] * presence._MAX_ERRORS)

        for _ in range(presence._MAX_ERRORS):
            presence.distance_mm()

        # Active low: driven low, then released high.
        assert wired.line.history == [False, True]
        assert presence._dead is False
        assert presence.distance_mm() == 900

    def test_recovery_is_counted_like_the_mic_array_recovery(self, wired,
                                                             monkeypatch):
        monkeypatch.setattr(presence, "_open_locked",
                            lambda: FakeSensor([900] * 5))
        wired.gate.install([OSError] * presence._MAX_ERRORS)
        for _ in range(presence._MAX_ERRORS):
            presence.distance_mm()
        names = [n for n, _ in wired.gate.counts]
        assert "presence_sensor_dead" in names
        assert "presence_sensor_reset" in names
        assert "presence_sensor_recovered" in names

    def test_a_failed_reset_leaves_the_gate_open(self, wired, monkeypatch):
        monkeypatch.setattr(presence, "_open_locked", lambda: None)
        wired.gate.install([OSError] * presence._MAX_ERRORS)
        for _ in range(presence._MAX_ERRORS):
            presence.distance_mm()
        assert presence._dead is True
        assert presence.available() is False
        assert presence.wait_for_presence(0.0) is True
        assert any(n == "presence_sensor_reset_failed"
                   for n, _ in wired.gate.counts)

    def test_resets_are_capped_so_a_dead_sensor_cannot_spin_the_loop(
            self, wired, monkeypatch):
        monkeypatch.setattr(presence, "_open_locked", lambda: None)
        for _ in range(5):
            presence._reset_via_xshut()
        assert presence._resets == 3            # presence_max_resets
        assert any(n == "presence_resets_exhausted"
                   for n, _ in wired.gate.counts)

    def test_the_gate_still_fails_open_while_resets_are_exhausted(
            self, wired, monkeypatch):
        monkeypatch.setattr(presence, "_open_locked", lambda: None)
        for _ in range(5):
            presence._reset_via_xshut()
        # The property that protects the night survives every failure path.
        assert presence.wait_for_presence(0.0) is True

    def test_close_releases_the_xshut_line(self, gate, monkeypatch):
        line = FakeLine()
        monkeypatch.setattr(presence, "_xshut", line, raising=False)
        presence.close()
        assert line.deinited is True


class TestXshutOnOpen:
    """The open path must drive XSHUT before it touches the bus.

    MEASURED on-device 2026-10-13: the breakout carries no pull-up on XSHUT,
    and a Raspberry Pi GPIO left as an input rests on its internal pull-down.
    So the pin sat low and held the sensor in reset. A bus scan returned only
    0x1a, the WM8960 codec. Driving the pin high made 0x29 appear, with
    identification registers 0xEE / 0xAA / 0x10.

    The pin used to be driven only inside `_reset_via_xshut()`, which needs
    `_MAX_ERRORS` consecutive READ failures to run. No read ever started: the
    failed open set `_dead`, and `_get_sensor()` then returned None for the
    life of the process. A correctly wired sensor stayed dead for good.
    """

    def _fake_driver(self, order, seen=None):
        module = types.ModuleType("adafruit_vl53l0x")

        def ctor(i2c, address=0x29, io_timeout_s=0):
            order.append("i2c")
            if seen is not None:
                seen["io_timeout_s"] = io_timeout_s
            return FakeSensor([900])

        module.VL53L0X = ctor
        return module

    def test_open_drives_xshut_before_talking_to_the_bus(self, gate,
                                                         monkeypatch):
        order = []
        line = FakeLine()
        monkeypatch.setattr(presence.cfg, "presence_xshut_gpio", 17,
                            raising=False)
        monkeypatch.setattr(presence, "_xshut_line",
                            lambda: (order.append("xshut"), line)[1])
        monkeypatch.setitem(sys.modules, "adafruit_vl53l0x",
                            self._fake_driver(order))

        sensor = presence._open_locked()

        assert sensor is not None
        assert order == ["xshut", "i2c"]

    def test_an_unwired_pin_does_not_block_the_open(self, gate, monkeypatch):
        order = []
        monkeypatch.setattr(presence.cfg, "presence_xshut_gpio", 0,
                            raising=False)
        monkeypatch.setitem(sys.modules, "adafruit_vl53l0x",
                            self._fake_driver(order))

        sensor = presence._open_locked()

        assert sensor is not None
        assert order == ["i2c"]

    def test_xshut_is_driven_even_when_the_driver_is_missing(self, gate,
                                                             monkeypatch):
        """Order matters more than the outcome. The pin goes high first, so a
        later retry or reset starts from a sensor that is actually running."""
        order = []
        line = FakeLine()
        monkeypatch.setattr(presence.cfg, "presence_xshut_gpio", 17,
                            raising=False)
        monkeypatch.setattr(presence, "_xshut_line",
                            lambda: (order.append("xshut"), line)[1])
        monkeypatch.setitem(sys.modules, "adafruit_vl53l0x", None)
        monkeypatch.delitem(sys.modules, "adafruit_vl53l0x")

        assert presence._open_locked() is None
        assert order == ["xshut"]


class TestIoTimeoutIsAlwaysBounded:
    """Every driver poll loop must be bounded, or a stalled sensor hangs us.

    The Adafruit driver polls the sensor's status registers in FIVE places and
    bounds none of them unless `io_timeout_s` is set. The parameter defaults
    to 0, which means off.

    MEASURED on-device 2026-10-13, minutes after the sensor was mounted behind
    the eye: `probe_vl53l0x.py --baseline` sat in `read_range()`'s poll loop
    for over three minutes in process state D (`wchan: i2c_dw_xfer`). It
    printed nothing and would never have returned.

    A hang defeats the whole fail-open design. `_halloween_loop` would block
    forever, stop calling `_note_progress()`, miss the `WatchdogSec=120` ping,
    and systemd would kill and restart the service. Fail-open only covers
    exceptions. So the driver has to be made to raise one.
    """

    def _driver(self, seen):
        module = types.ModuleType("adafruit_vl53l0x")

        def ctor(i2c, address=0x29, io_timeout_s=0):
            seen["io_timeout_s"] = io_timeout_s
            return FakeSensor([900])

        module.VL53L0X = ctor
        return module

    def test_the_open_passes_a_positive_timeout(self, gate, monkeypatch):
        seen = {}
        monkeypatch.setitem(sys.modules, "adafruit_vl53l0x",
                            self._driver(seen))

        assert presence._open_locked() is not None
        assert seen["io_timeout_s"] > 0, "a zero timeout means an unbounded poll"

    def test_the_timeout_covers_three_measurement_budgets(self, gate,
                                                          monkeypatch):
        """One budget is the expected wait, so one budget is not a margin."""
        seen = {}
        monkeypatch.setattr(presence.cfg, "presence_timing_budget_us", 200000,
                            raising=False)
        monkeypatch.setitem(sys.modules, "adafruit_vl53l0x",
                            self._driver(seen))

        presence._open_locked()

        assert seen["io_timeout_s"] == pytest.approx(0.8)

    def test_a_short_budget_still_gets_the_floor(self, gate, monkeypatch):
        """The constructor calibrates BEFORE the budget is applied, using the
        driver's own ~33 ms default. A budget-derived timeout alone could be
        shorter than that calibration needs."""
        seen = {}
        monkeypatch.setattr(presence.cfg, "presence_timing_budget_us", 20000,
                            raising=False)
        monkeypatch.setitem(sys.modules, "adafruit_vl53l0x",
                            self._driver(seen))

        presence._open_locked()

        assert seen["io_timeout_s"] == pytest.approx(0.5)

    def test_a_driver_timeout_is_counted_and_escalates_like_any_read_error(
            self, gate):
        """The driver raises RuntimeError on expiry. `distance_mm()` already
        counts any exception towards `_MAX_ERRORS` and the XSHUT reset, so the
        bound plugs straight into the recovery that is already tested."""
        # Install through the fixture, so the real `_get_sensor()` runs and the
        # `_dead` short-circuit is exercised rather than patched away.
        gate.install([RuntimeError("Timeout waiting for VL53L0X!")]
                     * presence._MAX_ERRORS)

        for _ in range(presence._MAX_ERRORS):
            assert presence.distance_mm() is None

        assert presence._dead is True
        # Fail open: a dead sensor must never shut the mic.
        assert presence.wait_for_presence(0.01) is True



class TestXshutIsPulsedNotJustDrivenHigh:
    """Opening the sensor must PULSE XSHUT low, not simply drive it high.

    Driving an already-high pin high is not a reset, and the sensor carries no
    other reset. This cost a whole afternoon.

    MEASURED on-device 2026-10-13. A probe run wedged the sensor. It still
    answered its address, returned a stale 8190 mm for three reads, raised
    `OSError [Errno 121]` (a bus NACK), then timed out on every read, 35 in a
    row, with no recovery. Three further probe processes reproduced the
    identical failure at the identical read, because:

      - `digitalio` leaves the line in its last state when the process exits.
        `pinctrl get 17` read `op dh pd | hi` with nothing running.
      - Each new process found XSHUT high and drove it high again.
      - The wedged sensor was never power-cycled, so it looked like dead
        hardware for ever.

    A low pulse restored it. So the open path must pulse, and the fix must not
    regress: a regression here is indistinguishable from a broken breakout.
    """

    def _fake_driver(self, order):
        module = types.ModuleType("adafruit_vl53l0x")

        def ctor(i2c, address=0x29, io_timeout_s=0):
            order.append("i2c")
            return FakeSensor([900])

        module.VL53L0X = ctor
        return module

    def _wire(self, monkeypatch, order, line):
        monkeypatch.setattr(presence.cfg, "presence_xshut_gpio", 17,
                            raising=False)
        monkeypatch.setattr(presence, "_xshut_line",
                            lambda: (order.append("xshut"), line)[1])
        monkeypatch.setitem(sys.modules, "adafruit_vl53l0x",
                            self._fake_driver(order))

    def test_the_open_drives_the_pin_low_then_high(self, gate, monkeypatch):
        order, line = [], FakeLine()
        self._wire(monkeypatch, order, line)

        assert presence._open_locked() is not None
        # A bare [True] would mean the pin was only driven high. That is the
        # bug: it leaves a wedged sensor wedged.
        assert line.history == [False, True]
        assert line.value is True

    def test_the_pulse_happens_before_any_bus_traffic(self, gate, monkeypatch):
        order, line = [], FakeLine()
        self._wire(monkeypatch, order, line)

        assert presence._open_locked() is not None
        assert order == ["xshut", "i2c"]

    def test_an_already_high_pin_is_still_pulsed(self, gate, monkeypatch):
        """The exact on-device case. The pin starts high, as a previous process
        left it, and the open must still power-cycle the sensor."""
        order, line = [], FakeLine()
        line.value = True
        line.history.clear()
        self._wire(monkeypatch, order, line)

        assert presence._open_locked() is not None
        assert False in line.history, "an already-high pin was never driven low"

    def test_a_failed_pulse_does_not_stop_the_open(self, gate, monkeypatch):
        """Fail open. A GPIO that cannot be driven must not make Bender deaf."""
        order = []

        class Stubborn(FakeLine):
            def __setattr__(self, name, value):
                if name == "value" and "history" in self.__dict__:
                    raise OSError("GPIO busy")
                object.__setattr__(self, name, value)

        self._wire(monkeypatch, order, Stubborn())

        assert presence._open_locked() is not None
        assert order == ["xshut", "i2c"]

    def test_the_reset_path_pulses_through_the_same_helper(self, gate,
                                                           monkeypatch):
        """One pulse implementation, so the two paths cannot drift apart."""
        line = FakeLine()
        line.history.clear()

        presence._pulse_xshut(line)

        assert line.history == [False, True]


class TestSignalRateLimit:
    """`presence_signal_rate_mcps` must reach the sensor, or reach is halved.

    The signal rate limit is the minimum return rate the sensor accepts as a
    real echo. The driver default of 0.25 MCPS is tuned for a good reflector.
    A child in a Halloween costume is a poor one.

    MEASURED on-device 2026-10-13, sensor mounted behind the left eye, walking
    in from 3 m and back out, 0 errors in every run:

        0.25 MCPS    reliable to  641 mm, max  872 mm
        0.0938 MCPS  reliable to 1196 mm, max 1267 mm

    Crosstalk did not come back at the lower limit: the empty-field baseline
    read 0 of 135 in range. That check is not optional, because a weaker echo
    threshold can start accepting the enclosure's own reflection, and that
    latches the gate OPEN for ever while looking like a working sensor.
    """

    def _driver(self, sensor):
        module = types.ModuleType("adafruit_vl53l0x")
        module.VL53L0X = lambda i2c, address=0x29, io_timeout_s=0: sensor
        return module

    def _open(self, monkeypatch, sensor, rate):
        monkeypatch.setattr(presence.cfg, "presence_xshut_gpio", 0,
                            raising=False)
        monkeypatch.setattr(presence.cfg, "presence_signal_rate_mcps", rate,
                            raising=False)
        monkeypatch.setitem(sys.modules, "adafruit_vl53l0x",
                            self._driver(sensor))
        return presence._open_locked()

    def test_a_configured_rate_is_applied(self, gate, monkeypatch):
        sensor = FakeSensor([900])
        assert self._open(monkeypatch, sensor, 0.1) is sensor
        assert sensor.signal_rate_limit == 0.1

    def test_zero_leaves_the_driver_default_alone(self, gate, monkeypatch):
        """Opting out must not write anything. 0 means 'do not touch'."""
        sensor = FakeSensor([900])
        assert self._open(monkeypatch, sensor, 0) is sensor
        assert sensor.signal_rate_limit == 0.25

    def test_none_leaves_the_driver_default_alone(self, gate, monkeypatch):
        """A null in bender_config.json must behave like 0, not crash."""
        sensor = FakeSensor([900])
        assert self._open(monkeypatch, sensor, None) is sensor
        assert sensor.signal_rate_limit == 0.25

    def test_the_rate_is_set_before_the_timing_budget(self, gate, monkeypatch):
        """Order matters to the driver, so pin it down.

        `measurement_timing_budget` recomputes the sensor's range timeouts.
        Setting the signal rate limit afterwards would leave them computed for
        the old limit.
        """
        order = []

        class Recorder(FakeSensor):
            armed = False               # armed after __init__, see below

            def __setattr__(self, name, value):
                if self.armed and name in ("signal_rate_limit",
                                           "measurement_timing_budget"):
                    order.append(name)
                object.__setattr__(self, name, value)

        monkeypatch.setattr(presence.cfg, "presence_timing_budget_us", 200000,
                            raising=False)
        sensor = Recorder([900])
        sensor.armed = True            # ignore the writes __init__ itself made
        self._open(monkeypatch, sensor, 0.1)

        assert order == ["signal_rate_limit", "measurement_timing_budget"]

    def test_a_sensor_that_rejects_the_rate_fails_open(self, gate, monkeypatch):
        """An older driver without the property must not make Bender deaf."""
        class Picky(FakeSensor):
            armed = False

            def __setattr__(self, name, value):
                if self.armed and name == "signal_rate_limit":
                    raise AttributeError("no such property")
                object.__setattr__(self, name, value)

        sensor = Picky([900])
        sensor.armed = True
        assert self._open(monkeypatch, sensor, 0.1) is None
        # Fail open: a sensor we could not configure must never shut the mic.
        assert presence.wait_for_presence(0.01) is True


class TestMetricsSingletonIsImported:
    """`presence.metrics` must be the writer, not the module.

    This class deliberately does NOT use the `gate` fixture. The fixture
    monkeypatches `presence.metrics` with a recorder, and that is exactly what
    hid the bug: `presence.py` had `import metrics`, which binds the MODULE,
    whose `count` lives on the `MetricsWriter` class and not on the module.
    Every one of the 15 `metrics.count(...)` calls in the gate raised
    `AttributeError`, and no test could see it.

    MEASURED on-device 2026-10-13, the first time the real gate ran against the
    real sensor. The sensor opened correctly, logged its ready line, and then
    `_open_locked()` died on `metrics.count("presence_sensor_ready", ...)`.

    The fail-open rule does NOT cover this. It covers exceptions from the
    sensor, caught inside `distance_mm()`. This one is raised by the gate's own
    bookkeeping, propagates out of `available()`, and takes `_halloween_loop`
    down with it. So a crash, not a silent open mic.
    """

    def test_count_is_callable_on_the_imported_object(self):
        assert callable(getattr(presence.metrics, "count", None))

    def test_timer_is_callable_on_the_imported_object(self):
        assert callable(getattr(presence.metrics, "timer", None))

    def test_it_is_not_the_module(self):
        import types
        assert not isinstance(presence.metrics, types.ModuleType)

    def test_it_is_the_same_object_every_module_shares(self):
        """One writer, one file, one lock. A second instance would race on
        metrics.jsonl and rotate it independently."""
        import metrics as metrics_module
        assert presence.metrics is metrics_module.metrics

    def test_every_metrics_call_in_the_gate_uses_a_real_method(self):
        """Catch a typo'd metric call without executing every branch."""
        import ast
        import inspect

        source = inspect.getsource(presence)
        used = {
            node.func.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "metrics"
        }
        assert used, "no metrics calls found; this test has gone stale"
        missing = sorted(n for n in used
                         if not callable(getattr(presence.metrics, n, None)))
        assert not missing, f"presence.py calls metrics.{missing} which do not exist"
