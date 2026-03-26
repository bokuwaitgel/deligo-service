from __future__ import annotations

import json

from fastapi import APIRouter, Depends
router = APIRouter(prefix="/api/health", tags=["health"])
@router.get("/")
def health_check():
    """Health check endpoint to verify API is running."""
    return {"status": "ok"}