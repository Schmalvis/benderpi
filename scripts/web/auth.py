"""PIN-based authentication for BenderPi web UI."""
import os
from fastapi import Request, HTTPException

DEFAULT_PIN = "2904"


def get_pin() -> str:
    return os.environ.get("BENDER_WEB_PIN", DEFAULT_PIN)


def verify_pin(pin: str) -> bool:
    return pin == get_pin()


async def require_pin(request: Request):
    """FastAPI dependency — checks X-Bender-Pin header."""
    pin = request.headers.get("X-Bender-Pin", "")
    if not verify_pin(pin):
        raise HTTPException(status_code=401, detail="Invalid PIN")
