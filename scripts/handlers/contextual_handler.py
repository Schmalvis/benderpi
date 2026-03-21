"""Handler for contextual queries: time, date, weather detail, system status."""

from __future__ import annotations

import os
import random
from datetime import datetime

import tts_generate
from handler_base import Handler, Response
from logger import get_logger
from config import cfg

log = get_logger("contextual_handler")

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


def _get_cpu_temp() -> str:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            millideg = int(f.read().strip())
        return f"{millideg // 1000}°C"
    except Exception:
        return "unknown"


def _get_uptime() -> str:
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
        try:
            import briefings
            weather_data = briefings.get_weather_text()
            if not weather_data:
                return None
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
