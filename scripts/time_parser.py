"""Natural language time parser for voice assistant input.

Pure-logic module — no external dependencies beyond Python stdlib.
Parses spoken time expressions into durations (seconds) or datetimes.
"""

import re
from datetime import datetime, timedelta

# ── Word-to-number map ──────────────────────────────────────────────

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty one": 21, "twenty two": 22, "twenty three": 23, "twenty four": 24,
    "twenty five": 25, "thirty": 30, "forty": 40, "forty five": 45,
    "fifty": 50, "sixty": 60,
    "a": 1, "an": 1,
}

# Build regex alternation for word numbers, longest first to avoid partial matches
_WORD_NUM_PATTERN = "|".join(
    re.escape(w) for w in sorted(WORD_NUMBERS, key=len, reverse=True)
)

# Unit multipliers in seconds
_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
}

_UNIT_PATTERN = "|".join(sorted(_UNIT_SECONDS, key=len, reverse=True))

# Number pattern: digits or word numbers
_NUM_PATTERN = rf"(?:\d+(?:\.\d+)?|{_WORD_NUM_PATTERN})"


def _to_number(s: str) -> float | None:
    """Convert a string (digit or word) to a number."""
    s = s.strip().lower()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return WORD_NUMBERS.get(s)


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace."""
    return " ".join(text.lower().split())


# ── parse_duration ──────────────────────────────────────────────────

def parse_duration(text: str) -> float | None:
    """Parse a spoken duration expression into seconds.

    Returns None if no duration found.

    Examples:
        "5 minutes" -> 300
        "ten minutes" -> 600
        "half an hour" -> 1800
        "an hour and a half" -> 5400
        "2 hours and 30 minutes" -> 9000
        "a few minutes" -> 180
    """
    if not text:
        return None

    text = _normalize(text)

    # Special case: "a few minutes"
    if "a few minutes" in text:
        return 180.0

    # Special case: "half an hour" / "half a minute"
    m = re.search(r"half\s+(?:an?\s+)(hour|minute|second)s?", text)
    if m:
        unit = m.group(1)
        return _UNIT_SECONDS.get(unit, 0) * 0.5

    # Pattern: "X and a half <unit>" e.g. "two and a half minutes", "an hour and a half"
    pat_and_half = (
        rf"({_NUM_PATTERN})\s+(hour|minute|second)s?\s+and\s+a\s+half"
        rf"|({_NUM_PATTERN})\s+and\s+a\s+half\s+(hour|minute|second)s?"
    )
    m = re.search(pat_and_half, text)
    if m:
        if m.group(1) is not None:
            # "an hour and a half"
            num = _to_number(m.group(1))
            unit = m.group(2)
        else:
            # "two and a half minutes"
            num = _to_number(m.group(3))
            unit = m.group(4)
        if num is not None and unit in _UNIT_SECONDS:
            return (num + 0.5) * _UNIT_SECONDS[unit]

    # Compound: "1 hour 30 minutes", "1 hour and 30 minutes"
    # Also handles single "5 minutes", "an hour", etc.
    pat_segment = rf"({_NUM_PATTERN})\s+({_UNIT_PATTERN})"
    matches = list(re.finditer(pat_segment, text))
    if matches:
        total = 0.0
        for match in matches:
            num = _to_number(match.group(1))
            unit = match.group(2)
            if num is not None and unit in _UNIT_SECONDS:
                total += num * _UNIT_SECONDS[unit]
        if total > 0:
            return total

    return None


# ── parse_alarm_time ────────────────────────────────────────────────

def parse_alarm_time(text: str) -> datetime | None:
    """Parse a spoken alarm time expression into a datetime.

    If the time is in the past today, assumes tomorrow.
    Returns None if no time expression found.

    Examples:
        "10am" -> today 10:00 (or tomorrow if past)
        "3:30pm" -> today 15:30
        "tomorrow at 6pm" -> tomorrow 18:00
        "tomorrow morning" -> tomorrow 08:00
    """
    if not text:
        return None

    text = _normalize(text)
    now = datetime.now()
    tomorrow = now.date() + timedelta(days=1)
    is_tomorrow = "tomorrow" in text

    # "tomorrow morning" / "tomorrow evening"
    if is_tomorrow:
        if "morning" in text:
            return datetime(tomorrow.year, tomorrow.month, tomorrow.day, 8, 0)
        if "evening" in text or "night" in text:
            return datetime(tomorrow.year, tomorrow.month, tomorrow.day, 18, 0)

    # Time with am/pm: "10am", "3:30pm", "6 pm"
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        ampm = m.group(3)
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

        if is_tomorrow:
            return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute)
        target = datetime(now.year, now.month, now.day, hour, minute)
        if target <= now:
            target += timedelta(days=1)
        return target

    # 24-hour format: "10:00", "15:30"
    m = re.search(r"(\d{1,2}):(\d{2})(?!\s*[ap]m)", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if is_tomorrow:
            return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, minute)
        target = datetime(now.year, now.month, now.day, hour, minute)
        if target <= now:
            target += timedelta(days=1)
        return target

    # "tomorrow at 6" (bare number after "at")
    if is_tomorrow:
        m = re.search(r"at\s+(\d{1,2})", text)
        if m:
            hour = int(m.group(1))
            return datetime(tomorrow.year, tomorrow.month, tomorrow.day, hour, 0)

    # Bare number after "at": "at 6" (assume AM if <=12)
    m = re.search(r"at\s+(\d{1,2})(?!\s*[ap]m|:\d)", text)
    if m:
        hour = int(m.group(1))
        target = datetime(now.year, now.month, now.day, hour, 0)
        if target <= now:
            target += timedelta(days=1)
        return target

    return None


# ── extract_label ───────────────────────────────────────────────────

def extract_label(text: str) -> str:
    """Extract a label/name from a timer or alarm command.

    Patterns:
        "set a timer for pasta for 10 minutes" -> "pasta"
        "set a timer for 10 minutes" -> "timer"
        "set an alarm for work at 6am" -> "work"
        "alarm at 10am" -> "alarm"

    Returns "timer" as default if no label found.
    """
    if not text:
        return "timer"

    text = _normalize(text)

    # "for <label> for <duration>" pattern
    m = re.search(r"for\s+(.+?)\s+for\s+", text)
    if m:
        label = m.group(1).strip()
        # Make sure the label isn't just a number/duration
        if label and not re.match(rf"^(?:{_NUM_PATTERN})\s*$", label):
            return label

    # "for <label> at <time>" pattern
    m = re.search(r"for\s+(.+?)\s+at\s+", text)
    if m:
        label = m.group(1).strip()
        # Skip if label looks like a duration or number
        if label and not re.match(rf"^(?:{_NUM_PATTERN})\s*$", label):
            # Skip if label is just "a timer", "an alarm", etc.
            if label not in ("a timer", "an alarm", "a reminder"):
                return label

    # Detect if it's an alarm command
    if re.search(r"\balarm\b", text):
        return "alarm"

    return "timer"


# ── CLI test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        "5 minutes",
        "ten minutes",
        "half an hour",
        "an hour and a half",
        "2 hours and 30 minutes",
        "set a timer for pasta for 10 minutes",
        "a few minutes",
        "twenty five minutes",
        "a minute",
    ]
    for t in tests:
        d = parse_duration(t)
        label = extract_label(t)
        print(f"  {t!r:50s} -> {d}s, label={label!r}")
