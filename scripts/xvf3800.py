"""reSpeaker XVF3800 USB mic array: the REBOOT control command.

Why this exists (2026-09-08): when the Pi cold boots with the array already
plugged in -- every 07:00 boot on BenderPi -- the capture stream can come up
corrupt. The kernel logs one `xhci-hcd: WARN: buffer overrun event` per USB
packet and the PCM is 91.7% exact zeros in a fixed 48-sample pattern (4 real
samples, 44 zeros). A service restart, a USB unbind/rebind and a sysfs port
power cycle all left it corrupt; unplugging the cable fixed it instantly. Seeed
support's fix for the same firmware fault after warm reboots is the array's own
REBOOT command over its vendor control interface, which is the software
equivalent of the reset button. Verified on-device: the array drops off the bus
0.25s after the command and is back, clean, at 1.0s.

Protocol (from respeaker's python_control/xvf_host.py): vendor control OUT
request 0, wValue = command id, wIndex = resource id, one uint8 payload.
Needs pyusb and write access to the device node -- see
udev/99-respeaker-xvf3800-control.rules.
"""
import time

from logger import get_logger
from metrics import metrics

log = get_logger("xvf3800")

VID, PID = 0x2886, 0x001A
_REBOOT_RESID, _REBOOT_CMDID = 48, 7   # "REBOOT": (48, 7, 1, "wo", "uint8")


def _usb():
    try:
        import usb.core
        import usb.util
    except ImportError:
        return None, None
    return usb.core, usb.util


def present() -> bool:
    """True if the array is enumerated and answers a descriptor read."""
    core, _ = _usb()
    if core is None:
        return False
    dev = core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        return False
    try:
        return dev.serial_number is not None
    except Exception:
        return False


def reboot(wait_s: float = 8.0, poll_s: float = 0.25) -> bool:
    """Send REBOOT and wait for the array to leave and rejoin the bus.

    Returns True once it has re-enumerated. Never raises: a missing pyusb, a
    missing device, a permission error, or no re-enumeration all return False
    and log an ERROR that says what to do.
    """
    core, util = _usb()
    if core is None:
        log.error("XVF3800 REBOOT unavailable: pyusb is not installed "
                  "(pip install pyusb)")
        metrics.count("xvf3800_reboot", ok=False, reason="no_pyusb")
        return False
    dev = core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        log.error("XVF3800 REBOOT: array not found on the USB bus")
        metrics.count("xvf3800_reboot", ok=False, reason="not_found")
        return False
    t0 = time.monotonic()
    try:
        dev.ctrl_transfer(
            util.CTRL_OUT | util.CTRL_TYPE_VENDOR | util.CTRL_RECIPIENT_DEVICE,
            0, _REBOOT_CMDID, _REBOOT_RESID, bytes([1]), 2000)
    except Exception as exc:
        log.error("XVF3800 REBOOT control transfer failed: %s (no write access "
                  "to the device node? install udev/99-respeaker-xvf3800-control.rules)",
                  exc)
        metrics.count("xvf3800_reboot", ok=False, reason="ctrl_transfer")
        return False
    finally:
        try:
            util.dispose_resources(dev)
        except Exception:
            pass

    gone = False
    while time.monotonic() - t0 < wait_s:
        time.sleep(poll_s)
        if not present():
            gone = True
        elif gone:
            elapsed = time.monotonic() - t0
            log.info("XVF3800 re-enumerated %.2fs after REBOOT", elapsed)
            metrics.count("xvf3800_reboot", ok=True, elapsed_s=round(elapsed, 2))
            return True
    log.error("XVF3800 did not re-enumerate within %.1fs of REBOOT (left the bus: %s)",
              wait_s, gone)
    metrics.count("xvf3800_reboot", ok=False, reason="no_reenumeration", gone=gone)
    return False
