"""Tests for PromotedHandler."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest


def test_handle_valid_sub_key(tmp_path):
    """Returns a Response when sub_key resolves to an existing WAV file."""
    wav = tmp_path / "my_response.wav"
    wav.write_bytes(b"RIFF")

    from handlers.promoted_handler import PromotedHandler

    handler = PromotedHandler(base_dir=str(tmp_path))
    result = handler.handle("some text", "PROMOTED", sub_key="my_response.wav")

    assert result is not None
    assert result.method == "promoted_tts"
    assert result.intent == "PROMOTED"
    assert result.wav_path == str(wav)
    assert result.text == "my_response.wav"


def test_handle_none_sub_key(tmp_path):
    """Returns None when sub_key is None."""
    from handlers.promoted_handler import PromotedHandler

    handler = PromotedHandler(base_dir=str(tmp_path))
    result = handler.handle("some text", "PROMOTED", sub_key=None)

    assert result is None


def test_handle_empty_sub_key(tmp_path):
    """Returns None when sub_key is an empty string."""
    from handlers.promoted_handler import PromotedHandler

    handler = PromotedHandler(base_dir=str(tmp_path))
    result = handler.handle("some text", "PROMOTED", sub_key="")

    assert result is None


def test_handle_missing_file(tmp_path):
    """Returns None when sub_key points to a non-existent file."""
    from handlers.promoted_handler import PromotedHandler

    handler = PromotedHandler(base_dir=str(tmp_path))
    result = handler.handle("some text", "PROMOTED", sub_key="nonexistent.wav")

    assert result is None


def test_intents_declaration():
    """PromotedHandler declares the PROMOTED intent."""
    from handlers.promoted_handler import PromotedHandler

    assert "PROMOTED" in PromotedHandler.intents


def test_the_default_base_is_the_repo_root():
    # index.json paths are repo-relative ("speech/responses/..."). The default
    # pointed at scripts/, so a handler built without base_dir looked for
    # scripts/speech/... and missed every clip. Responder always passes the
    # repo root, so only a caller built by hand (as on the device 2026-10-29,
    # 13:04:17) ever hit it.
    from handlers.promoted_handler import PromotedHandler

    root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
    assert PromotedHandler()._base_dir == root
