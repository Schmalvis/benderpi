"""BenderPi Web UI — FastAPI application."""
import os
import sys

_WEB_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_WEB_DIR)
_BASE_DIR = os.path.dirname(_SCRIPTS_DIR)
sys.path.insert(0, _SCRIPTS_DIR)

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from config import cfg
from metrics import metrics
from web.auth import require_configured_pin
from web.routes.auth import router as auth_router
from web.routes.health import router as health_router
from web.routes.actions import router as actions_router
from web.routes.config import router as config_router
from web.routes.puppet import router as puppet_router
from web.routes.logs import router as logs_router
from web.routes.timers import router as timers_router
from web.routes.status import router as status_router
from web.routes.remote import router as remote_router
from web.routes.vision import router as vision_router

_DIST_DIR = os.path.join(_BASE_DIR, "web", "dist")

# Fail closed: refuse to start unless a real PIN is configured. On BenderPi the
# 5-minute auto-pull restarts this service, so BENDER_WEB_PIN must be set in
# /home/pi/bender/.env *before* the deploy lands or bender-web will crash-loop.
require_configured_pin()

# Loud-not-silent secrets check (same cfg singleton bender-converse uses,
# reported here too since the web UI is its own process/entrypoint).
for _secret in cfg.validate():
    metrics.count("secrets_missing", secret=_secret)

app = FastAPI(title="BenderPi", docs_url=None, redoc_url=None)

# Security headers. CSP is deliberately strict; the committed Vite build inlines
# some styles, so style-src allows 'unsafe-inline'. No inline scripts are used.
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' ws: wss:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


app.include_router(auth_router)
app.include_router(health_router)
app.include_router(actions_router)
app.include_router(config_router)
app.include_router(puppet_router)
app.include_router(logs_router)
app.include_router(timers_router)
app.include_router(status_router)
app.include_router(remote_router)
app.include_router(vision_router)

if os.path.isdir(_DIST_DIR):
    app.mount("/", StaticFiles(directory=_DIST_DIR, html=True), name="static")
