"""The "say this" IPC: the note the dashboard leaves for the conversation process.

Why it exists, measured on-device 2026-10-01: the old puppet path stopped
bender-converse, took the speaker, and started it again -- 3.5s of silence
before a soundboard clip, 23s of deafness afterwards while Whisper and Qwen
reloaded, and a start-limit kill on the SIXTH action inside five minutes.

These tests cover the protocol, including the cases that would bite on
Halloween night: a stale note, a hand-edited path, and the service being down.
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import say_request


@pytest.fixture
def ipc(tmp_path, monkeypatch):
    """Point the protocol at a throwaway repo root."""
    base = tmp_path / "repo"
    (base / "speech" / "wav").mkdir(parents=True)
    monkeypatch.setattr(say_request, "_BASE_DIR", str(base))
    monkeypatch.setattr(say_request, "REQUEST_PATH",
                        str(base / ".say_request.json"))
    monkeypatch.setattr(say_request, "TEMP_DIR", str(base / ".say_tmp"))
    clip = base / "speech" / "wav" / "yo.wav"
    clip.write_bytes(b"RIFF....WAVEfake")
    return base, str(clip)


class TestTheHappyPath:
    def test_a_request_is_written_and_claimed_once(self, ipc):
        _, clip = ipc
        req_id = say_request.request(clip)
        assert req_id
        assert say_request.pending()
        payload = say_request.claim()
        assert payload["id"] == req_id
        assert payload["wav"] == os.path.realpath(clip)
        # claimed exactly once: a second consumer gets nothing
        assert not say_request.pending()
        assert say_request.claim() is None

    def test_a_relative_path_resolves_against_the_repo(self, ipc):
        base, _ = ipc
        assert say_request.request("speech/wav/yo.wav")
        payload = say_request.claim()
        assert payload["wav"] == os.path.realpath(
            os.path.join(str(base), "speech/wav/yo.wav"))

    def test_the_note_is_written_atomically(self, ipc):
        """A half-written note must never be readable: the consumer polls this
        file every 80ms frame."""
        _, clip = ipc
        say_request.request(clip)
        assert not os.path.exists(say_request.REQUEST_PATH + ".part")
        with open(say_request.REQUEST_PATH) as f:
            json.load(f)        # parses, so it was never half-written

    def test_pending_is_cheap(self, ipc):
        """Called on every 80ms wake-word frame, so it must not cost anything."""
        _, clip = ipc
        say_request.request(clip)
        start = time.monotonic()
        for _ in range(2000):
            say_request.pending()
        per_call_us = (time.monotonic() - start) / 2000 * 1e6
        assert per_call_us < 100, f"{per_call_us:.0f}us per check"


class TestDepthOneQueue:
    def test_a_second_request_replaces_the_first(self, ipc):
        """A puppeteer pressing a button twice wants the SECOND thing said,
        not two things queued behind a 5s clip."""
        _, clip = ipc
        first = say_request.request(clip)
        second = say_request.request(clip)
        assert first != second
        assert say_request.claim()["id"] == second
        assert say_request.claim() is None


class TestItRefusesWhatItShould:
    def test_a_path_outside_the_repo_is_refused(self, ipc):
        """The writer validates, but a stale or hand-edited note must never
        become an arbitrary-file read."""
        assert say_request.request("/etc/passwd") is None
        assert say_request.request("/tmp/elsewhere.wav") is None
        assert not say_request.pending()

    def test_a_traversal_path_is_refused(self, ipc):
        assert say_request.request("../../../etc/shadow.wav") is None

    def test_a_non_wav_is_refused(self, ipc):
        base, _ = ipc
        script = base / "speech" / "wav" / "evil.sh"
        script.write_text("#!/bin/sh\nrm -rf /\n")
        assert say_request.request(str(script)) is None

    def test_a_missing_file_is_refused(self, ipc):
        base, _ = ipc
        assert say_request.request(str(base / "speech" / "wav" / "nope.wav")) is None

    def test_a_claim_revalidates_the_path(self, ipc):
        """The file could have been hand-edited, or the clip deleted, between
        the write and the claim."""
        base, clip = ipc
        say_request.request(clip)
        with open(say_request.REQUEST_PATH, "w") as f:
            json.dump({"id": "x", "wav": "/etc/passwd", "temp": False,
                       "created": time.time()}, f)
        assert say_request.claim() is None

    def test_a_malformed_note_is_discarded_not_retried(self, ipc):
        """A note that cannot be parsed must not wedge the wake loop into
        claiming it on every frame for ever."""
        with open(say_request.REQUEST_PATH, "w") as f:
            f.write("{not json")
        assert say_request.claim() is None
        assert not say_request.pending()


class TestStaleness:
    def test_an_old_note_is_discarded(self, ipc):
        """A note found ten minutes later must not suddenly speak at a child."""
        _, clip = ipc
        say_request.request(clip)
        with open(say_request.REQUEST_PATH) as f:
            payload = json.load(f)
        payload["created"] = time.time() - 600
        with open(say_request.REQUEST_PATH, "w") as f:
            json.dump(payload, f)
        assert say_request.claim() is None

    def test_a_note_survives_a_long_conversation_turn(self, ipc):
        """The measured turn_total tail is 19s, so the window must exceed it."""
        assert say_request.DEFAULT_MAX_AGE_S > 19.0
        _, clip = ipc
        say_request.request(clip)
        with open(say_request.REQUEST_PATH) as f:
            payload = json.load(f)
        payload["created"] = time.time() - 20
        with open(say_request.REQUEST_PATH, "w") as f:
            json.dump(payload, f)
        assert say_request.claim() is not None


class TestTempRenders:
    def test_a_temp_render_is_deleted_after_playing(self, ipc):
        base, _ = ipc
        tmp = os.path.join(say_request.temp_dir(), "say_1.wav")
        with open(tmp, "wb") as f:
            f.write(b"RIFF....WAVEfake")
        say_request.request(tmp, temp=True)
        payload = say_request.claim()
        say_request.done(payload)
        assert not os.path.exists(tmp)

    def test_a_real_clip_is_never_deleted_even_if_flagged_temp(self, ipc):
        """Defence in depth: a `temp: true` flag on a library clip must not
        delete the clip library."""
        _, clip = ipc
        say_request.request(clip, temp=True)
        payload = say_request.claim()
        say_request.done(payload)
        assert os.path.exists(clip), "the clip library must be untouchable"

    def test_a_stale_note_takes_its_render_with_it(self, ipc):
        tmp = os.path.join(say_request.temp_dir(), "say_2.wav")
        with open(tmp, "wb") as f:
            f.write(b"RIFF....WAVEfake")
        say_request.request(tmp, temp=True)
        with open(say_request.REQUEST_PATH) as f:
            payload = json.load(f)
        payload["created"] = time.time() - 600
        with open(say_request.REQUEST_PATH, "w") as f:
            json.dump(payload, f)
        assert say_request.claim() is None
        assert not os.path.exists(tmp), "a discarded note must not leak its WAV"

    def test_orphans_are_swept(self, ipc):
        """A request never claimed (converse down) leaves its render behind,
        and these are a few hundred KB each."""
        d = say_request.temp_dir()
        old = os.path.join(d, "say_old.wav")
        new = os.path.join(d, "say_new.wav")
        for p in (old, new):
            with open(p, "wb") as f:
                f.write(b"RIFF")
        os.utime(old, (time.time() - 3600, time.time() - 3600))
        removed = say_request.sweep_temp()
        assert removed == 1
        assert not os.path.exists(old)
        assert os.path.exists(new)

    def test_the_sweep_ignores_everything_that_is_not_a_wav(self, ipc):
        d = say_request.temp_dir()
        keep = os.path.join(d, "notes.txt")
        with open(keep, "w") as f:
            f.write("x")
        os.utime(keep, (time.time() - 3600, time.time() - 3600))
        say_request.sweep_temp()
        assert os.path.exists(keep)


class TestClear:
    def test_clear_removes_the_note_and_any_partial(self, ipc):
        _, clip = ipc
        say_request.request(clip)
        with open(say_request.REQUEST_PATH + ".part", "w") as f:
            f.write("{}")
        say_request.clear()
        assert not say_request.pending()
        assert not os.path.exists(say_request.REQUEST_PATH + ".part")
