"""Unit tests for scripts/vlm.py — YOLO + LLM pipeline.

All Hailo hardware interaction is mocked; tests run offline.
"""
from __future__ import annotations

import importlib
import sys
import threading
import types
import unittest.mock as mock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hailo_mocks():
    """Build stub modules for all hailo imports used by vlm.py."""
    # hailo_platform
    hp_mod = types.ModuleType("hailo_platform")
    mock_vdevice_instance = mock.MagicMock()
    mock_vdevice_cls = mock.MagicMock(return_value=mock_vdevice_instance)
    mock_params = mock.MagicMock()
    mock_vdevice_cls.create_params.return_value = mock_params
    hp_mod.VDevice = mock_vdevice_cls

    # hailo_platform.genai — LLM (not VLM)
    hp_genai_mod = types.ModuleType("hailo_platform.genai")
    mock_llm_instance = mock.MagicMock()
    mock_llm_instance.generate_all.return_value = "a person sitting on a red chair"
    mock_llm_cls = mock.MagicMock(return_value=mock_llm_instance)
    hp_genai_mod.LLM = mock_llm_cls

    # hailo_apps defines
    ha_core_mod = types.ModuleType("hailo_apps")
    ha_python_mod = types.ModuleType("hailo_apps.python")
    ha_core_core_mod = types.ModuleType("hailo_apps.python.core")
    ha_common_mod = types.ModuleType("hailo_apps.python.core.common")
    ha_defines_mod = types.ModuleType("hailo_apps.python.core.common.defines")
    ha_defines_mod.SHARED_VDEVICE_GROUP_ID = "SHARED"

    # HailoInfer mock
    ha_inference_mod = types.ModuleType("hailo_apps.python.core.common.hailo_inference")
    mock_yolo_instance = mock.MagicMock()
    # get_input_shape returns (H, W, C) — yolov8m typically 640x640x3
    mock_yolo_instance.get_input_shape.return_value = (640, 640, 3)
    # Real YOLO NMS output: single named output whose buffer is a list of 80 class lists.
    mock_yolo_instance.output_type = {"yolov8m/yolov8_nms_postprocess": "FLOAT32"}

    def _fake_run(input_batch, callback):
        # Simulate real Hailo structure: 1 binding, single named output = 80-class list.
        # Each element is None or an ndarray of detections [[y1,x1,y2,x2,score], ...].
        detections = [None] * 80
        detections[0] = np.array([[0.1, 0.1, 0.9, 0.9, 0.92]])   # person
        detections[56] = np.array([[0.2, 0.2, 0.8, 0.8, 0.87]])  # chair
        mock_binding = mock.MagicMock()
        mock_binding.output.return_value.get_buffer.return_value = detections
        threading.Thread(
            target=callback,
            args=(mock.MagicMock(),),
            kwargs={"bindings_list": [mock_binding]},
            daemon=True,
        ).start()

    mock_yolo_instance.run.side_effect = _fake_run
    mock_yolo_cls = mock.MagicMock(return_value=mock_yolo_instance)
    ha_inference_mod.HailoInfer = mock_yolo_cls

    mocks = {
        "hailo_platform": hp_mod,
        "hailo_platform.genai": hp_genai_mod,
        "hailo_apps": ha_core_mod,
        "hailo_apps.python": ha_python_mod,
        "hailo_apps.python.core": ha_core_core_mod,
        "hailo_apps.python.core.common": ha_common_mod,
        "hailo_apps.python.core.common.defines": ha_defines_mod,
        "hailo_apps.python.core.common.hailo_inference": ha_inference_mod,
    }
    return mocks, mock_yolo_cls, mock_yolo_instance, mock_llm_cls, mock_llm_instance


def _load_vlm_module(extra_sys_mods=None, coco_labels=None):
    """Import vlm.py with hailo dependencies mocked, returning a fresh module."""
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("vlm",) or mod_name.startswith("vlm."):
            del sys.modules[mod_name]

    mocks, mock_yolo_cls, mock_yolo_instance, mock_llm_cls, mock_llm_instance = _make_hailo_mocks()
    if extra_sys_mods:
        mocks.update(extra_sys_mods)
    sys.modules.update(mocks)

    import vlm
    importlib.reload(vlm)

    # Patch label loading to avoid filesystem dependency
    if coco_labels is None:
        coco_labels = [f"class_{i}" for i in range(80)]
    vlm._load_labels = mock.MagicMock(return_value=coco_labels)

    return vlm, mock_yolo_cls, mock_yolo_instance, mock_llm_cls, mock_llm_instance


# ---------------------------------------------------------------------------
# Tests: lazy initialisation
# ---------------------------------------------------------------------------

class TestLazyInit:
    def test_import_does_not_init(self):
        """HailoInfer and LLM must NOT be initialised at import time."""
        vlm_mod, mock_yolo_cls, _, mock_llm_cls, _ = _load_vlm_module()
        mock_yolo_cls.reset_mock()
        mock_llm_cls.reset_mock()
        mock_yolo_cls.assert_not_called()
        mock_llm_cls.assert_not_called()

    def test_describe_scene_triggers_init(self):
        """First call to describe_scene() should initialise HailoInfer and LLM."""
        vlm_mod, mock_yolo_cls, _, mock_llm_cls, _ = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        mock_yolo_cls.assert_called_once()
        mock_llm_cls.assert_called_once()

    def test_describe_scene_only_inits_once(self):
        """HailoInfer and LLM should be initialised exactly once across multiple calls."""
        vlm_mod, mock_yolo_cls, _, mock_llm_cls, _ = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        vlm_mod.describe_scene(frame)
        vlm_mod.describe_scene(frame)
        assert mock_yolo_cls.call_count == 1
        assert mock_llm_cls.call_count == 1


# ---------------------------------------------------------------------------
# Tests: YOLO then LLM pipeline
# ---------------------------------------------------------------------------

class TestPipeline:
    def test_describe_scene_calls_yolo_then_llm(self):
        """describe_scene() must call YOLO run() then LLM generate_all()."""
        vlm_mod, _, mock_yolo_instance, _, mock_llm_instance = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        mock_yolo_instance.run.assert_called_once()
        mock_llm_instance.generate_all.assert_called_once()

    def test_returns_llm_response(self):
        """describe_scene() must return the LLM's response string."""
        vlm_mod, _, _, _, mock_llm_instance = _load_vlm_module()
        mock_llm_instance.generate_all.return_value = "a person sitting on a red chair"
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = vlm_mod.describe_scene(frame)
        assert result == "a person sitting on a red chair"

    def test_strips_im_end_artifact(self):
        """Response should be stripped of <|im_end|> artifacts."""
        vlm_mod, _, _, _, mock_llm_instance = _load_vlm_module()
        mock_llm_instance.generate_all.return_value = "a red chair<|im_end|>extra"
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = vlm_mod.describe_scene(frame)
        assert result == "a red chair"

    def test_strips_type_suffix(self):
        """Response should be stripped of '. [{'type'' suffix."""
        vlm_mod, _, _, _, mock_llm_instance = _load_vlm_module()
        mock_llm_instance.generate_all.return_value = "a red chair. [{'type': 'text'}]"
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = vlm_mod.describe_scene(frame)
        assert result == "a red chair"

    def test_custom_prompt_passed_to_llm(self):
        """Custom prompt text should appear in the LLM generate_all call."""
        vlm_mod, _, _, _, mock_llm_instance = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame, prompt="What colour is the object?")
        call_kwargs = mock_llm_instance.generate_all.call_args
        # messages is the first positional or keyword arg
        all_args = str(call_kwargs)
        assert "What colour is the object?" in all_args

    def test_detected_objects_appear_in_llm_call(self):
        """Objects detected by YOLO should be passed to the LLM."""
        labels = ["person"] + [f"class_{i}" for i in range(1, 80)]
        vlm_mod, _, _, _, mock_llm_instance = _load_vlm_module(coco_labels=labels)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        call_args_str = str(mock_llm_instance.generate_all.call_args)
        assert "person" in call_args_str

    def test_clear_context_called_after_inference(self):
        """llm.clear_context() must be called after each inference."""
        vlm_mod, _, _, _, mock_llm_instance = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        mock_llm_instance.clear_context.assert_called_once()

    def test_clear_context_called_each_time(self):
        """clear_context() should be called once per describe_scene() call."""
        vlm_mod, _, _, _, mock_llm_instance = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        vlm_mod.describe_scene(frame)
        assert mock_llm_instance.clear_context.call_count == 2


# ---------------------------------------------------------------------------
# Tests: confidence threshold filtering
# ---------------------------------------------------------------------------

class TestConfidenceFiltering:
    def test_objects_below_threshold_filtered_out(self):
        """Detections below 0.5 confidence must not reach the LLM prompt."""
        vlm_mod, _, mock_yolo_instance, _, mock_llm_instance = _load_vlm_module(
            coco_labels=["person", "bicycle"] + [f"class_{i}" for i in range(2, 80)]
        )

        def _low_conf_run(input_batch, callback):
            detections = [None] * 80
            detections[0] = np.array([[0.1, 0.1, 0.9, 0.9, 0.3]])   # below threshold
            detections[1] = np.array([[0.2, 0.2, 0.8, 0.8, 0.49]])  # also below
            mock_binding = mock.MagicMock()
            mock_binding.output.return_value.get_buffer.return_value = detections
            import threading as _t
            _t.Thread(
                target=callback,
                args=(mock.MagicMock(),),
                kwargs={"bindings_list": [mock_binding]},
                daemon=True,
            ).start()

        mock_yolo_instance.run.side_effect = _low_conf_run

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        call_args_str = str(mock_llm_instance.generate_all.call_args)
        assert "person" not in call_args_str
        assert "bicycle" not in call_args_str
        assert "nothing detected" in call_args_str


# ---------------------------------------------------------------------------
# Tests: timeout
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_returns_empty_string_on_timeout(self):
        """describe_scene() must return '' when inference exceeds timeout."""
        vlm_mod, _, _, _, mock_llm_instance = _load_vlm_module()
        import time
        mock_llm_instance.generate_all.side_effect = lambda **kw: time.sleep(5)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = vlm_mod.describe_scene(frame, timeout=0.1)
        assert result == ""

    def test_default_timeout_from_env(self, monkeypatch):
        """BENDER_VLM_TIMEOUT env var must set the default timeout."""
        monkeypatch.setenv("BENDER_VLM_TIMEOUT", "7.5")
        vlm_mod, _, _, _, _ = _load_vlm_module()
        assert vlm_mod._default_timeout() == 7.5

    def test_thread_pool_used(self, monkeypatch):
        """Inference must be submitted to a thread pool executor."""
        vlm_mod, _, _, _, _ = _load_vlm_module()
        submitted = []
        real_executor = vlm_mod._executor
        original_submit = real_executor.submit

        def tracking_submit(fn, *args, **kwargs):
            submitted.append(fn)
            return original_submit(fn, *args, **kwargs)

        monkeypatch.setattr(vlm_mod._executor, "submit", tracking_submit)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        assert len(submitted) == 1

    def test_shared_vdevice_group_id_used(self):
        """VDevice must be created with SHARED_VDEVICE_GROUP_ID."""
        vlm_mod, _, _, _, _ = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        from hailo_platform import VDevice
        params = VDevice.create_params.return_value
        assert params.group_id == "SHARED"
