"""Unit tests for scripts/vlm.py — Hailo VLM wrapper.

All Hailo hardware interaction is mocked; tests run offline.
"""
from __future__ import annotations

import importlib
import sys
import types
import unittest.mock as mock
from concurrent.futures import Future

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

    hp_genai_mod = types.ModuleType("hailo_platform.genai")
    mock_vlm_instance = mock.MagicMock()
    mock_vlm_instance.generate_all.return_value = "a red chair"
    mock_vlm_cls = mock.MagicMock(return_value=mock_vlm_instance)
    hp_genai_mod.VLM = mock_vlm_cls

    # hailo_apps defines
    ha_core_mod = types.ModuleType("hailo_apps")
    ha_python_mod = types.ModuleType("hailo_apps.python")
    ha_core_core_mod = types.ModuleType("hailo_apps.python.core")
    ha_common_mod = types.ModuleType("hailo_apps.python.core.common")
    ha_defines_mod = types.ModuleType("hailo_apps.python.core.common.defines")
    ha_defines_mod.SHARED_VDEVICE_GROUP_ID = "SHARED"
    ha_defines_mod.VLM_CHAT_APP = "vlm_chat"
    ha_defines_mod.HAILO10H_ARCH = "hailo10h"
    ha_core_util_mod = types.ModuleType("hailo_apps.python.core.common.core")
    mock_hef_path = mock.MagicMock()
    mock_hef_path.__str__ = mock.MagicMock(return_value="/fake/path/vlm.hef")
    ha_core_util_mod.resolve_hef_path = mock.MagicMock(return_value=mock_hef_path)

    mocks = {
        "hailo_platform": hp_mod,
        "hailo_platform.genai": hp_genai_mod,
        "hailo_apps": ha_core_mod,
        "hailo_apps.python": ha_python_mod,
        "hailo_apps.python.core": ha_core_core_mod,
        "hailo_apps.python.core.common": ha_common_mod,
        "hailo_apps.python.core.common.defines": ha_defines_mod,
        "hailo_apps.python.core.common.core": ha_core_util_mod,
    }
    return mocks, mock_vdevice_cls, mock_vlm_cls, mock_vlm_instance


def _load_vlm_module(extra_sys_mods=None):
    """Import vlm.py with hailo dependencies mocked, returning a fresh module."""
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("vlm",) or mod_name.startswith("vlm."):
            del sys.modules[mod_name]

    mocks, mock_vdevice_cls, mock_vlm_cls, mock_vlm_instance = _make_hailo_mocks()
    if extra_sys_mods:
        mocks.update(extra_sys_mods)
    sys.modules.update(mocks)

    import vlm
    importlib.reload(vlm)
    return vlm, mock_vdevice_cls, mock_vlm_cls, mock_vlm_instance


# ---------------------------------------------------------------------------
# Tests: lazy initialisation
# ---------------------------------------------------------------------------

class TestLazyInit:
    def test_import_does_not_init_vlm(self):
        """VLM must NOT be initialised at import time."""
        vlm_mod, mock_vdevice_cls, mock_vlm_cls, _ = _load_vlm_module()
        mock_vdevice_cls.reset_mock()
        mock_vlm_cls.reset_mock()
        # After import/reload, constructors should not have been called yet
        mock_vdevice_cls.assert_not_called()
        mock_vlm_cls.assert_not_called()

    def test_describe_scene_triggers_init(self):
        """First call to describe_scene() should initialise VDevice and VLM."""
        vlm_mod, mock_vdevice_cls, mock_vlm_cls, _ = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        mock_vdevice_cls.assert_called_once()
        mock_vlm_cls.assert_called_once()

    def test_describe_scene_only_inits_once(self):
        """VLM should be initialised exactly once across multiple calls."""
        vlm_mod, mock_vdevice_cls, mock_vlm_cls, _ = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        vlm_mod.describe_scene(frame)
        vlm_mod.describe_scene(frame)
        assert mock_vlm_cls.call_count == 1


# ---------------------------------------------------------------------------
# Tests: return value
# ---------------------------------------------------------------------------

class TestReturnValue:
    def test_returns_string_on_success(self):
        """describe_scene() must return a str when inference succeeds."""
        vlm_mod, _, _, mock_vlm_instance = _load_vlm_module()
        mock_vlm_instance.generate_all.return_value = "a red chair"
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = vlm_mod.describe_scene(frame)
        assert isinstance(result, str)
        assert result == "a red chair"

    def test_strips_model_artifact(self):
        """Response should be stripped of <|im_end|> artifacts."""
        vlm_mod, _, _, mock_vlm_instance = _load_vlm_module()
        mock_vlm_instance.generate_all.return_value = "a red chair<|im_end|>extra"
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = vlm_mod.describe_scene(frame)
        assert result == "a red chair"

    def test_custom_prompt_passed_through(self):
        """Custom prompt text should appear in the generate_all call."""
        vlm_mod, _, _, mock_vlm_instance = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame, prompt="What colour is the object?")
        call_kwargs = mock_vlm_instance.generate_all.call_args
        prompt_arg = call_kwargs[1].get("prompt") or call_kwargs[0][0]
        # The user prompt string should appear somewhere in the prompt structure
        prompt_str = str(prompt_arg)
        assert "What colour is the object?" in prompt_str


# ---------------------------------------------------------------------------
# Tests: frame preprocessing
# ---------------------------------------------------------------------------

class TestFramePreprocessing:
    def test_frame_resized_to_336x336(self):
        """Frame must be resized to 336×336 before VLM inference."""
        vlm_mod, _, _, mock_vlm_instance = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        call_kwargs = mock_vlm_instance.generate_all.call_args
        frames_arg = call_kwargs[1].get("frames") or call_kwargs[0][1]
        assert frames_arg[0].shape == (336, 336, 3)

    def test_frame_is_uint8(self):
        """Frame passed to VLM must be uint8."""
        vlm_mod, _, _, mock_vlm_instance = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.float32)
        vlm_mod.describe_scene(frame)
        call_kwargs = mock_vlm_instance.generate_all.call_args
        frames_arg = call_kwargs[1].get("frames") or call_kwargs[0][1]
        assert frames_arg[0].dtype == np.uint8


# ---------------------------------------------------------------------------
# Tests: clear_context
# ---------------------------------------------------------------------------

class TestClearContext:
    def test_clear_context_called_after_inference(self):
        """vlm.clear_context() must be called after each inference."""
        vlm_mod, _, _, mock_vlm_instance = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        mock_vlm_instance.clear_context.assert_called_once()

    def test_clear_context_called_each_time(self):
        """clear_context() should be called once per describe_scene() call."""
        vlm_mod, _, _, mock_vlm_instance = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        vlm_mod.describe_scene(frame)
        assert mock_vlm_instance.clear_context.call_count == 2


# ---------------------------------------------------------------------------
# Tests: timeout
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_returns_empty_string_on_timeout(self, monkeypatch):
        """describe_scene() must return '' when inference exceeds timeout."""
        vlm_mod, _, _, mock_vlm_instance = _load_vlm_module()

        import time
        mock_vlm_instance.generate_all.side_effect = lambda **kw: time.sleep(5)

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = vlm_mod.describe_scene(frame, timeout=0.1)
        assert result == ""

    def test_thread_pool_used(self, monkeypatch):
        """Inference must be submitted to a thread pool executor."""
        vlm_mod, _, _, mock_vlm_instance = _load_vlm_module()

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

    def test_default_timeout_from_env(self, monkeypatch):
        """BENDER_VLM_TIMEOUT env var must set the default timeout."""
        monkeypatch.setenv("BENDER_VLM_TIMEOUT", "7.5")
        vlm_mod, _, _, _ = _load_vlm_module()
        assert vlm_mod._default_timeout() == 7.5

    def test_shared_vdevice_group_id_used(self):
        """VDevice must be created with SHARED_VDEVICE_GROUP_ID."""
        vlm_mod, mock_vdevice_cls, _, _ = _load_vlm_module()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        vlm_mod.describe_scene(frame)
        params = mock_vdevice_cls.create_params.return_value
        assert params.group_id == "SHARED"


# ---------------------------------------------------------------------------
# Tests: clear_context on exception path
# ---------------------------------------------------------------------------

class TestClearContextOnException:
    def test_clear_context_called_even_when_generate_all_raises(self):
        """clear_context() must be called even if generate_all() raises."""
        vlm_mod, _, _, mock_vlm_instance = _load_vlm_module()
        mock_vlm_instance.generate_all.side_effect = RuntimeError("inference exploded")

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        with pytest.raises(Exception):
            # Drive _run_inference directly so the exception isn't swallowed
            # by describe_scene's outer except block.
            vlm_mod._run_inference(frame, "describe this")

        mock_vlm_instance.clear_context.assert_called_once()
