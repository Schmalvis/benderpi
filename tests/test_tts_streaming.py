"""Tests for tts_generate streaming generator cleanup."""
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test_speak_streaming_cleans_up_on_early_close(tmp_path, monkeypatch):
    """When generator is closed before all sentences are consumed, completed
    WAV files for unyielded sentences are deleted."""
    import tts_generate as tts

    created = []

    def fake_speak_single(text):
        f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=str(tmp_path))
        f.close()
        created.append(f.name)
        time.sleep(0.01)
        return f.name

    monkeypatch.setattr(tts, "_speak_single", fake_speak_single)

    gen = tts.speak_streaming("Hello world. How are you today. Goodbye then.")

    # Consume only the first WAV
    first = next(gen)
    os.unlink(first)

    # Close generator without consuming the rest
    gen.close()

    # Allow thread pool to settle
    time.sleep(0.15)

    # All files generated after the first must be cleaned up
    leaks = [p for p in created[1:] if os.path.exists(p)]
    assert leaks == [], f"Leaked temp WAVs after generator close: {leaks}"


def test_speak_streaming_single_sentence_no_leak(tmp_path, monkeypatch):
    """Single-sentence path yields one path and completes cleanly."""
    import tts_generate as tts

    paths = []

    def fake_speak_single(text):
        f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=str(tmp_path))
        f.close()
        paths.append(f.name)
        return f.name

    monkeypatch.setattr(tts, "_speak_single", fake_speak_single)

    result = list(tts.speak_streaming("Just one sentence."))
    assert len(result) == 1
    os.unlink(result[0])
    assert len(paths) == 1
