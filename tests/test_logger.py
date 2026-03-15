"""Tests for structured logging module."""
import logging

def test_get_logger_returns_child():
    from logger import get_logger
    log = get_logger("stt")
    assert log.name == "bender.stt"
    assert isinstance(log, logging.Logger)

def test_get_logger_same_instance():
    from logger import get_logger
    a = get_logger("audio")
    b = get_logger("audio")
    assert a is b

def test_root_logger_has_handlers():
    from logger import get_logger
    log = get_logger("test")
    root = logging.getLogger("bender")
    assert len(root.handlers) >= 1
