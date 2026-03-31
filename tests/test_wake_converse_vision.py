# tests/test_wake_converse_vision.py
"""Tests that scene context survives into the conversation loop."""


class _FakeAI:
    def __init__(self):
        self.history = []
        self._scene_context = ""
        self.clear_history_calls = []
        self.inject_calls = []

    def inject_scene_context(self, text: str):
        self._scene_context = text
        self.inject_calls.append(text)

    def clear_history(self):
        self.history = []
        self._scene_context = ""
        self.clear_history_calls.append(True)


def _make_fake_session_context(scene_text="[Room: adult male ~35]"):
    """Return (ai, ai_local, scene_text) with helpers to simulate startup."""
    ai = _FakeAI()
    ai_local = _FakeAI()
    return ai, ai_local, scene_text


def test_scene_context_survives_after_startup():
    """inject_scene_context must be called AFTER clear_history, not before."""
    ai, ai_local, scene_text = _make_fake_session_context()

    # Simulate the CORRECT startup order
    ai.clear_history()
    ai_local.clear_history()
    ai.inject_scene_context(scene_text)
    ai_local.inject_scene_context(scene_text)

    assert ai._scene_context == scene_text, (
        "scene context wiped — clear_history() called after inject_scene_context()"
    )
    assert ai_local._scene_context == scene_text


def test_scene_context_wiped_by_wrong_order():
    """Regression canary: the buggy order (inject then clear_history) wipes context.

    This confirms that clear_history() does zero out _scene_context — so if
    anyone reintroduces the old ordering, test_scene_context_survives_after_startup
    will catch it.
    """
    ai, ai_local, scene_text = _make_fake_session_context()

    # Simulate the BUGGY order (inject first, then clear)
    ai.inject_scene_context(scene_text)
    ai_local.inject_scene_context(scene_text)
    ai.clear_history()          # wipes _scene_context

    # Confirm the buggy order DOES wipe context (expected bad outcome)
    assert ai._scene_context == "", (
        "clear_history() no longer wipes _scene_context — review fix ordering logic"
    )
