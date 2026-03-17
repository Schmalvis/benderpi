# BenderPi Timers & Alarms — Design Spec

**Date:** 2026-03-17
**Status:** Reviewed
**Scope:** Voice-controlled timers and alarms with named labels, Bender-style alert behaviour, play-pause dismissal cycle, web UI management, and best-effort persistence.

---

## 1. Goals

1. **Voice-controlled timers** — "Hey Bender, set a timer for pasta for 10 minutes" → Bender confirms → fires after 10 minutes with character-appropriate alerts.
2. **Voice-controlled alarms** — "Hey Bender, set an alarm for 10am" → same pattern but clock-based.
3. **Named & concurrent** — multiple timers with labels, individually cancellable. "Cancel the pasta timer."
4. **Bender-style alerts** — random clips from a pool of ~8-10 alert lines, played in a play-pause cycle (play clip → listen for dismissal → repeat).
5. **Multi-modal dismissal** — voice ("Bender stop", "enough", "ok that's enough") or web UI button.
6. **Best-effort persistence** — timers saved to `timers.json`, reloaded on restart, expired timers fire immediately.
7. **Web UI integration** — view active timers, create from browser, cancel, dismiss.

### Non-goals

- Calendar integration (Google Calendar, iCal)
- Recurring/repeating timers
- Snooze functionality (can add later)
- Timezone handling beyond local Pi time

---

## 2. Timer Module — `scripts/timers.py`

Pure logic module. No audio/LED dependencies. Testable on dev machine.

### Data Model

Each timer in `timers.json`:
```json
{
  "id": "t_a1b2c3d4",
  "label": "pasta",
  "type": "timer",
  "created": "2026-03-17T10:00:00Z",
  "fires_at": "2026-03-17T10:10:00Z",
  "duration_s": 600,
  "fired": false,
  "dismissed": false
}
```

- `id`: unique, prefixed `t_` for timers, `a_` for alarms
- `label`: user-provided name (optional, defaults to "timer"/"alarm")
- `type`: `"timer"` (countdown) or `"alarm"` (clock time)
- `fires_at`: absolute UTC ISO timestamp (computed at creation for timers)
- `duration_s`: original duration in seconds (for display: "3 minutes remaining")
- `fired`: `true` once `fires_at` has passed
- `dismissed`: `true` once user dismisses

### Public API

```python
def create_timer(label: str, duration_seconds: float) -> dict
    """Create a countdown timer. Returns the timer dict."""

def create_alarm(label: str, fires_at: datetime) -> dict
    """Create a clock-based alarm. Returns the alarm dict."""

def cancel_timer(timer_id: str) -> bool
    """Cancel (remove) a timer by ID. Returns True if found."""

def dismiss_timer(timer_id: str) -> bool
    """Mark a fired timer as dismissed. Returns True if found."""

def dismiss_all_fired() -> int
    """Dismiss all currently firing timers. Returns count dismissed."""

def list_timers() -> list[dict]
    """Return all active timers (not dismissed). Includes time remaining."""

def check_fired() -> list[dict]
    """Return timers where fires_at has passed and not dismissed."""
```

### Persistence

- `timers.json` at project root (gitignored)
- Atomic writes: write to `timers.json.tmp`, then `os.replace()` to `timers.json`
- `threading.Lock` for within-process thread safety
- Both `bender-converse` and `bender-web` processes read/write this file
- Dismissed timers older than 24h pruned on each `list_timers()` call
- On service start: reload `timers.json`, call `check_fired()` — fire any expired timers immediately

---

## 3. Duration & Time Parser — `scripts/time_parser.py`

Parses natural language time expressions from STT output into durations or datetimes.

### Supported Patterns

**Durations (timers):**
- "5 minutes", "ten minutes", "30 seconds", "2 hours"
- "an hour", "half an hour", "a minute", "a few minutes" (→ 3 min)
- "an hour and a half", "1 hour 30 minutes"
- "one and a half hours", "two and a half minutes"
- Word numbers: "one" through "sixty"

**Clock times (alarms):**
- "10am", "3:30pm", "10:00", "6pm"
- "tomorrow at 6", "tomorrow at 10am"
- "in the morning" (context-dependent: 6-11am), "tonight" (6pm-11pm)

### Public API

```python
def parse_duration(text: str) -> float | None
    """Extract a duration in seconds from text. Returns None if not parseable."""

def parse_alarm_time(text: str) -> datetime | None
    """Extract a target datetime from text. Returns None if not parseable."""

def extract_label(text: str) -> str
    """Extract the timer label from text.
    'set a timer for pasta for 10 minutes' → 'pasta'
    'set a timer for 10 minutes' → 'timer'
    """
```

### Implementation

Regex-based with a word-number lookup table. No external dependencies. The parser is a pure function — easy to test with many examples.

Word numbers map: `{"one": 1, "two": 2, ..., "sixty": 60, "half": 0.5, "quarter": 0.25}`.

---

## 4. Intent Classification

### New Intents

Add to `intent.py`:

**TIMER patterns** (checked before PERSONAL, after HA_CONTROL):
```python
TIMER_PATTERNS = [
    r"\bset (a |an )?(timer|alarm)\b",
    r"\btimer for\b",
    r"\balarm (for|at)\b",
    r"\bremind me in\b",
    r"\bwake me (up )?(at|in)\b",
]
```

**TIMER_CANCEL patterns:**
```python
TIMER_CANCEL_PATTERNS = [
    r"\bcancel (the |my )?(timer|alarm)\b",
    r"\bstop (the |my )?(timer|alarm)\b",
    r"\bremove (the |my )?(timer|alarm)\b",
    r"\bdelete (the |my )?(timer|alarm)\b",
]
```

**TIMER_STATUS patterns:**
```python
TIMER_STATUS_PATTERNS = [
    r"\bhow (long|much time)\b.{0,20}\b(timer|alarm|left)\b",
    r"\bwhat timers\b",
    r"\bany (timers|alarms)\b",
    r"\btime remaining\b",
    r"\bhow long left\b",
]
```

**TIMER_DISMISS patterns** (used during alert mode, not normal classification):
```python
TIMER_DISMISS_PATTERNS = [
    r"\b(stop|enough|ok|okay|shut up|quiet|silence|dismiss)\b",
    r"\bthat'?s?\s*(enough|ok|fine)\b",
    r"\bplease stop\b",
    r"\byes\b",  # "yes I hear you"
]
```

### Classification priority

TIMER, TIMER_CANCEL, and TIMER_STATUS checked after HA_CONTROL but before WEATHER (they're specific command patterns).

---

## 5. Timer Handler — `scripts/handlers/timer_handler.py`

Bridges between intent classification and the timer module. Generates Bender-style TTS responses.

### Public API

```python
def handle_set(user_text: str) -> str
    """Parse text, create timer/alarm, return Bender confirmation WAV path."""

def handle_cancel(user_text: str) -> str
    """Parse text, cancel matching timer, return Bender response WAV path."""

def handle_status(user_text: str) -> str
    """List active timers, return Bender response WAV path."""
```

### Confirmation responses (TTS)

**Timer set:**
- "Fine. {label} timer set for {duration}. I'll yell at you when it's done."
- "Timer for {label}, {duration}. Don't blame me if you forget about it."
- "{duration} timer. Got it. I'll be counting. Not really, I have better things to do."

**Alarm set:**
- "Alarm set for {time}. I'll wake you up. Aggressively."
- "Fine. {time}. I'll be here, waiting. As always."

**Timer cancelled:**
- "The {label} timer is cancelled. You're welcome."
- "Gone. Poof. No more {label} timer."

**No timer found:**
- "What timer? I don't see any timer. Are you hallucinating?"

**Status:**
- "You've got {count} timer(s) running. {label1}: {remaining1}. {label2}: {remaining2}."
- "No timers. Congratulations, you're free. For now."

---

## 6. Alert Mode — in `wake_converse.py`

When `check_fired()` returns fired timers, the main loop enters **alert mode** instead of normal wake-word listening.

### Play-Pause Cycle

```
1. Open audio session
2. Set LEDs to alert pattern (fast red/orange pulse)
3. Play random alert clip (~2-3 seconds)
4. Close audio session
5. Open mic, listen for ~3 seconds via STT
6. Check if text matches TIMER_DISMISS patterns
7. If dismissed → announce "Finally. {label} timer dismissed." → exit alert mode
8. If not dismissed → loop back to step 2
9. Auto-dismiss after 60 seconds (safety cap)
```

### Alert clips (pre-generated TTS via prebuild_responses.py)

Pool of ~10 lines, randomly selected each cycle:
- "Hey! Your timer's done! Hello?!"
- "Timer! It's done! Are you deaf?!"
- "Ding ding ding! That's your timer, meatbag!"
- "Wake up! Your {label} timer went off!"
- "Your {label} is done. You're welcome. Now dismiss me."
- "HEY! {label}! DONE! What part of that don't you understand?!"
- "I've been yelling about your {label} timer for a while now. Just saying."
- "Still here. Still alerting. Still being ignored. Story of my life."
- "Oh sure, just let the robot keep yelling. That's fine. I don't have feelings."
- "TIMER! DONE! DISMISS ME! Please. I'm begging you."

Note: Clips with `{label}` are generated dynamically at alert time (not pre-built). Clips without labels can be pre-built.

### LED behaviour during alert

Fast alternating red/orange pulse:
```python
# Alternate between red and orange every 200ms
leds.set_alert_flash(True)  # new function in leds.py
```

New `leds.py` function:
```python
def set_alert_flash(on: bool):
    """Start/stop fast red-orange alternating flash for timer alerts."""
```

### Integration with main loop

In `wake_converse.py`'s `main()` loop, check for fired timers during the wake-word idle loop (between `wait_for_wakeword()` iterations):

```python
while True:
    # Check for fired timers before listening for wake word
    fired = timers.check_fired()
    if fired:
        run_timer_alert(fired)
        continue

    wait_for_wakeword()
    # ... normal session ...
```

### Integration with conversation sessions

During an active conversation, also check for fired timers between turns. If a timer fires mid-conversation:
1. Interrupt the normal flow
2. Play alert with label: "Hey! Your {label} timer just went off!"
3. Auto-dismiss (don't enter full alert mode mid-conversation — that would be confusing)
4. Resume conversation

---

## 7. Responder Integration

Add TIMER, TIMER_CANCEL, and TIMER_STATUS to `responder.py`:

```python
if intent_name == "TIMER":
    from handlers import timer_handler
    wav = timer_handler.handle_set(user_text)
    return Response(text="(timer set)", wav_path=wav, method="handler_timer",
                    intent="TIMER", sub_key=None, is_temp=True,
                    needs_thinking=True, model=None)

if intent_name == "TIMER_CANCEL":
    from handlers import timer_handler
    wav = timer_handler.handle_cancel(user_text)
    return Response(...)

if intent_name == "TIMER_STATUS":
    from handlers import timer_handler
    wav = timer_handler.handle_status(user_text)
    return Response(...)
```

---

## 8. Web UI Integration

### New API endpoints (in `app.py`)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/timers` | GET | List all active timers with time remaining |
| `/api/timers` | POST | Create a timer/alarm from UI. Body: `{"label": "...", "duration_s": 600}` or `{"label": "...", "fires_at": "..."}` |
| `/api/timers/{id}` | DELETE | Cancel a timer |
| `/api/timers/{id}/dismiss` | POST | Dismiss a fired timer |
| `/api/timers/dismiss-all` | POST | Dismiss all firing timers |

### UI — add to Dashboard or as a sidebar element

**Timer display in the sidebar:**
- When timers are active, show a small timer badge/count on the sidebar
- Clicking opens a timer popover or section showing: label, time remaining (live countdown via JS), cancel/dismiss buttons

**Timer section on Dashboard tab:**
- Active timers list with countdown
- "Set Timer" quick form: label input + duration selector (presets: 1m, 3m, 5m, 10m, 15m, 30m, 1h, custom)
- Cancel and dismiss buttons per timer

### Timer countdown in UI

JS polls `/api/timers` every 5 seconds. Between polls, the countdown display updates client-side using `fires_at` timestamp minus current time. When a timer fires (remaining <= 0), the UI shows a flashing "FIRING" badge with a dismiss button.

---

## 9. File Changes

### New files

| File | Purpose |
|---|---|
| `scripts/timers.py` | Timer CRUD, persistence, check_fired() |
| `scripts/time_parser.py` | Duration/time NLP parser |
| `scripts/handlers/timer_handler.py` | Bender-style timer responses |
| `tests/test_timers.py` | Timer module tests |
| `tests/test_time_parser.py` | Parser tests |
| `tests/test_timer_handler.py` | Handler tests |

### Modified files

| File | Changes |
|---|---|
| `scripts/intent.py` | Add TIMER, TIMER_CANCEL, TIMER_STATUS patterns and classification |
| `scripts/responder.py` | Add TIMER/TIMER_CANCEL/TIMER_STATUS handling |
| `scripts/wake_converse.py` | Check fired timers in main loop, alert mode, mid-conversation interrupt |
| `scripts/leds.py` | Add `set_alert_flash()` function |
| `scripts/prebuild_responses.py` | Add timer alert clips (generic ones without labels) |
| `scripts/web/app.py` | Add timer CRUD endpoints |
| `scripts/web/static/dashboard.js` | Add timer section |
| `scripts/web/static/app.js` | Add timer badge to sidebar |
| `scripts/web/static/style.css` | Timer display styles |
| `.gitignore` | Add `timers.json` |
| `bender_config.json` | Add `timer_alert_max_seconds: 60` |
| `scripts/config.py` | Add `timer_alert_max_seconds: int = 60` |

---

## 10. Implementation Order

1. `time_parser.py` + tests (pure logic, no dependencies)
2. `timers.py` + tests (pure logic, file-based persistence)
3. Intent patterns (TIMER, TIMER_CANCEL, TIMER_STATUS) + tests
4. `timer_handler.py` + responder integration
5. Alert mode in `wake_converse.py` (play-pause cycle, LED flash, dismissal)
6. Pre-build timer alert clips
7. Web API endpoints + dashboard timer section
8. Config, .gitignore, docs
