"""scripts/xvf3800.py: the reSpeaker XVF3800 REBOOT control command.

2026-09-08: the array came up feeding a 91.7%-zero stream after the 07:00 cold
boot. Service restart, USB unbind/rebind and a sysfs port power cycle did not
clear it; a cable replug and (verified on-device) the array's own REBOOT
command do. These tests pin the wire format and the wait-for-re-enumeration.
"""
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "scripts")


class _FakeDev:
    def __init__(self, log):
        self.log = log
        self.serial_number = "S1"

    def ctrl_transfer(self, *args):
        self.log.append(args)
        return len(args[4]) if len(args) > 4 else 0


@pytest.fixture
def fake_usb(monkeypatch):
    """Install fake usb.core / usb.util; `presence` scripts what find() sees."""
    state = {"log": [], "presence": [], "dev": None}
    core = types.SimpleNamespace()
    util = types.SimpleNamespace(CTRL_OUT=0x00, CTRL_TYPE_VENDOR=0x40,
                                 CTRL_RECIPIENT_DEVICE=0x00,
                                 dispose_resources=lambda d: None)

    def find(idVendor=None, idProduct=None):
        assert (idVendor, idProduct) == (0x2886, 0x001A)
        if state["presence"]:
            here = state["presence"].pop(0)
        else:
            here = True
        return state["dev"] if here else None

    core.find = find
    state["dev"] = _FakeDev(state["log"])
    usb_mod = types.ModuleType("usb")
    usb_mod.core, usb_mod.util = core, util
    monkeypatch.setitem(sys.modules, "usb", usb_mod)
    monkeypatch.setitem(sys.modules, "usb.core", core)
    monkeypatch.setitem(sys.modules, "usb.util", util)
    sys.modules.pop("xvf3800", None)
    import xvf3800
    monkeypatch.setattr(xvf3800.metrics, "count", lambda *a, **k: None)
    monkeypatch.setattr(xvf3800.time, "sleep", lambda s: None)
    return xvf3800, state


class TestReboot:
    def test_sends_reboot_and_waits_for_reenumeration(self, fake_usb):
        xvf3800, state = fake_usb
        # first find(): the command target; then gone, gone, back
        state["presence"] = [True, False, False, True]
        assert xvf3800.reboot(wait_s=5, poll_s=0) is True
        (bm, breq, wvalue, windex, payload, timeout), = state["log"]
        assert bm == 0x40                      # OUT | vendor | device
        assert (breq, wvalue, windex) == (0, 7, 48)   # REBOOT: cmdid 7, resid 48
        assert payload == b"\x01"

    def test_false_when_device_never_leaves_the_bus(self, fake_usb, monkeypatch):
        xvf3800, state = fake_usb
        clock = [0.0]
        monkeypatch.setattr(xvf3800.time, "monotonic", lambda: clock.__setitem__(0, clock[0] + 0.5) or clock[0])
        state["presence"] = [True] + [True] * 50
        assert xvf3800.reboot(wait_s=3, poll_s=0) is False

    def test_false_when_device_absent(self, fake_usb):
        xvf3800, state = fake_usb
        state["presence"] = [False]
        assert xvf3800.reboot(wait_s=1, poll_s=0) is False
        assert state["log"] == []

    def test_false_when_control_transfer_denied(self, fake_usb):
        xvf3800, state = fake_usb

        def denied(*a):
            raise OSError("[Errno 13] Access denied (insufficient permissions)")

        state["dev"].ctrl_transfer = denied
        assert xvf3800.reboot(wait_s=1, poll_s=0) is False

    def test_false_without_pyusb(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "usb", None)
        monkeypatch.setitem(sys.modules, "usb.core", None)
        sys.modules.pop("xvf3800", None)
        import xvf3800
        monkeypatch.setattr(xvf3800.metrics, "count", lambda *a, **k: None)
        assert xvf3800.reboot() is False
