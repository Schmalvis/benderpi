"""Handler for weather briefing responses."""
from __future__ import annotations
import briefings
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("weather_handler")


class WeatherHandler(Handler):
    intents = ["WEATHER"]

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        wav_path = briefings.get_weather_wav()
        if not wav_path:
            log.warning("WeatherHandler: no weather WAV available")
            return None
        return Response(text="weather briefing", wav_path=wav_path,
                       method="handler_weather", intent=intent, sub_key=sub_key)
