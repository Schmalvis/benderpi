"""Unit tests for scripts/vision.py — thin orchestrator over camera.py + vlm.py.

All hardware interaction is mocked; these tests run offline.
"""
from __future__ import annotations

import importlib
import sys
import types
import unittest.mock as mock
from datetime import datetime

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vision_module(
    camera_mock=None,
    vlm_mock=None,
):
    """Import vision.py with camera and vlm mocked, returning a fresh module."""
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("vision",) or mod_name.startswith("vision."):
            del sys.modules[mod_name]

    # Build stub camera module
    cam_mod = types.ModuleType("camera")
    if camera_mock is None:
        camera_mock = _default_camera_mock()
    cam_mod.acquire_camera = camera_mock["acquire_camera"]
    cam_mod.release_camera = camera_mock["release_camera"]
    cam_mod.capture_frame = camera_mock["capture_frame"]
    sys.modules["camera"] = cam_mod

    # Build stub vlm module
    vlm_mod = types.ModuleType("vlm")
    if vlm_mock is None:
        vlm_mock = _default_vlm_mock()
    vlm_mod.describe_scene = vlm_mock["describe_scene"]
    sys.modules["vlm"] = vlm_mod

    import vision
    importlib.reload(vision)
    return vision, camera_mock, vlm_mock


def _default_camera_mock():
    import numpy as np
    frame = np.zeros((1080, 1920, 3), dtype="uint8")
    return {
        "acquire_camera": mock.MagicMock(return_value=mock.MagicMock()),
        "release_camera": mock.MagicMock(),
        "capture_frame": mock.MagicMock(return_value=frame),
    }


def _default_vlm_mock(description="A robot sits in a room."):
    return {
        "describe_scene": mock.MagicMock(return_value=description),
    }


# ---------------------------------------------------------------------------
# SceneDescription tests
# ---------------------------------------------------------------------------

def test_scene_description_is_empty_when_blank():
    vision, _, _ = _make_vision_module()
    sd = vision.SceneDescription(description="")
    assert sd.is_empty() is True


def test_scene_description_is_empty_whitespace_only():
    vision, _, _ = _make_vision_module()
    sd = vision.SceneDescription(description="   \n  ")
    assert sd.is_empty() is True


def test_scene_description_is_not_empty_when_has_text():
    vision, _, _ = _make_vision_module()
    sd = vision.SceneDescription(description="A cat sits on a mat.")
    assert sd.is_empty() is False


def test_to_context_string_returns_stripped_description():
    vision, _, _ = _make_vision_module()
    sd = vision.SceneDescription(description="  A cat.  ")
    assert sd.to_context_string() == "A cat."


def test_to_context_string_returns_empty_string_when_empty():
    vision, _, _ = _make_vision_module()
    sd = vision.SceneDescription(description="")
    assert sd.to_context_string() == ""


def test_to_context_string_returns_empty_string_when_whitespace_only():
    vision, _, _ = _make_vision_module()
    sd = vision.SceneDescription(description="   ")
    assert sd.to_context_string() == ""


# ---------------------------------------------------------------------------
# analyse_scene tests
# ---------------------------------------------------------------------------

def test_analyse_scene_returns_scene_description():
    vision, _, _ = _make_vision_module()
    result = vision.analyse_scene()
    assert isinstance(result, vision.SceneDescription)


def test_analyse_scene_uses_vlm_description():
    vision, _, vlm_mock = _make_vision_module()
    result = vision.analyse_scene()
    assert result.description == "A robot sits in a room."


def test_analyse_scene_sets_captured_at():
    vision, _, _ = _make_vision_module()
    result = vision.analyse_scene()
    assert result.captured_at is not None
    assert isinstance(result.captured_at, datetime)


def test_analyse_scene_acquires_camera():
    vision, cam_mock, _ = _make_vision_module()
    vision.analyse_scene()
    cam_mock["acquire_camera"].assert_called_once()


def test_analyse_scene_releases_camera():
    vision, cam_mock, _ = _make_vision_module()
    vision.analyse_scene()
    cam_mock["release_camera"].assert_called_once()


def test_analyse_scene_passes_frame_to_vlm():
    vision, cam_mock, vlm_mock = _make_vision_module()
    frame = cam_mock["capture_frame"].return_value
    vision.analyse_scene()
    from config import cfg
    vlm_mock["describe_scene"].assert_called_once_with(frame, prompt=cfg.vlm_prompt)


def test_analyse_scene_returns_empty_on_camera_exception():
    cam_mock = _default_camera_mock()
    cam_mock["capture_frame"].side_effect = RuntimeError("Camera exploded")
    vision, _, _ = _make_vision_module(camera_mock=cam_mock)
    result = vision.analyse_scene()
    assert isinstance(result, vision.SceneDescription)
    assert result.is_empty() is True


def test_analyse_scene_releases_camera_even_on_exception():
    cam_mock = _default_camera_mock()
    cam_mock["capture_frame"].side_effect = RuntimeError("Camera exploded")
    vision, cam_mock, _ = _make_vision_module(camera_mock=cam_mock)
    vision.analyse_scene()
    cam_mock["release_camera"].assert_called_once()


def test_analyse_scene_returns_empty_on_vlm_exception():
    vlm_mock = _default_vlm_mock()
    vlm_mock["describe_scene"].side_effect = RuntimeError("VLM timed out")
    vision, _, _ = _make_vision_module(vlm_mock=vlm_mock)
    result = vision.analyse_scene()
    assert result.is_empty() is True


def test_analyse_scene_releases_camera_even_on_vlm_exception():
    vlm_mock = _default_vlm_mock()
    vlm_mock["describe_scene"].side_effect = RuntimeError("VLM timed out")
    vision, cam_mock, _ = _make_vision_module(vlm_mock=vlm_mock)
    vision.analyse_scene()
    cam_mock["release_camera"].assert_called_once()


# ---------------------------------------------------------------------------
# acquire_camera / release_camera delegation tests
# ---------------------------------------------------------------------------

def test_acquire_camera_delegates_to_camera_module():
    vision, cam_mock, _ = _make_vision_module()
    result = vision.acquire_camera()
    cam_mock["acquire_camera"].assert_called_once()
    assert result is cam_mock["acquire_camera"].return_value


def test_release_camera_delegates_to_camera_module():
    vision, cam_mock, _ = _make_vision_module()
    vision.release_camera()
    cam_mock["release_camera"].assert_called_once()


def test_analyse_scene_returns_empty_on_acquire_error():
    cam_mock = _default_camera_mock()
    cam_mock["acquire_camera"].side_effect = RuntimeError("camera unavailable")
    vision, cam_mock, _ = _make_vision_module(camera_mock=cam_mock)
    result = vision.analyse_scene()
    assert result.is_empty()
    # release_camera still called in finally (safe — guarded against refcount 0)
    cam_mock["release_camera"].assert_called_once()
