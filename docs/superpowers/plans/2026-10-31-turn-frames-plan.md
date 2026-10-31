# Turn frames: a camera frame each time Bender starts to reply

Status: **plan, revision 3, reviewed twice (the cap)**. Nothing here is deployed.

## Goal

Save one camera frame each time Bender starts to reply, with the sensor distance,
tied to the conversation log turn. The owner can then see who was in front of him
when he spoke, and a later VLM pass can read the same frames offline.

## Why

Live 2026-10-31 the logs could not answer "was anyone there?". The owner reported
Bender talking to nobody. The logs show the gate state, not the scene.

## Design

`bender-converse` writes a small note. `bender-web` takes the frame. See the
Review log for the reason.

```
bender-converse                        bender-web
---------------                        ----------
_halloween_loop, gate open             turn_frames worker thread
  turn_frames.warm()       ---->         polls <frames>/.queue every 0.2 s
handle_turn(), after end-session         drops any note over 5 s old
  _snap()                                acquire, settle, capture, encode, save
    turn_frames.request()  ---->         appends one line to outcomes.jsonl
    (one empty file, no camera)          holds the camera 15 s, then releases
```

| Decision | Choice | Reason |
|---|---|---|
| Default | **Off** (`turn_frames_enabled: false`) | Frames hold images of children and a public street. Keeping them is an explicit act |
| Gate | `bender-converse` only, `turn_frames_enabled is True` | One gate. A note over 5 s old is dropped, so a frame needs a note from a process that had the feature on 5 s ago |
| Trigger | `handle_turn`, after the end-session check. One frame per reply | The person has just spoken and he starts to answer |
| No greeting frame | **Cut** | A cold open during the greeting is the overlap `f435936` moved away from. The first reply frame comes seconds later |
| No puppet frames | **Cut** | The owner typed the line. This also removes an unvalidated `id` path |
| Warm-up | `_halloween_loop` calls `turn_frames.warm()` when the gate is open **and `presence.available()` is true**, before the capture, inside its own guard | The camera opens while no audio plays. The first frame of a Halloween session is then warm and settled. Household mode has no gate, so its first frame opens cold (see Open risk). A gate that fails open passes on every loop, so warming there would hold the camera open all night: with no usable sensor the first frame opens cold too. Decided by the coordinator on 2026-10-31, after the Task 7 review |
| Where the camera runs | Daemon thread in `bender-web` | See Room, `_check_camera` and the frames share one ref-counted camera in one process. `bender-converse` never imports `picamera2` while `vlm_enabled` is false |
| Queue | Empty file per request in `<frames>/.queue/`, named `<YYYY-MM-DD>__<name>`. Newest by `st_mtime_ns` wins. A note is removed **before** the capture | A slow camera cannot build a backlog. The name carries the date, so the logged path matches the file. A crash cannot retry a note for ever |
| Stale note | Age over 5 s by `abs(now - mtime)`: dropped and recorded. All notes are removed at worker start | A late frame shows the wrong person under the wrong turn. `abs` because the clock steps at boot |
| Backlog limit | `request()` skips when 32 notes wait | A stopped `bender-web` cannot fill the folder |
| Camera use | `acquire_camera`, then wait until `camera.opened_at() + turn_frames_settle_s` (0.5), capture, hold 15 s after the last note, release | Frames after the first are settled. A camera already open for See Room needs no wait. Fewer open and close cycles |
| Size | 1280x720 JPEG, quality 80 | Enough for faces if the light allows, and for `vlm.py`, which resizes to 336x336 |
| Colour | `camera.to_pil(frame)`, one named function, lazy PIL import, `Image.frombuffer("RGB", size, frame, "raw", "BGR", 0, 1)` | The array is BGR. Measured 6.2 ms worst GIL stall, against 56.6 ms for `fromarray(arr[..., ::-1])` |
| Path | `<frames>/YYYY-MM-DD/<name>.jpg`, date from the note. `.tmp` in the same folder, then rename. Folders 0700 with each level created explicitly, files 0600 | A reader never sees half a file. The sweep removes any leftover `.tmp` with its folder |
| `<frames>` | `turn_frames.frames_dir()`, one function, honours `BENDER_LOG_DIR` | Writer, sweep and status cannot disagree |
| Name | `<session_id>_tNN`. `request()` refuses anything not matching `^[\w.-]{1,64}$` | Joins a frame to its log turn |
| Log link | Turn entry gains `frame_requested`, `presence_mm`, `presence_age_s` | It records a request, not a result. `outcomes.jsonl` holds the result |
| Outcomes | `<frames>/YYYY-MM-DD/outcomes.jsonl`: one line per note: `name`, `outcome` (`saved`, `failed`, `stale`, `dropped`), `error`, `requested_at`, `taken_at` | Tells the owner what happened to each request. Persists over restarts. Swept with the folder. `taken_at` minus `requested_at` shows the true frame delay |
| Retention | `turn_frames_retention_days`, default 7, schema minimum 1. None or under 1 means 7. Never kept for ever. A folder goes once its date is `retention_days` or more behind today, so frames last about 7 days at most | The promise is a maximum |
| Sweep | Inside the worker: at start, then hourly. Runs with the feature off. Never raises. **No call from `watchdog_notify.py`** | A raise there would stop health alerts. `bender-web` starts at each daily boot, so a sweep always runs. Nothing sweeps while `bender-web` is down |
| Failure | Every error is caught and recorded. Nothing reaches the session | The feature must never stop him talking |
| Access | No web route serves a frame. `.gitignore` covers `logs/frames/` | Owner reads them over SSH |
| Control | Checkbox in the Config page **Maintenance** section | Maintenance stays visible in Halloween mode. Save returns the usual `restart_required` banner |
| Proof it works | Maintenance line from `GET /api/frames/status` | See "Status". Failures must not wait for the owner to read logs |

### Public API (`scripts/turn_frames.py`)

```python
def enabled() -> bool                 # `is True`, None-safe, read by bender-converse
def frames_dir() -> str
def request(name: str) -> str | None  # never raises. One note. Returns the relative path, or None
def warm() -> None                    # never raises. A note that opens the camera and takes no frame
def start_worker() -> None            # idempotent, never raises
def stop_worker() -> None             # for tests. Does not wait on the camera
def status() -> dict                  # see Status
```

### Status

`status()` reads the disk, plus the worker thread in the same process.

| Field | Source | Why |
|---|---|---|
| `enabled_saved` | `bender_config.json` | Saved value. A running `bender-converse` applies it after a restart |
| `saved_today`, `failed_today`, `stale_today`, `dropped_today` | Today's `outcomes.jsonl` | Survives a deploy restart |
| `last_saved` | Today's `outcomes.jsonl` | |
| `waiting_notes`, `oldest_note_age_s` | `.queue` | The only cross-check between the two processes |
| `worker_alive`, `worker_stuck_s` | Thread state, and seconds since the worker's last loop pass | A worker stuck in a C++ call counts no failures. This is how it shows |

The route answers from `bender-web`. If that process is down, the page is down too.

### Changes to `scripts/camera.py`

Shared code, and these fixes come first.

- `_CAPTURE_TIMEOUT_S = 3.0`, a module constant. Do not read it from `cfg`: `wait=None` blocks for ever, and `wait=0` returns a `Job`.
- `capture_frame()` passes `wait=_CAPTURE_TIMEOUT_S`. On timeout it calls `cancel_all_and_flush()`, then raises `TimeoutError`.
- `_close_camera()` calls `cancel_all_and_flush()`, then `close()`. **It never calls `stop()`.** `stop()` can block for ever after one exception in `process_requests`.
- `_open_camera()` puts `configure()` inside the `try`. It leaks an open camera today: `atexit.register(self.close)` keeps a strong reference.
- `_cam_lock` is taken with a timeout of `_LOCK_TIMEOUT_S = 5.0` everywhere. `acquire_camera`, `release_camera` and `capture_frame` raise `CameraBusy` on timeout. This protects the 8-thread default executor, which STOP BENDER needs.
- `opened_at()` returns the monotonic time of the last open, or None.
- `to_pil(frame)` is the only place that knows the channel order.
- Docstrings at `camera.py:16,35,119` say RGB. Correct them to BGR in memory.
- `tests/test_camera.py:151` pins `assert_called_once_with("main")`, so that test changes.

**The See Room stream changes too.** `puppet.py:357` calls `camera.capture_frame()`
through `vision`, not `cam.capture_array`. `cancel_all_and_flush()` is global, so a
direct call could lose its job and block with no timeout. One job at a time, under
one lock, always bounded. A timeout ends the stream, and `finally` releases.

**The real guarantee.** Python-level waits are bounded: the lock wait at 5 s, and
the capture wait at 3 s. A hang that waits in Python holds `_cam_lock` in that one
thread. Routes then get `CameraBusy` after 5 s, and STOP BENDER still works. The
camera stays unusable until `bender-web` restarts.

**A C++ hang is not bounded.** `Picamera2()`, `configure()`, `start()` and `close()`
are C++ calls with no timeout. The libcamera binding has no `gil_scoped_release`, so
a hang inside one of them holds the GIL as well as the lock. That freezes all of
`bender-web`, including STOP BENDER. Recovery is SSH: `sudo systemctl stop
bender-converse`, then `sudo systemctl restart bender-web`, which can wait the
default 90 s for SIGKILL. No hang of this kind is on record. Measured on this dev
clone, python3-libcamera 0.7.2+rpt20261009-1. The device build is not verified.

`worker_stuck_s` shows a hang in the worker thread only. A hang in a pool thread
reads as a healthy worker, and `failed_today` rises when the next note arrives.

**Open for the owner, before the feature is enabled:** accept this risk, or move
camera ownership to a third process. The second is a design change, not on this
branch.

### Presence fields

`presence.last_reading()` returns `(mm, age_s)`, under a lock if the module needs
one. The module records the time of each reading that completes, including out of
range. A read that raises changes nothing.

| `presence_mm` | `presence_age_s` | Meaning |
|---|---|---|
| number | number | Someone in range at that reading |
| null | number | Out of range at that reading |
| null | null | Never read: sensor off or dead |

`bender-web` never reads the sensor. The value can be 8 s old or more, from before
the last capture. The age says so. These fields matter on a dark night, when the
frame may show little.

### `ConversationSession._snap()`

```python
def _snap(self) -> None:
    try:
        if not turn_frames.enabled():
            return
        log_ = self._session_log
        path = turn_frames.request(f"{log_.session_id}_t{log_.turn + 1:02d}")
        if path is not None:
            mm, age_s = presence.last_reading()
            log_.note(frame_requested=path, presence_mm=mm, presence_age_s=age_s)
    except Exception as exc:
        log.warning("turn frame request failed: %s", exc)
```

`SessionLogger.note(**fields)` holds fields and merges them into the next
`log_turn` entry, then clears them. This avoids changing the 9 `log_turn` call
sites. Paths that end a session with no `log_turn` drop the note. The session ends
in each of those cases, so no stale note reaches a later turn.

## Open risk: audio dropouts

Commit `f435936` blames CPU load: "libcamera/ISP pipeline init on Pi 5 is
CPU-intensive (~4s)". Moving the camera to another process removes GIL sharing. It
does **not** remove CPU contention on the 4 cores that run Piper. The ~4 s figure
disagrees with the 42-271 ms opens in the journal, and neither is explained.

This plan reduces the risk. It does not prove it gone:

- no frame during the greeting
- the Halloween warm-up opens the camera while no audio plays
- the 15 s hold keeps the ISP streaming through replies, at a CPU cost not yet measured

Household mode's first frame still opens cold during the thinking clip. So does the
first frame of a Halloween session with no usable sensor.

The owner's on-device test decides. **If he hears dropouts, turn the feature off.**

## Facts verified

Confirmed by the reviewers unless marked.

- `picamera2` 0.3.38 at `/usr/lib/python3/dist-packages/picamera2/`, libcamera 0.7.2, Python 3.13.5, uvicorn 0.32.0, FastAPI 0.115.0, Starlette 0.38.6 on the device. Pillow 12.1.1.
- `RGB888` is `[B, G, R]` in memory: `picamera2/request.py:257,363`. `make_array` returns a contiguous copy.
- `capture_array(name="main", wait=None, signal_function=None)`: `picamera2.py:2210`. Timeout raises.
- `cancel_all_and_flush()` (`:1375-1387`) cancels every job, including See Room's.
- Jobs append under `self.lock` and run FIFO in one thread (`:1479-1482`).
- `Picamera2` open took 42-271 ms over 9 opens. Close took 528-535 ms.
- At 0.3.38 a refused open raises `"Camera __init__ sequence did not complete."`. `vision.py:91`'s `"busy" in str(e)` never matches. Do not match on message text.
- `_check_camera` (`puppet.py:226`) and `analyse_scene` share the ref count. No `bender-web` route needs exclusive use.
- With `vlm_enabled` false no `bender-converse` path opens the camera (`wake_converse.py:902`, `vision_handler.py:65`). With it true, `session.py:57-59` does, and that open collides with the 15 s hold. `vlm_enabled` is false on the device.
- `bender-web` is one uvicorn process. `asyncio.to_thread` uses 8 threads. STOP BENDER runs on that pool (`actions.py:243,256`).
- Worker hook: `FastAPI(lifespan=...)` at `app.py:39`. Module scope is wrong: `importlib.reload(web.app)` runs in about 14 test sites. Lifespan runs only under `with TestClient(app)`, and no test does that today. `serve_web_local.py:159` does run it, which is harmless with no notes.
- uvicorn re-raises SIGTERM at `server.py:332`, so the process dies before `atexit`. The kernel releases the camera. A lifespan shutdown must not wait on the camera.
- `git_pull.sh:235` restarts `bender-web` after each deploy. `bender-web.service` has no `TimeoutStopSec`, so the default is 90 s.
- `scripts/web/routes/logs.py:28,144` filter on `.jsonl`, so `logs/frames/` is invisible there.
- `.gitignore` does not cover `logs/frames/`. `git_pull.sh` never runs `git clean`.
- `presence._last_mm` is not updated when a read raises. `sensor.range` has no lock.
- Video config caps exposure at 33 ms. `camera.py` sets no `AfMode`.
- `CmaTotal` 65536 kB, `CmaFree` 192-240 kB with models resident. Frame buffers use the `system` heap through the IOMMU.

## Tasks

| # | Task | Files | Tier | Reason |
|---|---|---|---|---|
| 1 | Baseline commit of the slider fix on the feature branch | see Constraints | orchestrator | No code to write |
| 2 | `camera.py` fixes and See Room through `capture_frame`, with tests | `camera.py`, `vision.py`, `web/routes/puppet.py`, `tests/test_camera.py`, web tests | opus | Hang recovery across a third-party event loop, a held lock, shared by See Room |
| 3 | Config keys and schema, `.gitignore`, pin keys in `bender_config.json`, HTTP-layer tests | `config.py`, `config_schema.py`, `bender_config.json`, `.gitignore`, tests | sonnet | Shared config. A cleared box sends null |
| 4 | `SessionLogger.note()`, `presence.last_reading()`, with tests | `conversation_log.py`, `presence.py`, tests | sonnet | `presence.py` has a history of crashes |
| 5 | `turn_frames.py` with tests | new module, new test file | sonnet | Thread, queue, held camera |
| 6 | Web wiring: lifespan start, `GET /api/frames/status`, with tests | `web/app.py`, a route file, tests | sonnet | Startup path of the process that holds STOP BENDER |
| 7 | Hooks: `_snap()` in `session.py`, `warm()` in `_halloween_loop`, with tests | `session.py`, `wake_converse.py`, tests | sonnet | Hot path |
| — | **Opus review gate on tasks 2, 5, 6 and 7, before the final review** | | opus | The failure modes live here |
| 8 | Config page checkbox and status line, rebuild `web/dist` | `Config.svelte`, `api.js`, `web/dist` | sonnet | Save flow has known traps |
| 9 | Device check script, not run in this work | `scripts/probe_turn_frames.py` | sonnet | See the owner's test |
| 10 | Docs | `CLAUDE.md`, `HANDOVER.md` | haiku | Text only |
| 11 | Colours in See Room and the VLM, added by the owner | `web/routes/puppet.py`, `vision.py`, `vlm.py`, tests | sonnet | Two consumers treat a BGR frame as RGB |
| 12 | White listening LED, added by the owner | `config.py`, `bender_config.json`, `leds.py`, `HANDOVER.md`, tests | sonnet | Shipped default and code default must agree |

## Constraints

- Run on local branch `feat/turn-frames`, from `main`. **Never push. Never commit to `main`.**
- The owner is using the live device tonight. **Nothing in this run touches BenderPi. No SSH at all.** The installed source facts are recorded above. For Picamera2 source questions, use the copy on this dev clone if one exists, or stop and ask.
- Baseline commit holds only the slider fix: `HANDOVER.md`, `tests/test_web_puppet.py`, `web/src/lib/api.js`, `web/src/lib/components/VolumeSlider.svelte`, `web/src/pages/Config.svelte`, `web/dist/`. Commit this plan too. Leave `docs/superpowers/plans/2026-06-12-hey-bender-wake-word.md` and every other untracked file out. They are the owner's.
- Tests run on this dev clone only: `venv/bin/python -m pytest -q`. Hardware is mocked at import.
- `web/dist` is built here and committed. `cd web && npm run build`. Filenames change on each build.
- Device config wins on deploy. Absent keys fall back to `config.py`, so a missing number renders as an empty box on the Config page. The device's file needs the keys pinned once, by the owner's deploy step.
- A `cfg` value can be `None` when a key is cleared. Read through a None-safe helper, as `presence._cfg()` does.
- Merging to `main` deploys within 5 minutes and restarts `bender-converse` and `bender-web`, even with the feature off. The owner chooses when.
- Commit attribution line: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Test plan

`tests/test_camera.py`:

- capture timeout calls `cancel_all_and_flush`, raises, and the camera can close and reopen
- `_close_camera` calls cancel then `close`, and never `stop`
- refcount returns to 0 after a timeout and after a failed open
- `configure()` failing closes the camera
- a held `_cam_lock` raises `CameraBusy` within the timeout, from acquire, release and capture
- `to_pil`: a pure blue BGR array gives pure blue. This test fails if the channel order changes
- `opened_at` moves on open only

See Room, through the route:

- the stream uses `capture_frame`
- a capture timeout ends the stream and releases
- `_check_camera` returns False on `CameraBusy`, and does not block

`tests/test_turn_frames.py`, with a fake camera module:

- off by default, and off creates no note
- `request()` refuses a bad name, and stays inside `frames_dir()`
- `request()` skips at 32 waiting notes
- newest note by mtime wins, and the rest record `dropped`
- a note over 5 s old makes no frame and records `stale`, in both clock directions
- notes present at worker start are removed
- the note is gone before the capture starts
- camera refused: records `failed`, worker keeps running, next note works once the 120 s open retry has passed. `CameraBusy` has no such wait
- release always runs when capture raises, and a failed acquire releases nothing
- the worker waits for settle on a fresh open, and not on an open camera
- `warm()` opens the camera and saves no frame
- the camera releases 15 s after the last note
- saved size is 1280x720, colours are right, no `.tmp` remains, a failed write leaves no `.jpg`
- modes are 0700 and 0600 at every level
- the worker recreates `.queue` after `rm -r logs/frames/`
- the worker survives an exception anywhere in its loop, including the sweep
- sweep removes expired dated folders only, and ignores `.queue` and any other name
- sweep with None or 0 uses 7 and deletes
- sweep never raises, and runs with the feature off
- writer, sweep and status resolve one directory
- `status()` counts from `outcomes.jsonl`, counts `.jpg` only, and shows a stuck worker
- importing `turn_frames` does not import `picamera2`

`session.py` and `wake_converse.py`, with real `ConversationSession` objects:

- a raising `turn_frames.request` does not stop a turn
- turn entries gain the three fields, and off leaves the log unchanged
- each `handle_turn` name matches its log row
- an end-session note makes no frame request
- a held note does not leak to a later turn
- `session.start()` makes no request
- `warm()` raising does not stop `_halloween_loop`
- `available()` False with `wait_for_presence()` True: no `warm()`, and the capture still runs
- `available()` True: `warm()` runs

HTTP layer, the 2026-10-14 lesson:

- `PUT /api/config` with `turn_frames_enabled: null`, and a cleared retention box
- `turn_frames_retention_days: 0` returns 422
- `GET /api/frames/status` needs auth
- `tests/test_frontend_routes_exist.py` covers the new call
- `with TestClient(app)` starts the worker once, and `stop_worker` ends it
- importing the app does not start the worker or sweep the real `logs/frames`

Full suite stays green: `venv/bin/python -m pytest -q`. The suite must not write
frames under the repo's `logs/`.

### Tasks 11 and 12, added by the owner on 2026-10-31

The owner added both during the run. They run after Task 10 and before the final
review. They were not peer reviewed. The final review is their check.

Task 11: `capture_frame()` gives BGR, and two consumers treat it as RGB.

- See Room uses `camera.to_pil(frame)`, not `Image.fromarray(frame)`.
- `vision.analyse_scene` converts before `vlm.describe_scene`, with
  `np.asarray(camera.to_pil(frame))`. **No channel swap inside `vlm.py`.**
  `describe_scene` documents an RGB input, and a later VLM pass over saved turn
  frames reads RGB from JPEG files. A swap there would make those frames wrong.
- `vlm._preprocess` keeps its code. Its docstring changes.
- Each test fails on the old code: a pure-colour BGR frame through `analyse_scene`,
  and through the See Room stream as a decoded JPEG.

Task 12: the mouth LED is white while waiting and listening, not blue.

- `led_listening_colour` is `(255, 255, 255)` in `config.py` and in the shipped
  `bender_config.json`.
- `led_colour`, `led_talking_colour`, `led_listening_enabled`, `led_brightness`,
  `leds.set_level`, the off state and every web file stay as they are.
- The device keeps its own `bender_config.json`. Merging alone leaves it blue, so
  the owner must change that key on the device once.

### Owner's on-device test, after an approved deploy

Not part of this run. `scripts/probe_turn_frames.py`, with `bender-converse` running and See Room closed:

1. 300 acquire and release cycles, reporting fd count, `VmRSS`, thread count and `CmaFree` at each 50.
2. `capture_frame` on a forced short timeout, then close and reopen. This proves the real recovery path.
3. CPU of `bender-web` during a 15 s hold.

By hand:

4. Night image: a person at 500 mm, settle at 0.5, 1 and 2 s. Read the frames.
5. Enable. One session with Piper talking and **See Room closed**, so the open is real. Listen for dropouts. If he drops out, turn it off.
6. Open See Room during a session. Confirm frames and See Room both work.

## Rollback

- Before merge: delete the branch.
- After deploy: clear the Config page checkbox and restart `bender-converse`. Or set `turn_frames_enabled` to `false`.
- Frames already saved: `rm -r logs/frames/` on the device.
- The `camera.py` and See Room changes stay live whether the feature is on or off. To undo them, revert the commit from Task 2.

## Out of scope

- Any VLM run, and any change to `vlm_enabled`.
- A web gallery, or a route that serves a frame.
- Better night images: exposure tuning, a light, `AfMode`.
- Face detection, person counts, or any analysis.
- Frames for the greeting, for puppet lines, or in Puppet Mode.
- `TimeoutStopSec` on `bender-web.service`. Unit files are not auto-deployed.
- Deploying, or enabling on the device.

## Review log

### Round 1

Reviewer A (opus, technical) and Reviewer B (sonnet, adversarial).

| Finding | Severity | Fix |
|---|---|---|
| Camera open during playback brings back the dropout fixed in `f435936` | Critical (both) | Camera moved to `bender-web`. See round 2 for the correction |
| A hung capture holds `_cam_lock` and the camera for ever. `wait=` alone is not enough | Critical (both) | `camera.py` fixes, Task 2 at opus |
| Collision with See Room, `_check_camera` caching `False` | Important (both) | One process, one ref count |
| Cold open gives the worst exposure | Important (B) | 15 s hold, settle by `opened_at`, night test. `AfMode` parked |
| Retention fails silently | Important (B) | Retention and Sweep rows |
| Double-flip hazard and a 56.6 ms GIL stall | Important (B) | `camera.to_pil`, `frombuffer` form. `nice` dropped |
| Stale `presence_mm` | Important (both) | `last_reading()`, three-state table |
| `<log dir>` ambiguous | Important (B) | `frames_dir()` |
| Checkbox placement, restart trap, no proof it works | Important (B) | Maintenance section, banner, status line |
| `configure()` leaks an open camera | Important (A) | Task 2 |
| `_snap` not guarded | Important (A) | `_snap` rewritten |
| Single session does not cover repeat open and close | Important (A) | Probe script |

### Round 2

Reviewers fresh to revision 2. Reviewer B raised 2 Critical. Reviewer A raised 0 Critical and 6 Important.

| Finding | Severity | Fix |
|---|---|---|
| "No GIL shared" answers the wrong cause: `f435936` blames CPU load | Critical (B), Important (A) | Open risk section. Greeting frame cut. Warm-up added. Test runs with See Room closed. Named remedy: turn it off |
| A stuck `_cam_lock` fills the 8-thread pool and stops STOP BENDER | Critical (B), Important (A) | `_LOCK_TIMEOUT_S`, `CameraBusy`, real guarantee stated |
| `close()` alone is wrong, and `stop()` is unsafe | Important (A) | Cancel then `close`, order fixed in the plan |
| `cancel_all_and_flush` is global, and See Room had an unbounded wait | Important (both) | See Room goes through `capture_frame` |
| A stale note saves a wrong frame, with the feature off | Important (both) | 5 s age limit, notes cleared at start, date in the note |
| Log claims frames that do not exist | Important (B) | `frame_requested`, `outcomes.jsonl` |
| Status can look healthy while nothing saves, and `failed_today` had no source | Important (both) | Status table, heartbeat, queue depth, counts from disk |
| 7-day hole on a long-running `bender-web` | Important (B) | Hourly sweep in the worker |
| Tiers | Important (B) | Task 4 to sonnet, Task 6 added to the opus gate |
| Task 1 test: the owner's test could not detect a dropout | Important (A) | See Room closed in step 5 |

Scope cuts taken from Reviewer B: greeting frames, puppet frames, the
`watchdog_notify.py` sweep call. Kept against the reviewer's advice: the presence
fields, because a dark frame may show little, and the 15 s hold, because the
alternative opens the camera during audio on every frame.

Parked: orphan frames on abort paths (the session ends). Local date folders against
UTC log timestamps (the name carries the session id). A wrong clock at boot can
misdate a folder. The legal point on filming a public street stays with the owner.

### Opus gate, during implementation, 2026-10-31

The gate refuted the old "real guarantee", which said that STOP BENDER still works
during any C++ hang. That holds only for a wait in Python. The text above is
corrected. The gate's I1 stays open for the owner. Its M2 is recorded beside it:
`worker_stuck_s` does not show a hang in a pool thread.

`warm()` gained the `presence.available()` condition after the Task 7 review.
Decided by the coordinator. See the Warm-up row.

The gate also found that each failed `Picamera2()` leaks one fd. The worker now
waits 120 s after a failed open that is not `CameraBusy`. A frame note in that time
records `failed`.
