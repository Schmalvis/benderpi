"""Unit tests for IMX500-based person detection in vision.py.

All camera/IMX500 interaction is mocked — these tests run offline.
"""
import types
import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


# ---------------------------------------------------------------------------
# SceneDescription / PersonInfo tests (no mocking needed)
# ---------------------------------------------------------------------------

from vision import SceneDescription, PersonInfo


def test_scene_empty_context_string():
    s = SceneDescription()
    assert s.to_context_string() == "[Room: empty]"


def test_scene_one_person_context_string():
    s = SceneDescription(persons=[PersonInfo(confidence=0.9, bbox=(10, 20, 100, 200))])
    assert s.to_context_string() == "[Room: 1 person]"


def test_scene_two_people_context_string():
    s = SceneDescription(persons=[
        PersonInfo(confidence=0.9, bbox=(0, 0, 100, 200)),
        PersonInfo(confidence=0.7, bbox=(150, 0, 300, 200)),
    ])
    assert s.to_context_string() == "[Room: 2 people]"


def test_scene_is_empty_true():
    assert SceneDescription().is_empty() is True


def test_scene_is_empty_false():
    s = SceneDescription(persons=[PersonInfo(confidence=0.8, bbox=(0, 0, 320, 480))])
    assert s.is_empty() is False


# ---------------------------------------------------------------------------
# analyse_scene() with mocked camera
# ---------------------------------------------------------------------------

import vision as _vision_module


def _make_fake_outputs(boxes, scores, classes):
    """Return list of 4 tensors in efficientdet_lite0_pp output format."""
    n = len(scores)
    return [
        np.array(boxes, dtype=np.float32).reshape(1, n, 4),  # boxes (pixel coords)
        np.array([scores], dtype=np.float32),                  # scores
        np.array([classes], dtype=np.float32),                 # classes
        np.array([[n]], dtype=np.float32),                     # count
    ]


def test_analyse_scene_no_persons(monkeypatch):
    """All detections below threshold → empty SceneDescription."""
    fake_cam = types.SimpleNamespace(capture_metadata=lambda: {})
    fake_imx500 = types.SimpleNamespace(
        get_outputs=lambda metadata, add_batch=False: _make_fake_outputs(
            boxes=[[10, 10, 100, 200]],
            scores=[0.1],   # below 0.35 threshold
            classes=[0.0],  # person class
        )
    )
    monkeypatch.setattr(_vision_module, '_cam', fake_cam)
    monkeypatch.setattr(_vision_module, '_imx500', fake_imx500)

    result = _vision_module.analyse_scene()
    assert result.is_empty()


def test_analyse_scene_person_detected(monkeypatch):
    """Person above threshold → SceneDescription with 1 person."""
    fake_cam = types.SimpleNamespace(capture_metadata=lambda: {})
    fake_imx500 = types.SimpleNamespace(
        get_outputs=lambda metadata, add_batch=False: _make_fake_outputs(
            boxes=[[10, 20, 200, 400]],
            scores=[0.45],  # above 0.35 threshold
            classes=[0.0],  # class 0 = person
        )
    )
    monkeypatch.setattr(_vision_module, '_cam', fake_cam)
    monkeypatch.setattr(_vision_module, '_imx500', fake_imx500)

    result = _vision_module.analyse_scene()
    assert len(result.persons) == 1
    assert result.persons[0].confidence == pytest.approx(0.45)


def test_analyse_scene_non_person_filtered(monkeypatch):
    """Non-person class above threshold is not included."""
    fake_cam = types.SimpleNamespace(capture_metadata=lambda: {})
    fake_imx500 = types.SimpleNamespace(
        get_outputs=lambda metadata, add_batch=False: _make_fake_outputs(
            boxes=[[0, 0, 160, 240]],
            scores=[0.95],
            classes=[75.0],  # class 75 = clock, not person
        )
    )
    monkeypatch.setattr(_vision_module, '_cam', fake_cam)
    monkeypatch.setattr(_vision_module, '_imx500', fake_imx500)

    result = _vision_module.analyse_scene()
    assert result.is_empty()


def test_analyse_scene_inference_not_ready(monkeypatch):
    """None from get_outputs (model warmup) → empty scene, no crash."""
    fake_cam = types.SimpleNamespace(capture_metadata=lambda: {})
    fake_imx500 = types.SimpleNamespace(
        get_outputs=lambda metadata, add_batch=False: None
    )
    monkeypatch.setattr(_vision_module, '_cam', fake_cam)
    monkeypatch.setattr(_vision_module, '_imx500', fake_imx500)

    result = _vision_module.analyse_scene()
    assert result.is_empty()


def test_analyse_scene_camera_not_initialised(monkeypatch):
    """No camera initialised → empty scene, no crash."""
    monkeypatch.setattr(_vision_module, '_cam', None)
    monkeypatch.setattr(_vision_module, '_imx500', None)

    result = _vision_module.analyse_scene()
    assert result.is_empty()
