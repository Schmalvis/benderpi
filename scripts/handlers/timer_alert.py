"""Timer alert interaction — plays alert sound, listens for dismissal."""
from __future__ import annotations

import os
import re
import random
import time
from typing import Callable

from config import cfg
from handler_base import load_clips_from_index
from logger import get_logger
from metrics import metrics

log = get_logger("timer_alert")

DISMISS_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\b(stop|enough|ok|okay|shut up|quiet|silence|dismiss)\b",
        r"\bthat'?s?\s*(enough|ok|fine)\b",
        r"\bplease stop\b",
        r"\byes\b",
        r"\bgot it\b",
        r"\bthank(s| you)\b",
    ]
]


class TimerAlertRunner:
    def __init__(self, index_path: str = None, base_dir: str = None):
        _base = base_dir or os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        self._base_dir = _base
        _idx = index_path or os.path.join(
            self._base_dir, "speech", "responses", "index.json"
        )
        self._alert_clips = load_clips_from_index("timer_alerts", _idx, self._base_dir)

    def _is_dismiss(self, text: str) -> bool:
        if not text:
            return False
        return any(p.search(text) for p in DISMISS_PATTERNS)

    def run(
        self,
        fired_timers: list[dict],
        on_chunk: Callable | None = None,
        on_done: Callable | None = None,
        on_flash: Callable[[bool], None] | None = None,
    ) -> None:
        """Play-pause alert cycle for fired timers until dismissed."""
        import audio
        import stt
        import timers as timers_mod
        import tts_generate

        labels = [t["label"] for t in fired_timers]
        label_str = ", ".join(labels) if labels else "timer"
        log.info("Timer alert: %s", label_str)
        metrics.count("timer_alert", labels=label_str)

        max_seconds = cfg.timer_alert_max_seconds
        start_time = time.time()
        dismissed_by_voice = False

        # Start LED alert flash
        if on_flash:
            on_flash(True)

        while time.time() - start_time < max_seconds:
            # 1. Play an alert clip
            audio.open_session()
            if self._alert_clips:
                clip = random.choice(self._alert_clips)
                audio.play(clip, on_chunk=on_chunk, on_done=on_done)
            else:
                # Fallback: generate TTS
                wav = tts_generate.speak(f"Timer for {label_str} is done!")
                audio.play(wav, on_chunk=on_chunk, on_done=on_done)
                try:
                    os.unlink(wav)
                except OSError:
                    pass
            audio.close_session()

            # 2. Listen for dismissal (~3 seconds)
            text = stt.listen_and_transcribe()
            if text and self._is_dismiss(text):
                log.info("Timer dismissed by voice: %r", text)
                dismissed_by_voice = True
                break

            # Also check web UI dismissal (file-based)
            remaining_fired = timers_mod.check_fired()
            if not remaining_fired:
                log.info("Timer dismissed via UI")
                break

        # Stop LED flash
        if on_flash:
            on_flash(False)

        # Dismiss all fired timers
        count = timers_mod.dismiss_all_fired()
        log.info("Dismissed %d timer(s)", count)
        metrics.count(
            "timer_dismissed",
            count=count,
            method="voice" if dismissed_by_voice else "timeout",
        )

        # Play dismissal confirmation
        audio.open_session()
        responses = [
            f"Finally. {label_str} timer dismissed.",
            f"About time. {label_str} done and dismissed.",
            "Dismissed. You're welcome. Again.",
        ]
        wav = tts_generate.speak(random.choice(responses))
        audio.play(wav, on_chunk=on_chunk, on_done=on_done)
        try:
            os.unlink(wav)
        except OSError:
            pass
        audio.close_session()
