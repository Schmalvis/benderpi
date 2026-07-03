# Hailo NPU Wedge-Recovery Runbook (BenderPi)

**Use this if, after enabling `hailo_stt_enabled: true`, the Hailo-10H NPU stops
responding** — symptoms: `bender-converse` logs show `HAILO_INVALID_OPERATION`,
`HAILO_VDMA_LAUNCH_TRANSFER`, driver/VDMA timeouts, or STT/LLM hang forever and
never produce a reply.

You (a human at the machine) follow this **top to bottom**. Stop as soon as a
step recovers the device. Do **not** skip ahead to unplugging — try the softer
steps first. Everything here is done over SSH (`ssh pi@192.168.68.132`) or at the
Pi directly.

Device facts (verified on this Pi, 2026-07):
- PCIe address: `0001:01:00.0` (Hailo-10H)
- Kernel driver module: **`hailo1x_pci`** (note: *not* `hailo_pci`)
- CLI: `/usr/bin/hailortcli` (HailoRT 5.3.0)

> **Golden rule:** if any command below **hangs for more than ~30 seconds**,
> press `Ctrl-C` and move to the next step. A wedged NPU often makes the very
> tools meant to fix it hang, because they also have to talk to the dead device.

---

## Step 0 — Release the device first (always do this)

The service holds the NPU. Nothing else can reset it cleanly while the service
is running. Stop it:

```bash
sudo systemctl stop bender-converse
```

Then check whether the device is even reachable:

```bash
hailortcli scan
```

- **Responds** (`Device: pci/0001:01:00.0`) → the NPU is alive; the problem was
  software state. Go to **Step 5** (verify) then restart the service. You are
  probably done.
- **Hangs or errors** → the NPU is wedged. Continue to Step 1.

---

## Step 1 — Firmware soft reset (least disruptive, no reboot)

Ask the Hailo firmware to reset itself. Try the gentlest type first:

```bash
sudo hailortcli fw-control reset --reset-type soft
```

If that errors or does nothing, try the core reset, then a full chip reset:

```bash
sudo hailortcli fw-control reset --reset-type nn_core
sudo hailortcli fw-control reset --reset-type chip
```

Then confirm it came back:

```bash
hailortcli fw-control identify   # should print device info
hailortcli scan
```

- **Identify/scan work now** → go to **Step 5**.
- **Still hung/failing** (or the reset command itself hung — you `Ctrl-C`'d it)
  → the firmware control channel is also wedged. Continue to Step 2.

---

## Step 2 — PCIe function-level reset (sysfs)

Reset the PCIe endpoint itself without a reboot:

```bash
echo 1 | sudo tee /sys/bus/pci/devices/0001:01:00.0/reset
```

Confirm:

```bash
hailortcli scan
```

- **Works** → Step 5.
- **No** → Continue to Step 3.

---

## Step 3 — Reload the kernel driver

Unload and reload the Hailo PCIe driver. This re-initialises the kernel's side
of the link. (Refcount must be 0 — Step 0 already stopped the only consumer.)

```bash
sudo modprobe -r hailo1x_pci   # may take a few seconds
sudo modprobe hailo1x_pci
hailortcli scan
```

If `modprobe -r` **hangs** (the driver's teardown is trying to talk to the dead
device), `Ctrl-C` and go straight to Step 4 — do not force it.

- **Scan works** → Step 5.
- **No / modprobe hung** → Continue to Step 4.

---

## Step 4 — Remove + rescan the PCIe device

Force the kernel to forget the device and re-enumerate it:

```bash
echo 1 | sudo tee /sys/bus/pci/devices/0001:01:00.0/remove
echo 1 | sudo tee /sys/bus/pci/rescan
sudo modprobe hailo1x_pci        # in case rescan didn't auto-bind
hailortcli scan
```

- **Works** → Step 5.
- **No** → the endpoint is hard-wedged. Continue to Step 6 (reboot).

---

## Step 5 — Verify BEFORE restarting the service

Only restart Bender once the NPU is provably healthy:

```bash
hailortcli scan                 # lists 0001:01:00.0
hailortcli fw-control identify   # prints serial / fw version cleanly
```

If both are clean:

```bash
sudo systemctl start bender-converse
sudo journalctl -u bender-converse -f   # watch for a clean STT + LLM turn
```

Say "Hey Bender" and confirm one full turn (it transcribes, then replies)
without a Hailo error. **Done.**

---

## Step 6 — Warm reboot (if software recovery failed)

A normal reboot re-initialises the PCIe root complex, re-loads `hailo1x_pci`,
and cold-starts the driver stack on boot. On a Pi 5 + HAT this is almost always
enough to recover a wedged endpoint:

```bash
sudo reboot
```

Wait ~60–90s, reconnect, and verify:

```bash
ssh pi@192.168.68.132
hailortcli scan && hailortcli fw-control identify
```

- **Clean** → the service auto-starts on boot; watch `journalctl` for a good
  turn. Done.
- **Still wedged after a clean reboot** → the rarer, worse case. Go to Step 7.

---

## Step 7 — Physical power cycle (last resort)

**Only if `sudo reboot` came back up but the NPU is *still* wedged.** A warm
reboot does not necessarily cut power to the HAT's own rails or force the Hailo
firmware to cold-boot; a full power-off does.

1. `sudo shutdown -h now` and wait for the Pi to fully power down (activity LED
   off).
2. **Physically unplug the Pi's power** for ~15 seconds.
3. Plug back in, let it boot (~60–90s).
4. Verify: `hailortcli scan && hailortcli fw-control identify`.
5. Watch `sudo journalctl -u bender-converse -f` for a clean turn.

---

## If it wedges repeatedly

If the NPU wedges again shortly after re-enabling STT, **the fix did not hold**.
Roll back to the known-good state so BenderPi keeps working on CPU STT:

1. Edit `/home/pi/bender/bender_config.json`: set `"hailo_stt_enabled": false`.
2. `sudo systemctl restart bender-converse`.

STT falls back to `faster-whisper` on CPU (slower but stable), and the LLM keeps
the NPU to itself — the configuration that ran reliably before this change.
Then report back with the `journalctl` output around the crash so the release
sequence can be revisited.

---

## Honesty / uncertainty notes

- Steps 1–4 (fw-control reset, PCIe reset, driver reload, remove/rescan) are the
  documented non-destructive options and *may* recover a wedged device — but
  when VDMA is hard-wedged, the firmware-control channel is frequently dead too,
  so these can hang or no-op. That is expected; escalate rather than wait.
- A **warm `sudo reboot` (Step 6) is the reliable software recovery** and should
  be tried before any physical power cycle. Physical unplug (Step 7) is only for
  the rare case where a clean reboot still leaves the NPU wedged.
- Whether a physical power cycle is *ever* strictly required on this exact
  Pi 5 + AI HAT+ could not be confirmed from docs alone. The safe assumption
  encoded above — reboot first, unplug only if reboot fails — holds on
  essentially all Pi/HAT setups because reboot re-enumerates PCIe and reloads
  the driver.
