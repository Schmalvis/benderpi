"""BenderPi Web UI — FastAPI application."""
import os
from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles

from web.auth import require_pin

_WEB_DIR = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIR = os.path.join(_WEB_DIR, "static")
_ASSETS_DIR = os.path.join(_WEB_DIR, "assets")

app = FastAPI(title="BenderPi", docs_url=None, redoc_url=None)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/actions/service-status", dependencies=[Depends(require_pin)])
async def service_status():
    return {"running": True, "uptime": "unknown"}


# Static files (must be last — catches all unmatched routes)
if os.path.isdir(_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
