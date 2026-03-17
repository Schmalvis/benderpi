"""Timer and alarm handler — creates, cancels, and reports on timers with Bender personality."""

import os
import sys
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import timers
import time_parser
import tts_generate
from logger import get_logger
from metrics import metrics

log = get_logger("timer_handler")

# --- Response templates ---

SET_TIMER_RESPONSES = [
    "Fine. {label} timer set for {duration}. I'll yell at you when it's done.",
    "{duration} timer for {label}. Got it. I'll be counting. Not really.",
    "Timer for {label}, {duration}. Don't blame me if you forget about it.",
    "Done. {label} timer, {duration}. You owe me one.",
]

SET_ALARM_RESPONSES = [
    "Alarm set for {time}. I'll wake you up. Aggressively.",
    "Fine. {time}. I'll be here, waiting. As always.",
    "{time} alarm. Got it. Don't oversleep, meatbag.",
]

CANCEL_RESPONSES = [
    "The {label} timer is cancelled. You're welcome.",
    "Gone. No more {label} timer. Poof.",
    "Cancelled. One less thing for me to yell about.",
]

NO_TIMER_RESPONSES = [
    "What timer? I don't see any timer. Are you hallucinating?",
    "There's no timer to cancel. Maybe you dreamt it.",
    "Timer not found. I blame you.",
]

STATUS_RESPONSES = [
    "You've got {count} timer running. {details}",
    "{count} active timer. {details}",
]

STATUS_MULTI_RESPONSES = [
    "You've got {count} timers running. {details}",
    "{count} timers. {details} You're busy.",
]

NO_TIMERS_RESPONSES = [
    "No timers. Congratulations, you're free. For now.",
    "Nothing ticking. You want me to set one?",
    "Zero timers. Just like the number of compliments I get around here.",
]

PARSE_FAIL_RESPONSES = [
    "I didn't catch how long. Try again, and this time use actual numbers.",
    "How long? Be specific. I'm a robot, not a mind reader.",
    "I need a time. Like five minutes. Or ten seconds. Not whatever you just said.",
]


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable string."""
    if seconds < 60:
        s = int(seconds)
        return f"{s} second{'s' if s != 1 else ''}"
    elif seconds < 3600:
        m = int(seconds / 60)
        return f"{m} minute{'s' if m != 1 else ''}"
    else:
        h = int(seconds / 3600)
        m = int((seconds % 3600) / 60)
        if m:
            return f"{h} hour{'s' if h != 1 else ''} and {m} minute{'s' if m != 1 else ''}"
        return f"{h} hour{'s' if h != 1 else ''}"


def _format_remaining(seconds: float) -> str:
    """Format remaining seconds concisely."""
    if seconds <= 0:
        return "done"
    return _format_duration(seconds)


def handle_set(user_text: str) -> str:
    """Parse text, create timer or alarm, return Bender TTS WAV path."""
    # Try duration first (timer)
    duration = time_parser.parse_duration(user_text)
    if duration:
        label = time_parser.extract_label(user_text)
        timer = timers.create_timer(label, duration)
        text = random.choice(SET_TIMER_RESPONSES).format(
            label=label, duration=_format_duration(duration))
        log.info("Timer created: %s (%s, %.0fs)", timer["id"], label, duration)
        metrics.count("timer_created", type="timer", label=label)
        return tts_generate.speak(text)

    # Try alarm time
    alarm_time = time_parser.parse_alarm_time(user_text)
    if alarm_time:
        label = time_parser.extract_label(user_text)
        alarm = timers.create_alarm(label, alarm_time)
        time_str = alarm_time.strftime("%-I:%M %p" if alarm_time.minute else "%-I %p")
        text = random.choice(SET_ALARM_RESPONSES).format(time=time_str)
        log.info("Alarm created: %s (%s, %s)", alarm["id"], label, alarm_time)
        metrics.count("timer_created", type="alarm", label=label)
        return tts_generate.speak(text)

    # Couldn't parse
    text = random.choice(PARSE_FAIL_RESPONSES)
    return tts_generate.speak(text)


def handle_cancel(user_text: str) -> str:
    """Cancel a timer matching the label in text. Return TTS WAV path."""
    # Extract label from "cancel the pasta timer"
    label = time_parser.extract_label(user_text)
    active = timers.list_timers()

    # Find matching timer by label
    match = None
    for t in active:
        if t["label"].lower() == label.lower():
            match = t
            break

    # If no label match, cancel the most recent one
    if not match and active:
        if label in ("timer", "alarm"):
            match = active[-1]

    if match:
        timers.cancel_timer(match["id"])
        text = random.choice(CANCEL_RESPONSES).format(label=match["label"])
        log.info("Timer cancelled: %s (%s)", match["id"], match["label"])
        metrics.count("timer_cancelled", label=match["label"])
    else:
        text = random.choice(NO_TIMER_RESPONSES)

    return tts_generate.speak(text)


def handle_status(user_text: str) -> str:
    """Report active timers. Return TTS WAV path."""
    active = timers.list_timers()

    if not active:
        text = random.choice(NO_TIMERS_RESPONSES)
    elif len(active) == 1:
        t = active[0]
        details = f"{t['label']}: {_format_remaining(t.get('remaining_s', 0))} left."
        text = random.choice(STATUS_RESPONSES).format(count=1, details=details)
    else:
        details = ". ".join(
            f"{t['label']}: {_format_remaining(t.get('remaining_s', 0))}"
            for t in active
        )
        text = random.choice(STATUS_MULTI_RESPONSES).format(count=len(active), details=details)

    return tts_generate.speak(text)
