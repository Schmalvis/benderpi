"""Authentication endpoints: PIN login + short-lived stream tokens."""
from fastapi import APIRouter, Body, Depends

from web.auth import attempt_login, issue_stream_token, require_token

router = APIRouter()


@router.post("/api/auth/login")
async def login(body: dict = Body(...)):
    """Verify the PIN and return a signed session token.

    429 while rate-limited, 401 on wrong PIN, 503 if no PIN configured.
    """
    pin = str(body.get("pin", ""))
    token = attempt_login(pin)
    return {"token": token}


@router.get("/api/auth/stream-token", dependencies=[Depends(require_token)])
async def stream_token():
    """Mint a short-lived token for URL-embedded auth (camera stream, mic ws)."""
    return {"token": issue_stream_token()}
