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


class TestTypeCoercionGuard:
    """Config.__init__ must reject overrides whose type disagrees with the
    class-level default, log-and-skip rather than crash the service at boot."""

    def test_string_where_int_is_skipped(self, tmp_path, capsys):
        import json
        from config import Config
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"ai_max_tokens": "lots"}))
        c = Config(config_path=str(p), env_path="/dev/null")
        # default preserved, not the bad string
        assert c.ai_max_tokens == 150
        assert "ignoring override" in capsys.readouterr().err

    def test_int_accepted_where_float_declared(self, tmp_path):
        """JSON has one number type; an int for a float field is legitimate
        (e.g. an int http_timeout_s). Must NOT be skipped by the type guard.
        (local_llm_timeout is a poor probe here — it hits the separate
        _clamp_local_llm_timeout ceiling; http_timeout_s is unclamped.)"""
        import json
        from config import Config
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"http_timeout_s": 12, "speech_rate": 2}))
        c = Config(config_path=str(p), env_path="/dev/null")
        assert c.http_timeout_s == 12
        assert c.speech_rate == 2

    def test_bool_where_number_is_skipped(self, tmp_path):
        import json
        from config import Config
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"speech_rate": True}))
        c = Config(config_path=str(p), env_path="/dev/null")
        assert c.speech_rate == 1.0  # default kept, bool rejected

    def test_led_tuple_special_case_still_works(self, tmp_path):
        import json
        from config import Config
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"led_colour": [10, 20, 30]}))
        c = Config(config_path=str(p), env_path="/dev/null")
        assert c.led_colour == (10, 20, 30)

    def test_production_config_has_no_type_warnings(self, capsys):
        """The real deployed bender_config.json must not trigger the guard —
        legit legacy values (ints for floats) must survive untouched."""
        import os
        from config import Config
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg_path = os.path.join(base, "bender_config.json")
        Config(config_path=cfg_path, env_path="/dev/null")
        assert "ignoring override" not in capsys.readouterr().err


class TestLoadDotenv:
    """_load_dotenv must warn per malformed line (with line number) rather
    than silently drop the whole file, and never crash on a missing file."""

    def test_malformed_line_warns_and_rest_still_parsed(self, tmp_path, capsys, monkeypatch):
        from config import Config
        env_path = tmp_path / ".env"
        env_path.write_text("this line has no equals sign\nHA_TOKEN=good-token\n")
        monkeypatch.delenv("HA_TOKEN", raising=False)
        c = Config(config_path=str(tmp_path / "nonexistent.json"), env_path=str(env_path))
        assert c.ha_token == "good-token"
        err = capsys.readouterr().err
        assert "malformed line" in err
        assert ":1:" in err  # line-numbered

    def test_missing_env_file_no_crash(self, tmp_path):
        from config import Config
        c = Config(
            config_path=str(tmp_path / "nonexistent.json"),
            env_path=str(tmp_path / "does-not-exist.env"),
        )
        assert c is not None

    def test_comments_and_blank_lines_skipped_silently(self, tmp_path, capsys, monkeypatch):
        from config import Config
        env_path = tmp_path / ".env"
        env_path.write_text("# a comment\n\nHA_TOKEN=abc\n")
        monkeypatch.delenv("HA_TOKEN", raising=False)
        c = Config(config_path=str(tmp_path / "nonexistent.json"), env_path=str(env_path))
        assert c.ha_token == "abc"
        assert "malformed" not in capsys.readouterr().err


class TestValidate:
    """Config.validate() -- warn-loudly-not-crash for empty required secrets,
    scoped to what the active config actually needs."""

    def test_hybrid_backend_warns_on_missing_anthropic_key(self, tmp_path, monkeypatch):
        from config import Config
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HA_TOKEN", raising=False)
        c = Config(config_path=str(tmp_path / "nonexistent.json"), env_path="/dev/null")
        c.ai_backend = "hybrid"
        c.anthropic_api_key = ""
        c.ha_token = "present"
        missing = c.validate()
        assert missing == ["anthropic_api_key"]

    def test_local_only_backend_does_not_require_anthropic_key(self, tmp_path):
        from config import Config
        c = Config(config_path=str(tmp_path / "nonexistent.json"), env_path="/dev/null")
        c.ai_backend = "local_only"
        c.anthropic_api_key = ""
        c.ha_token = "present"
        missing = c.validate()
        assert "anthropic_api_key" not in missing

    def test_missing_ha_token_always_warns(self, tmp_path):
        from config import Config
        c = Config(config_path=str(tmp_path / "nonexistent.json"), env_path="/dev/null")
        c.ai_backend = "local_only"
        c.anthropic_api_key = ""
        c.ha_token = ""
        missing = c.validate()
        assert "ha_token" in missing

    def test_all_secrets_present_returns_empty(self, tmp_path):
        from config import Config
        c = Config(config_path=str(tmp_path / "nonexistent.json"), env_path="/dev/null")
        c.ai_backend = "hybrid"
        c.anthropic_api_key = "sk-test"
        c.ha_token = "test-token"
        assert c.validate() == []


def test_vlm_defaults(tmp_path):
    from config import Config
    cfg = Config(config_path=str(tmp_path / "nonexistent.json"))
    assert cfg.vlm_timeout == 4.0
    assert cfg.vlm_prompt == "Briefly describe what you see."


def test_vlm_json_override(tmp_path):
    import json
    from config import Config
    json_path = tmp_path / "bender_config.json"
    json_path.write_text(json.dumps({
        "vlm_timeout": 6.0,
        "vlm_prompt": "What objects are visible?"
    }))
    cfg = Config(config_path=str(json_path))
    assert cfg.vlm_timeout == 6.0
    assert cfg.vlm_prompt == "What objects are visible?"
