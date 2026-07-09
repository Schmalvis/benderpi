"""Shared test fixtures for BenderPi tests."""
import os
import sys
import types

# Ensure scripts/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# python-dotenv is present on BenderPi but absent on some dev/CI machines.
# wake_converse imports it at module top, so stub it if missing so the whole
# suite is runnable off-device. Real dotenv (when installed) is left untouched.
if "dotenv" not in sys.modules:
    try:
        import dotenv  # noqa: F401
    except ImportError:
        _dotenv = types.ModuleType("dotenv")
        _dotenv.dotenv_values = lambda *a, **k: {}
        _dotenv.load_dotenv = lambda *a, **k: False
        sys.modules["dotenv"] = _dotenv
