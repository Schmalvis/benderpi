"""BenderPi Web UI — FastAPI application."""
import os
import sys

_WEB_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_WEB_DIR)
_BASE_DIR = os.path.dirname(_SCRIPTS_DIR)
sys.path.insert(0, _SCRIPTS_DIR)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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

app = FastAPI(title="BenderPi", docs_url=None, redoc_url=None)

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
