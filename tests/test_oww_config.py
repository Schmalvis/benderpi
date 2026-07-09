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
    # Lowered from 0.5 to 0.35 alongside N-of-M temporal smoothing: a lower
    # per-frame bar recovers recall, and requiring multiple frames over it
    # (oww_frames_required-of-oww_window) restores precision.
    assert cfg.oww_threshold == 0.35


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
