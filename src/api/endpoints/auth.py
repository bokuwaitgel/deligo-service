"""Auth proxy endpoints — log drivers into the deligo platform via our backend
so the frontend never talks to api.deligo.mn directly with raw credentials.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth_utils import get_bearer_token
from src.services.deligo_user_proxy import (
    DeligoApiError,
    change_status,
    driver_orders,
    login as deligo_login,
    pick_driver_id,
    user_info,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    scope_id: str
    user: Dict[str, Any]


class OrderListRequest(BaseModel):
    offset: int = 0
    page_size: int = Field(default=50, ge=1, le=200)


class ChangeStatusRequest(BaseModel):
    sales_id: str
    status_id: int


def _require_token(token: str) -> str:
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return token


def _handle_deligo_error(exc: DeligoApiError) -> HTTPException:
    if exc.status_code == 401:
        return HTTPException(status_code=401, detail="Deligo authentication failed")
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/login", response_model=LoginResponse)
def login_endpoint(payload: LoginRequest):
    try:
        body = deligo_login(payload.email.strip(), payload.password)
    except DeligoApiError as exc:
        raise _handle_deligo_error(exc) from exc

    token = body.get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail="Deligo did not return an access_token")

    try:
        info = user_info(token)
    except DeligoApiError as exc:
        raise _handle_deligo_error(exc) from exc

    scope_id = pick_driver_id(info)
    if not scope_id:
        raise HTTPException(status_code=403, detail="Хэрэглэгчид жолоочийн ID олдсонгүй")

    return LoginResponse(token=token, scope_id=scope_id, user=info)


@router.post("/me")
def me_endpoint(token: str = Depends(get_bearer_token)):
    _require_token(token)
    try:
        info = user_info(token)
    except DeligoApiError as exc:
        raise _handle_deligo_error(exc) from exc
    return {"status": "ok", "user": info}


@router.post("/orders/driver")
def driver_orders_endpoint(
    payload: OrderListRequest,
    token: str = Depends(get_bearer_token),
):
    _require_token(token)
    try:
        info = user_info(token)
        driver_id = pick_driver_id(info)
        if not driver_id:
            raise HTTPException(status_code=403, detail="Жолоочийн ID олдсонгүй")
        items: List[Dict[str, Any]] = driver_orders(
            token, driver_id, offset=payload.offset, page_size=payload.page_size
        )
    except DeligoApiError as exc:
        raise _handle_deligo_error(exc) from exc
    return {"status": "ok", "data": items, "scope_id": driver_id}


@router.post("/orders/changestatus")
def change_status_endpoint(
    payload: ChangeStatusRequest,
    token: str = Depends(get_bearer_token),
):
    _require_token(token)
    try:
        change_status(token, payload.sales_id, payload.status_id)
    except DeligoApiError as exc:
        raise _handle_deligo_error(exc) from exc
    return {"status": "ok"}
