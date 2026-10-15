# BenderPi Handover Context
Last updated: 2026-10-15

---

## Open side investigations

- **Vosk as an STT engine** — `docs/superpowers/plans/2026-10-01-investigate-vosk-stt.md`.
  Raised 2026-10-01. The appeal is that it STREAMS: measured `stt_transcribe` is
  508-2227ms (worst 4551ms) of dead air after the speaker stops, in front of the
  LLM's own 1.0-3.5s. A partial hypothesis could start the turn early. Not
  started, and not before the Halloween safety work.

---

## 2026-10-15 (3) — INCIDENT: Bender would not stop, and END was working fine

**16 days to Halloween.** The owner turned Halloween mode on, Bender started
chatting, and four presses of the sidebar END did not stop him.

### Every press worked. That was the problem.

```
19:29:18  Heard (open mic): "So, you're searching for the other one?..."
19:29:50  owner presses END
19:29:52  Remote end-session: abrupt exit          <- session 1 ENDED
19:30:00  Heard (open mic): 'M-K-'                 <- session 2 starts, 8s later
19:30:29  owner presses END  -> Playback aborted   <- session 2 ENDED
19:30:35  Heard (open mic): 'Everyone has their key.'
19:30:37  owner presses END  -> Remote end-session <- session 3 ENDED
19:30:44  Heard (open mic): 'Can you search up like?'
```

Halloween mode has no wake word. Ending a session just lets the next sound in
the room start another one. The owner's own voice, while he was reporting the
problem, kept re-triggering it.

**END means "end this one". It is not a stop. Nothing in `_halloween_loop`
can make it one.**

Stopped with `sudo systemctl stop bender-converse`. The stale
`.session_active.json` was cleared by hand, or `session-status` reports a live
conversation on a box where nothing runs.

### My earlier fix went to the wrong loop

The end-session check added that morning sits at `wake_converse.py:836`, in the
**household** session loop. `_halloween_loop` is a separate code path and was
untouched. Three captures had no check: the trigger capture, and both in
`_halloween_turn_loop`. All three now read `cfg.end_session_file` and call
`_cleanup_abort_files()` before ending.

That makes END prompt. **It still does not silence him**, and the tests say so
in as many words.

### What was built: a STOP button in the sidebar

| state | button | colour |
|---|---|---|
| running | ■ STOP BENDER | error red |
| stopped | ▶ START BENDER | success green |
| poll failed | BENDER ? (disabled) | muted |

It reuses `POST /api/actions/toggle-mode`, the route behind Puppet Mode, so
entering ends any live session first, stops the service, then clears the IPC
notes. It lives in the sidebar on every page, desktop and mobile, because
"shut up" is not something you go looking through pages for.

The `null` state matters: a STOP button that reads START while Bender is
talking is worse than no button at all.

**A latched pause was considered and rejected.** The owner's worst acceptable
failure is SILENCE. A quiet flag nobody remembers to clear is more dangerous
than a Bender who answers the door too eagerly.
`test_the_open_mic_restarts_after_an_end_and_that_is_by_design` fails if anyone
adds a `paused`/`quiet`/`halted` name to `_halloween_loop`, so the next person
has to decide what clears it before a child arrives.

### Device left safe

`halloween_mode` and `presence_sensor_enabled` both set back to **false**
(two lines changed, JSON verified). `bender-converse` is `enabled`, so a reboot
would otherwise have brought the open mic straight back.

### Also this session: Docker and Open WebUI

Unrelated to the incident, done just before it.

- **Docker Engine 29.9.1** installed from the official trixie repo. `pi` added
  to the `docker` group. One new nftables table, one unused `docker0` bridge,
  nothing else disturbed.
- **`hailo-ollama` was already installed** (from `hailo-gen-ai-model-zoo 5.3.0`)
  and had never been run. It now has a systemd unit, enabled, on `0.0.0.0:8000`.
  It ships manifests for six models: `deepseek_r1:1.5b`, `llama3.2:1b`,
  `qwen2:1.5b`, `qwen2.5:1.5b`, `qwen2.5-coder:1.5b`, `qwen3:1.7b`.
  `qwen2.5:1.5b` is pulled (2.46 GB, in `~/.local/share/hailo-ollama/models/`).
- **Open WebUI** in a container, `--network=host`, port **8888**,
  `restart=unless-stopped` (not the book's `always`, so a manual stop survives
  a reboot).

**Open WebUI costs 1034 MB of the 4 GB.** Measured: `bender-converse` is only
152 MB, because Whisper and Qwen live on the Hailo chip, not in RAM. Open WebUI
is 6.8× Bender. Swap went 57 MB → 279 MB when it started.
**`docker stop open-webui2` before Halloween night.**

**Untested:** whether a chat request works while `bender-converse` holds the
KV-Cache. The incident interrupted that test. Expect it to fail, because the
KV-Cache is a singleton (measured 2026-07-30).

**Exposure, not yet addressed:** Open WebUI binds `0.0.0.0:8888` with no PIN,
on the LAN and the tailnet, and the first visitor becomes admin.

### Puppet mode DOES work in Halloween mode

Asked and answered from the code. `_halloween_loop` has an explicit
`if say_request.pending(): _serve_say_requests()` with a comment saying the
owner may still want to puppet on the night. `scripts/web/routes/puppet.py` has
zero references to `halloween`. `halloween_enabled()` gates the AI prompt, the
model, intent classification, the handler chain, the session greeting and which
loop runs — none of them the puppet path.

**Caveat:** the check sits BETWEEN sessions, so a puppet line waits for a child
mid-conversation to finish, up to `halloween_max_turns` (6). When idle the wait
is short, because `wait_for_presence` is bounded at 5s and loops back.

### Tests: 1489 passed, 1 skipped

`tests/test_halloween_end_session.py`, 8 tests, AST-based. Mutation-tested:
reverting the fix fails 5 of the 8.

---

## 2026-10-15 (2) — the dead vision links, and a room nobody looked at

Started as "investigate the dead vision links". It found three layers, the
second worse than the first.

### Layer 1: three frontend calls to routes deleted six months ago

`web/src/lib/api.js` held `getVisionPassive`, `enableVisionPassive` and
`disableVisionPassive`, all pointed at `/api/vision/passive`. `bfbd831` deleted
those three endpoints on 2026-04-02, and `c9743b2` had deleted the watcher
thread behind them minutes earlier. Neither commit touched the frontend.

`Config.svelte` polled the GET every 60 seconds. The device journal held **93**
`404 Not Found` lines for it, and it was still firing once a minute from a
browser on the LAN while this was being read.

A full audit of `api.js` against the device's own `openapi.json`: **3 of 39
frontend calls dead, all three these.** The other 36 are live.

### Layer 2: the live route could not work, and lied when it failed

`POST /api/vision/analyse` is live. The Ask Bender button calls it. It had
never been called once in the journal. It could not work:

1. `vlm.describe_scene` returned `""` for a failed look AND for an empty answer.
2. `vision.analyse_scene` flattened both into the same empty `SceneDescription`.
3. `scene.is_empty()` was therefore True either way.
4. The route prompted the AI with "your camera just scanned the room and
   detected nobody".
5. `_speak_in_background` takes `service_lease()`, which stops and restarts
   `bender-converse`.

So one click cost about 23s of deafness to hear Bender confidently describe a
room nothing had looked at. And that was the **only** reachable outcome: the
on-chip KV-Cache is a singleton, the resident conversation LLM holds it, and
the VLM load fails every time (measured 2026-07-30).

`cfg.vlm_enabled` did not stop it. Only `wake_converse.py:805` read that flag.

**`VisionHandler` had the same bug, and it is reachable by voice.** Ask "what
can you see" in a household session and the answer was "I don't see anyone.
Either the room's empty, or you've finally achieved irrelevance." Every time.
Halloween mode already drops `VisionHandler` from the chain, so the door was
never exposed.

### Layer 3: two Config controls that nothing reads

`vision_confidence_threshold` and `vision_allowlist` (25 COCO labels) had **0**
readers outside `config.py`. Both are YOLO settings. `0d0d4ff` replaced YOLO
object detection with the Qwen2-VL VLM, which returns one free-text sentence:
there are no boxes, labels or confidence scores left to filter. `config_schema.py`
does not model either key, so they passed on `extra="allow"`, got written to
`bender_config.json`, and were never read back. `vision_model`, `vlm_timeout`
and `vlm_lazy_poll_s` are orphans the same way.

So the whole Vision section of the Config page was inert. Every control in it.

### What changed

| decision | action |
|---|---|
| Delete, do not rebuild | The entire Vision section is gone from `Config.svelte`, with the three dead `api.js` calls |
| Gate it, and fix the lie | `/api/vision/analyse` returns 503 when `vlm_enabled` is false, 502 when there is no description, and never fabricates a scene |

Rebuilding passive mode was rejected. It means releasing the conversation LLM
to load the VLM on a timer, then reloading it. The LLM load alone is 9.7s
measured. Bender would go deaf on a schedule, and the owner's worst acceptable
failure is silence.

**The contract change to remember:** `vlm.describe_scene` now returns `None`
for a failed look and `str` for an answer. `SceneDescription.failed()` is
separate from `is_empty()`, and **every caller must check `failed()` first**.
An empty description is not evidence of an empty room.

### The guard that would have caught all of it

`tests/test_frontend_routes_exist.py` reads the FastAPI app's own OpenAPI
document and checks every `/api/` call in `web/src/lib/api.js` against it.
Nothing in the repo could see this seam before: the Python tests exercise the
routes that exist, and `npm run build` does not know what the server serves.

Mutation-tested: re-adding `getVisionPassive` to `api.js` fails the test and
names the function and the path.

Use `app.openapi()["paths"]`, never `app.routes`. On FastAPI 0.139 / Starlette
1.3 a route removed from `app.routes` still answers.

### Verified in a browser

Config page: no Vision section, the other seven sections intact, and the 60s
404 gone from the console. Puppet page, `vision_enabled` false: no Ask Bender
button, and a note saying why. `vision_enabled` true: the button appears,
fires, and renders the reply.

### Tests: 1481 passed, 1 skipped (was 1458)

Three of the new route tests passed alone and failed in the full suite. Cause:
`tests/test_vision.py` calls `importlib.reload(vision)`, so `web.routes.vision`
holds the OLD module and `patch.dict(sys.modules, ...)` never reaches it. The
real Picamera2 then ran and raised "No camera number 0 found". Patch the route
module's own names (`patch.object(vroute._vision, ...)`), the same fix
`tests/test_vision_handler.py` documents for the handler.

`test_vision_handler.py` was also rewritten. All five of its tests asserted
only `resp is not None`, and all five passed with the handler describing a room
it had never looked at.

### Side find, not acted on

701 MiB of orphaned partial downloads in the device model directory:
`.Qwen2-VL-2B-Instruct.hef.*.tmp`, four files, dated 2026-04-02. The disk has
81G free, so this is clutter, not a problem.

---

## 2026-10-15 — Puppet Mode is a real switch, and Halloween has a restart button

**16 days to Halloween.**

### Puppet mode did not exist

`toggleMode` sat in `web/src/lib/api.js` with **no caller**. `POST
/api/actions/toggle-mode` worked and nothing called it. The Puppet page was a nav
tab that loaded clips and a camera status, and never touched the session.

So a puppeteer typing lines for Bender had Bender answering the room over the
top. Puppet playback hands the RUNNING process a say-request over IPC
(`say_request.py`) to dodge a measured 23 s model reload, so the session stayed
alive by design and kept hallucinating replies.

Now there is a Puppet Mode switch at the top of the page:

| state | effect |
|---|---|
| ON | ends any live session, stops `bender-converse`, clears the IPC notes |
| OFF | clears the notes FIRST, then starts the service (~23 s to wake word) |

Opening the page also ends any live session on its own.

**The order is the whole fix, and the tests assert it.** Entering writes
`cfg.abort_file` and `cfg.end_session_file` BEFORE taking the service guard: the
abort note is what releases a guard held by an in-flight clip. Leaving clears
them BEFORE the start, or a stale end-session note ends the first real
conversation on turn one.

The switch keeps no flag of its own. It reads `GET
/api/actions/service-status`, because a second copy of that state can drift.

### The END button was slow for the same reason

Separate bug, found while tracing the above. The idle capture loop in
`wake_converse.py` never checked `cfg.end_session_file`. It did
`text = stt.listen_and_transcribe(...)`, then `if not text: continue` — straight
back into the capture. `stt.py` has no awareness of either note.

So the sidebar END button did nothing until the silence timeout happened to
fire, and a noisy room resets that timeout on every capture that returns text.

The loop now checks after the capture returns and before the `if not text`
branch. It calls `_cleanup_abort_files()` first, because `session.end()` →
`_remove_session_file()` clears `session_file` and `end_session_file` but **not**
`abort_file`, and `_check_abort_on_chunk` polls that at ~10 Hz. A leftover kills
the next session's greeting.

**Still not done:** `stt.py` cannot abort a capture in flight. Worst case after
END is one capture, ~6 s idle (`stt_speech_onset_timeout_s`) and up to 15 s
(`max_record_seconds`). No further reply is ever spoken. Threading a
`should_stop` callback into `stt._record_utterance` was set aside deliberately,
to keep the blast radius small before Halloween, and because
`tests/test_stt_onset.py:213` asserts a call site by exact source text.

### Halloween: Save & Restart Bender

A **Save & Restart Bender** button now sits in the Halloween section, shown
whenever `halloween_mode` or `presence_sensor_enabled` differ from the saved
copy. Save-then-restart as two separate steps is how the mode got flipped in the
UI and left inert on the device: `cfg` is read once at process start. It compares
against `savedConfig`, so flipped-and-flipped-back is not pending.

### Verified in a browser, not just in tests

A throwaway server with the four action routes faked, driven by Playwright, so
every state was reachable off-device. Confirmed both switch states, the pending
band appearing and clearing, and `halloween_mode` plus `presence_sensor_enabled`
written to disk. `bender_config.json` was restored afterwards.

Intercept faked routes in an `@app.middleware("http")`. Removing entries from
`app.routes` does **not** stop the original handlers answering on FastAPI 0.139.

### Tests: 1458 passed, 1 skipped (was 1437)

- `tests/test_web_actions_mode.py` (15) — asserts the ORDER. A fake
  `_toggle_mode_guarded` snapshots which notes exist at the moment systemctl runs.
- `tests/test_wake_loop_end_session.py` (6) — walks the AST rather than matching
  source text, for the `test_stt_onset.py:213` reason above. Mutation-tested:
  removing the new block fails 2 of the 6.

---

## 2026-10-13/14 — the presence gate is measured, fixed and verified, but still OFF

**17 days to Halloween.** The VL53L0X distance gate now works. It is not enabled.
Enabling it is a config change, not a code change. Read "Enabling it" below before
you touch the switch.

### The finding that cost an afternoon: XSHUT must be PULSED, not driven high

`digitalio` leaves a GPIO line in its last state when the process exits.
`pinctrl get 17` read `op dh pd` with the pin still **hi** and nothing running.
So a sensor left wedged by one process was found already-high by the next process,
driven high again, and never reset. The VL53L0X carries no other reset.

The failure looked exactly like dead hardware:

1. three stale reads of 8190 mm
2. `OSError [Errno 121]`, a bus NACK, three to five times
3. 35 consecutive `RuntimeError: Timeout waiting for VL53L0X!`, no recovery

It was deterministic. It reproduced at the identical read in three consecutive
runs, at two different timing budgets. One low pulse restored the sensor completely.

`_open_locked()` and `_reset_via_xshut()` now share `_pulse_xshut()`. The two paths
cannot drift apart. `probe_vl53l0x.py --xshut 17` pulses too, and says so in its banner.

**Diagnostic worth remembering:** when the sensor looks dead, run
`ssh pi@BenderPi.local pinctrl get 17` with nothing running. A `hi` there means the
line is stuck, not the sensor.

### The crash that the test fixture hid

`presence.py` did `import metrics`. That binds the **module**. `count()` lives on the
`MetricsWriter` class, and the singleton is `metrics = MetricsWriter()` at
`scripts/metrics.py:91`. Every other module does `from metrics import metrics`.

So all 15 `metrics.count(...)` calls in the gate raised `AttributeError`.
**Fail-open does not cover this.** Fail-open catches exceptions from the sensor,
inside `distance_mm()`. This one propagated out of `available()` and would have
taken `_halloween_loop` down on the very first open.

The `gate` test fixture monkeypatched `presence.metrics`, so no test could see it.
`TestMetricsSingletonIsImported` (5 tests) deliberately does **not** use that fixture.
One of them walks the AST of `presence.py` and checks every `metrics.*` call names a
real method. Verified: three of the new assertions fail against the old import.

Fixed in `7898839`. **The device ran the broken version from 653c5ec until
41bdd4f deployed on 2026-10-14.** Nothing crashed, because the gate was off.

### Reach comes from the signal rate, not the timing budget

The usual advice is that a longer timing budget reaches further. On this build that
is false. Measured on-device, sensor mounted behind the left eye, walking in from
3 m and back out. Zero errors in every run.

| setting | reliable to | best read | per pass |
|---|---|---|---|
| 0.25 MCPS (driver default), 200 ms | 641 mm | 872 mm | 0.40 s |
| 0.0938 MCPS, 500 ms | — | 1233 mm | 0.70 s |
| **0.0938 MCPS, 200 ms (shipped)** | 407-1267 mm, 18 consecutive reads | **1267 mm** | **0.40 s** |

500 ms bought nothing but delay. `presence_signal_rate_mcps` is the reach lever.
The driver quantises 0.1 to 0.09375.

**The noise ceiling is about 1200 mm**, from three independent numbers: an empty-field
stray at 1220 mm, a walk maximum of 1233 mm at 500 ms, and 1267 mm at 200 ms.

**Do not read the gate's read rate off the probe.** The probe's loop adds its own
0.20 s `--interval` sleep. In the production path the gate measured **0.207 s per
loop pass** at `presence_poll_s` 0.10, so the read itself costs about 0.11 s.

### Shipped calibration

```json
"presence_sensor_enabled": false,
"presence_xshut_gpio": 17,
"presence_max_mm": 1000,
"presence_clear_mm": 1400,
"presence_min_mm": 0,
"presence_signal_rate_mcps": 0.1,
"presence_timing_budget_us": 200000
```

`presence_max_mm` is 1000, not the 2000 the doorstep requirement asked for. 2000 is
not reachable on this build. The owner's own requirement made it moot: children come
to **arm's reach, 400-600 mm**, to take candy from the chest cavity. 1000 leaves
400 mm of margin and sits below the 1200 mm noise ceiling.

`presence_min_mm` stays **0**. The crosstalk baseline passed twice: 0 of 91 in range
at 0.25 MCPS, 0 of 135 at 0.0938 MCPS. The second run mattered. A lower signal rate
accepts weaker echoes, so it can start accepting a mount reflection that 0.25 MCPS
rejected. **Re-run the baseline after any change to the mounting or to the signal rate.**

```bash
venv/bin/python scripts/probe_vl53l0x.py --xshut 17 --long-range --baseline
```

**`presence_min_mm` is a FLOOR. It discards readings at or UNDER the value.** The
probe's own MARGINAL advice was wrong until 2026-10-13: it told the owner to set this
key to the maximum stray reading whatever the distance. For a stray at 1220 mm that
floor would have discarded every real reading under 1220 mm, and the gate would never
have opened. `_CROSSTALK_MAX_MM = 300` now splits near strays (the enclosure, raise
the floor) from far ones (the noise ceiling, do NOT raise the floor).

### The gate is verified end to end

Verified 2026-10-13 by enabling the gate in process and calling `present()` in a loop
at the shipped thresholds. Not simulated. The real sensor.

| run | result |
|---|---|
| empty field, 60 s | 769 reads, 0 errors, 0 arrivals, gate shut |
| **walk in and out** | **435 reads, 0 errors, 3 arrivals (767, 888, 848 mm), 3 departures, closest 322 mm, no flapping** |

Hysteresis works. The gate stayed **OPEN** at 1047, 1170 and 1241 mm, all past the
1000 mm entry gate. It closed two reads after the walker went out of range.

**The gate closes on an out-of-range reading, not on crossing `presence_clear_mm`.**
1400 mm is beyond what this sensor reliably sees. Do not describe 1400 as the normal
exit path. The band still does its job, as the hysteresis readings above show.

On-device log line, confirming the calibration is live:

```
Presence sensor ready at 0x29 (budget 199865.0 us, io timeout 0.80 s,
signal rate 0.09375 MCPS, gate <= 1000 mm)
```

### Halloween mode in the Web UI

The Config page now has a Normal/Halloween switch at the top (`41bdd4f`).

- The switch sets `halloween_mode` **and** `presence_sensor_enabled` together.
- **Both flags are needed.** They are independent in the code.
  `halloween_mode` alone gives an ungated open mic: 88.6 sessions/hour, deaf 79% of
  the time. `presence_sensor_enabled` alone is completely inert.
  `wait_for_presence()` is called in exactly one place, `wake_converse.py:160`,
  inside `_halloween_loop`.
- Clearing the gate checkbox decouples them. A red panel then quotes what that costs.
  The owner can still do it. The owner cannot do it unaware.
- In Halloween mode the **AI Backend, Home Assistant, Vision and LED** sections hide.
  **General, Maintenance and Watchdog stay** — speech rate and the health thresholds
  still matter on the night.
- `presence_*` keys are now modelled in `config_schema.py`, not passed through on
  `extra="allow"`. `presence_clear_mm` must exceed `presence_max_mm` or the save is
  refused with 422. Equal values would flap the mic open and shut.

**Not yet seen in a browser.** The bundle builds clean and contains the new strings.
No visual check was done. Look at it after the deploy.

### Three more defects, found 2026-10-14 by actually opening the page

The Halloween UI had never been looked at in a browser. It was served off-device
with the test suite's hardware stubs (`scripts/serve_web_local.py`) and driven with
Playwright. That took twenty minutes and found three real faults.

**1. Confirming reads rendered as an empty box.** `presence_confirm_reads` was not
in `bender_config.json`, and `GET /api/config` returns the raw file, not the merged
effective config. Fixed by pinning `presence_confirm_reads: 2` and
`presence_poll_s: 0.1` in `bender_config.json`.

**2. A null in `bender_config.json` crashed the gate.** This is the dangerous one.

- `getattr(cfg, key, default)` returns None when the attribute **exists** and holds
  None. The default only covers a **missing** attribute.
- `config.py`'s `_override_type_ok` accepts None for every key **by design**.
- So `int(None)` raised `TypeError` straight out of `present()`.
- **Fail-open does not cover that.** It catches sensor exceptions inside
  `distance_mm()`. This propagates past it and takes `_halloween_loop` down. The
  same shape as the `import metrics` crash, and found the same way: by running it.
- The route in: a Svelte `type="number"` input binds an emptied box to `null`, and
  the editor PUTs the whole blob. So clearing a box and saving was enough.

Every `cfg` read in `presence.py` now goes through `presence._cfg()`. It treats None
as unset and keeps a real `0` or `False`. 11 call sites. 9 new tests, all of which
fail against the old code.

**3. A null gate distance defeated the schema's hysteresis check.** `config_schema.py`
refuses a save where `presence_clear_mm <= presence_max_mm`. It cannot judge a value
it was never sent. Clear the gate-distance box, and `presence_max_mm` falls back to
the 2000 mm code default while the shipped `presence_clear_mm` of 1400 sits **under**
it. A child at 1800 mm is then inside the gate **and** past the release band at the
same time, so the mic flaps open and shut on every read.

`present()` now collapses an inverted band to `near_mm`, warns once, and counts
`presence_bad_band`. 4 new tests.

### And a 500 that had been there all along

Every 422 from a custom validator was really a **500**, on every config key, not just
the presence ones.

A pydantic validator that raises `ValueError` leaves the exception **object** in each
error's `ctx`. `e.errors(include_url=False)` keeps it. Starlette then cannot encode
the `HTTPException` detail:

```
TypeError: Object of type ValueError is not JSON serializable
```

So the Config editor showed "500 Internal Server Error" and named no field, for
`oww_threshold`, `ha_url`, `oww_model_path`, the path-injection keys and the presence
bounds alike. The save was still refused, so nothing was ever corrupted. Only the
diagnosis was lost.

**Why it survived:** `test_config_schema.py` exercises the **model**, never the HTTP
layer. And pydantic's own **type** errors carry no `ctx`, so a bad type returned a
correct 422. Only a bad **value** broke.

`_field_errors()` in `scripts/web/routes/config.py` now passes `include_context=False`.
It also drops `input`, because a whole-model validator's `input` is the **entire**
submitted body — a save carrying `ha_token` got it echoed straight back in the error
body. `tests/test_web_config.py` covers both, through the route. 10 of the new tests
fail against the old code.

**The lesson worth keeping:** schema tests are not route tests. A validator can be
perfect and still return the wrong status.

### What the browser check actually showed

All three states render correctly. Screenshots were taken of each.

| state | result |
|---|---|
| Normal | Halloween card at the top, every section visible |
| Halloween | AI Backend, Home Assistant, Vision and LED hidden; General, Maintenance and Watchdog kept |
| gate cleared | red panel quoting 88.6 sessions/hour, 79%, 22-45s |
| release under gate | inline red warning, and the save refused with the server's own message |

Serve it off-device again with:

```bash
BENDER_WEB_PIN=<pin> venv/bin/python scripts/serve_web_local.py
```

That script stubs `board`, `busio`, `neopixel_spi` and the rest the way
`tests/conftest.py` does. The dev clone has no adafruit-blinka.

### Enabling it — the order matters

1. Confirm the device is on `41bdd4f` or later. **Not 653c5ec.** That commit has the
   metrics crash, so `presence_sensor_enabled: true` would take the service down.
2. Open the Config page. Switch to Halloween. Save.
3. Restart `bender-converse`.
4. Watch for `Presence sensor ready` and for `presence_read_error` in the metrics.
5. **A family test evening before any child speaks to Bender.**

**Halloween mode REFUSES when `ai_local is None`.** It logs `HALLOWEEN MODE REFUSED`,
counts `halloween_refused`, and falls back to the wake-word loop. It will not speak a
raw error line to a child.

Mind the restart budget. `StartLimitIntervalSec=300` / `StartLimitBurst=5`. Space
restarts out, or `sudo systemctl reset-failed bender-converse` between them.

### Still open

- The original 3-minute D-state hang in `read_range()` is **still unexplained**. It is
  contained, not fixed: `io_timeout_s` turns it into a counted `presence_read_error`
  that escalates to the XSHUT power-cycle. Watch that metric once the gate is live.
- Confirm the lug hole pitch and diameter, then set `posts = true` in
  `hardware/vl53l0x_eye_mount.scad`.
- Record doorstep ambience: `capture_background.py --label doorstep --hours 2`.
- The Halloween UI has not been looked at on the DEVICE, only off-device at
  `scripts/serve_web_local.py`. The rendering is the same build; the data is not.

### The dev-clone server caused two false alarms, both mine

Handing the owner a URL for `serve_web_local.py` cost an hour. Read this before
doing it again.

**It had two bugs that made it unreachable.** It bound `127.0.0.1` only, so the
URL worked only in a browser on the same box. And it came up on port **8080**,
not 8099: `config.py` loads `.env` at import, the dev clone's `.env` carries
`BENDER_WEB_PORT=8080`, and the port was read AFTER importing the app, so
`os.environ.setdefault` had already filled it in. The 8099 default never
applied. Both fixed. The port and host are now read before the import, and
`BENDER_WEB_HOST=0.0.0.0` opts into the LAN with loopback still the default.

**Then it produced watchdog alerts for a healthy device.** Three of them,
including an error-severity "mic likely needs a physical reseat". The giveaway
was the path in the third: `/home/pi/projects/benderpi/logs`, the dev clone, not
`/home/pi/bender/logs`. Cause: the test suite wrote to the repo's own
`logs/metrics.jsonl`. It imports the real `metrics` singleton, the mic-stall
tests emit real `wake_loop_stall_reinit` and `wake_loop_stall_exit` counts, and
`mic_stall_reinit_threshold` is 3. Two suite runs were enough.

| | dev clone | device |
|---|---|---|
| `wake_loop_stall_reinit`, 24h | 24 | **0** |
| `wake_loop_stall_exit`, 24h | 24 | **0** |
| `session_start`, all time | **0** | normal |
| `NRestarts` | n/a | **0** |

The dev clone's 48,548-line metrics file spanned 2026-05-09 to 2026-10-14 and
held **zero** real sessions. All of it was test exhaust. It is backed up in the
session scratchpad and moved out of `logs/`.

Fix: `metrics.py` and `logger.py` both honour **`BENDER_LOG_DIR`**, and
`conftest.py` sets it to a temp directory before anything imports them. Proven
by byte-comparing `logs/metrics.jsonl` across a full suite run: unchanged.
`tests/test_metrics.py::TestTheSuiteDoesNotWriteToTheRepoLogs` holds it, 4 tests,
2 of which fail with the override removed.

**And puppet mode appeared broken.** The owner clicked soundboard clips for
several minutes and heard nothing. They were on the dev-clone server. It has no
speaker, `pyaudio` is a stub, the 88 clips in `speech/wav/` are gitignored and
absent, and `service_guard` shells out to `systemctl stop bender-converse` for a
unit that is not installed. The device UI logs confirmed it: `bender-web` on the
device had served nothing but my own curl checks, while the dev server held an
established connection from the owner's browser. The device itself was fully
stocked, 466 response clips and 88 originals.

Fix: `serve_web_local.py` now returns **503 with a sentence** for `/api/puppet`,
`/api/vision`, `/api/camera`, `/api/actions`, `/api/config/volume` and
`/api/mic`. Refusing beats pretending. The startup banner says the same thing.

**The rule:** the dev-clone server is for LOOKING AT PAGES. Anything that makes
a sound, moves hardware or restarts a service belongs on the device UI, port
8080.

The third alert was true but useless, so it is gone too. `check_session_liveness`
now says nothing where `service_liveness_unit` is not installed on the box, which
is the guard `_probe_unit_state` has carried all along. Only an EXPLICIT "not
installed" silences it: `_unit_is_installed()` returns True/False/None, and None
("systemctl missing or the call failed") still alerts. Verified on the device:
`LoadState=loaded`, so its alerting is untouched. The three pre-existing tests in
`test_watchdog_session_liveness.py` now inject `installed_probe` — without that
they passed on the device and failed on a dev clone, which is worse than either.

### Two more defects the browser found, neither fixed

- **`/api/vision/passive` does not exist.** `web/src/lib/api.js:123-125` calls
  GET, POST and DELETE on it. Only `/api/vision/analyse` is implemented. The
  device returns 404 too. So the Config page shows a red **"Not Found"** under
  Vision -> Passive Mode on every load, six buttons (OFF, 15m, 30m, 1h, 3h, ∞)
  do nothing, and `Config.svelte:33` re-polls every 60s so the error cannot
  clear. Halloween mode hides the Vision section, so it disappears there by
  luck. Needs a decision: implement the routes, or remove the dead control.
- **The three UI fonts never load.** `web/src/app.css:1` imports Orbitron,
  Rajdhani and JetBrains Mono from Google Fonts. The CSP at `app.py:47` is
  `style-src 'self' 'unsafe-inline'`, which blocks it. Cosmetic, and present on
  the device. Self-hosting the fonts suits the offline-first rule. Relaxing the
  CSP to call Google from a doorstep device does not.

---

## 2026-09-30 — v0.3 SHIPPED for near-field; far-field is open work

**Deployed:** `models/hey_bender_v0.3_r35.onnx` at `oww_threshold` 0.10,
live 11:38 on 2026-09-30. Rollback is one command, v0.1 is still on disk:

```bash
bash scripts/deploy_hey_bender.sh hey_bender_v0.1.onnx --threshold 0.1 --no-eval
```

**Measured, 26 clean held-out clips, threshold 0.10, `--profile near_field`:**

| | v0.1 (was deployed) | v0.3_r35 (now deployed) |
|---|---|---|
| near-field recall | 25% | **92%** |
| far-field recall | 0% | 14% |
| hard-negative false wakes | 0/28 | 3/28 (11%) |
| household ambient | 0/h | 0/h |
| close-range conversation | 0/h | 0/h |

So talking to it from ~1m should work ~11 times in 12, against 3 in 12 before.
Across the room it still mostly will not answer.

**Scope decision (owner, 2026-09-30):** keep the wake phrase, accept near-field
now, treat far-field as later work. That is why `eval_wake_model.py` has two
gate profiles rather than one relaxed set — `full` is unchanged, `near_field`
gates near-field recall at 70% and household false wakes at zero, and prints
far-field recall without gating it so a dropped target cannot quietly stop
being measured.

**What was tried, so nobody repeats it.** Five training runs, ~$3.60 of a $21
HF balance:

| run | recall @0.35 | note |
|---|---|---|
| v0.1 synthetic only | 2/26 | the baseline |
| v0.2 r20 / r35 | 6/26, 8/26 | trained on the MISLABELLED clips |
| v0.3 r35 (clean) | 6/26, near-field 92% @0.10 | shipped |
| livekit-wakeword v1 | 2/26 | no better; see below |

**livekit-wakeword is NOT a drop-in**, contrary to the research summary. The
same model scoring the same audio reads 0.965 through openWakeWord's streaming
front-end and 0.005 through livekit's stateless one, so a livekit model needs
livekit's engine. Its `predict()` also re-scores a full 2s window per call:
34.8 ms measured on the Pi 5, i.e. 44% of one core at an 80 ms hop, 22% at
160 ms. The wake loop was deliberately never wired to it. `scripts/
train_wakeword_livekit_hf.py` works and is kept; it used 14% real clips,
conv_attention/small, 50k steps — a tuned run (medium, 100k steps, 35% real)
is untested.

**Far-field follow-up, when it matters.** Every model scored 0/2 on
`far_normal`, `another_room`, `seated_far`, `moving` and `off_axis`. The
cheapest untried levers, in order: 100+ positives captured specifically at 3m
and off-axis; then a tuned livekit run; then the wake phrase itself, which the
owner has ruled out for now ("bender" sits among vendor, bend, blender,
gender, lavender, surrender, remember, defender).

**Watch for:** false wakes in ordinary use. Held-out ambient and conversation
are 0/hour, but v0.3 does wake on 3 of 28 deliberately-adversarial phrases
("hey vendor", "hey bend"), where v0.1 woke on none. If phantom wakes appear,
`grep -a "Wake word detected" logs/bender.log` and raise `oww_threshold`
before suspecting anything else.

---

## 2026-09-22 → 09-29 — wake-word v0.2/v0.3: a mislabelled dataset, and where it left us

**Read this before touching the wake word.** Every recall number recorded
before 2026-09-29 is void.

**What went wrong.** 115 of the 130 captured "positive" clips did not contain
"hey bender". The capture prompts described the recording CONDITION and never
printed the words, so the speaker read the condition aloud: close_normal_002
is "This is my normal speaking voice", far_normal_001 is "3 metres away",
off_axis is "facing away from the device". openWakeWord labels by directory
with no transcript, so those clips trained as the wake word, copied 50-88
times each. Two GPU runs, ~$1.80 and a day of analysis rested on it. Nothing
downstream could see it — clean audio, correct levels, happy VAD, and a
manifest that recorded the level and the model score but never what was said.

**Fixed.** `capture_wake_samples.py` prints the exact words for every clip and
classifies each recording with on-device Whisper (ok / unclear / wrong); the
transcript and verdict go into the manifest. `audit_wake_samples.py` applies
the same check to old clips and quarantines failures. Whisper CANNOT confirm a
2s far-field clip — it renders a correct "hey bender" as "Hey Pender", "Hey,
Sander", "A feather", "Ebato" — so only a sentence with no phrase-like token
is auto-rejected; short mangled ones are kept. A strict match would have
condemned 84 of 116 good recordings, and briefly did.

**Current dataset (device, gitignored, mirrored to the private HF dataset
`Schmalvis/bender-wake-samples`):** 109 positives (83 train / 26 held out),
150 hard negatives, 20 min ambient, 10 min conversation, 107 clips in
`quarantine/`. Split frozen in `data/wake_samples/split.json`.

**True baseline, 26 clean held-out clips, live 2-of-4 smoothing, @0.35:**

| model | recall | ordinary speech | hard-neg FP | ambient FP |
|---|---|---|---|---|
| v0.1 (synthetic only) | 2/26 (8%) | 1/12 | 0/28 | 0/h |
| v0.2 r35 (poisoned clips) | 8/26 (31%) | 4/12 | 2/28 | 0/h |
| v0.3 r35 (clean clips) | 6/26 (23%) | **5/12 (42%)** | 2/28 | 0/h |

Real clips clearly beat synthetic-only. Clean vs poisoned differs by two
clips, which is inside the noise of a 26-clip set — do not over-read it. The
plateau is ~25-35% recall against an 80% ship gate. **Nothing has shipped:**
`deploy_hey_bender.sh` refuses a model that fails the gates, and v0.1 is still
the deployed model.

**Tooling built this week:** `split_wake_samples.py` (frozen split),
`eval_wake_model.py` (held-out scoring with the live smoothing, ship gates,
corrupt-mic guard), `audit_wake_samples.py`, `upload_wake_samples.py` (private
HF dataset), `train_hey_bender_hf.py` (HF Jobs; the Modal script is superseded
— HF has the model repo, the data and the credit). Sweep and eval cost ~$2.70
of a $21 HF balance.

**Next levers, in order of expected value:**
1. More real clips — 300-400, and a second speaker. 83 training clips is thin.
2. Migrate to **livekit-wakeword** (Apache-2.0, same frozen front-end as
   openWakeWord so the wake loop and config keys survive; generates adversarial
   negatives automatically). Researched 2026-09-28: Picovoice is out (free tier
   ended 30 June 2026), sherpa-onnx KWS measured 14% recall on these clips,
   microWakeWord is the hedge.
3. **Change the wake phrase.** "bender" sits in a dense phonetic neighbourhood
   — vendor, bend, blender, gender, lavender, surrender, remember, defender —
   and nobody has costed this option.

---

## 2026-09-08 — Evening of use (batch 2 verification) + commit 4

Batch 2 commits 1–3 were live since 2026-09-03 18:26. The evening-of-use script
(plan §Verification step 4) ran 2026-09-08 12:28–12:45, groups 1–5 and 7 (group 6,
the background-chatter test, needs a second person and is still pending).
Logs: `logs/2026-09-08.jsonl` on the device, sessions `d8aee88a` … `468d9d53`.

**First, the morning was lost to a dead mic.** "Hey Bender" got no response at
all; the wake loop logged peak score 0.001 all morning. Root cause was NOT the
model or batch 2: the XVF3800 came up corrupt at the 07:00 cold boot (91.7% zero
samples, a kernel `buffer overrun` per USB packet). Service restart, USB
unbind/rebind and a sysfs port power cycle did nothing; a physical replug fixed it
instantly. Documented in CLAUDE.md → Known Issues. The daily 22:00/07:00 power cycle
makes this a recurring risk; nothing detects it yet (the stddev sentinel is fooled
by the 1-in-12 real samples). **Next: a startup self-test that measures the zero
fraction and raises a loud metric/HA alert.**

**Results against the five criteria** (9 local-LLM turns, 6 wakes at 0.161–0.947):

| Criterion | Result |
|---|---|
| zero refusals / stage directions / quoted replies | no refusals, no stage directions; **one** quote-wrapped 3-sentence reply (opening mark on sentence 1, closing on 3 — the pair-only stripper missed it) |
| no reply > 3 sentences | pass (cap fired twice) |
| `turn_total` p95 < 15s | **fail**: 32.7s (markdown ransom note, first sentence 45 tokens), 17.5s, 13.8s; the rest 5.4–13.3s. `ai_hailo_ttfs` 1.1–4.6s except the 11.0s markdown turn |
| < 2 rejected 120ms captures / session | **fail**: 1–4 per session, ten in a row in the first test session, one every 0.97s at RMS 14–47 (speech: 238–2277) |
| ≥ 1 cloud escalation on the refusal bait | **fail**: the model complied with a markdown document and nothing in the gate matched it |

Other findings: "Are you an AI?" → "…from the TV show Futurama. How can I help you
today?" (fourth wall + helpdesk line, gate passed it); follow-up 3 of the multi-turn
test spoke `😅 [{'type': 'text', 'text': …` before the `<|im_start|>` behind it was
caught; "Tell me about the year 3000" matched the PERSONAL/age clip (`year` in the
pattern) so the long-answer test never reached the model; a real "Okay." (1.7s,
RMS 1229) was discarded by the `whisper_hallucinations` blocklist.

**Commit 4 (this commit) fixes all of the above except recall:**
- `stt.py`: `stt_vad_warmup_frames` (5). Reproduced offline: a *fresh* webrtcvad
  instance flags its first 3–5 frames as speech on quiet-room audio in every mode,
  and every capture builds a fresh instance — that was the periodic 120–150ms
  artefact, not room noise. Level-independent, so `stt_min_speech_rms` stays off.
- `ai_local.py`: `_FORMAT_BREAK_RE` (markdown heading/rule/bold/bullet, JSON
  payload) is a derail on any sentence and a `format_break` gate failure on
  sentence 1; `_clean_sentence()` strips an unpaired double quote and emoji;
  `HARD_FAIL_PHRASES` += "how can i help", "tv show".
- `intent.py`: age pattern no longer matches a bare `year`.
- `bender_config.json`: `okay` removed from `whisper_hallucinations`.
- Tests: +4 `test_stt_onset.py`, +2 `test_intent.py`, +15 `test_reply_cleaning.py`,
  +3 `test_decode_control.py`.

**Commit 5 — corrupt mic stream: detect, auto-recover, alert.** Software
recovery exists after all: the array's own `REBOOT` command over its USB vendor
control interface (`scripts/xvf3800.py`; protocol from respeaker's
`python_control/xvf_host.py`; Seeed support's fix for the same firmware fault
after warm reboots). Verified on-device 2026-09-08 13:41: off the bus at 0.25s,
back at 1.0s, stream clean. Pi 5 cannot cut USB VBUS per port (all ports ganged,
root disk on USB), so this is the only hands-free reset.
- `audio.mic_selftest()` reports `zero_frac` / `corrupt` (`mic_zero_frac_max` 0.5).
- `wake_converse._recover_corrupt_mic()`: REBOOT, wait, re-measure; metrics
  `mic_stream_corrupt` → `mic_stream_recovered` or `mic_stream_corrupt_persisting`.
- Wake loop: exact-zero-fraction sentinel (`wake_corrupt_alarm_s` 30s) → REBOOT →
  stall escalation. Also logs `peak zero N%` on the per-minute idle line.
- `watchdog.py`: `mic_stream_corrupt` alert — warning when recovered, error
  "unplug and replug" when persisting.
- Device: `udev/99-respeaker-xvf3800-control.rules` applied (plugdev write access);
  `pyusb` added to requirements (the deploy installs it; the device already had it
  in pi's user site-packages).
- **PROVEN 2026-09-09.** The array came up corrupt again at the 07:00 cold boot
  (91.9% zeros, `max_rms` 5.3). Self-test caught it 07:00:33 → `REBOOT` →
  re-enumerated in 1.38s → second self-test 2.6% zeros 07:00:36 → listening
  07:01:00. 3.5s, fully automatic, `NRestarts=0`. Metrics in order:
  `mic_selftest`(ok=false) → `mic_stream_corrupt` → `xvf3800_reboot`(ok=true) →
  `mic_selftest`(ok=true) → `mic_stream_recovered`. The fault is a **daily**
  risk, not a one-off — two corrupt cold boots out of two observed.
- Tests: `tests/test_xvf3800.py` (5), +3 `test_mic_selftest.py`, +3
  `test_watchdog.py`, +3 `test_wake_loop_heartbeat.py`. Suite 937.

**Next:** deploy commits 4 and 5; group 6 of the script when a second person is around;
`capture_wake_samples.py` (still zero samples — recall is now the dominant failure:
the user "had to repeat a lot"); the mic zero-fraction self-test; then batch 3.

---

## 2026-09-03 — Fourth review + batch 1 fixes

Review: `docs/benderpi-fable-project-review-2026-09-03.md` (59 findings, verified;
five sub-reviews + live device evidence). Batch 1 of its sequencing is implemented
here, **uncommitted at time of writing** — push deliberately, it auto-deploys.
Suite: 832 passed (806 + 26 new in `tests/test_batch1_regressions.py`).

| Ref | Fix | Where |
|---|---|---|
| C1 | `speak_from_iter()` now sanitises every sentence; an emoji-only or tag-only sentence is skipped. This was the only live LLM→Piper path and it bypassed `f0d215d` entirely | `tts_generate.py` |
| C2 | Cloud stream yields its final sentence (was never spoken; one-sentence replies were silence) | `ai_response.py` |
| C3 | Voice alarms are naive local datetimes; `timers` now normalises to aware UTC at the boundary and on load, and `check_fired` skips a malformed entry instead of raising on every main-loop pass | `timers.py` |
| H2 | `audio._lock` is an `RLock`: `play()` called `open_session()` while holding it and deadlocked whenever the output stream was lost | `audio.py` |
| H5 | `service_guard._start_converse()` checks the exit code, logs `start-limit-hit`, tries `reset-failed` + one retry, exposes `last_start_failed` | `web/service_guard.py`; new sudoers grant on the device |
| H6 | Missing `ANTHROPIC_API_KEY` no longer crashes boot; responder routes `local_only` when no cloud responder exists | `wake_converse.py`, `session.py`, `responder.py` |
| M21 | `classify()` strips trailing `.!?,` — "Okay." was UNKNOWN and cost an LLM turn | `intent.py` |
| L9 | Hard-timeout clip looked up under `promoted/` where prebuild writes it | `session.py` |
| L7 | Weather text collapses the double space left by an empty wind line; sanitiser compares whitespace-normalised text so it no longer warns every morning | `briefings.py`, `tts_generate.py` |
| M12 | `.gitignore`: `.env.*`, `*.bak`, `hailort.log`, `memory/`, `.understand-anything/` | `.gitignore` |

Device-side (done over SSH, not auto-deployed):
- `.env.bak.20260710170727` moved out of the repo dir to `/home/pi/.env.bak.20260710170727` (mode 600).
- journald is now persistent: `/etc/systemd/journald.conf.d/50-persistent.conf`. See CLAUDE.md.
- `/etc/sudoers.d/bender-web-reset-failed` added for H5.

**Deliberately not done:** disabling glances/rpcbind (review L17). `rpcbind` +
`rpc-statd` are required by the NFSv3 mount of `/home/pi/projects` from the QNAP.
glances is likely fleet monitoring. Bind glances to 127.0.0.1/tailscale instead.

**Next:** batch 2 (session quality: H7 control tokens, H8 gate phrases, H9 sampling,
M1 VAD onset, M2 idle timeout, M5 reset-after-with) then one evening of real use,
capturing wake-word samples in the same sitting. Plan with exact code changes,
tests, the on-device sampling spike and the evening script:
`docs/superpowers/plans/2026-09-03-batch2-session-quality.md`.

Batch 2 progress:
- **Commit 1 (M1, M2) done, not yet deployed:** `stt_onset_frames` (3) onset gate,
  `stt_speech_onset_timeout_s` (6.0), flush only after playback, idle clock starts
  after the reply, monotonic time in the session loop. 14 tests in
  `tests/test_stt_onset.py`. Suite 846. Deploy together with commit 2 (one restart).
- **Commit 2 (H8, M5) done, not yet deployed:** `_clean_sentence()` strips wrapping
  quotes, speaker labels, and stage directions from every streamed sentence before
  the gate and the log; `HARD_FAIL_PHRASES` gains ten refusal openers; the gate
  takes `stream=True` (min 3 chars, hedge judged on length only); on a gate failure
  the stream path leaves the completion first and resets the on-chip context while
  still holding `_infer_lock`; `_reset_context()` takes the lock non-blocking and
  defers via `_context_dirty` when a zombie generation holds it. One new system-prompt
  rule against self-quoting. 35 tests in `tests/test_reply_cleaning.py`. Suite 881.
- **Commit 3 (H7, H9) done, not yet deployed:** `_hailo_sampling_kwargs()` (random
  seed per call, `top_p` 0.9, `frequency_penalty` 1.1, `do_sample`, 80-token local
  cap) shared by both Hailo call sites and mirrored to Ollama; stop after
  `ai_max_sentences` (3); a chat-template token on any sentence stops decoding,
  drops the sentence, clears the on-chip context (`ai_hailo_derailed`); a tail cut
  mid-sentence by the cap is dropped once something has been said
  (`ai_hailo_truncated_tail`). Spike result is in the batch-2 plan: the penalty
  scale is HF-style, 1.1 is HailoRT's own default, 1.0 repeats, 1.3 derails.
  16 tests in `tests/test_decode_control.py`. Suite 897.
- `review_log.py` (M20): counts the live AI method names; the promotion list works again.
- **Next:** push (one deploy), then the evening-of-use script in the plan, then
  `capture_wake_samples.py` in the same sitting.

---

## 2026-07-30 (2) — Hailo token streaming + on-chip context protocol fix

Recommendation #2 of the Fable architecture review, plus a same-day fix to a bug
that #1 (residency) introduced.

### The bug residency introduced

HailoRT keeps conversation context **on-chip** between `generate()` calls and
**rejects a system-role message on any call after the first**:

```
[HailoRT] [error] CHECK failed - System role messages can only be provided on the first prompt
```

`_HailoLLMResponder.generate()` sent `[system, *history]` every turn. That was
fine while the LLM object was destroyed and rebuilt per turn (every turn got a
fresh context) — residency removed that reset, so **turn 2+ of any multi-turn AI
session raised HAILO_INVALID_OPERATION(6) and fell over to Ollama**. Shipped in
the morning, found and fixed the same day by the streaming spike.

The fix is the correct protocol: system prompt once per context, then only the
new user message. `_HailoLLMResponder` now tracks `_context_fresh` /
`_context_turns` instead of keeping a message list (the chip *is* the history).
`clear_context()` is called on session end, on a quality-gate rejection, and
every `ai_max_history` turns to bound on-chip growth.

**Not resending history is also a speed win, not just a constraint:** TTFT drops
from ~1101ms on a fresh context to **~371ms** on later turns, because the model
stops re-encoding the whole conversation.

### Real token streaming

`LLM.generate()` is a token-yielding context manager on HailoRT 5.3. The old
comment in `ai_local.py` — "Hailo doesn't expose token-level streaming" — was
simply false, and the path faked streaming with `generate_all()` + one yield.
It now streams sentences into the existing `ResponseStream` pipeline.

Details that took a spike to establish:
- `<|im_end|>` arrives as its own discrete token; filtered per-token, stripped
  from the buffer, and a trailing partial `<|…` fragment is dropped so it can
  never be spoken.
- Aborting mid-stream does **not** wedge the LLM — the next `generate()` is fine.
- Ollama failover now only applies **before the first sentence is yielded**.
  Mid-reply, the turn ends early rather than splicing a second model's voice on.
- `_flush_sentence()` is now shared by the Hailo and Ollama paths, so sentence
  segmentation (and therefore where TTS starts) is identical on both.

### Measured on-device (real implementation, real hardware)

5-turn sustained session plus a new session after `clear_history()`:

```
first-sentence latency: min 665ms  max 3580ms  mean 2125ms  (n=7)
code failures: 0
```

Baseline `ai_local_first_sentence_ms` median was **14,563ms**. New metric
`ai_hailo_ttfs` records time-to-first-sentence directly.

One quality-gate rejection occurred mid-run and the responder recovered cleanly
— that gate is load-bearing: the model does still occasionally open with an
out-of-character "I'm an AI…", especially on identity questions.

### Verification

20 new tests (`tests/test_hailo_stream.py`) covering the context protocol,
streaming, the quality gate, and failover semantics. Full suite 647 passed vs
597 at the pre-#1 baseline, with an identical 64-failure set throughout (those
64 are pre-existing on a dev box lacking `board`/blinka).

`tests/test_ollama_stream.py::test_local_ai_responder_stream_hailo_success_yields_full_text`
was rewritten — it asserted the old fake-streaming contract ("yields the full
text as one item"), which is exactly what this change removes.

### Note for future on-device work

Now that the LLM is resident, **`bender-converse` holds the KV-Cache
permanently**, so a second process cannot load an LLM or VLM while it runs
(`HAILO_INVALID_OPERATION(6)`). Any LLM spike now needs
`sudo systemctl stop bender-converse` first. Whisper is unaffected.

---

## 2026-07-30 — Hailo residency: models held for the process lifetime

**Status: implemented on `main` (uncommitted at time of writing), verified on-device,
NOT yet deployed.** Deploying means pushing to `main`, which auto-deploys within ~5
minutes and restarts the live service — do it deliberately.

### What changed

New `scripts/hailo_hub.py` owns the Hailo VDevice and holds **Whisper-Small and
Qwen2.5-1.5B resident for the life of the process**. `stt.py` and `ai_local.py` borrow
handles from it. The per-turn release/reload choreography (`stt.release()`,
`release_chip()`, `reset_hailo()`) is now a no-op when `hailo_resident` is true —
the calls are still there, still tested, and still work when it's false.

Both models pre-load on the existing startup warm-up thread, so neither the first wake
word nor the first AI turn of a process pays an init cost.

### Why this is safe now (it wasn't before)

The blocker was "can Whisper and Qwen HEFs coexist on the Hailo-10H?" — the question
that gated `llm_warm_session`, `vlm_enabled`, and any second model. **Answered: yes.**
Hailo's own `voice_assistant` reference app builds one VDevice in the `SHARED` group,
constructs both `Speech2Text` and `LLM` on it, and holds both for the process lifetime.
Reproduced on BenderPi.

The real constraint is narrower: the **KV-Cache is a singleton**. Whisper doesn't use
it; LLM and VLM each need exclusive hold. So `Whisper + (LLM XOR VLM)`. That is the
mechanical reason `vlm_enabled` stays off, and it also kills the idea of running a
function-calling model *alongside* the chat model.

### Expected win (measured, not estimated)

- `ai_hailo_load` median **8,424ms on every AI turn** → once per process.
- Whisper reload ~2.5s per post-AI turn → once per process. This one was previously
  *invisible*: it ran untimed inside `listen_and_transcribe()` before the mic opened,
  so it also delayed the start of recording. Now timed as `ai_hailo_stt_load`.
- `ai_local_first_sentence_ms` median **14,563ms** should drop to ~4–6s even before
  streaming lands.

### Verification done

- 27 new tests (`tests/test_hailo_hub.py`, `tests/test_hailo_resident_wiring.py`), all
  passing. Full suite: 624 passed vs 597 at HEAD, with an **identical 64-failure set**
  both before and after — those 64 are pre-existing on a dev box without `board`/blinka
  and are unrelated to this change.
- On-device smoke test against real hardware: both models resident on one shared
  VDevice (STT load 2,989ms / LLM load 9,717ms), repeat getter calls cached with no
  reload, Whisper inference working with the LLM resident, and `close()` releasing
  models + the shared VDevice cleanly and idempotently. The live service was untouched
  and healthy afterwards.

### The one unproven thing

Multi-week stability of a permanently-held VDevice. Spike-verified for minutes and
backed by the vendor pattern, but only a soak proves weeks. That is exactly what
`hailo_resident: false` is for — a one-config-edit rollback, no code change.

**After deploy, check:** `ai_hailo_load` should appear once per process instead of once
per AI turn (visible in STATUS.md, no new instrumentation needed). If the accelerator
misbehaves, flip `hailo_resident` to false and restart.

### Next

This is recommendation #1 of `docs/benderpi-fable-architecture-review-2026-07-30.md`.
**#2 is real Hailo token streaming** — `ai_local.py`'s comment claiming "Hailo doesn't
expose token-level streaming" is **false** on HailoRT 5.3: `LLM.generate()` is a context
manager that yields tokens (measured TTFT **1.1s** with the full Bender system prompt,
6.9 tok/s). Today the Hailo path fakes streaming by calling `generate_all` and yielding
once, which is most of the remaining first-sentence latency. #1 + #2 together take first
audio from ~15.5s to ~3.5–5s.

---

## 2026-07-10 — Fable review implementation session (branch `fable-review-fixes`)

The Fable multi-agent review (`docs/benderpi-fable-review-2026-07-09.md`, 27 agents,
88 findings → 15 prioritized groups) has been implemented end-to-end on
`fable-review-fixes`. **Not yet merged to main or pushed** — do that deliberately,
after re-reading the auth-hardening deploy-order warning just below, since a couple of
these changes fail closed on missing config and auto-deploy will restart the live
service within ~5 minutes of the merge landing.

**What shipped, roughly in dependency order:**
- **Mic-stall watchdog** (`3fd6fdf`) — all blocking mic reads (wake loop + STT) now go
  through `audio.MicReader`, a daemon thread + queue with a read timeout; a wedged USB
  read raises `MicStallError` instead of hanging forever, escalating to a bounded reinit
  then `sys.exit(1)` for systemd to restart. Directly targets the failure mode below.
- **Wake-word N-of-M smoothing + RMS sentinel + training sweep** (`c5d4450`) — see the
  2026-07-09 wake-word section below for detail; still needs on-device threshold
  calibration.
- **STT confidence-gated hallucination filtering** (`999071b`).
- **Hailo warm-LLM session mode** (`28bb70b`) — opt-in, default off; see the 2026-07-09
  warm-Hailo section below.
- **Web auth hardening** (`6c13e77`) — PIN → signed ~12h token, rate limiting, fails
  closed on missing/placeholder PIN. **Deploy-order-sensitive — see the warning below.**
- **Config PUT validation** (`b563476`) — typed pydantic schema for `PUT /api/config`.
- **WM8960 access serialization** (`fb94907`) — `service_guard.py` centralises the
  puppet/vision/mic-stream stop-restart race behind one lock; bounds two previously
  unbounded web streams (camera MJPEG, mic websocket).
- **hey_bender_v0.1 config default** (`1b33e40`) — committed config now points at the
  trained model, fails loudly at startup if it's missing rather than silently falling
  back to a bundled model.
- **Secrets validation** (`cc7d81d`) — `Config.validate()` warns loudly on missing
  secrets instead of degrading silently; per-line `.env` parse errors are no longer
  swallowed.
- **Auto-deploy hardening** (`1a44a7a`) — `git_pull.sh` now runs a syntax preflight +
  bounded restart + post-restart health check, and rolls back to the last good commit
  on any gate failure instead of shipping a broken pull to the live device.
- **Test suite restored** (`8ab80db`) — broken imports fixed, `httpx` added to
  `requirements.txt` (was only reaching the venv transitively via `anthropic`), plus new
  coverage (audio RMS helpers, STT hallucination filtering, LED alert-flash race) and a
  pre-push hook (`.githooks/pre-push`, `git config core.hooksPath .githooks` to enable).
- **Intent ordering fix** (`d1475b7`) — DISMISSAL now takes priority over HA_CONTROL so
  "stop" doesn't get swallowed as a device command (TIMER_CANCEL still checked first so
  "stop the timer" isn't shadowed by DISMISSAL's bare `\bstop\b`).
- **Log rotation + watchdog alert push** (`70dc13f`) — `metrics.jsonl` size-rotates
  (10MB, 2 backups); `scripts/watchdog_notify.py` + `bender-watchdog.timer` (not
  auto-installed — see Runtime Config section) actually runs `watchdog.py`'s checks on a
  schedule and pushes alerts to HA (nothing did before this).
- **Piper crash resilience + WAV cache** (`e0954fb`) — content-hash cache avoids
  re-synthesizing identical text; pool recovers from a crashed Piper subprocess instead
  of wedging TTS.
- **Docs sync** (this commit) — recovered `bender-git-pull.{service,timer}` from the
  live device into `systemd/` (previously existed nowhere in git — lost if the SD card
  died), stripped dead Porcupine references from `setup.sh`/`README.md`, fixed a stale
  `vlm.py` docstring (`BGR` → `RGB`), reconciled `CLAUDE.md`'s Project Structure /
  Runtime Config / Web UI sections against the actual `scripts/` tree and shipped
  features, marked `IMPLEMENTATION_PLAN.md`'s Hailo-NPU-LLM item shipped.

**hey_bender_v0.1 wake-word model — recall is a known, open concern.** The trained
custom model (superseding the interim `hey_jarvis` stand-in) evaluates at roughly
**0.794 accuracy / 0.589 recall**. Recall this low means roughly 4 in 10 genuine "Hey
Bender" attempts don't fire — the N-of-M smoothing above buys back some precision
headroom to lower the detection threshold, but a real recall fix needs either a better
training sweep (`scripts/train_hey_bender.py::sweep` exists for this — see the
2026-07-09 wake-word section) or more/better training data. Don't mistake "user says
'hey bender' and nothing happens" for a regression — check the loaded-model log line
and recent `wake_score_log_interval_s` peak-score lines in journald before assuming
something broke.

**XVF3800 USB mic — the 6-day stall (recurring since ~Jul 3) is resolved two ways, not
one.** A physical reseat of the USB connector on 2026-07-09 (~20:11) fixed the
immediate hardware-level symptom (buffer overruns). Independently, the mic-stall
watchdog above (`3fd6fdf`) landed as the software-side fix so a *future* wedged read —
physical reseat or not — gets detected and escalated instead of silently killing wake
word for days with a heartbeat that keeps lying. Treat both as necessary: the reseat
fixed this specific incident, the watchdog is what catches the next one whether or not
someone's around to notice the mic went quiet.

---

## ⚠️ Web UI auth hardening (2026-07-09) — DEPLOY ORDER MATTERS

The web UI now uses a token scheme (PIN → signed ~12h token) and **fails closed**:
`bender-web` refuses to start if `BENDER_WEB_PIN` is unset, empty, or a placeholder
(`CHANGE_ME`/`2904`). The 5-minute auto-pull will restart the service, so:

**Before this branch merges to main, set a real PIN on BenderPi:**
```bash
grep -q '^BENDER_WEB_PIN=' /home/pi/bender/.env && \
  sed -i 's/^BENDER_WEB_PIN=.*/BENDER_WEB_PIN=<real-pin>/' /home/pi/bender/.env || \
  echo 'BENDER_WEB_PIN=<real-pin>' >> /home/pi/bender/.env
```
If the PIN is still a placeholder when the deploy lands, `bender-web` will crash-loop.
After deploy: `sudo systemctl status bender-web` and log in from a browser (a restart
invalidates the browser token — that's expected; just re-enter the PIN).

Also verify on real hardware (couldn't be tested off-device):
- Camera MJPEG stream + mic websocket auth via short-lived stream tokens (token
  validated only at connection open, so it must **not** kill a live stream when it
  expires mid-stream).
- Puppet speak + camera + mic together (WM8960 single-rate constraint).
- CSP: load the UI, confirm no console errors (build uses inline `style=` attrs →
  `style-src 'unsafe-inline'`; no inline scripts).

**Admin-tier decision (deferred):** a second PIN for the sudoers-backed systemctl
actions was considered and **deferred** — on a single-user LAN it adds friction with
no real threat-model win. Revisit only if the UI is ever exposed off-LAN.

---

## Post-Deploy Steps (run on Pi after git pull)

### Install/update dependencies
```bash
cd /home/pi/bender
venv/bin/pip install -r requirements.txt
```

### Generate pre-built response clips
Run this after any changes to response text or on a fresh deploy:
```bash
venv/bin/python scripts/prebuild_responses.py
```

### Restart conversation service
```bash
sudo systemctl restart bender-converse
```

---

## One-Time Setup (already done on this Pi — notes for re-provisioning)

### Web UI PIN + port
```bash
nano /home/pi/bender/.env
# Add: BENDER_WEB_PIN=<your-pin>  BENDER_WEB_PORT=8080
# BENDER_WEB_PIN is MANDATORY — bender-web fails closed on a missing/placeholder PIN.
```

### Install web UI service
```bash
sudo cp systemd/bender-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bender-web
sudo systemctl start bender-web
```

### Set up sudoers for web UI
```bash
sudo tee /etc/sudoers.d/bender-web << 'EOF'
pi ALL=(ALL) NOPASSWD: /bin/systemctl restart bender-converse
pi ALL=(ALL) NOPASSWD: /bin/systemctl stop bender-converse
pi ALL=(ALL) NOPASSWD: /bin/systemctl start bender-converse
pi ALL=(ALL) NOPASSWD: /bin/systemctl restart bender-web
pi ALL=(ALL) NOPASSWD: /bin/systemctl status bender-converse
EOF
sudo chmod 440 /etc/sudoers.d/bender-web
```

### Install Ollama (local LLM)
Already installed. To restore on a fresh Pi:
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:1.5b
sudo systemctl enable ollama
sudo systemctl start ollama
# Verify: ollama list should show qwen2.5:1.5b
```

### Hailo AI HAT+ 2 setup
The Hailo-10H is installed and `hailo-h10-all` v5.1.1 is present. The Whisper-Base HEF lives at
`/usr/local/hailo/resources/models/hailo10h/Whisper-Base.hef`. To restore it on a fresh Pi:
```bash
# Install hailo packages (via apt — already configured)
sudo apt install hailo-h10-all

# Install hailo-apps into bender venv (for the download tool)
git clone https://github.com/hailo-ai/hailo-apps.git ~/hailo-apps
sudo mkdir -p /usr/local/hailo/resources && sudo chown -R pi:pi /usr/local/hailo
cd ~/hailo-apps && /home/pi/bender/venv/bin/pip install -e . --quiet

# Download Whisper-Base HEF (~137MB)
/home/pi/bender/venv/bin/hailo-download-resources --group whisper_chat --arch hailo10h

sudo systemctl restart bender-converse
```

---

## 2026-05-14 — Audio resilience + decoupling (complete)

Implemented `docs/superpowers/plans/2026-05-14-audio-resilience.md` (12 tasks). All committed to main.

**What changed:**
- Central device discovery (`audio.find_input_device` / `find_output_device`) — no more hardcoded ALSA indices
- Wake-loop stall detection (30s zero-read → reinit) + sd_notify watchdog (`WatchdogSec=120`)
- `watchdog.check_session_liveness` — alerts if no conversation in 6h (would have caught the 1.5-day freeze)
- Lazy vision injection — STT starts immediately after greeting; VLM result injected when the AI call fires
- `response_hard_timeout_s=20` — inference thread bounded; plays `error_timeout.wav` on breach
- HTTP timeouts on briefings + Anthropic client (`http_timeout_s=10`, httpx.Timeout)
- 18 magic constants migrated to `bender_config.json`; `WHISPER_HALLUCINATIONS` to JSON

**Pi deploy required** (one-time steps for this plan):
```bash
cd /home/pi/bender && git pull origin main
sudo apt-get install -y libsystemd-dev pkg-config
venv/bin/pip install -r requirements.txt
venv/bin/python scripts/prebuild_responses.py  # generates error_timeout.wav
sudo cp systemd/bender-converse.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart bender-converse
```
Verify: `systemctl show bender-converse -p WatchdogUSec,Type,NotifyAccess` → `WatchdogUSec=2min`, `Type=notify`, `NotifyAccess=main`.

---

## 2026-05-14 — vlm.py Qwen2-VL-2B rewrite + reSpeaker stereo + web fixes (complete)

Deployed alongside the audio-resilience plan.

**What changed:**
- `vlm.py`: replaced YOLO+LLM two-step pipeline with Qwen2-VL-2B direct VLM
  (`hailo_platform.genai.VLM`, 336×336 RGB input, single HEF)
- `audio.py`: added `xvf_dsnoop` to `find_input_device` fallback hint chain
- `wake_converse.py`: xvf_dsnoop stereo detection + left-channel downmix for Porcupine
- `web/app.py`: `_check_camera()` TTL cache (10s); `vision_analyse` stops/starts
  `bender-converse` around TTS to prevent mic-bleed feedback loop

**Pi deploy status (2026-05-14 16:42):** deployed, service running cleanly.
Verified: `WatchdogUSec=2min`, `Type=notify`, `READY=1 sent`, mic=`mic_shared idx=11 ch=1`.

---

## 2026-05-24/25 — Architectural deepening (complete)

All 4 refactor candidates identified in the architecture review are done and deployed.

**1. `ha_control.py` decomposition** — 440-line monolith split into 4 focused modules:
- `handlers/entity_registry.py` — entity discovery + TTL cache (60s)
- `handlers/entity_matcher.py` — pure fuzzy matching, two-phase scoring
- `handlers/ha_client.py` — injectable HAClient Protocol + UrllibHAClient
- `handlers/ha_control.py` — thin orchestrator; `make_default(cfg)` factory
- `handlers/ha_handler.py` — pronoun state (`_last_entities`) moved to handler instance

**2. `wake_converse.py` → ConversationSession** — new `scripts/session.py`:
- `ConversationSession` owns: audio open/close, greeting, turn dispatch, vision injection, logging, IPC
- `VisionProvider` Protocol + `FutureVisionProvider` (ThreadPoolExecutor)
- `TurnResult` dataclass with `should_end` + `end_reason`
- `wake_converse.py` reduced to wake word detection + STT loop (~120 lines vs ~500 before)

**3. `briefings.py` — source abstraction + new capabilities**:
- `_get_briefing_wav(key, ttl, wav_path, generate_text, fallback_text)` — core pattern extracted
- `BriefingSource` dataclass + `_SOURCES` list drives `refresh_all()` as a loop
- `get_weather_wav_for_location(location)` — now cached (was ephemeral temp file)
- New: `get_time_wav(timezone)` / `get_time_text(timezone)` — 60s TTL, pure zoneinfo

**4. `web/app.py` → domain routers** — 967-line flat file split into 9 modules:
- `routes/health.py`, `actions.py`, `config.py`, `puppet.py`, `logs.py`, `timers.py`, `status.py`, `remote.py`, `vision.py`
- `app.py` is now 32 lines; auth applied at router level

All deployed and verified clean on BenderPi (2026-05-25).

---

## 2026-07-09 — Wake-word recall/precision + training sweep (code complete, needs on-device calibration)

Fable review group #10. Two independent tracks.

**Runtime (`scripts/wake_converse.py`, `wait_for_wakeword`):**
- **N-of-M temporal smoothing.** Per-frame threshold lowered `oww_threshold` 0.5 → 0.35 (recovers recall) and now requires `oww_frames_required` (2) of the last `oww_window` (4) frames over threshold before firing (restores precision by suppressing single-frame spikes). Set `oww_frames_required: 1` to disable smoothing. Smoothing state (a `deque`) resets on every stream (re)open, so a stale score from before a session/reinit can never trigger.
- **RMS input sentinel.** The 6-day XVF3800 incident had the mic feeding zeros — reads returned frames so the stall detector never fired, but the wake word could never trigger. New `audio.rms()`-based sentinel: if rolling input RMS stays below `wake_rms_floor` (30) for `wake_silence_alarm_s` (120s), it escalates through the same reinit-then-exit path as a hard stall and emits a `wake_mic_silent` metric.
- **`predict()` hardened** with try/except (`wake_predict_error` metric) so one bad frame can't kill the loop.
- **Periodic idle log** every `wake_score_log_interval_s` (60s): peak score + peak RMS, so journald distinguishes "nobody spoke" (scores ~0, RMS healthy) from "mic feeding garbage" (RMS ~0).

**Training (`scripts/train_hey_bender.py`):**
- `target_recall` exposed as a CLI param (was hardcoded 0.5) across `train`/`main`/`_write_config`.
- New `sweep` local_entrypoint: `modal run scripts/train_hey_bender.py::sweep` runs a small parallel grid (n_samples × augmentation_rounds × target_recall, ~6 combos) via `.starmap`, uploads each ONNX under a grid-tagged name (never overwrites `hey_bender_v0.1.onnx`), prints a ranking table.

**⚠️ STILL NEEDS ON-DEVICE WORK (an agent cannot do this):**
1. **Live threshold/N-of-M calibration.** The 0.35 threshold + 2-of-4 window are starting points tuned against the *old* hey_jarvis model's score distribution. After any retrain, and for the current model, they MUST be tuned live by saying "hey bender" at distance/volume variations on BenderPi. N-of-M adds up to ~240ms detection latency — acceptable, but verify it feels responsive.
2. **`wake_rms_floor` calibration.** 30 is a guess. Measure the XVF3800's real idle noise floor (a quiet room is NOT zero) via the new periodic "peak RMS" log line over a quiet overnight soak. Too high → reinit loops.
3. **Soak test** the RMS sentinel overnight via journald before trusting the escalation.
4. The RMS sentinel catches *garbage/zero* audio, NOT a hung `stream.read()` — that hung-read case is covered by g5's `MicReader` read-timeout, a separate mechanism.
5. Sweep synthetic-validation metrics rank candidates; they do NOT predict real-mic recall. Use the table to pick candidates, then confirm live.

Tests: `tests/test_oww_smoothing.py` (new), `tests/test_oww_config.py` (updated). Also fixed a pre-existing g5 breakage in `tests/test_oww_detection.py` (its bail raised from `read()` no longer reaches the main loop now reads run on the MicReader daemon thread) and added a `dotenv` stub to `tests/conftest.py` so the wake tests run off-device.

---

## 2026-07-09 — Warm Hailo LLM session + reload-tax metrics + lock hardening (code complete, warm mode hardware-gated)

Fable review group #15. Cuts the per-turn Hailo HEF reload tax — but the headline feature ships **default-off** because it's gated on a hardware fact we can't verify off-device.

**Metrics-first (pure wins, always on):**
- `ai_local.py._load()` now wraps VDevice+LLM construction in `metrics.timer("ai_hailo_load")` — the per-turn HEF reload tax was previously *invisible* (it ran outside the `ai_hailo_call` timer). In per-turn-release mode this fires on every AI turn, so `ai_hailo_load` directly measures what warm mode would save.
- New counters: `hailo_busy_lockout` (a turn hit an in-flight generate_all() and failed over to Ollama) and `hailo_release_skipped` (release deferred — `reason=warm_session` per-turn in warm mode, or `reason=infer_in_flight` when a zombie holds the lock).
- **Zombie-lock alerting:** `_HailoLLMResponder` tracks consecutive skipped releases + a `_infer_lock_held_since` timestamp. After 3 consecutive skips it emits `hailo_lock_stuck` + an error log. `watchdog.py` raises an **error** alert on any `hailo_lock_stuck` (`hailo_lock_stuck_threshold`, default 1). All surfaced in STATUS.md's new "Hailo NPU (LLM)" section via `generate_status.py`.

**Config invariant (`config.py`):** `local_llm_timeout` is now warn-and-clamped at load time to `response_hard_timeout_s - 2`. Prevents the inversion where the hard-timeout join kills the inference thread before Ollama can time out and cleanly fail over. Deployed config (25 < 45) was already safe; this makes it a load-time guarantee, not a coincidence. CAVEAT: for the *streaming* Ollama path the `requests` timeout is per-read (inter-token gap), not a total budget — a slow-but-steady stream can still exceed the hard timeout. The clamp only bounds the connect / non-stream case.

**Warm session (opt-in, `llm_warm_session`, default `false`):** when true, `session.py` passes `warm=True` to the per-turn `release_chip()` (a no-op that keeps the VDevice resident), and `end()` calls `release_chip(warm=False)` to release once per session. `end()`'s release is `_infer_lock`-guarded, so a mid-session zombie leaves the device held — reclaimed by process-exit `close()` or the next STT load.

**⚠️ STILL NEEDS ON-DEVICE WORK (an agent cannot do this):**
1. **HEF-coexistence spike — this gates the whole warm feature.** Load Whisper HEF + Qwen HEF concurrently under the shared `"SHARED"` group and alternate transcribe/generate on the Hailo-10H. The existing per-turn mutual-release design (stt.py's settle window, "freeing the KV-Cache for STT" comments) *implies* the two HEFs may not coexist resident even when idle. If they can't, `llm_warm_session=true` will fail STT on turn 2. Do NOT flip the flag until this passes.
2. **Measure the reload tax first.** Deploy with the flag off, read `ai_hailo_load` in STATUS.md over a few multi-turn sessions to quantify the saving before flipping.
3. **Warm-mode soak.** With the flag on, run multi-turn conversations and deliberately induce a hard-timeout zombie mid-session; confirm `end()`'s skip-release doesn't strand the VDevice across *sessions* (it's a cross-session strand now, not cross-turn). Watch for `hailo_lock_stuck` in STATUS.md.
4. Auto-deploy ships within ~5 min of merge to main; the flag defaults off so this is safe to land, but do the spike before any config edit that enables it.

---

## Current Priorities
- **Merge `fable-review-fixes` to `main` deliberately, not by habit.** Before merging:
  set a real `BENDER_WEB_PIN` on the Pi (see the auth-hardening warning below — the web
  service now fails closed), and budget for the on-device verification listed in each
  2026-07-09 section below (wake-word threshold calibration, warm-Hailo-session HEF
  coexistence spike, stream-token/WM8960-serialization checks). None of this was
  testable off-device.
- Monitor Ollama escalation rates
- Monitor Hailo LLM KV-Cache on restart — retry cooldown should clear it within 60s if it occurs
- Test timers: "Hey Bender, set a timer for pasta for 5 minutes"
- Monitor local LLM escalation rates — collect data for future ML classifier training (~500+ queries needed)
- **Wire up new briefing capabilities** — `get_time_wav()` and `get_weather_wav_for_location()` exist but aren't yet connected to intent routing in `responder.py`

## Recent Decisions
- **Diagnosis fixes (2026-05-09):** bender-converse was inactive for 3.5 weeks. Three bugs fixed:
  - `local_llm_timeout` raised 3 → 25s; `LocalAIResponder.warm_up()` pre-loads Ollama in background thread at startup (`scripts/ai_local.py`, `scripts/wake_converse.py`)
  - Hailo LLM `_load()` now retries after 60s cooldown instead of permanently caching failure; `close()` + atexit handler releases VDevice/KV-Cache on SIGTERM (`scripts/ai_local.py`)
  - Camera-busy `RuntimeError` in `analyse_scene()` now logs at WARNING (not ERROR+traceback) (`scripts/vision.py`)
  - Service verified running: `Ollama model pre-loaded`, `Listening for 'Hey Bender'` confirmed in logs
- Architecture refactor (2026-03-21): handler registry replaces if/elif dispatch in responder.py
  - New handlers: `handlers/clip_handler.py`, `pregen_handler.py`, `promoted_handler.py`, `weather_handler.py`, `news_handler.py`, `ha_handler.py`, `timer_alert.py`
  - To add a new handler: create class extending `Handler` from `handler_base.py`, declare `intents`, implement `handle()`, add to handler list in `Responder.__init__`
  - `audio.py` decoupled from `leds` — uses `on_chunk`/`on_done` callbacks
  - Config unified: stt.py, tts_generate.py, ha_control.py, briefings.py all use `cfg` singleton
  - IPC paths (`session_file`, `end_session_file`) centralised in config.py
- Added voice-controlled timers and alarms with named labels and concurrent support
- Timer alerts use play-pause cycle (play clip → listen for dismissal → repeat) respecting WM8960 constraint
- Timer persistence via timers.json (best-effort, survives restarts)
- Duration parser supports natural language: "ten minutes", "half an hour", "an hour and a half"
- UI redesigned with Futurama Theme C (scan lines, glows, gradient cards, animated status)
- Added persistent sidebar with quick controls (volume, LED, puppet-only, silent wake, end session)
- End-session via file-based IPC (.end_session / .session_active.json)
- LED listening/talking colours: blue when listening, white when talking (configurable)
- Silent wake word mode: LED-only notification, no audio greeting
- Speech rate via config (Piper --length-scale), wired into tts_generate.py
- **Raspberry Pi AI HAT+ 2 (Hailo-10H, 40 TOPS) installed 2026-03-18** — STT now runs Whisper-Base on NPU via `hailo_platform.genai.Speech2Text`. CPU faster-whisper fallback retained in `stt.py` if HEF unavailable. Metric label changed from `tiny.en` to `whisper-base-hailo`.
- **Local LLM via Ollama (2026-03-21, deployed 2026-03-22):** Qwen2.5-1.5B on CPU, hybrid routing with quality-check escalation to Claude
  - STT stays on Hailo NPU, LLM runs on CPU via Ollama (model switching penalty makes NPU impractical for both)
  - Scenario-based routing: conversation/knowledge/creative, each configurable via web UI
  - Local-first: try local LLM, escalate to Claude on quality failure (hedge phrases, too short, timeout)
  - Rich logging captures routing decisions in `ai_routing` field of conversation log for future ML classifier training
  - Config keys: `ai_backend`, `local_llm_model`, `local_llm_url`, `local_llm_timeout`, `ai_routing`
  - New file: `scripts/ai_local.py` — `LocalAIResponder` with `QualityCheckFailed` exception
- **Svelte + Tailwind UI migration (2026-03-22):** Vanilla JS frontend replaced with Svelte 4 + Tailwind CSS
  - Built locally with Vite, output committed to `web/dist/` — no Node.js on Pi
  - FastAPI serves `web/dist/` instead of `scripts/web/static/`
  - Futurama theme preserved via CSS custom properties wrapped as Tailwind utilities
  - 5 pages: Dashboard, Puppet, Config, Logs, Remote
  - Centralised API client (`web/src/lib/api.js`), Svelte stores for shared state
  - Mobile responsive: bottom tab bar on narrow screens
  - Bundle: ~75 KB JS + ~18 KB CSS (gzipped: ~27 KB total)

## Known Issues
- **Weather briefing 401 Unauthorized** — HA token likely expired. Check `HA_TOKEN` in `/home/pi/bender/.env`.
- **Hailo LLM KV-Cache conflict** — if KV-Cache is still locked after restart (e.g. from bender-web YOLO pipeline holding Hailo), bender-converse retries every 60s. Should self-heal; if not, `sudo systemctl restart bender-web` to release.
- **`del self._vdevice` may not fully release Hailo VDevice** — `del` decrements Python refcount but Hailo SDK may hold internal refs. If KV-Cache lock recurs, check Hailo SDK for an explicit `release()`/`close()` API.
- **Camera-busy check uses substring match** — `"busy" in str(e).lower()` works for today's libcamera EBUSY message. If future SDK versions change the wording it silently regresses. Deferred `CameraBusyError` typed exception in `scripts/camera.py` would fix this cleanly.
- **ReSpeaker XVF3800 4-Mic Array** — now primary mic. Device discovery is by name (`mic_shared`/`seeed`), not hardcoded index — USB hotplug no longer breaks audio. The mic-stall watchdog (`audio.MicReader`, 2026-07-10) now catches a wedged read; a 6-day stall on this device was previously invisible to the heartbeat.
- **hey_bender_v0.1 wake-word recall (~0.589)** — roughly 4 in 10 genuine "hey bender" attempts don't fire. N-of-M smoothing (2026-07-09) buys threshold headroom but doesn't fix the underlying model; needs a systematic training sweep (`train_hey_bender.py::sweep`) or more data. See the 2026-07-09 wake-word section.
- Piper --json-input mode needs verification on Pi (using warm-up fallback)
- Intent false positives reduced but not eliminated
- Thinking sound timing: plays after get_response() returns, not during generation
- Local LLM quality depends on Qwen2.5-1.5B — expect escalation for factual/knowledge queries
- Log retention: conversation logs on SD card accumulate routing data for future classifier training — back up periodically

## Future Considerations
- Recurring/repeating timers
- Timer snooze functionality
- Local ML intent classifier (collecting training data via logging)
- Train embedding-based query classifier on HuggingFace once ~500+ labelled routing queries collected (replaces keyword heuristic in `_classify_scenario`)
- Revisit Hailo NPU for LLM if model switching improves — `local_llm_url` can point at hailo-ollama (`localhost:8000`) instead of CPU Ollama (`localhost:11434`)
- Streaming LLM responses — start TTS on first sentence before full response completes
- Adaptive routing thresholds — auto-switch scenario to cloud_only if escalation rate exceeds threshold
- Persistent Piper process (verify --json-input on Pi)
- Split get_response() into classify() + generate() for thinking sounds during generation
- Hailo Whisper-Small HEF when it stabilises on Hailo-10H (currently broken upstream)
- Camera/AI vision via Hailo NPU (future hardware addition)
- Motorised elements for physical Bender model
- **Clip categorisation** — raw WAVs in speech/wav/ all land in one "clips" bucket. Categorise under headings (greetings, insults, sound effects, etc.). Data task: label clips in index.json, frontend already groups by category.
