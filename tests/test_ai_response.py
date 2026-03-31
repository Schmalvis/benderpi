import sys
from unittest.mock import MagicMock, patch, PropertyMock

# anthropic is not installed in the test environment; stub it before any import
anthropic_mock = MagicMock()
sys.modules.setdefault("anthropic", anthropic_mock)

import pytest
from ai_response import AIResponder


class TestAIResponderInjectSceneContext:
    """Tests for inject_scene_context() on AIResponder."""

    def _make_responder(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("ai_response.anthropic.Anthropic"):
                return AIResponder()

    def test_inject_scene_context_prepends_to_first_message(self):
        """Scene context is prepended to the first user turn."""
        responder = _make_responder_with_mock_api()
        mock_create, responder = responder

        responder.inject_scene_context("[Room: adult male ~35]")
        responder.respond("hello")

        call_kwargs = mock_create.call_args.kwargs
        messages = call_kwargs["messages"]
        assert messages[0]["content"] == "[Room: adult male ~35] hello"

    def test_inject_scene_context_not_repeated_on_second_turn(self):
        """Scene context is NOT prepended on subsequent turns."""
        mock_create, responder = _make_responder_with_mock_api()

        responder.inject_scene_context("[Room: adult male ~35]")
        responder.respond("hello")
        responder.respond("and another thing")

        # Second call — check its messages list
        second_call_kwargs = mock_create.call_args_list[1].kwargs
        messages = second_call_kwargs["messages"]
        # Last user message should be plain text, no context prefix
        user_messages = [m for m in messages if m["role"] == "user"]
        assert user_messages[-1]["content"] == "and another thing"

    def test_clear_history_resets_scene_context(self):
        """clear_history() resets _scene_context to empty string."""
        _, responder = _make_responder_with_mock_api()
        responder.inject_scene_context("[Room: adult male ~35]")
        assert responder._scene_context == "[Room: adult male ~35]"
        responder.clear_history()
        assert responder._scene_context == ""

    def test_no_context_injected_when_none_set(self):
        """If inject_scene_context was never called, user text is unchanged."""
        mock_create, responder = _make_responder_with_mock_api()
        responder.respond("hello")

        call_kwargs = mock_create.call_args.kwargs
        messages = call_kwargs["messages"]
        assert messages[0]["content"] == "hello"

    def test_scene_context_initial_value_is_empty(self):
        """_scene_context starts as empty string."""
        _, responder = _make_responder_with_mock_api()
        assert responder._scene_context == ""


def _make_responder_with_mock_api():
    """Helper: create AIResponder with mocked Anthropic client. Returns (mock_create, responder)."""
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="Bite my shiny metal ass!")]

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("ai_response.anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_message
            responder = AIResponder()

    # Keep the mock accessible after construction
    responder.client = mock_client
    return mock_client.messages.create, responder
