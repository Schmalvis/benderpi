# Dashboard review — control and latency

**Measured on-device 2026-10-01.** Everything below is a number from BenderPi,
not an estimate, unless marked *inferred*.

Requested by the owner as part of the Halloween work: the dashboard is how
Bender is puppeted today, and it has to be fast enough to hold a child's
attention from the other side of a front garden.

---

## The headline

**A soundboard tap makes no sound for 3.5 seconds, and the wake word is deaf
for 23 seconds afterwards.** Five taps inside five minutes kills the service.

That is not a frontend problem. The frontend is fine. It is the cost of how
puppet playback takes the speaker away from the wake loop.

---

## The measured puppet path

Every puppet action — typed text, soundboard clip, vision narrate, mic listen —
runs `service_guard.service_lease()`, which stops `bender-converse`, does the
work, and starts it again. Measured from journald, restart of 20:15:58:

| Step | Measured |
|---|---|
| `systemctl stop bender-converse` | **3.0s** (20:15:58 → 20:16:01) |
| guard's settle sleep | 0.5s (hardcoded) |
| Piper TTS, typed text, cold | **224–2365ms** (3 samples: 224, 2241, 2365) |
| Piper TTS, repeated text | **2ms** (cached) |
| playback | length of the clip |
| `systemctl start`, blocking to `READY=1` | **3.0s** (20:16:01 → 20:16:04) |
| Whisper resident again | 20:16:09 (**+8s**) |
| Qwen resident again | 20:16:18 (**+17s**) |
| **wake word actually listening** | 20:16:21 (**+23s**) |

**Operator-perceived dead time before any sound:**

| Action | Dead time |
|---|---|
| soundboard clip | **3.5s** |
| typed text, new wording | **3.7–5.9s** |
| typed text, repeated | **3.5s** |

**And the limits bite.** The unit has `StartLimitBurst=5` per 300s, and every
lease is one stop plus one start. So the **sixth puppet action in five minutes
leaves Bender dead**. `_start_converse()` already detects this and tries one
`reset-failed` plus retry, which is good defensive work — but it is recovery
from a design that spends a restart per button press.

---

## Finding 1 (P0) — puppet playback should not touch the service

**The fix already has a precedent in this codebase.** `cfg` defines three
file-based IPC paths and the web UI already uses two of them:

```
.session_active.json   written by session.py, read by the web UI
.end_session           written by the web UI, read by the session loop
.abort_playback        written by the web UI, read mid-playback
```

So the pattern is established: the web process drops a file, the converse
process acts on it. A fourth file closes this finding.

**Design.** `POST /api/puppet/speak` renders the WAV as it does now, then
writes `.say_request.json` (`{"wav": "...", "id": ...}`) instead of taking the
lease. The wake loop checks for that file between openWakeWord frames — a
frame is 80ms and `os.path.exists` costs microseconds, so a check every frame
is free. On finding one it does exactly what a conversation does: close the
capture stream, `audio.open_session()`, play, `audio.close_session()`, resume
listening. The WM8960 single-rate constraint is respected because this is the
same handover the session already performs.

**Expected:** 3.5s of dead time becomes the mic-to-speaker handover, which the
session path already pays and which is a few hundred ms (*inferred* — the
session's own handover is not separately instrumented). No systemctl calls, no
model reloads, no start limit, and the wake word stays alive between taps.

**Keep the lease for what genuinely needs it.** The ambient mic websocket and
vision narrate both want the *capture* device, which the wake loop is holding;
those still need the service out of the way. Only playback moves.

**Risk to state honestly:** this puts playback inside the wake loop's thread,
so a bad WAV or a wedged DAC now stalls the wake loop rather than a web worker.
It needs the same timeout and broad exception handling the Halloween loop just
got, plus a progress stamp so the new watchdog heartbeat covers it.

---

## Finding 2 (P1) — a second tap is an error, not a queue

`service_lease()` waits 2.0s for the lock, then raises `ServiceBusy`, which the
route turns into **409 "Bender is already speaking — try again in a moment"**.

That is right for the mic stream and for vision. It is wrong for a soundboard
being played at children: the operator taps, nothing happens for 3.5s, they tap
again, and get an error toast. A depth-1 queue (replace any pending request,
never stack) matches what a puppeteer actually wants. With Finding 1 done, the
window shrinks to the handover, which makes this far less likely but not
impossible.

---

## Finding 3 (P1) — the UI never says Bender is deaf

For 23 seconds after every puppet action, "Hey Bender" does nothing. The
dashboard shows no indication of this. The operator cannot tell the difference
between "he is still rebooting his ears" and "the wake word missed me again",
which matters a great deal given the wake model's measured 25% recall at the
old threshold.

Cheap fix regardless of Finding 1: `.session_active.json` and a service-state
probe already exist, so a "listening / deaf / speaking" indicator needs no new
backend work.

---

## Finding 4 (P2) — polling continues in a backgrounded tab

| Poll | Interval | File |
|---|---|---|
| health | **5s** | `web/src/lib/stores/health.js:18` |
| timers | per store | `web/src/lib/stores/timers.js:7` |
| dashboard status | 30s | `web/src/pages/Dashboard.svelte:28` |
| vision passive | 60s | `web/src/pages/Config.svelte:33` |

None of them check `document.visibilityState`. A phone left on the dashboard in
a pocket keeps asking a 4GB Pi for health 12 times a minute, all night. The
Page Visibility API makes this a few lines. Low impact, near-zero cost to fix.

---

## Finding 5 (P2) — pre-render the lines you will actually use

Piper costs **2.2s cold and 2ms warm** for the same sentence. The Halloween
bank is already pre-rendered, which is why those lines are instant. Anything
the owner expects to type on the night should be a soundboard favourite
instead, so it is never rendered live at the door.

---

## Measured and fine, no action

| Thing | Measured |
|---|---|
| `/api/puppet/clips` | **1.5ms** for 124 clips, uncached, per request |
| JS bundle | 120 KB |
| CSS bundle | 20 KB |
| camera MJPEG + mic websocket | already capped, after an earlier fix |
| mutating routes | already audit-logged |

The frontend is not the problem. Do not spend time there.

---

## Order of work

1. **Finding 1.** It is the whole latency story and it removes the start-limit
   risk before Halloween.
2. **Finding 3.** Cheap, and it tells the operator what is happening.
3. **Finding 2.** Only worth doing after 1, which changes its shape.
4. **Findings 4 and 5.** Tidy-ups, any time.

Not started. This document is the review, not the change.
