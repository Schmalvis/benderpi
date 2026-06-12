"""Handler for time queries."""
from __future__ import annotations

import briefings
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("time_handler")


class TimeHandler(Handler):
    intents = ["TIME"]

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        if sub_key:
            log.info("TimeHandler: timezone=%s", sub_key)
        wav_path = briefings.get_time_wav(sub_key)
        if not wav_path:
            log.warning("TimeHandler: no time WAV available")
            return None
        return Response(
            text="time", wav_path=wav_path,
            method="handler_time", intent=intent, sub_key=sub_key,
        )
