"""Per-user proxy calls to api.deligo.mn.

The existing `deligo_integration` module logs in with a fixed service account
and caches a single token. The frontend now needs to log end-users in (driver or
shop) and act as them — those calls live here so we don't pollute the service
account cache.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DELIGO_API_URL = os.getenv("DELIGO_API_URL", "https://api.deligo.mn").rstrip("/")
_HTTP_TIMEOUT_SECONDS = float(os.getenv("DELIGO_HTTP_TIMEOUT", "20"))
_HTTP_TIMEOUT = httpx.Timeout(_HTTP_TIMEOUT_SECONDS, connect=5.0)
_MAX_STATUS_DESCRIPTION_PARAM_LEN = int(os.getenv("DELIGO_STATUS_DESC_MAX_LEN", "180000"))


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
    print(f"[Deligo] POST /api/sales/integration  {criteria_field}={value}  offset={offset} pageSize={page_size}")
    body = _post("/api/sales/integration", json=payload, token=token)
    if isinstance(body, list):
        print(f"[Deligo] Got {len(body)} orders (list)")
        return body
    if isinstance(body, dict):
        for key in ("data", "items"):
            chunk = body.get(key)
            if isinstance(chunk, list):
                print(f"[Deligo] Got {len(chunk)} orders (body.{key})")
                return chunk
            if isinstance(chunk, dict):
                inner = chunk.get("items") or chunk.get("data")
                if isinstance(inner, list):
                    print(f"[Deligo] Got {len(inner)} orders (body.{key}.inner)")
                    return inner
    print(f"[Deligo] No orders found, body keys: {list(body.keys()) if isinstance(body, dict) else type(body)}")
    return []


def driver_orders(token: str, driver_id: str, offset: int = 0, page_size: int = 50) -> List[Dict[str, Any]]:
    return _order_list(token, "es.driver_id", driver_id, offset, page_size)


def shop_orders(token: str, company_id: str, offset: int = 0, page_size: int = 50) -> List[Dict[str, Any]]:
    return _order_list(token, "es.company_id", company_id, offset, page_size)


def sales_detail(sales_id: str) -> Optional[Dict[str, Any]]:
    # sales/get is intentionally called without Authorization header.
    body = _post("/api/sales/get", json={"id": sales_id}, token=None)

    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, dict):
            return data
    return None


def get_status_description(token: str, sales_id: str, status_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Fetch the latest status description for a sales record."""
    payload: Dict[str, Any] = {"id": sales_id}
    if status_id is not None:
        payload["statusId"] = status_id
    body = _post("/api/sales/getStatusDescription", json=payload, token=token)
    if isinstance(body, dict):
        data = body.get("data")
        print(f"[Deligo] getStatusDescription for sales_id={sales_id} status_id={status_id} response data keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        if isinstance(data, dict):
            return data
    return None


def change_status(
    token: str,
    sales_id: str,
    status_id: int,
    status_description: Optional[str] = None,
    proof: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"{DELIGO_API_URL}/api/sales/changestatus"
    payload: Dict[str, Any] = {"id": sales_id, "statusId": status_id}
    if status_description:
        # Forward full description as-is (including image_base64) per driver workflow requirement.
        payload["statusDescription"] = status_description

    if proof:
        # check proof got image base 64
        if proof.get("image_base64") or proof.get("imageDataUrl"):
            payload["image"] = proof.get("image_base64") or proof.get("imageDataUrl")

    print(f"[Deligo] POST /api/sales/changestatus  sales_id={sales_id} status_id={status_id} proof={'yes' if proof else 'no'}")

    max_attempts = 3
    retryable_statuses = {500, 502, 503, 504}
    last_transport_exc: Optional[Exception] = None
    last_response: Optional[httpx.Response] = None

    for attempt in range(1, max_attempts + 1):
        try:
            r = httpx.post(
                url,
                json=payload,
                headers=headers,
                timeout=_HTTP_TIMEOUT,
            )
            last_response = r
            print(f"[Deligo] changestatus response: status={r.status_code} body={r.text[:500]}")
        except httpx.HTTPError as exc:
            last_transport_exc = exc
            logger.warning(
                "Deligo changestatus transport error (attempt %s/%s): sales_id=%s status_id=%s",
                attempt,
                max_attempts,
                sales_id,
                status_id,
                exc_info=True,
            )
            if attempt < max_attempts:
                time.sleep(0.35 * attempt)
                continue
            break

        if r.status_code == 401:
            raise DeligoApiError("Deligo token rejected", status_code=401)

        if r.status_code < 400:
            warning_message: Optional[str] = None
            try:
                body = r.json()
                if isinstance(body, dict):
                    status_raw = body.get("status")
                    message_raw = body.get("message")
                    warning_raw = body.get("warning")

                    if (
                        isinstance(status_raw, str)
                        and status_raw.strip().lower() == "warning"
                        and isinstance(message_raw, str)
                        and message_raw.strip()
                    ):
                        warning_message = message_raw.strip()
                    elif isinstance(message_raw, str) and message_raw.strip() and not warning_raw:
                        # Some Deligo responses send a warning-like message field without warning key.
                        warning_message = message_raw.strip()

                    if isinstance(warning_raw, str) and warning_raw.strip():
                        warning_message = warning_raw.strip()
                    else:
                        warnings_raw = body.get("warnings")
                        if isinstance(warnings_raw, list):
                            warnings = [str(item).strip() for item in warnings_raw if str(item).strip()]
                            if warnings:
                                warning_message = "; ".join(warnings)
                        elif isinstance(warnings_raw, str) and warnings_raw.strip():
                            warning_message = warnings_raw.strip()
            except ValueError:
                warning_message = None

            return {"ok": True, "warning": warning_message}

        if r.status_code in retryable_statuses and attempt < max_attempts:
            logger.warning(
                "Deligo changestatus retryable response (attempt %s/%s): status=%s sales_id=%s",
                attempt,
                max_attempts,
                r.status_code,
                sales_id,
            )
            time.sleep(0.35 * attempt)
            continue

        raise DeligoApiError(r.text or f"HTTP {r.status_code}", status_code=r.status_code)

    if last_transport_exc is not None:
        raise DeligoApiError(f"Failed to reach deligo after {max_attempts} attempts: {last_transport_exc}") from last_transport_exc

    if last_response is not None:
        raise DeligoApiError(
            last_response.text or f"HTTP {last_response.status_code}",
            status_code=last_response.status_code,
        )

    raise DeligoApiError("Failed to reach deligo")


def pick_driver_id(user: Dict[str, Any]) -> Optional[str]:
    for key in ("driver_id", "id", "user_id"):
        v = user.get(key)
        if v is not None:
            return str(v)
    return None


def pick_company_id(user: Dict[str, Any]) -> Optional[str]:
    for key in ("company_id", "store_id"):
        v = user.get(key)
        if v is not None:
            return str(v)
    return None
