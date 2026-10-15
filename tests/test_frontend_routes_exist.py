"""Every /api/ path the frontend calls must exist on the server.

Why this file exists
--------------------
On 2026-04-02, `c9743b2` deleted the passive-vision watcher thread from
`wake_converse.py` and `bfbd831` deleted the three `/api/vision/passive`
endpoints behind it. Neither commit touched the frontend. `web/src/lib/api.js`
kept `getVisionPassive`, `enableVisionPassive` and `disableVisionPassive`, and
`Config.svelte` kept six buttons plus a 60-second poll pointed at all three.

It shipped, it built, the suite passed, and nobody noticed for six months. The
device journal held **93** `GET /api/vision/passive HTTP/1.1 404 Not Found`
lines by 2026-10-15, one a minute whenever the Config page was open, and the
page showed a red error under a control set that could not work.

Nothing in the repo could catch that. The Python tests exercise the routes that
exist. `npm run build` does not know what the server serves. The two halves are
checked separately and the seam between them was unguarded.

This test is the guard. It reads the FastAPI app's own OpenAPI document, so it
cannot be fooled by `app.routes`, which does not enumerate reliably on FastAPI
0.139 / Starlette 1.3 — a route removed from `app.routes` still answers.

A failure here means one of two things, and both are real bugs:
  * the frontend calls something the backend no longer serves, or
  * a route was renamed and only one side was updated.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_API_JS = os.path.join(_REPO, "web", "src", "lib", "api.js")

# WebSocket routes are not in the OpenAPI document. `/ws/puppet/mic` is opened
# directly in Puppet.svelte, not through api.js, so nothing here needs it.


def _shape(path: str) -> str:
    """Collapse `${id}` (frontend) and `{id}` (OpenAPI) to one placeholder."""
    return re.sub(r"\$\{[^}]*\}|\{[^}]*\}", "{x}", path).rstrip("/")


def _frontend_calls():
    """(export name, path, method) for every /api/ call in api.js."""
    src = open(_API_JS, encoding="utf-8").read()
    calls = []
    for m in re.finditer(r"export const (\w+)[^\n]*?['\"`](/api/[^'\"`?]*)", src):
        line = src[m.start():src.find("\n", m.start())]
        method = re.search(r"method:\s*'(\w+)'", line)
        calls.append((m.group(1), m.group(2),
                      (method.group(1) if method else "GET").upper()))
    return calls


@pytest.fixture(scope="module")
def spec_paths():
    os.environ["BENDER_WEB_PIN"] = "testpin"
    import importlib
    import web.app
    importlib.reload(web.app)
    doc = web.app.app.openapi()
    return {_shape(p): {m.upper() for m in ops
                        if m in ("get", "post", "put", "delete", "patch")}
            for p, ops in doc["paths"].items()}


def test_api_js_is_readable_and_declares_calls():
    """A guard that finds nothing is worse than no guard."""
    calls = _frontend_calls()
    assert len(calls) >= 20, f"only parsed {len(calls)} calls — regex broken?"


def test_every_frontend_call_hits_a_real_route(spec_paths):
    dead = []
    for name, path, method in _frontend_calls():
        served = spec_paths.get(_shape(path))
        if served is None:
            dead.append(f"{name}: {method} {path} — no such path on the server")
        elif method not in served:
            dead.append(f"{name}: {method} {path} — server serves {sorted(served)}")
    assert not dead, (
        "web/src/lib/api.js calls routes the server does not serve:\n  "
        + "\n  ".join(dead)
        + "\n\nEither restore the route or delete the frontend call. A dead call "
          "ships as a red error in the UI and a 404 per poll in the journal."
    )
