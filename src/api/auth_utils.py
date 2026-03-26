from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException


def require_api_key(x_api_key: str = Header(..., description="API key")) -> str:
    """FastAPI dependency that validates the X-API-Key header."""
    expected = os.getenv("API_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="API_KEY not configured on server")
    if not hmac.compare_digest(x_api_key.strip(), expected):
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return x_api_key


def get_bearer_token(authorization: str = Header(default="", include_in_schema=False)) -> str:
    """FastAPI dependency that extracts the Bearer token from the Authorization header."""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""
