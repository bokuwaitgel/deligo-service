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
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

DELIGO_API_URL = os.getenv("DELIGO_API_URL", "https://api.deligo.mn").rstrip("/")
_HTTP_TIMEOUT_SECONDS = float(os.getenv("DELIGO_HTTP_TIMEOUT", "20"))
_HTTP_TIMEOUT = httpx.Timeout(_HTTP_TIMEOUT_SECONDS, connect=5.0)
_MAX_STATUS_DESCRIPTION_PARAM_LEN = int(os.getenv("DELIGO_STATUS_DESC_MAX_LEN", "180000"))

# Shared TTL cache for /api/sales/get — keyed by sales_id.
# Same TTL env var as deligo_integration so both modules stay consistent.
_PROXY_DETAIL_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PROXY_DETAIL_CACHE_TTL = int(os.getenv("DELIGO_DETAIL_CACHE_TTL", "300"))  # 0 = disabled
_PROXY_DETAIL_CACHE_MAX = 2000

# Short-lived cache for /api/sales/integration list responses.
# Key: (criteria_field, value, created_date, offset, page_size)
# 40 drivers × 960 refreshes/day → with 60s TTL this cuts to ~40 × 480 = 19,200 actual calls/day.
_LIST_CACHE: Dict[Tuple[str, str, str, int, int], Tuple[float, List[Dict[str, Any]]]] = {}
_LIST_CACHE_TTL = int(os.getenv("DELIGO_LIST_CACHE_TTL", "60"))  # seconds; 0 = disabled


def _get_list_cache(key: Tuple[str, str, str, int, int]) -> Optional[List[Dict[str, Any]]]:
    if not _LIST_CACHE_TTL:
        return None
    entry = _LIST_CACHE.get(key)
    if entry and time.time() < entry[0]:
        return entry[1]
    return None


def _set_list_cache(key: Tuple[str, str, str, int, int], data: List[Dict[str, Any]]) -> None:
    if not _LIST_CACHE_TTL:
        return
    if len(_LIST_CACHE) >= 500:
        now = time.time()
        expired = [k for k, (exp, _) in _LIST_CACHE.items() if exp < now]
        for k in expired or list(_LIST_CACHE)[:100]:
            _LIST_CACHE.pop(k, None)
    _LIST_CACHE[key] = (time.time() + _LIST_CACHE_TTL, data)


def _get_proxy_cached_detail(sales_id: str) -> Optional[Dict[str, Any]]:
    if not _PROXY_DETAIL_CACHE_TTL:
        return None
    entry = _PROXY_DETAIL_CACHE.get(sales_id)
    if entry and time.time() < entry[0]:
        return entry[1]
    return None


def _set_proxy_cached_detail(sales_id: str, detail: Dict[str, Any]) -> None:
    if not _PROXY_DETAIL_CACHE_TTL:
        return
    if len(_PROXY_DETAIL_CACHE) >= _PROXY_DETAIL_CACHE_MAX:
        now = time.time()
        expired = [k for k, (exp, _) in _PROXY_DETAIL_CACHE.items() if exp < now]
        for k in expired or list(_PROXY_DETAIL_CACHE)[:200]:
            _PROXY_DETAIL_CACHE.pop(k, None)
    _PROXY_DETAIL_CACHE[sales_id] = (time.time() + _PROXY_DETAIL_CACHE_TTL, detail)


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


def _order_list(
    token: str,
    criteria_field: str,
    value: str,
    offset: int,
    page_size: int,
    created_date: Optional[str] = None,
    default_today: bool = True,
) -> List[Dict[str, Any]]:
    criteria: Dict[str, Any] = {
        criteria_field: {
            "value": str(value),
            "operator": "=",
            "dataType": "integer",
        }
    }
    # Default the date filter to today unless the caller opts out (default_today=
    # False). The filter is on t6.created_date, which is NULL for orders that have
    # no delivery/status row yet (e.g. brand-new unassigned orders), so applying it
    # silently drops those orders. The shop list wants those too, so it opts out.
    if not created_date and default_today:
        created_date = datetime.now(ZoneInfo("Asia/Ulaanbaatar")).strftime("%Y-%m-%d")
    if created_date:
        print(f"[Deligo] Adding created_date filter to criteria: {created_date}")
        # Deligo expects this exact key — TO_CHAR on the SQL side normalizes the
        # timestamp column to a YYYY-MM-DD string for equality comparison.
        # Skip the filter entirely when no date is provided; Deligo rejects
        # criteria entries with a null value ("must contain" error).
        criteria["TO_CHAR(t6.created_date, 'YYYY-MM-DD')"] = {
            "value": created_date,
            "operator": "=",
            }
    payload = {
        "criteria": criteria,
        "paging": {"offset": offset, "pageSize": page_size},
    }
    date_suffix = f" created_date={created_date}" if created_date else ""
    cache_key = (criteria_field, str(value), created_date or "", offset, page_size)
    cached_list = _get_list_cache(cache_key)
    if cached_list is not None:
        print(f"[Deligo] LIST CACHE HIT  {criteria_field}={value}{date_suffix}  offset={offset} pageSize={page_size}")
        return cached_list
    print(f"[Deligo] POST /api/sales/integration  {criteria_field}={value}{date_suffix}  offset={offset} pageSize={page_size}")
    body = _post("/api/sales/integration", json=payload, token=token)
    if isinstance(body, list):
        print(f"[Deligo] Got {len(body)} orders (list)")
        _set_list_cache(cache_key, body)
        return body
    if isinstance(body, dict):
        for key in ("data", "items"):
            chunk = body.get(key)
            if isinstance(chunk, list):
                print(f"[Deligo] Got {len(chunk)} orders (body.{key})")
                _set_list_cache(cache_key, chunk)
                return chunk
            if isinstance(chunk, dict):
                inner = chunk.get("items") or chunk.get("data")
                if isinstance(inner, list):
                    print(f"[Deligo] Got {len(inner)} orders (body.{key}.inner)")
                    _set_list_cache(cache_key, inner)
                    return inner
    print(f"[Deligo] No orders found, body keys: {list(body.keys()) if isinstance(body, dict) else type(body)}")
    return []


def driver_orders(
    token: str,
    driver_id: str,
    offset: int = 0,
    page_size: int = 50,
    created_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return _order_list(token, "es.driver_id", driver_id, offset, page_size, created_date=created_date)


def shop_orders(
    token: str,
    company_id: str,
    offset: int = 0,
    page_size: int = 50,
    created_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # Do NOT force today's date on the shop list. The filter is on t6.created_date
    # (the sales/registration day), so defaulting to today silently hides every
    # order registered on a previous day — e.g. orders created late on 2026-06-12
    # vanish the moment the clock rolls over to 2026-06-13. The shop dashboard
    # wants the company's recent orders regardless of registration day (paging
    # still caps the result). A caller-supplied created_date (date search) is
    # honored when present.
    return _order_list(
        token, "es.company_id", company_id, offset, page_size,
        created_date=created_date, default_today=False,
    )


def sales_detail(sales_id: str) -> Optional[Dict[str, Any]]:
    # sales/get is intentionally called without Authorization header.
    cached = _get_proxy_cached_detail(sales_id)
    if cached is not None:
        return cached
    body = _post("/api/sales/get", json={"id": sales_id}, token=None)
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, dict):
            _set_proxy_cached_detail(sales_id, data)
            return data
    return None


def _normalize_status_description(data: Dict[str, Any]) -> Dict[str, Any]:
    # Deligo has returned multiple key shapes over time; normalize to stable keys.
    description = (
        data.get("description")
        or data.get("statusDescription")
        or data.get("status_description")
        or data.get("statusDescriptionText")
        or data.get("note")
        or data.get("comment")
        or data.get("reason")
    )
    file_path = (
        data.get("file_path")
        or data.get("filePath")
        or data.get("imageUrl")
        or data.get("image")
        or data.get("photo")
    )
    return {
        "description": str(description).strip() if description is not None else None,
        "file_path": str(file_path).strip() if file_path is not None else None,
    }


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
            return _normalize_status_description(data)
    return None


def change_status(
    token: str,
    sales_id: str,
    status_id: int,
    status_description: Optional[str] = None,
    proof: Optional[Dict[str, Any]] = None,
    status_publish_date: Optional[str] = None,
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

    if status_publish_date:
        payload["statusPublishDate"] = status_publish_date

    print(f"[Deligo] POST /api/sales/changestatus  sales_id={sales_id} status_id={status_id} proof={'yes' if proof else 'no'} status_publish_date={status_publish_date}")

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
