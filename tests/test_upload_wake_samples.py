"""Uploading the captured samples to a private Hugging Face dataset.

This is household audio: family conversation, the television, whatever was in
the room. The upload path is the one place it leaves the device, so the two
things worth pinning are that a public repo is refused and that clips never go
up without the frozen split (which would let a training run use the held-out
ones).
"""
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import upload_wake_samples as up


@pytest.fixture
def samples(tmp_path, monkeypatch):
    root = tmp_path / "data" / "wake_samples"
    for mode, n in (("positive", 3), ("hard_negative", 2)):
        d = root / mode / "martin"
        d.mkdir(parents=True)
        for i in range(n):
            (d / f"mid_normal_{i:03d}.wav").write_bytes(b"RIFF" * 10)
    for mode in ("ambient", "conversation"):
        (root / mode).mkdir(parents=True)
        (root / mode / "000.wav").write_bytes(b"RIFF" * 10)
    split = {
        "positive": {"train": ["a.wav"], "holdout": ["b.wav"]},
        "hard_negative": {"train": ["c.wav"], "holdout": ["d.wav"], "watch": []},
        "ambient": {"background": ["e.wav"], "holdout": ["f.wav"]},
        "conversation": {"train": [], "holdout": []},
    }
    (root / "split.json").write_text(json.dumps(split))
    monkeypatch.setattr(up, "SAMPLES_DIR", str(root))
    monkeypatch.setattr(up, "SPLIT_PATH", str(root / "split.json"))
    return root, split


class TestSplitGuard:
    def test_missing_split_is_refused(self, samples):
        root, _ = samples
        os.remove(root / "split.json")
        with pytest.raises(SystemExit, match="split_wake_samples"):
            up.check_split(str(root / "split.json"))

    def test_a_split_with_no_holdout_is_refused(self, samples):
        root, split = samples
        split["positive"]["holdout"] = []
        (root / "split.json").write_text(json.dumps(split))
        with pytest.raises(SystemExit, match="holds out no positives"):
            up.check_split(str(root / "split.json"))

    def test_a_valid_split_passes(self, samples):
        root, _ = samples
        assert up.check_split(str(root / "split.json"))["positive"]["holdout"]


class TestPrivacy:
    def _api(self, private):
        calls = {"created": None, "uploaded": False}

        class _Api:
            def whoami(self):
                return {"name": "someone"}

            def create_repo(self, **kw):
                calls["created"] = kw

            def repo_info(self, **kw):
                return types.SimpleNamespace(private=private)

            def upload_folder(self, **kw):
                calls["uploaded"] = True

        return _Api, calls

    def _run(self, monkeypatch, api_cls):
        hub = types.ModuleType("huggingface_hub")
        hub.HfApi = api_cls
        monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
        monkeypatch.setattr(sys, "argv", ["upload_wake_samples.py"])
        up.main()

    def test_repo_is_created_private(self, samples, monkeypatch, capsys):
        api, calls = self._api(private=True)
        self._run(monkeypatch, api)
        assert calls["created"]["private"] is True
        assert calls["created"]["repo_type"] == "dataset"
        assert calls["uploaded"] is True

    def test_a_public_repo_is_refused_before_uploading(self, samples, monkeypatch):
        api, calls = self._api(private=False)
        with pytest.raises(SystemExit, match="PUBLIC"):
            self._run(monkeypatch, api)
        assert calls["uploaded"] is False

    def test_dry_run_uploads_nothing(self, samples, monkeypatch, capsys):
        api, calls = self._api(private=True)
        hub = types.ModuleType("huggingface_hub")
        hub.HfApi = api
        monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
        monkeypatch.setattr(sys, "argv", ["upload_wake_samples.py", "--dry-run"])
        up.main()
        assert calls["uploaded"] is False
        assert "nothing uploaded" in capsys.readouterr().out


class TestSummary:
    def test_counts_every_mode(self, samples):
        root, _ = samples
        c = up.summarise(str(root))
        assert c["positive"] == 3
        assert c["hard_negative"] == 2
        assert c["ambient"] == 1
        assert c["conversation"] == 1
        assert c["bytes"] > 0
