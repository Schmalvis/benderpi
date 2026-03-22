import pytest
from handler_base import Response, Handler, load_clips_from_index


class TestResponse:
    def test_required_fields(self):
        r = Response(text="hi", wav_path="/tmp/a.wav", method="real_clip", intent="GREETING")
        assert r.text == "hi"
        assert r.wav_path == "/tmp/a.wav"
        assert r.method == "real_clip"
        assert r.intent == "GREETING"

    def test_defaults(self):
        r = Response(text="hi", wav_path="/tmp/a.wav", method="real_clip", intent="GREETING")
        assert r.sub_key is None
        assert r.is_temp is False
        assert r.needs_thinking is False
        assert r.model is None

    def test_all_fields(self):
        r = Response(
            text="yo", wav_path="/tmp/b.wav", method="ai_fallback",
            intent="UNKNOWN", sub_key="job", is_temp=True,
            needs_thinking=True, model="claude-haiku-4-5-20251001",
        )
        assert r.sub_key == "job"
        assert r.is_temp is True
        assert r.needs_thinking is True
        assert r.model == "claude-haiku-4-5-20251001"


class TestHandler:
    def test_base_handler_raises(self):
        h = Handler()
        with pytest.raises(NotImplementedError):
            h.handle("hello", "GREETING")

    def test_default_intents_empty(self):
        h = Handler()
        assert h.intents == []


class TestLoadClipsFromIndex:
    def test_loads_key(self, tmp_path):
        import json
        index = {"thinking": ["clips/think1.wav", "clips/think2.wav"]}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index))
        # Create the actual files so they pass the existence check
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        (clips_dir / "think1.wav").write_bytes(b"RIFF" + b"\x00" * 40)
        (clips_dir / "think2.wav").write_bytes(b"RIFF" + b"\x00" * 40)
        clips = load_clips_from_index("thinking", str(index_path), str(tmp_path))
        assert len(clips) == 2
        assert all(str(tmp_path) in c for c in clips)

    def test_missing_key_returns_empty(self, tmp_path):
        import json
        index = {"other": ["a.wav"]}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index))
        clips = load_clips_from_index("thinking", str(index_path), str(tmp_path))
        assert clips == []

    def test_missing_file_returns_empty(self, tmp_path):
        clips = load_clips_from_index("thinking", str(tmp_path / "nope.json"), str(tmp_path))
        assert clips == []

    def test_filters_nonexistent_files(self, tmp_path):
        import json
        index = {"thinking": ["exists.wav", "missing.wav"]}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index))
        (tmp_path / "exists.wav").write_bytes(b"RIFF" + b"\x00" * 40)
        clips = load_clips_from_index("thinking", str(index_path), str(tmp_path))
        assert len(clips) == 1


class TestLoadClipsFromIndexObjects:
    def test_loads_object_entries(self, tmp_path):
        import json
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        (clips_dir / "think1.wav").write_bytes(b"RIFF" + b"\x00" * 40)
        index = {"thinking": [{"file": "clips/think1.wav", "label": "Hmm..."}]}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index))
        clips = load_clips_from_index("thinking", str(index_path), str(tmp_path))
        assert len(clips) == 1
        assert clips[0].endswith("think1.wav")

    def test_loads_mixed_entries(self, tmp_path):
        import json
        clips_dir = tmp_path / "clips"
        clips_dir.mkdir()
        (clips_dir / "a.wav").write_bytes(b"RIFF" + b"\x00" * 40)
        (clips_dir / "b.wav").write_bytes(b"RIFF" + b"\x00" * 40)
        index = {"thinking": ["clips/a.wav", {"file": "clips/b.wav", "label": "Hmm"}]}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index))
        clips = load_clips_from_index("thinking", str(index_path), str(tmp_path))
        assert len(clips) == 2


class TestResponseRoutingLog:
    def test_routing_log_default_none(self):
        r = Response(text="hi", wav_path="/tmp/a.wav", method="real_clip", intent="GREETING")
        assert r.routing_log is None

    def test_routing_log_set(self):
        log = {"scenario": "conversation", "routing_rule": "local_first"}
        r = Response(text="hi", wav_path="/tmp/a.wav", method="ai_local",
                     intent="UNKNOWN", routing_log=log)
        assert r.routing_log == log
        assert r.routing_log["scenario"] == "conversation"
