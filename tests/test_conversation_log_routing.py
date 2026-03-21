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
