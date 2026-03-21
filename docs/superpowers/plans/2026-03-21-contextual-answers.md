# Contextual Answers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-data responses for time, date, weather detail, and system status queries, delivered in Bender's voice via templates or AI.

**Architecture:** A new `CONTEXTUAL` intent in `intent.py` with sub-keys (time, date, weather_detail, status). A new `ContextualHandler` in `handlers/contextual_handler.py` using templates for time/date/status, AI for weather detail. Registered in the dispatch table via `responder.py`.

**Tech Stack:** Python 3.13, pytest, datetime, subprocess (for uptime/CPU temp)

**Spec:** `docs/superpowers/specs/2026-03-21-contextual-answers-design.md`

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `scripts/handlers/contextual_handler.py` | ContextualHandler — time, date, weather_detail, status sub-handlers |
| `tests/test_contextual_handler.py` | Tests for ContextualHandler |

### Modified files

| File | Changes |
|---|---|
| `scripts/intent.py` | Add CONTEXTUAL_PATTERNS and classify check before PERSONAL |
| `scripts/responder.py` | Import and register ContextualHandler |
| `tests/test_intent.py` | Add tests for CONTEXTUAL patterns |

---

## Task 1: Add CONTEXTUAL intent to intent.py

**Files:**
- Modify: `scripts/intent.py`
- Modify: `tests/test_intent.py`

- [ ] **Step 1: Write tests for CONTEXTUAL patterns**

Add to `tests/test_intent.py`:

```python
class TestContextualIntent:
    def test_what_time(self):
        intent, sub = classify("what time is it")
        assert intent == "CONTEXTUAL"
        assert sub == "time"

    def test_whats_the_time(self):
        intent, sub = classify("what's the time")
        assert intent == "CONTEXTUAL"
        assert sub == "time"

    def test_what_date(self):
        intent, sub = classify("what's the date today")
        assert intent == "CONTEXTUAL"
        assert sub == "date"

    def test_what_day(self):
        intent, sub = classify("what day is it")
        assert intent == "CONTEXTUAL"
        assert sub == "date"

    def test_temperature(self):
        intent, sub = classify("how hot is it")
        assert intent == "CONTEXTUAL"
        assert sub == "weather_detail"

    def test_is_it_raining(self):
        intent, sub = classify("is it raining outside")
        assert intent == "CONTEXTUAL"
        assert sub == "weather_detail"

    def test_status_how_are_you_doing(self):
        intent, sub = classify("how are you doing")
        assert intent == "CONTEXTUAL"
        assert sub == "status"

    def test_system_status(self):
        intent, sub = classify("system status")
        assert intent == "CONTEXTUAL"
        assert sub == "status"

    def test_feelings_stays_personal(self):
        """'how are you feeling' should stay PERSONAL, not CONTEXTUAL."""
        intent, sub = classify("how are you feeling")
        assert intent == "PERSONAL"
        assert sub == "feelings"

    def test_how_are_you_bare_stays_personal(self):
        """Bare 'how are you' should stay PERSONAL."""
        intent, sub = classify("how are you")
        assert intent == "PERSONAL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_intent.py::TestContextualIntent -v`

- [ ] **Step 3: Add CONTEXTUAL_PATTERNS to intent.py**

Add the patterns after the existing PERSONAL_PATTERNS (around line 120):

```python
CONTEXTUAL_PATTERNS = [
    # Time
    ("time", r"\b(what time|what's the time|tell me the time|current time|time is it)\b"),
    ("time", r"\b(what hour|how late is it)\b"),
    # Date
    ("date", r"\b(what('s| is) the date|what day is it|today's date|current date)\b"),
    ("date", r"\b(what month|what year is it)\b"),
    # Weather detail (conversational, not full briefing)
    ("weather_detail", r"\b(how (hot|cold|warm|chilly) is it)\b"),
    ("weather_detail", r"\b(is it (raining|snowing|sunny|cloudy|windy))\b"),
    ("weather_detail", r"\b(what('s| is) the temperature|how many degrees)\b"),
    # System status
    ("status", r"\b(how are you (doing|running)|how('s| is) it going)\b"),
    ("status", r"\b(system status|your status|health check|you ok|are you ok)\b"),
]
```

- [ ] **Step 4: Add CONTEXTUAL check in classify()**

In `classify()`, add the CONTEXTUAL check BEFORE the PERSONAL check (around line 255). The order matters — "how are you doing" must match CONTEXTUAL/status, while "how are you feeling" must match PERSONAL/feelings.

```python
    # CONTEXTUAL (before PERSONAL — more specific patterns)
    for sub_key, pattern in CONTEXTUAL_PATTERNS:
        if re.search(pattern, text_lower):
            log.debug("Intent: CONTEXTUAL/%s", sub_key)
            return "CONTEXTUAL", sub_key
```

- [ ] **Step 5: Run tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_intent.py -v`
Expected: all tests PASS including new CONTEXTUAL tests and existing PERSONAL tests

- [ ] **Step 6: Commit**

```bash
git add scripts/intent.py tests/test_intent.py
git commit -m "feat: add CONTEXTUAL intent for time, date, weather detail, status queries"
```

---

## Task 2: Create ContextualHandler

**Files:**
- Create: `scripts/handlers/contextual_handler.py`
- Create: `tests/test_contextual_handler.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_contextual_handler.py
import os
import sys
import types
from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from handlers.contextual_handler import ContextualHandler


class TestContextualTime:
    @patch("handlers.contextual_handler.tts_generate")
    def test_time_returns_response(self, mock_tts):
        mock_tts.speak.return_value = "/tmp/test.wav"
        h = ContextualHandler()
        resp = h.handle("what time is it", "CONTEXTUAL", sub_key="time")
        assert resp is not None
        assert resp.method == "handler_contextual"
        assert resp.intent == "CONTEXTUAL"
        assert resp.sub_key == "time"
        assert resp.is_temp is True
        assert resp.needs_thinking is True
        mock_tts.speak.assert_called_once()
        # Verify the TTS text contains a time-like pattern
        spoken_text = mock_tts.speak.call_args[0][0]
        assert any(c.isdigit() for c in spoken_text)  # Contains numbers (time)

    @patch("handlers.contextual_handler.tts_generate")
    def test_time_text_contains_period(self, mock_tts):
        mock_tts.speak.return_value = "/tmp/test.wav"
        h = ContextualHandler()
        resp = h.handle("what time is it", "CONTEXTUAL", sub_key="time")
        spoken = mock_tts.speak.call_args[0][0]
        # Should contain AM or PM
        assert "AM" in spoken.upper() or "PM" in spoken.upper() or ":" in spoken


class TestContextualDate:
    @patch("handlers.contextual_handler.tts_generate")
    def test_date_returns_response(self, mock_tts):
        mock_tts.speak.return_value = "/tmp/test.wav"
        h = ContextualHandler()
        resp = h.handle("what's the date", "CONTEXTUAL", sub_key="date")
        assert resp is not None
        assert resp.method == "handler_contextual"
        assert resp.sub_key == "date"
        mock_tts.speak.assert_called_once()


class TestContextualStatus:
    @patch("handlers.contextual_handler.tts_generate")
    @patch("handlers.contextual_handler._get_cpu_temp", return_value="42°C")
    @patch("handlers.contextual_handler._get_uptime", return_value="3 hours")
    @patch("handlers.contextual_handler._get_session_count", return_value=5)
    def test_status_returns_response(self, mock_sessions, mock_uptime, mock_temp, mock_tts):
        mock_tts.speak.return_value = "/tmp/test.wav"
        h = ContextualHandler()
        resp = h.handle("how are you doing", "CONTEXTUAL", sub_key="status")
        assert resp is not None
        assert resp.sub_key == "status"
        spoken = mock_tts.speak.call_args[0][0]
        assert "42" in spoken  # CPU temp in response
        assert "3 hours" in spoken  # Uptime in response


class TestContextualUnknownSubKey:
    def test_unknown_sub_key_returns_none(self):
        h = ContextualHandler()
        resp = h.handle("something", "CONTEXTUAL", sub_key="unknown")
        assert resp is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_contextual_handler.py -v`

- [ ] **Step 3: Implement ContextualHandler**

Create `scripts/handlers/contextual_handler.py`:

```python
"""Handler for contextual queries: time, date, weather detail, system status."""

from __future__ import annotations

import os
import random
import subprocess
from datetime import datetime

import tts_generate
from handler_base import Handler, Response
from logger import get_logger
from config import cfg

log = get_logger("contextual_handler")

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TIME_TEMPLATES = [
    "It's {time}. What, your eyes don't work? Get a clock, meatbag.",
    "The time is {time}. You're welcome, flesh tube.",
    "{time}. Now stop bothering me with stuff your phone can tell you.",
    "It's {time}, baby! Time to bend some girders. Or drink. Probably drink.",
]

DATE_TEMPLATES = [
    "It's {date}. Another day of dealing with you humans.",
    "{date}. Mark it in your calendar, the day you bothered Bender.",
    "Today is {date}. Time flies when you're made of metal.",
]

STATUS_TEMPLATES = [
    "I've been running for {uptime}. CPU's at {cpu_temp}. {sessions} sessions today. I'm doing better than you, that's for sure.",
    "{cpu_temp} CPU, {uptime} uptime, {sessions} conversations. I'm basically perfect.",
    "Still alive after {uptime}. {cpu_temp} on the processor. Handled {sessions} of you meatbags today.",
]


# ---------------------------------------------------------------------------
# System info helpers
# ---------------------------------------------------------------------------

def _get_cpu_temp() -> str:
    """Read CPU temperature from sysfs. Returns e.g. '42°C' or 'unknown'."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            millideg = int(f.read().strip())
        return f"{millideg // 1000}°C"
    except Exception:
        return "unknown"


def _get_uptime() -> str:
    """Read system uptime. Returns e.g. '3 hours' or 'unknown'."""
    try:
        with open("/proc/uptime") as f:
            seconds = int(float(f.read().split()[0]))
        if seconds < 3600:
            return f"{seconds // 60} minutes"
        elif seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''}"
        else:
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            return f"{days} day{'s' if days != 1 else ''}, {hours} hour{'s' if hours != 1 else ''}"
    except Exception:
        return "unknown"


def _get_session_count() -> int:
    """Count today's sessions from conversation logs."""
    try:
        log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
        today_file = os.path.join(log_dir, datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        if not os.path.exists(today_file):
            return 0
        count = 0
        with open(today_file) as f:
            for line in f:
                if '"session_start"' in line:
                    count += 1
        return count
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class ContextualHandler(Handler):
    """Handles contextual queries with real data + Bender personality."""

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

    def _handle_time(self) -> Response:
        now = datetime.now()
        time_str = now.strftime("%I:%M %p").lstrip("0")
        text = random.choice(TIME_TEMPLATES).format(time=time_str)
        wav = tts_generate.speak(text)
        return Response(
            text=text, wav_path=wav, method="handler_contextual",
            intent="CONTEXTUAL", sub_key="time",
            is_temp=True, needs_thinking=True,
        )

    def _handle_date(self) -> Response:
        now = datetime.now()
        date_str = now.strftime("%A, %B %d").replace(" 0", " ")
        text = random.choice(DATE_TEMPLATES).format(date=date_str)
        wav = tts_generate.speak(text)
        return Response(
            text=text, wav_path=wav, method="handler_contextual",
            intent="CONTEXTUAL", sub_key="date",
            is_temp=True, needs_thinking=True,
        )

    def _handle_weather_detail(self, user_text: str) -> Response | None:
        """Use AI to answer weather question with real data."""
        try:
            import briefings
            weather_data = briefings.get_weather_text()
            if not weather_data:
                return None  # Fall through to AI fallback
        except Exception:
            return None

        prompt_text = (
            f"The user asked: '{user_text}'. "
            f"Current weather in {cfg.location}: {weather_data}. "
            f"Answer briefly (1-2 sentences) as Bender from Futurama. "
            f"Include the actual data in your answer."
        )
        try:
            from ai_response import AIResponder
            ai = AIResponder()
            wav = ai.respond(prompt_text)
            return Response(
                text=prompt_text, wav_path=wav, method="handler_contextual",
                intent="CONTEXTUAL", sub_key="weather_detail",
                is_temp=True, needs_thinking=True, model=cfg.ai_model,
            )
        except Exception as e:
            log.warning("Weather detail AI failed: %s", e)
            return None

    def _handle_status(self) -> Response:
        cpu_temp = _get_cpu_temp()
        uptime = _get_uptime()
        sessions = _get_session_count()
        text = random.choice(STATUS_TEMPLATES).format(
            cpu_temp=cpu_temp, uptime=uptime, sessions=sessions,
        )
        wav = tts_generate.speak(text)
        return Response(
            text=text, wav_path=wav, method="handler_contextual",
            intent="CONTEXTUAL", sub_key="status",
            is_temp=True, needs_thinking=True,
        )
```

- [ ] **Step 4: Run tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_contextual_handler.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/handlers/contextual_handler.py tests/test_contextual_handler.py
git commit -m "feat: add ContextualHandler for time, date, weather detail, status queries"
```

---

## Task 3: Register ContextualHandler in responder

**Files:**
- Modify: `scripts/responder.py`

- [ ] **Step 1: Add import and registration**

In `scripts/responder.py`:

1. Add import (in the handler import block inside `__init__`):
```python
from handlers.contextual_handler import ContextualHandler
```

2. Add to the handlers list (after PromotedHandler, before WeatherHandler):
```python
ContextualHandler(),
```

- [ ] **Step 2: Run full test suite**

Run: `cd /c/ws/benderpi && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add scripts/responder.py
git commit -m "feat: register ContextualHandler in responder dispatch table"
```
