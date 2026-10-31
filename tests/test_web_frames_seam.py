"""The Config page and the frames status route must agree.

`Config.svelte` reads fields off the `GET /api/frames/status` body. A field
the page reads but `turn_frames.status()` never returns renders as nothing, and
the page shows a calm line while the worker is dead. Each side's own tests
pass, so this reads the keys out of the page source and checks them against
the real `status()`.

The checkbox must also stay in the Maintenance section, which Halloween mode
keeps visible. Inside `{#if !halloweenOn}` it would vanish on the one night the
frames matter.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

_WEB_SRC = os.path.join(os.path.dirname(__file__), "..", "web", "src")


def _source(*parts):
    with open(os.path.join(_WEB_SRC, *parts), encoding="utf-8") as f:
        return f.read()


def _page():
    return _source("pages", "Config.svelte")


def test_the_page_names_its_status_object_framesStatus():
    assert re.search(r"\bframesStatus\.\w+", _page()), (
        "Config.svelte no longer reads framesStatus.<key>; this seam test "
        "reads nothing")


def test_every_status_field_the_page_reads_is_a_key_of_status():
    import turn_frames
    used = set(re.findall(r"\bframesStatus\.(\w+)", _page()))
    assert used, "the page reads no status field"
    missing = used - set(turn_frames.status())
    assert not missing, f"the page reads fields status() never returns: {sorted(missing)}"


def test_api_js_declares_the_status_call():
    src = _source("lib", "api.js")
    assert re.search(
        r"export const getFramesStatus = \(\) => request\('/api/frames/status'\);",
        src)


def test_the_status_call_is_one_the_routes_test_checks():
    sys.path.insert(0, os.path.dirname(__file__))
    import test_frontend_routes_exist as routes
    calls = {name: (path, method) for name, path, method in routes._frontend_calls()}
    assert calls.get("getFramesStatus") == ("/api/frames/status", "GET")


def _maintenance(src):
    start = src.index("<!-- Maintenance -->")
    end = src.index("<!-- Watchdog -->")
    return src[start:end]


def test_the_checkbox_sits_in_maintenance():
    src = _page()
    section = _maintenance(src)
    assert "bind:checked={$config.turn_frames_enabled}" in section
    assert "Keep a camera frame each time Bender replies" in section
    assert src.count("bind:checked={$config.turn_frames_enabled}") == 1


def test_the_checkbox_is_outside_every_halloween_block():
    """Walk the markup, tracking open `{#if}` blocks at the checkbox."""
    src = _page()
    pos = src.index("bind:checked={$config.turn_frames_enabled}")
    stack = []
    for m in re.finditer(r"\{#if ([^}]*)\}|\{/if\}", src[:pos]):
        if m.group(0) == "{/if}":
            stack.pop()
        else:
            stack.append(m.group(1))
    assert not any("halloweenOn" in cond for cond in stack), stack
