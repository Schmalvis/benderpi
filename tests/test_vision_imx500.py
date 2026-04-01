def test_config_vision_defaults():
    """Config exposes vision_model, threshold, and allowlist with sane defaults."""
    import importlib, config as _cfg_mod
    importlib.reload(_cfg_mod)
    from config import Config
    c = Config()
    assert c.vision_model == "yolo11n"
    assert c.vision_confidence_threshold == 0.20
    assert isinstance(c.vision_allowlist, list)
    assert "person" in c.vision_allowlist
    assert "laptop" in c.vision_allowlist


"""Unit tests for IMX500-based person detection in vision.py.

All camera/IMX500 interaction is mocked — these tests run offline.
"""
import types
import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


# ---------------------------------------------------------------------------
# DetectedObject / SceneDescription tests (no mocking needed)
# ---------------------------------------------------------------------------

def test_detected_object_fields():
    """DetectedObject holds label, confidence, bbox."""
    from vision import DetectedObject
    obj = DetectedObject(label="person", confidence=0.85, bbox=(10, 20, 100, 200))
    assert obj.label == "person"
    assert obj.confidence == 0.85
    assert obj.bbox == (10, 20, 100, 200)


def test_scene_description_is_empty_when_no_objects():
    from vision import SceneDescription
    assert SceneDescription().is_empty() is True


def test_scene_description_is_not_empty_with_object():
    from vision import SceneDescription, DetectedObject
    scene = SceneDescription(objects=[DetectedObject("person", 0.9, (0, 0, 100, 200))])
    assert scene.is_empty() is False


def test_persons_helper_filters_to_people_only():
    from vision import SceneDescription, DetectedObject
    scene = SceneDescription(objects=[
        DetectedObject("person", 0.9, (0, 0, 100, 200)),
        DetectedObject("laptop", 0.7, (200, 0, 400, 300)),
        DetectedObject("person", 0.6, (400, 0, 640, 400)),
    ])
    persons = scene.persons()
    assert len(persons) == 2
    assert all(p.label == "person" for p in persons)


def test_to_context_string_empty():
    from vision import SceneDescription
    assert SceneDescription().to_context_string() == "[Room: empty]"


def test_to_context_string_single_person():
    from vision import SceneDescription, DetectedObject
    scene = SceneDescription(objects=[DetectedObject("person", 0.9, (0, 0, 100, 200))])
    assert scene.to_context_string() == "[Room: 1 person]"


def test_to_context_string_multi_class():
    from vision import SceneDescription, DetectedObject
    scene = SceneDescription(objects=[
        DetectedObject("person", 0.9, (0, 0, 100, 200)),
        DetectedObject("person", 0.7, (200, 0, 300, 400)),
        DetectedObject("laptop", 0.6, (300, 0, 500, 300)),
        DetectedObject("bottle", 0.5, (400, 0, 450, 200)),
        DetectedObject("bottle", 0.4, (450, 0, 500, 200)),
    ])
    result = scene.to_context_string()
    assert result.startswith("[Room:")
    assert "2 people" in result
    assert "1 laptop" in result
    assert "2 bottles" in result


# ---------------------------------------------------------------------------
# analyse_scene() with mocked camera
# ---------------------------------------------------------------------------

import vision as _vision_module


def _make_fake_outputs(boxes, scores, classes):
    """Return list of 4 tensors in efficientdet_lite0_pp output format.
    Always pads to 100 detections to match real hardware output shape.
    """
    assert len(boxes) == len(scores) == len(classes)
    n_real = len(scores)
    # Pad to 100 slots with zero-confidence padding
    pad = 100 - n_real
    boxes_padded = list(boxes) + [[0.0, 0.0, 0.0, 0.0]] * pad
    scores_padded = list(scores) + [0.0] * pad
    classes_padded = list(classes) + [0.0] * pad
    return [
        np.array(boxes_padded, dtype=np.float32).reshape(1, 100, 4),
        np.array([scores_padded], dtype=np.float32),   # (1, 100)
        np.array([classes_padded], dtype=np.float32),  # (1, 100)
        np.array([[100.0]], dtype=np.float32),          # count
    ]


def _make_fake_cam():
    """SimpleNamespace camera with no-op stop/close for release_camera() compatibility."""
    return types.SimpleNamespace(
        capture_metadata=lambda: {},
        stop=lambda: None,
        close=lambda: None,
    )


def test_analyse_scene_no_persons(monkeypatch):
    """All detections below threshold → empty SceneDescription."""
    fake_cam = _make_fake_cam()
    fake_imx500 = types.SimpleNamespace(
        get_outputs=lambda metadata, add_batch=False: _make_fake_outputs(
            boxes=[[10, 10, 100, 200]],
            scores=[0.1],   # below 0.35 threshold
            classes=[0.0],  # person class
        )
    )
    monkeypatch.setattr(_vision_module, 'acquire_camera', lambda: fake_cam)
    monkeypatch.setattr(_vision_module, 'release_camera', lambda: None)
    monkeypatch.setattr(_vision_module, '_imx500', fake_imx500)

    result = _vision_module.analyse_scene()
    assert result.is_empty()


def test_analyse_scene_person_detected(monkeypatch):
    """Person above threshold → SceneDescription with 1 person."""
    fake_cam = _make_fake_cam()
    fake_imx500 = types.SimpleNamespace(
        get_outputs=lambda metadata, add_batch=False: _make_fake_outputs(
            boxes=[[10, 20, 200, 400]],
            scores=[0.45],  # above 0.35 threshold
            classes=[0.0],  # class 0 = person
        )
    )
    monkeypatch.setattr(_vision_module, 'acquire_camera', lambda: fake_cam)
    monkeypatch.setattr(_vision_module, 'release_camera', lambda: None)
    monkeypatch.setattr(_vision_module, '_imx500', fake_imx500)

    result = _vision_module.analyse_scene()
    assert len(result.persons) == 1
    assert result.persons[0].confidence == pytest.approx(0.45)


def test_analyse_scene_non_person_filtered(monkeypatch):
    """Non-person class above threshold is not included."""
    fake_cam = _make_fake_cam()
    fake_imx500 = types.SimpleNamespace(
        get_outputs=lambda metadata, add_batch=False: _make_fake_outputs(
            boxes=[[0, 0, 160, 240]],
            scores=[0.95],
            classes=[75.0],  # class 75 = clock, not person
        )
    )
    monkeypatch.setattr(_vision_module, 'acquire_camera', lambda: fake_cam)
    monkeypatch.setattr(_vision_module, 'release_camera', lambda: None)
    monkeypatch.setattr(_vision_module, '_imx500', fake_imx500)

    result = _vision_module.analyse_scene()
    assert result.is_empty()


def test_analyse_scene_inference_not_ready(monkeypatch):
    """None from get_outputs (model warmup) → empty scene, no crash."""
    fake_cam = _make_fake_cam()
    fake_imx500 = types.SimpleNamespace(
        get_outputs=lambda metadata, add_batch=False: None
    )
    monkeypatch.setattr(_vision_module, 'acquire_camera', lambda: fake_cam)
    monkeypatch.setattr(_vision_module, 'release_camera', lambda: None)
    monkeypatch.setattr(_vision_module, '_imx500', fake_imx500)

    result = _vision_module.analyse_scene()
    assert result.is_empty()


def test_analyse_scene_camera_not_initialised(monkeypatch):
    """Camera init failure → empty scene, no crash."""
    monkeypatch.setattr(_vision_module, 'acquire_camera', lambda: (_ for _ in ()).throw(RuntimeError("no camera")))

    result = _vision_module.analyse_scene()
    assert result.is_empty()
