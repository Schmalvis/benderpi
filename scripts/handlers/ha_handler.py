"""Handler for Home Assistant control responses."""
from __future__ import annotations
from handlers import ha_control
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("ha_handler")


class HAHandler(Handler):
    intents = ["HA_CONTROL"]

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        wav_path = ha_control.control(text)
        if not wav_path:
            log.warning("HAHandler: HA control returned no WAV")
            return None
        return Response(text=text, wav_path=wav_path, method="handler_ha",
                       intent=intent, sub_key=sub_key)
