"""Tests for the vision module.

All tests mock Picamera2 and cv2 — no hardware required.
"""
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# Stub heavy hardware/library imports before importing vision
sys.modules.setdefault("picamera2", MagicMock())
sys.modules.setdefault("cv2", MagicMock())

import vision  # noqa: E402 — must come after stubs
from vision import FaceInfo, SceneDescription  # noqa: E402


# ---------------------------------------------------------------------------
# SceneDescription.to_context_string
# ---------------------------------------------------------------------------
class TestSceneDescriptionContextString:
    def test_one_adult_male(self):
        faces = [FaceInfo(age_estimate=35, gender="male", confidence=0.95, bbox=(10, 10, 50, 80))]
        scene = SceneDescription(faces=faces, captured_at=datetime.now())
        assert scene.to_context_string() == "[Room: adult male ~35]"

    def test_multiple_faces(self):
        faces = [
            FaceInfo(age_estimate=40, gender="female", confidence=0.9, bbox=(0, 0, 60, 90)),
            FaceInfo(age_estimate=8, gender="male", confidence=0.85, bbox=(100, 0, 50, 70)),
        ]
        scene = SceneDescription(faces=faces, captured_at=datetime.now())
        assert scene.to_context_string() == "[Room: adult female ~40, child male ~8]"

    def test_empty_scene(self):
        scene = SceneDescription(faces=[], captured_at=datetime.now())
        assert scene.to_context_string() == "[Room: empty]"

    def test_teen_label(self):
        faces = [FaceInfo(age_estimate=16, gender="female", confidence=0.8, bbox=(0, 0, 50, 70))]
        scene = SceneDescription(faces=faces, captured_at=datetime.now())
        assert scene.to_context_string() == "[Room: teen female ~16]"

    def test_age_boundary_13_is_teen(self):
        faces = [FaceInfo(age_estimate=13, gender="male", confidence=0.8, bbox=(0, 0, 40, 60))]
        scene = SceneDescription(faces=faces, captured_at=datetime.now())
        assert scene.to_context_string() == "[Room: teen male ~13]"

    def test_age_boundary_18_is_adult(self):
        faces = [FaceInfo(age_estimate=18, gender="female", confidence=0.8, bbox=(0, 0, 40, 60))]
        scene = SceneDescription(faces=faces, captured_at=datetime.now())
        assert scene.to_context_string() == "[Room: adult female ~18]"


class TestSceneDescriptionIsEmpty:
    def test_empty_returns_true(self):
        scene = SceneDescription(faces=[])
        assert scene.is_empty() is True

    def test_with_faces_returns_false(self):
        faces = [FaceInfo(age_estimate=30, gender="male", confidence=0.9, bbox=(0, 0, 50, 60))]
        scene = SceneDescription(faces=faces)
        assert scene.is_empty() is False


# ---------------------------------------------------------------------------
# analyse_scene — no models
# ---------------------------------------------------------------------------
def test_analyse_scene_no_models():
    """Returns empty SceneDescription when models are unavailable."""
    with patch.object(vision, "models_available", return_value=False):
        result = vision.analyse_scene()
    assert isinstance(result, SceneDescription)
    assert result.is_empty()


# ---------------------------------------------------------------------------
# analyse_scene — fully mocked hardware + cv2
# ---------------------------------------------------------------------------
def test_analyse_scene_mocked():
    """Verify FaceInfo built correctly with mocked camera and DNN nets."""
    # Fake 640x480 RGB frame
    fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Fake detections: shape (1, 1, 1, 7)
    # col 2 = confidence 0.9, cols 3-6 = normalised bbox
    fake_detections = np.zeros((1, 1, 1, 7), dtype=np.float32)
    fake_detections[0, 0, 0, 2] = 0.9    # confidence
    fake_detections[0, 0, 0, 3] = 0.1    # x1_norm → 64
    fake_detections[0, 0, 0, 4] = 0.1    # y1_norm → 48
    fake_detections[0, 0, 0, 5] = 0.3    # x2_norm → 192
    fake_detections[0, 0, 0, 6] = 0.4    # y2_norm → 192

    # Age prediction: index 4 → AGE_MAP[4] = 28
    fake_age_preds = np.zeros((1, 8), dtype=np.float32)
    fake_age_preds[0, 4] = 1.0

    # Gender prediction: index 0 → "male"
    fake_gender_preds = np.zeros((1, 2), dtype=np.float32)
    fake_gender_preds[0, 0] = 1.0

    # --- Build net mocks ---
    face_net_mock = MagicMock()
    face_net_mock.forward.return_value = fake_detections

    age_net_mock = MagicMock()
    age_net_mock.forward.return_value = fake_age_preds

    gender_net_mock = MagicMock()
    gender_net_mock.forward.return_value = fake_gender_preds

    # --- Build Picamera2 mock ---
    cam_mock = MagicMock()
    cam_mock.capture_array.return_value = fake_frame
    cam_class_mock = MagicMock(return_value=cam_mock)

    picamera2_module_mock = MagicMock()
    picamera2_module_mock.Picamera2 = cam_class_mock

    # --- Build cv2 mock wired to real numpy arrays ---
    fake_blob = np.zeros((1, 3, 300, 300), dtype=np.float32)
    cv2_mock = MagicMock()
    cv2_mock.dnn.blobFromImage.return_value = fake_blob

    def fake_readnet(model_path, proto_path):
        if "res10" in model_path:
            return face_net_mock
        elif "age" in model_path:
            return age_net_mock
        else:
            return gender_net_mock

    cv2_mock.dnn.readNet.side_effect = fake_readnet

    # Pre-load the cached nets so _load_nets isn't called with wrong cv2
    vision._face_net = face_net_mock
    vision._age_net = age_net_mock
    vision._gender_net = gender_net_mock

    with patch.object(vision, "models_available", return_value=True), \
         patch.object(vision, "_load_nets"), \
         patch.dict("sys.modules", {"picamera2": picamera2_module_mock, "cv2": cv2_mock}):
        result = vision.analyse_scene()

    assert isinstance(result, SceneDescription)
    assert len(result.faces) == 1

    face = result.faces[0]
    assert face.age_estimate == 28          # AGE_MAP[4]
    assert face.gender == "male"
    assert abs(face.confidence - 0.9) < 1e-5
    # bbox = (x1, y1, w, h): x1=int(0.1*640)=64, y1=int(0.1*480)=48
    assert face.bbox[0] == 64
    assert face.bbox[1] == 48

    assert result.to_context_string() == "[Room: adult male ~28]"
