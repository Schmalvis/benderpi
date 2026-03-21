# Contextual Answers — Design Spec

**Date:** 2026-03-21
**Status:** Draft
**Scope:** Add real-data responses for time, date, weather details, and system status queries, delivered in Bender's voice via templates or AI.

---

## Problem Statement

When users ask Bender factual questions like "What time is it?", he responds with generic personality lines ("Time for a beer!") without providing the actual information. Users expect real data alongside the sass — like Alexa with attitude.

---

## Constraints

- Uses the new handler registry — each query type is a handler class
- Template responses are pre-built TTS at startup where possible, or generated on-demand via Piper
- AI responses use Claude API (existing `ai_response.py` path)
- System status reads from the Pi directly (no external APIs beyond HA for weather)
- Timer status already exists — no changes needed there
- All responses must be in Bender's voice/personality

---

## Design

### New Intent: CONTEXTUAL

Add a single `CONTEXTUAL` intent to `intent.py` with sub-keys for the query type:

```python
CONTEXTUAL_PATTERNS = [
    # Time
    ("time",   r"\b(what time|what's the time|tell me the time|current time|time is it)\b"),
    ("time",   r"\b(what hour|how late)\b"),
    # Date
    ("date",   r"\b(what('s| is) the date|what day|today's date|current date)\b"),
    ("date",   r"\b(what month|what year)\b"),
    # Weather detail (conversational, not full briefing)
    ("weather_detail", r"\b(how (hot|cold|warm) is it|what('s| is) the temperature|degrees)\b"),
    ("weather_detail", r"\b(is it (raining|snowing|sunny|cloudy))\b"),
    # System status
    ("status", r"\b(how are you (doing|feeling|running)|how('s| is) it going)\b"),
    ("status", r"\b(system status|your status|health check|you ok)\b"),
]
```

**Intent priority:** CONTEXTUAL must be checked **before** PERSONAL in `intent.py`'s `classify()` function, because "how are you" currently matches the PERSONAL `feelings` sub-key. The CONTEXTUAL patterns are more specific (e.g., "how are you doing/running") while PERSONAL catches the general "how are you feeling/do you feel".

**Overlap resolution:**
- "How are you doing?" → CONTEXTUAL (status) — returns real system data
- "How are you feeling?" → PERSONAL (feelings) — returns personality response
- "How are you?" (bare) → PERSONAL (feelings) — existing behaviour preserved

### Handler: ContextualHandler

New file: `scripts/handlers/contextual_handler.py`

```python
class ContextualHandler(Handler):
    intents = ["CONTEXTUAL"]

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        if sub_key == "time":
            return self._handle_time()
        elif sub_key == "date":
            return self._handle_date()
        elif sub_key == "weather_detail":
            return self._handle_weather_detail(text)
        elif sub_key == "status":
            return self._handle_status()
        return None
```

### Response Strategy by Sub-key

#### Time (template-based)

```python
TIME_TEMPLATES = [
    "It's {time}. What, your eyes don't work? Get a clock, meatbag.",
    "The time is {time}. You're welcome, flesh tube.",
    "{time}. Now stop bothering me with stuff your phone can tell you.",
    "It's {time}, baby! Time to bend some girders. Or drink. Probably drink.",
]
```

- `{time}` filled with `datetime.now().strftime("%-I:%M %p")` (e.g., "3:45 PM")
- Response generated on-demand via Piper TTS (not pre-built — time changes every call)
- `is_temp=True`, `needs_thinking=True` (TTS generation takes a moment)

#### Date (template-based)

```python
DATE_TEMPLATES = [
    "It's {date}. Another day of dealing with you humans.",
    "{date}. Mark it in your calendar — the day you bothered Bender.",
    "Today is {date}. Time flies when you're made of metal.",
]
```

- `{date}` filled with `datetime.now().strftime("%A, %B %-d")` (e.g., "Friday, March 21")
- Same on-demand TTS approach as time

#### Weather Detail (AI-generated)

For conversational weather queries ("how hot is it?", "is it raining?"), fetch the current state from HA and pass it to Claude for a Bender-voiced response:

```python
def _handle_weather_detail(self, text):
    # Fetch current weather state from HA (reuse briefings.get_weather_text())
    weather_data = briefings.get_weather_text()
    if not weather_data:
        return None  # Fall through to AI fallback

    prompt = f"The user asked: '{text}'. Current weather: {weather_data}. " \
             f"Answer briefly (1-2 sentences) as Bender from Futurama."
    # Use AIResponder for the response
    ...
```

- Falls through to the standard AI fallback if HA is unavailable
- Uses existing `briefings.get_weather_text()` for data (already cached)
- AI generates a natural response with actual temperature/conditions
- `is_temp=True`, `needs_thinking=True`

**Difference from WEATHER intent:** The existing WEATHER intent plays a full pre-cached weather briefing WAV. CONTEXTUAL weather_detail is for quick conversational answers ("It's 12 degrees and cloudy, meatbag. Wear a coat.").

#### System Status (template-based)

```python
STATUS_TEMPLATES = [
    "I've been running for {uptime}. CPU's at {cpu_temp}. {sessions} sessions today. I'm doing better than you, that's for sure.",
    "{cpu_temp} CPU, {uptime} uptime, {sessions} conversations. I'm basically perfect.",
    "Still alive after {uptime}. {cpu_temp} on the processor. Handled {sessions} of you meatbags today.",
]
```

Data sources:
- `{uptime}` — `subprocess.run(["uptime", "-p"])` or read `/proc/uptime` and format
- `{cpu_temp}` — read `/sys/class/thermal/thermal_zone0/temp` and format (e.g., "42°C")
- `{sessions}` — count session_start entries in today's conversation log

On-demand TTS, same as time/date.

### Registration

Add `ContextualHandler` to the handler list in `Responder.__init__()`:

```python
handlers = [
    RealClipHandler(...),
    PreGenHandler(...),
    PromotedHandler(...),
    WeatherHandler(),
    NewsHandler(),
    HAHandler(),
    TimerHandler(),
    ContextualHandler(),  # NEW
]
```

---

## Files Changed

| File | Changes |
|---|---|
| `scripts/intent.py` | Add `CONTEXTUAL_PATTERNS` and `CONTEXTUAL` intent classification before PERSONAL |
| `scripts/handlers/contextual_handler.py` | New — `ContextualHandler` with time, date, weather_detail, status sub-handlers |
| `scripts/responder.py` | Add `ContextualHandler()` to handler list in `__init__` |
| `tests/test_intent.py` | Add tests for CONTEXTUAL patterns — time, date, weather_detail, status |
| `tests/test_contextual_handler.py` | New — tests for each sub-key with mocked system data |

---

## Testing Strategy

- **Intent classification:** Test that "what time is it", "what's the date", "how hot is it", "how are you doing" all classify as CONTEXTUAL with correct sub-keys
- **Overlap with PERSONAL:** Test that "how are you feeling" still classifies as PERSONAL, not CONTEXTUAL
- **Time/date templates:** Mock `datetime.now()`, verify formatted output in TTS text
- **Weather detail:** Mock `briefings.get_weather_text()`, verify data is passed to AI
- **System status:** Mock `/proc/uptime` and thermal zone reads, verify formatted output
- **Handler integration:** Test via `Responder.get_response()` end-to-end
