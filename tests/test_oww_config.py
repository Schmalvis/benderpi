"""Tests for OWW config fields."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_oww_config_fields_exist():
    from config import cfg
    assert hasattr(cfg, 'oww_model_path'), "cfg missing oww_model_path"
    assert hasattr(cfg, 'oww_threshold'), "cfg missing oww_threshold"
    assert isinstance(cfg.oww_model_path, str)
    assert isinstance(cfg.oww_threshold, float)
    assert 0.0 < cfg.oww_threshold <= 1.0


def test_oww_threshold_default_is_reasonable():
    from config import cfg
    # 0.5 -> 0.35 alongside N-of-M temporal smoothing: a lower per-frame bar
    # recovers recall, and requiring multiple frames over it
    # (oww_frames_required-of-oww_window) restores precision.
    #
    # 0.35 -> 0.1 on 2026-08-01, because 0.35 never fired for a real human.
    # Measured on-device: hey_bender_v0.1 scores 0.97 on a synthetic (Piper)
    # "hey bender" but only 0.23-0.27 on a live speaker -- it was trained on
    # TTS-generated positives and doesn't generalise to real voices. Over 34h
    # of continuous listening it never exceeded 0.023 on non-wake audio (loud
    # speech at RMS 11363 scored 0.001), so precision is ample and the gap
    # between noise and voice is wide and empty.
    #
    # Not set just under 0.27: those are the best *single* frame in an
    # utterance, and oww_frames_required needs a second frame over the line.
    assert cfg.oww_threshold == 0.1


def test_oww_smoothing_fields_exist():
    from config import cfg
    assert hasattr(cfg, 'oww_frames_required')
    assert hasattr(cfg, 'oww_window')
    assert isinstance(cfg.oww_frames_required, int)
    assert isinstance(cfg.oww_window, int)
    assert 1 <= cfg.oww_frames_required <= cfg.oww_window


def test_no_pvporcupine_imports_in_source():
    """Ensure no Python source file in scripts/ imports pvporcupine."""
    import glob
    scripts = glob.glob('scripts/**/*.py', recursive=True)
    for path in scripts:
        with open(path) as f:
            content = f.read()
        assert 'pvporcupine' not in content, \
            f"Found pvporcupine import in {path}"
        assert 'PORCUPINE_ACCESS_KEY' not in content, \
            f"Found PORCUPINE_ACCESS_KEY in {path}"
