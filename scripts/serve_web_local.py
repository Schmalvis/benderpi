#!/usr/bin/env python3
"""Serve the built web UI on a dev box, so the pages can be looked at.

    BENDER_WEB_PIN=1234 venv/bin/python scripts/serve_web_local.py

Binds 127.0.0.1 by default, so that URL only works in a browser on the same
box. To reach it from a laptop or phone on the LAN:

    BENDER_WEB_PIN=1234 BENDER_WEB_HOST=0.0.0.0 \
        venv/bin/python scripts/serve_web_local.py

Why this exists: the Halloween mode UI shipped without anyone seeing it in a
browser. It compiled, the bundle held the right strings, and the tests passed.
Twenty minutes with a real browser then found an empty input box, a config value
that crashed the gate, and a 500 that had been masquerading as a 422 for months.
A page that has never been rendered has not been tested.

`scripts/web/app.py` imports `leds`, which imports `board`, `busio` and
`neopixel_spi`. Those ship with adafruit-blinka and only exist in a
`--system-site-packages` venv on the Pi, so the app cannot even be imported on a
dev clone. The stubs below are the same ones `tests/conftest.py` installs, for
the same reason, and the real modules win wherever they are present.

Serves the committed `web/dist/`, so run `cd web && npm run build` first if the
Svelte source has changed. Reads and writes the dev clone's own
`bender_config.json`: a save here is a real save, to this checkout.
"""
import os
import sys
import types
from unittest.mock import MagicMock

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE_DIR, "scripts"))
os.chdir(_BASE_DIR)


class _PermissiveModule(types.ModuleType):
    """Answers any attribute with a MagicMock.

    Enumerating pin names (board.SCK, board.MOSI, board.D10, ...) is a losing
    game, so don't: the same reasoning as tests/conftest.py.
    """

    def __getattr__(self, item):
        value = MagicMock(name=f"{self.__name__}.{item}")
        setattr(self, item, value)
        return value


def _stub(name: str) -> None:
    if name in sys.modules:
        return
    try:
        __import__(name)          # the real hardware lib wins on the Pi
    except Exception:
        sys.modules[name] = _PermissiveModule(name)


def _lan_ip() -> str:
    """The address a browser elsewhere on the LAN should use.

    Does not send anything: a UDP connect only picks the route.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 1))   # TEST-NET-1, never routed
        return s.getsockname()[0]
    except Exception:
        return socket.gethostname()
    finally:
        s.close()



# Routes that need hardware this box does not have. Each one fails on a dev
# clone anyway, but it fails in a way that looks like a bug in Bender: a puppet
# clip reports success and plays nothing, because pyaudio is a stub, the 88
# clips in speech/wav/ are gitignored and absent, and `service_guard` shells out
# to `systemctl stop bender-converse` for a unit that is not installed here.
# On 2026-10-14 that cost real debugging time: the owner clicked soundboard
# clips on this server and reasonably concluded Bender was broken. Better to
# refuse with a sentence than to pretend.
_HARDWARE_PREFIXES = (
    "/api/puppet",
    "/api/vision",
    "/api/camera",
    "/api/actions",
    "/api/config/volume",
    "/api/mic",
)


def _block_hardware_routes(app) -> None:
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def _refuse_hardware(request, call_next):
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in _HARDWARE_PREFIXES):
            return JSONResponse(
                status_code=503,
                content={"detail":
                         "This is the dev clone, which has no speaker, no "
                         "camera and no bender-converse service. Use the "
                         "device UI on port 8080 for anything that makes a "
                         "sound or touches hardware."},
            )
        return await call_next(request)


def main() -> int:
    for hw in ("board", "busio", "neopixel_spi", "adafruit_blinka", "cv2",
               "adafruit_vl53l0x", "digitalio", "picamera2",
               "usb", "usb.core", "usb.util"):
        _stub(hw)

    if "pyaudio" not in sys.modules:
        try:
            import pyaudio  # noqa: F401
        except ImportError:
            # Concrete, not permissive: audio.py does arithmetic on what the
            # API returns, and a MagicMock raises TypeError against an int.
            pa = types.ModuleType("pyaudio")
            pa.paInt16, pa.paContinue, pa.paComplete = 8, 0, 1
            pa.PyAudio = lambda *a, **k: types.SimpleNamespace()
            sys.modules["pyaudio"] = pa

    if not os.environ.get("BENDER_WEB_PIN"):
        print("BENDER_WEB_PIN is not set. The auth gate fails closed without it.",
              file=sys.stderr)
        return 2
    if not os.path.isdir(os.path.join(_BASE_DIR, "web", "dist")):
        print("web/dist is missing. Run `cd web && npm run build` first.",
              file=sys.stderr)
        return 2

    # Read the address BEFORE importing the app. `config.py` loads `.env` at
    # import time, the dev clone's `.env` carries the device's
    # BENDER_WEB_PORT=8080, and `os.environ.setdefault` then fills it in. Read
    # it afterwards and the 8099 default below never applies: the server comes
    # up on 8080 instead, which is what happened on 2026-10-14.
    port = int(os.environ.get("BENDER_WEB_PORT", "8099"))
    # Loopback by default, because this serves a PIN-gated admin UI that can
    # restart services and write config. Set BENDER_WEB_HOST=0.0.0.0 to reach it
    # from a browser on another machine; the dev box has no firewall of its own.
    host = os.environ.get("BENDER_WEB_HOST", "127.0.0.1")

    import uvicorn
    from web.app import app
    _block_hardware_routes(app)
    shown = "127.0.0.1" if host in ("127.0.0.1", "localhost") else _lan_ip()
    # flush=True: stdout is a pipe whenever this is backgrounded into a log
    # file, so the URL otherwise sits in the buffer until the process exits.
    print(f"http://{shown}:{port}/   (dev clone, not the device)", flush=True)
    print("Config and Logs work here. Puppet, Vision, Volume and Actions do"
          " not: no speaker, no camera, no bender-converse.", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
