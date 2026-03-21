"""Tests for NewsHandler."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest


def test_handle_success():
    """Returns a Response when briefings returns a WAV path."""
    with patch("briefings.get_news_wav", return_value="/tmp/news.wav"):
        from handlers.news_handler import NewsHandler

        handler = NewsHandler()
        result = handler.handle("what's in the news", "NEWS")

        assert result is not None
        assert result.wav_path == "/tmp/news.wav"
        assert result.method == "handler_news"
        assert result.intent == "NEWS"
        assert result.text == "news briefing"


def test_handle_failure():
    """Returns None when briefings returns None (no WAV available)."""
    with patch("briefings.get_news_wav", return_value=None):
        from handlers.news_handler import NewsHandler

        handler = NewsHandler()
        result = handler.handle("what's in the news", "NEWS")

        assert result is None


def test_handle_passes_sub_key():
    """sub_key is forwarded to the Response."""
    with patch("briefings.get_news_wav", return_value="/tmp/news.wav"):
        from handlers.news_handler import NewsHandler

        handler = NewsHandler()
        result = handler.handle("headlines", "NEWS", sub_key="bbc")

        assert result is not None
        assert result.sub_key == "bbc"


def test_intents_declaration():
    """NewsHandler declares the NEWS intent."""
    from handlers.news_handler import NewsHandler

    assert "NEWS" in NewsHandler.intents
