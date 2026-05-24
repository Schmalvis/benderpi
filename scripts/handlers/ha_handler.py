"""Handler for Home Assistant control responses."""
from __future__ import annotations

from handlers import ha_control
from handlers.ha_control import make_default
from handler_base import Handler, Response
from config import cfg
from logger import get_logger

log = get_logger("ha_handler")


class HAHandler(Handler):
    intents = ["HA_CONTROL"]

    def __init__(self) -> None:
        self._registry, self._matcher, self._client = make_default(cfg)
        self._last_entities: list[dict] = []

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        wav_path, self._last_entities = ha_control.control(
            text,
            registry=self._registry,
            matcher=self._matcher,
            client=self._client,
            last_entities=self._last_entities,
        )
        if not wav_path:
            log.warning("HAHandler: HA control returned no WAV")
            return None
        return Response(text=text, wav_path=wav_path, method="handler_ha",
                        intent=intent, sub_key=sub_key)
