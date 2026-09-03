"""Regression tests for the 2026-09-03 review, batch 1.

Each test pins one fix from docs/benderpi-fable-project-review-2026-09-03.md:

  C1  speak_from_iter() sanitises every sentence (the only live LLM → Piper path)
  C2  respond_streaming() yields the final sentence
  C3  a voice alarm (naive local datetime) does not crash check_fired()
  H2  audio.play() with a lost output stream returns instead of deadlocking
  H5  service_guard reports a failed bender-converse start
  H6  responder runs local-only when the cloud responder is absent
  M21 trailing punctuation does not defeat AFFIRMATION
  L7  a double space in the weather text is not "unspeakable content"
"""
import json
import os
import sys
import threading
import wave
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("anthropic", MagicMock())


# ---------------------------------------------------------------------------
# C1 — streaming TTS sanitises per sentence
# ---------------------------------------------------------------------------

class TestSpeakFromIterSanitises:
    def _run(self, sentences):
        import tts_generate
        seen = []

        def fake_single(text):
            seen.append(text)
            return f"/tmp/{len(seen)}.wav"

        with patch.object(tts_generate, "_speak_single", side_effect=fake_single):
            list(tts_generate.speak_from_iter(iter(sentences)))
        return seen

    def test_emoji_and_control_tokens_never_reach_piper(self):
        seen = self._run([
            "How are you today? 😂",
            "[{'type': 'emoji'}]",
            "Bite my shiny metal ass.<|im_start|>user\nwhat now",
        ])
        joined = " ".join(seen)
        assert "😂" not in joined
        assert "<|im_start|>" not in joined
        assert "[{" not in joined
        assert "How are you today?" in seen[0]
        assert any("Bite my shiny metal ass." in s for s in seen)

    def test_sentence_with_nothing_speakable_is_skipped_not_spoken(self):
        seen = self._run(["Hello there.", "😂😂", "<tool_call>"])
        assert seen == ["Hello there."]

    def test_plain_sentences_pass_through_in_order(self):
        seen = self._run(["One.", "Two.", "Three."])
        assert seen == ["One.", "Two.", "Three."]


# ---------------------------------------------------------------------------
# C2 — cloud stream yields its final sentence
# ---------------------------------------------------------------------------

class TestRespondStreamingFinalSentence:
    def _responder(self, chunks):
        from ai_response import AIResponder
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("ai_response.anthropic.Anthropic"):
                r = AIResponder()

        @contextmanager
        def fake_stream(**_kw):
            yield SimpleNamespace(text_stream=iter(chunks))

        r.client = MagicMock()
        r.client.messages.stream = fake_stream
        return r

    def test_two_sentence_reply_yields_both(self):
        r = self._responder(["Bite me. ", "I'm busy."])
        with patch("ai_response.metrics"):
            out = list(r.respond_streaming("hello"))
        assert out == ["Bite me.", "I'm busy."]

    def test_one_sentence_reply_is_not_silence(self):
        r = self._responder(["Shut up, ", "meatbag."])
        with patch("ai_response.metrics"):
            out = list(r.respond_streaming("hello"))
        assert out == ["Shut up, meatbag."]

    def test_history_stores_reply_exactly_once(self):
        r = self._responder(["Bite me. ", "I'm busy."])
        with patch("ai_response.metrics"):
            list(r.respond_streaming("hello"))
        assistant = [m for m in r.history if m["role"] == "assistant"]
        assert assistant[-1]["content"] == "Bite me. I'm busy."


# ---------------------------------------------------------------------------
# C3 — voice alarms are naive local datetimes; timers must not crash
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_timers(tmp_path, monkeypatch):
    import timers
    monkeypatch.setattr(timers, "_FILE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(timers, "_TMP_FILE", str(tmp_path / "timers.json.tmp"))
    timers._cache = None
    yield timers
    timers._cache = None


class TestAlarmNaiveDatetime:
    def test_voice_alarm_round_trip_does_not_raise(self, isolated_timers):
        import time_parser
        t = time_parser.parse_alarm_time("wake me up at 6am")
        assert t is not None and t.tzinfo is None  # the shape the parser really returns
        isolated_timers.create_alarm("alarm", t)
        # These three all raised TypeError before the fix.
        isolated_timers.check_fired()
        isolated_timers.list_timers()
        isolated_timers.dismiss_all_fired()

    def test_naive_past_alarm_fires(self, isolated_timers):
        past_local = datetime.now() - timedelta(minutes=1)
        isolated_timers.create_alarm("late", past_local)
        fired = isolated_timers.check_fired()
        assert [f["label"] for f in fired] == ["late"]

    def test_naive_future_alarm_does_not_fire(self, isolated_timers):
        future_local = datetime.now() + timedelta(hours=1)
        isolated_timers.create_alarm("later", future_local)
        assert isolated_timers.check_fired() == []

    def test_legacy_naive_entry_on_disk_is_tolerated(self, isolated_timers):
        # An entry written by the old code, before normalisation existed.
        legacy = [{
            "id": "a_legacy", "label": "old", "type": "alarm",
            "created": datetime.now(timezone.utc).isoformat(),
            "fires_at": (datetime.now() - timedelta(minutes=5)).isoformat(),
            "duration_s": None, "fired": False, "dismissed": False,
        }]
        with open(isolated_timers._FILE, "w") as f:
            json.dump(legacy, f)
        isolated_timers._cache = None
        fired = isolated_timers.check_fired()
        assert [f["id"] for f in fired] == ["a_legacy"]

    def test_malformed_entry_is_skipped_not_fatal(self, isolated_timers):
        bad = [{"id": "a_bad", "label": "x", "type": "alarm", "fires_at": "not-a-date",
                "dismissed": False}]
        with open(isolated_timers._FILE, "w") as f:
            json.dump(bad, f)
        isolated_timers._cache = None
        assert isolated_timers.check_fired() == []


# ---------------------------------------------------------------------------
# H2 — play() must not deadlock when the output stream is gone
# ---------------------------------------------------------------------------

class TestPlayReopenDoesNotDeadlock:
    def test_play_with_no_stream_returns(self, tmp_path):
        for key in list(sys.modules):
            if key == "audio":
                del sys.modules[key]
        import audio

        stream = MagicMock()
        stream.is_active.return_value = True
        fake_pa = MagicMock()
        fake_pa.open.return_value = stream
        audio._pa = fake_pa
        audio._stream = None  # the reopen branch inside play()
        audio.get_output_device_index = lambda: 0

        wav_path = tmp_path / "beep.wav"
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"\x00\x00" * 441)

        result = {}

        def run():
            try:
                audio.play(str(wav_path))
            except Exception as exc:  # pragma: no cover - reported below
                result["exc"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "play() deadlocked on the reopen path"
        assert "exc" not in result, result.get("exc")
        assert fake_pa.open.called


# ---------------------------------------------------------------------------
# H5 — a failed bender-converse start is reported, not swallowed
# ---------------------------------------------------------------------------

class TestStartConverseReportsFailure:
    def _rc(self, code, err=""):
        return SimpleNamespace(returncode=code, stdout="", stderr=err)

    def test_start_limit_hit_is_logged_and_returned(self):
        from web import service_guard as sg
        calls = []

        def fake_run(argv, **_kw):
            calls.append(argv)
            if "start" in argv:
                return self._rc(1, "Job for bender-converse.service failed: start-limit-hit")
            return self._rc(0)

        with patch.object(sg, "_IS_LINUX", True), \
             patch.object(sg.subprocess, "run", side_effect=fake_run), \
             patch.object(sg, "log") as log:
            ok = sg._start_converse()

        assert ok is False
        assert sg.last_start_failed is True
        assert any("reset-failed" in argv for argv in calls)
        assert log.error.called

    def test_retry_after_reset_failed_succeeds(self):
        from web import service_guard as sg
        starts = iter([self._rc(1, "start-limit-hit"), self._rc(0)])

        def fake_run(argv, **_kw):
            if "start" in argv:
                return next(starts)
            return self._rc(0)

        with patch.object(sg, "_IS_LINUX", True), \
             patch.object(sg.subprocess, "run", side_effect=fake_run), \
             patch.object(sg, "log"):
            ok = sg._start_converse()
        assert ok is True
        assert sg.last_start_failed is False


# ---------------------------------------------------------------------------
# H6 — no cloud responder → local-only routing, not an error line
# ---------------------------------------------------------------------------

class TestRoutingWithoutCloud:
    @pytest.fixture
    def responder(self):
        from responder import Responder
        with patch("responder.Responder.__init__", return_value=None):
            r = Responder.__new__(Responder)
            r._dispatch = {}
            return r

    @patch("responder.tts_generate")
    @patch("responder.cfg")
    def test_local_first_without_cloud_uses_local_unconditionally(self, mock_cfg, mock_tts, responder):
        mock_cfg.ai_backend = "hybrid"
        mock_cfg.ai_routing = {"conversation": "local_first"}
        mock_cfg.ai_model = "claude-haiku"
        mock_tts.speak.return_value = "/tmp/local.wav"
        ai_local = MagicMock()
        ai_local.generate_stream.return_value = iter(["Bite my shiny metal ass."])

        resp = responder._respond_ai("hello bender", None, "UNKNOWN", None, ai_local)

        assert resp.method == "ai_local_stream"
        assert resp.routing_log["routing_rule"] == "local_only"
        ai_local.generate_stream.assert_called_once()


# ---------------------------------------------------------------------------
# M21 — trailing punctuation from Whisper
# ---------------------------------------------------------------------------

class TestIntentTrailingPunctuation:
    @pytest.mark.parametrize("text", ["Okay.", "Great!", "Cheers.", "Nice one.", "Thanks!"])
    def test_affirmation_survives_punctuation(self, text):
        import intent
        assert intent.classify(text)[0] == "AFFIRMATION"

    @pytest.mark.parametrize("text", ["Bye.", "That's all.", "Stop!"])
    def test_dismissal_survives_punctuation(self, text):
        import intent
        assert intent.classify(text)[0] == "DISMISSAL"


# ---------------------------------------------------------------------------
# L7 — whitespace is not unspeakable content
# ---------------------------------------------------------------------------

class TestSanitiserIgnoresWhitespace:
    def test_double_space_does_not_warn(self):
        import tts_generate
        with patch.object(tts_generate, "log") as log, \
             patch.object(tts_generate, "metrics") as metrics:
            out = tts_generate._sanitize_for_speech(
                "Right, weather. Currently 10 degrees and partly cloudy.  Partly cloudy.")
        assert out == "Right, weather. Currently 10 degrees and partly cloudy. Partly cloudy."
        assert not log.warning.called
        assert not metrics.count.called

    def test_real_junk_still_warns(self):
        import tts_generate
        with patch.object(tts_generate, "log") as log, \
             patch.object(tts_generate, "metrics"):
            tts_generate._sanitize_for_speech("Hi there 😂")
        assert log.warning.called

    def test_weather_text_has_no_double_space_when_calm(self):
        import briefings

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({
                    "state": "partlycloudy",
                    "attributes": {"temperature": 10.2, "humidity": 88, "wind_speed": 3},
                }).encode()

        with patch.object(briefings.urllib.request, "urlopen", return_value=_Resp()), \
             patch.object(briefings, "_get_forecast", return_value=[]):
            text = briefings.get_weather_text()
        assert "  " not in text
        assert text.endswith(".")
