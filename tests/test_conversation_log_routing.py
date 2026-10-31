import json
import pytest
from conversation_log import SessionLogger


class TestAIRoutingLog:
    def test_log_turn_without_routing(self, tmp_path, monkeypatch):
        """ai_routing absent when not provided."""
        monkeypatch.setattr("conversation_log.LOG_DIR", str(tmp_path))
        sl = SessionLogger()
        sl.log_turn("hello", "GREETING", None, "real_clip", "hey meatbag")
        log_file = list(tmp_path.glob("*.jsonl"))[0]
        entry = json.loads(log_file.read_text().strip().split("\n")[-1])
        assert entry["type"] == "turn"
        assert "ai_routing" not in entry

    def test_log_turn_with_routing(self, tmp_path, monkeypatch):
        """ai_routing present when provided."""
        monkeypatch.setattr("conversation_log.LOG_DIR", str(tmp_path))
        sl = SessionLogger()
        routing = {
            "scenario": "conversation",
            "routing_rule": "local_first",
            "local_attempted": True,
            "quality_check_passed": True,
            "final_method": "ai_local",
        }
        sl.log_turn("what do you think", "UNKNOWN", None,
                     "ai_local", "Humans are pathetic", ai_routing=routing)
        log_file = list(tmp_path.glob("*.jsonl"))[0]
        entry = json.loads(log_file.read_text().strip().split("\n")[-1])
        assert entry["ai_routing"]["scenario"] == "conversation"
        assert entry["ai_routing"]["final_method"] == "ai_local"


class TestNote:
    """SessionLogger.note(): fields held for the next log_turn, then cleared."""

    @staticmethod
    def _entries(tmp_path):
        log_file = list(tmp_path.glob("*.jsonl"))[0]
        return [json.loads(line) for line in log_file.read_text().splitlines()]

    @staticmethod
    def _turn(sl, **kw):
        sl.log_turn("hello", "GREETING", None, "real_clip", "hey meatbag", **kw)

    def test_no_note_entry_is_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setattr("conversation_log.LOG_DIR", str(tmp_path))
        sl = SessionLogger()
        self._turn(sl)
        entry = self._entries(tmp_path)[0]
        entry.pop("ts")
        assert entry == {
            "type": "turn", "session_id": sl.session_id, "turn": 1,
            "user_text": "hello", "intent": "GREETING", "sub_key": None,
            "method": "real_clip", "response_text": "hey meatbag",
            "model": None,
        }
        assert list(entry) == ["type", "session_id", "turn", "user_text",
                               "intent", "sub_key", "method",
                               "response_text", "model"]

    def test_note_merges_into_next_turn(self, tmp_path, monkeypatch):
        monkeypatch.setattr("conversation_log.LOG_DIR", str(tmp_path))
        sl = SessionLogger()
        sl.note(frame_requested="a/b.jpg", presence_mm=640, presence_age_s=0.4)
        self._turn(sl)
        entry = self._entries(tmp_path)[0]
        assert entry["frame_requested"] == "a/b.jpg"
        assert entry["presence_mm"] == 640
        assert entry["presence_age_s"] == 0.4
        assert entry["turn"] == 1

    def test_note_is_cleared_after_one_turn(self, tmp_path, monkeypatch):
        monkeypatch.setattr("conversation_log.LOG_DIR", str(tmp_path))
        sl = SessionLogger()
        sl.note(frame_requested="a/b.jpg")
        self._turn(sl)
        self._turn(sl)
        second = self._entries(tmp_path)[1]
        assert "frame_requested" not in second

    def test_two_notes_merge(self, tmp_path, monkeypatch):
        monkeypatch.setattr("conversation_log.LOG_DIR", str(tmp_path))
        sl = SessionLogger()
        sl.note(frame_requested="a/b.jpg")
        sl.note(presence_mm=None, presence_age_s=1.5)
        self._turn(sl)
        entry = self._entries(tmp_path)[0]
        assert entry["frame_requested"] == "a/b.jpg"
        assert entry["presence_mm"] is None
        assert entry["presence_age_s"] == 1.5

    def test_core_fields_win(self, tmp_path, monkeypatch):
        monkeypatch.setattr("conversation_log.LOG_DIR", str(tmp_path))
        sl = SessionLogger()
        sl.note(type="evil", session_id="x", turn=99, user_text="u",
                intent="I", sub_key="s", method="m", response_text="r",
                model="mdl", ai_routing={"a": 1}, frame_requested="f.jpg")
        self._turn(sl)
        entry = self._entries(tmp_path)[0]
        assert entry["type"] == "turn"
        assert entry["session_id"] == sl.session_id
        assert entry["turn"] == 1
        assert entry["user_text"] == "hello"
        assert entry["intent"] == "GREETING"
        assert entry["sub_key"] is None
        assert entry["method"] == "real_clip"
        assert entry["response_text"] == "hey meatbag"
        assert entry["model"] is None
        assert "ai_routing" not in entry
        assert entry["frame_requested"] == "f.jpg"

    def test_core_ai_routing_wins_over_note(self, tmp_path, monkeypatch):
        monkeypatch.setattr("conversation_log.LOG_DIR", str(tmp_path))
        sl = SessionLogger()
        sl.note(ai_routing={"fake": True})
        self._turn(sl, ai_routing={"real": True})
        assert self._entries(tmp_path)[0]["ai_routing"] == {"real": True}

    def test_note_is_cleared_when_the_write_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("conversation_log.LOG_DIR", str(tmp_path))
        sl = SessionLogger()
        sl.note(frame_requested="a/b.jpg")

        def boom(record):
            raise OSError("disk full")

        monkeypatch.setattr("conversation_log._write", boom)
        with pytest.raises(OSError):
            self._turn(sl)
        monkeypatch.undo()
        monkeypatch.setattr("conversation_log.LOG_DIR", str(tmp_path))
        self._turn(sl)
        entry = self._entries(tmp_path)[-1]
        assert "frame_requested" not in entry

    def test_note_does_not_leak_between_loggers(self, tmp_path, monkeypatch):
        monkeypatch.setattr("conversation_log.LOG_DIR", str(tmp_path))
        a, b = SessionLogger(), SessionLogger()
        a.note(frame_requested="a.jpg")
        self._turn(b)
        assert "frame_requested" not in self._entries(tmp_path)[0]
