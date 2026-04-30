"""Per-user proxy calls to api.deligo.mn.

The existing `deligo_integration` module logs in with a fixed service account
and caches a single token. The frontend now needs to log end-users in (driver or
shop) and act as them — those calls live here so we don't pollute the service
account cache.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DELIGO_API_URL = os.getenv("DELIGO_API_URL", "https://api.deligo.mn").rstrip("/")
_HTTP_TIMEOUT = 10.0


class DeligoApiError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _post(path: str, *, json: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{DELIGO_API_URL}{path}"
    try:
        r = httpx.post(url, json=json or {}, headers=headers, timeout=_HTTP_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("Deligo proxy request failed: %s", path, exc_info=True)
        raise DeligoApiError(f"Failed to reach deligo: {exc}") from exc

    if r.status_code == 401:
        raise DeligoApiError("Deligo token rejected", status_code=401)
    if r.status_code >= 400:
        try:
            body = r.json()
            message = body.get("message") or body.get("error") or r.text
        except Exception:
            message = r.text or f"HTTP {r.status_code}"
        raise DeligoApiError(message, status_code=r.status_code)
    if not r.text:
        return None
    try:
        return r.json()
    except ValueError:
        return r.text


def login(email: str, password: str) -> Dict[str, Any]:
    body = _post("/api/user/login", json={"email": email, "password": password})
    if not isinstance(body, dict):
        raise DeligoApiError("Unexpected login response from deligo")
    return body


def user_info(token: str) -> Dict[str, Any]:
    body = _post("/api/user/info", token=token)
    if not isinstance(body, dict):
        raise DeligoApiError("Unexpected user info response from deligo")
    if isinstance(body.get("data"), dict):
        return body["data"]
    if isinstance(body.get("user"), dict):
        return body["user"]
    return body


def _order_list(token: str, criteria_field: str, value: str, offset: int, page_size: int) -> List[Dict[str, Any]]:
    payload = {
        "criteria": {
            criteria_field: {
                "value": str(value),
                "operator": "=",
                "dataType": "integer",
            }
        },
        "paging": {"offset": offset, "pageSize": page_size},
    }
    body = _post("/api/sales/integration", json=payload, token=token)
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("data", "items"):
            value = body.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                inner = value.get("items") or value.get("data")
                if isinstance(inner, list):
                    return inner
    return []


def driver_orders(token: str, driver_id: str, offset: int = 0, page_size: int = 50) -> List[Dict[str, Any]]:
    return _order_list(token, "es.driver_id", driver_id, offset, page_size)


def change_status(token: str, sales_id: str, status_id: int) -> bool:
    params = f"?sales_id={sales_id}&status_id={status_id}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"{DELIGO_API_URL}/api/sales/changestatus{params}"
    try:
        r = httpx.post(
            url,
            json={"id": sales_id, "statusId": status_id},
            headers=headers,
            timeout=_HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise DeligoApiError(f"Failed to reach deligo: {exc}") from exc
    if r.status_code == 401:
        raise DeligoApiError("Deligo token rejected", status_code=401)
    if r.status_code >= 400:
        raise DeligoApiError(r.text or f"HTTP {r.status_code}", status_code=r.status_code)
    return True


def pick_driver_id(user: Dict[str, Any]) -> Optional[str]:
    for key in ("driver_id", "id", "user_id"):
        v = user.get(key)
        if v is not None:
            return str(v)
    return None
