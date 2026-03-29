"""Tests for centralised config module."""
import json
import os
import tempfile

def test_config_loads_defaults(tmp_path):
    """Config should have sensible defaults without any files."""
    from config import Config
    cfg = Config(config_path=str(tmp_path / "nonexistent.json"))
    assert cfg.sample_rate == 44100
    assert cfg.whisper_model == "tiny.en"
    assert cfg.silence_timeout == 8.0
    assert cfg.ai_model == "claude-haiku-4-5-20251001"
    assert cfg.log_level == "INFO"

def test_config_loads_json_overrides(tmp_path):
    """Config should load overrides from a JSON file."""
    from config import Config
    json_path = tmp_path / "bender_config.json"
    json_path.write_text(json.dumps({"whisper_model": "base.en", "silence_timeout": 12.0}))
    cfg = Config(config_path=str(json_path))
    assert cfg.whisper_model == "base.en"
    assert cfg.silence_timeout == 12.0
    assert cfg.sample_rate == 44100  # unchanged default

def test_config_env_overrides(tmp_path, monkeypatch):
    """Env vars should override JSON and defaults."""
    from config import Config
    monkeypatch.setenv("BENDER_LOG_LEVEL", "DEBUG")
    cfg = Config(config_path=str(tmp_path / "nonexistent.json"))
    assert cfg.log_level == "DEBUG"

def test_config_speech_rate_default(tmp_path):
    """speech_rate should default to 1.0 when no config file exists."""
    from config import Config
    cfg = Config(config_path=str(tmp_path / "nonexistent.json"))
    assert cfg.speech_rate == 1.0

def test_config_speech_rate_override(tmp_path):
    """speech_rate should be overridable from JSON."""
    from config import Config
    json_path = tmp_path / "bender_config.json"
    json_path.write_text(json.dumps({"speech_rate": 0.8}))
    cfg = Config(config_path=str(json_path))
    assert cfg.speech_rate == 0.8

def test_config_secrets_from_env(monkeypatch):
    """Secrets come from env/.env only, never from config JSON."""
    from config import Config
    monkeypatch.setenv("HA_TOKEN", "test-token-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cfg = Config()
    assert cfg.ha_token == "test-token-123"
    assert cfg.anthropic_api_key == "sk-test"

def test_session_file_default():
    """cfg.session_file should point to .session_active.json in base dir."""
    from config import cfg
    assert cfg.session_file.endswith(".session_active.json")

def test_end_session_file_default():
    """cfg.end_session_file should point to .end_session in base dir."""
    from config import cfg
    assert cfg.end_session_file.endswith(".end_session")

def test_ha_exclude_entities_default():
    """cfg.ha_exclude_entities should be a list."""
    from config import cfg
    assert isinstance(cfg.ha_exclude_entities, list)

def test_abort_file_default():
    """cfg.abort_file should point to .abort_playback in base dir."""
    from config import cfg
    assert cfg.abort_file.endswith(".abort_playback")

def test_dismissal_ends_session_default():
    """cfg.dismissal_ends_session should default to True."""
    from config import cfg
    assert cfg.dismissal_ends_session is True

def test_ha_room_synonyms_default():
    from config import cfg
    assert isinstance(cfg.ha_room_synonyms, dict)


class TestLocalLLMConfig:
    def test_defaults(self, tmp_path):
        from config import Config
        empty_cfg = tmp_path / "empty.json"
        empty_cfg.write_text("{}")
        c = Config(config_path=str(empty_cfg), env_path="/dev/null")
        assert c.ai_backend == "hybrid"
        assert c.local_llm_model == "qwen2.5:1.5b"
        assert c.local_llm_url == "http://localhost:11434"
        assert c.local_llm_timeout == 3
        assert c.ai_routing == {
            "conversation": "cloud_first",
            "knowledge": "cloud_first",
            "creative": "cloud_first",
        }

    def test_config_override(self, tmp_path):
        import json
        from config import Config
        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text(json.dumps({
            "ai_backend": "cloud_only",
            "local_llm_timeout": 10,
            "ai_routing": {"conversation": "cloud_only",
                           "knowledge": "cloud_only",
                           "creative": "local_only"},
        }))
        c = Config(config_path=str(cfg_file), env_path="/dev/null")
        assert c.ai_backend == "cloud_only"
        assert c.local_llm_timeout == 10
        assert c.ai_routing["conversation"] == "cloud_only"
        assert c.ai_routing["creative"] == "local_only"
