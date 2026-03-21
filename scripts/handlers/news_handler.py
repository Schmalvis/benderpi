"""Handler for news briefing responses."""
from __future__ import annotations
import briefings
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("news_handler")


class NewsHandler(Handler):
    intents = ["NEWS"]

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        wav_path = briefings.get_news_wav()
        if not wav_path:
            log.warning("NewsHandler: no news WAV available")
            return None
        return Response(text="news briefing", wav_path=wav_path,
                       method="handler_news", intent=intent, sub_key=sub_key)
