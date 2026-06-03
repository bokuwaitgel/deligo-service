"""Deligo external API client (api.deligo.mn).

Uses frontend-provided driver token when available, otherwise calls unauthenticated
for endpoints that allow it.

The two calls we actually need:

- POST /api/sales/integration  — list sales for a driver (by driver_id)
- POST /api/sales/get          — fetch full detail for one sales_number

Driver-related fields come from this service at read time; we no longer
store driver_id/driver_name locally.

Configure via env:
    DELIGO_API_URL       (default https://api.deligo.mn)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

DELIGO_API_URL = os.getenv("DELIGO_API_URL", "https://api.deligo.mn").rstrip("/")
DELIGO_API_EMAIL = os.getenv("DELIGO_API_EMAIL", "")
DELIGO_API_PASSWORD = os.getenv("DELIGO_API_PASSWORD", "")
_HTTP_TIMEOUT = 10.0

# wfm_status_id -> machine code / human label, from the status spreadsheet.
STATUS_CODE_MAP: Dict[int, str] = {
    1: "salesNew",
    3: "salesDone",
    5: "salesDelivery",
    8: "salesDriverDone",
    12: "deliveryCancel",
    13: "deliveryTomorrow",
    14: "deliveryNophone",
    15: "deliveryNoCall",
    16: "deliveryGotoAddress",
    17: "deliveryNextBuy",
    23: "exchanged",
}

STATUS_LABEL_MAP: Dict[int, str] = {
    1: "Шинэ захиалга",
    3: "Хүргэсэн",
    5: "Хуваарилсан",
    8: "Жолооч хүлээн авсан",
    12: "Авахаа больсон",
    13: "Маргааш авна",
    14: "Утсаа аваагүй",
    15: "Дугаар холбогдохгүй",
    16: "Хаягаар очсон",
    17: "Дараа авна",
    23: "Сольж авсан",
}

# Statuses that represent a terminal / closed state.
# 3=Хүргэсэн (delivered), 12=Авахаа больсон (cancelled), 23=Сольж авсан (exchanged)
CLOSED_WFM_STATUS_IDS = {3, 12, 23}
_CLOSED_STATUS_IDS = CLOSED_WFM_STATUS_IDS  # kept for in-module backwards compat

# Statuses that represent an open / in-progress order for a driver.
# 1=Шинэ, 5=Хуваарилсан, 8=Жолооч хүлээн авсан
ACTIVE_WFM_STATUS_IDS = {1, 5, 8}
ACTIVE_STATUS_CODES = {STATUS_CODE_MAP[i] for i in ACTIVE_WFM_STATUS_IDS}

# Statuses for orders that are still on the driver's delivery queue and should be
# ranked by the driver's confirmed sort_order. 5=Хуваарилсан, 8=Жолооч хүлээн авсан.
# Any other status (delivered, deferred, etc.) is pushed to the end of the list.
OPEN_WFM_STATUS_IDS = {5, 8}
OPEN_STATUS_CODES = {STATUS_CODE_MAP[i] for i in OPEN_WFM_STATUS_IDS}

# ---------------------------------------------------------------------------
# In-process TTL cache for /api/sales/get results.
# Avoids N round-trips when the same order is fetched multiple times within
# a short window (e.g. driver list refresh hitting 20-30 orders each time).
# ---------------------------------------------------------------------------
_DETAIL_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_DETAIL_CACHE_TTL = int(os.getenv("DELIGO_DETAIL_CACHE_TTL", "300"))  # seconds; 0 = disabled
_DETAIL_CACHE_MAX = 2000


def _get_cached_detail(sales_id: str) -> Optional[Dict[str, Any]]:
    if not _DETAIL_CACHE_TTL:
        return None
    entry = _DETAIL_CACHE.get(sales_id)
    if entry and time.time() < entry[0]:
        return entry[1]
    return None


def _set_cached_detail(sales_id: str, detail: Dict[str, Any]) -> None:
    if not _DETAIL_CACHE_TTL:
        return
    if len(_DETAIL_CACHE) >= _DETAIL_CACHE_MAX:
        now = time.time()
        expired = [k for k, (exp, _) in _DETAIL_CACHE.items() if exp < now]
        for k in expired or list(_DETAIL_CACHE)[:200]:
            _DETAIL_CACHE.pop(k, None)
    _DETAIL_CACHE[sales_id] = (time.time() + _DETAIL_CACHE_TTL, detail)


class _TokenCache:
    token: Optional[str] = None
    expires_at: float = 0.0


_service_token_cache = _TokenCache()
_missing_service_credentials_logged = False


def _service_login() -> Optional[str]:
    global _missing_service_credentials_logged
    if not DELIGO_API_EMAIL or not DELIGO_API_PASSWORD:
        if not _missing_service_credentials_logged:
            logger.info("DELIGO_API_EMAIL / DELIGO_API_PASSWORD not set; tracking fallback auth is disabled")
            _missing_service_credentials_logged = True
        return None

    try:
        r = httpx.post(
            f"{DELIGO_API_URL}/api/user/login",
            json={"email": DELIGO_API_EMAIL, "password": DELIGO_API_PASSWORD},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
        token = body.get("access_token")
        expires_in = int(body.get("expires_in") or 0)
        if token:
            _service_token_cache.token = token
            _service_token_cache.expires_at = time.time() + max(expires_in - 60, 60)
        return token
    except Exception:
        logger.warning("Deligo service login request failed", exc_info=True)
        return None


def _service_token() -> Optional[str]:
    if _service_token_cache.token and time.time() < _service_token_cache.expires_at:
        return _service_token_cache.token
    return _service_login()


def _post_with_retry(
    path: str,
    payload: Dict[str, Any],
    *,
    use_service_auth: bool = False,
) -> Optional[httpx.Response]:
    url = f"{DELIGO_API_URL}{path}"

    headers: Optional[Dict[str, str]] = None
    if use_service_auth:
        tok = _service_token()
        if not tok:
            return None
        headers = {"Authorization": f"Bearer {tok}"}

    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT)

        if use_service_auth and r.status_code == 401:
            _service_token_cache.token = None
            tok = _service_token()
            if not tok:
                return None
            headers = {"Authorization": f"Bearer {tok}"}
            r = httpx.post(url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT)

        return r
    except Exception:
        logger.warning("Deligo request failed: %s", path, exc_info=True)
        return None


def get_driver_sales(
    driver_id: str,
    offset: int = 0,
    page_size: int = 50,
    use_service_auth: bool = False,
) -> List[Dict[str, Any]]:
    """List sales assigned to a driver via POST /api/sales/integration."""
    payload = {
        "criteria": {
            "es.driver_id": {
                "value": driver_id,
                "operator": "=",
                "dataType": "integer",
            },
            "TO_CHAR(t6.created_date, 'YYYY-MM-DD')": {
                "value": date.today().isoformat(),
                "operator": "=",
            },
        },
        "paging": {"offset": offset, "pageSize": page_size},
    }
    r = _post_with_retry("/api/sales/integration", payload, use_service_auth=use_service_auth)

    # Keep behavior consistent with get_sales_detail: if service auth is
    # unavailable or rejected, retry once without auth for compatibility.
    if r is None and use_service_auth:
        logger.warning(
            "Deligo sales/integration with service auth returned no response; retrying unauthenticated for driver %s",
            driver_id,
        )
        r = _post_with_retry("/api/sales/integration", payload, use_service_auth=False)

    if r is not None and r.status_code in (401, 403) and use_service_auth:
        logger.warning(
            "Deligo sales/integration with service auth returned %s; retrying unauthenticated for driver %s",
            r.status_code,
            driver_id,
        )
        r = _post_with_retry("/api/sales/integration", payload, use_service_auth=False)

    if r is None or r.status_code != 200:
        if r is not None:
            logger.warning("Deligo sales list returned %s for driver %s", r.status_code, driver_id)
        return []
    body = r.json()
    data = body.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("items") or data.get("data") or []
    return []


def change_sales_status(sales_id: str, status_id: int, driver_token: Optional[str] = None) -> bool:
    """Change a sales order's wfm status via POST /api/sales/changestatus.

    If `driver_token` is provided the call is made with the driver's own Deligo
    JWT (matching the Postman «change_status /Driver/» request). When not supplied,
    the request is sent without Authorization header.

    Returns True on HTTP 200, False otherwise.
    """
    status_label = STATUS_LABEL_MAP.get(status_id, str(status_id))
    token_source = "driver" if driver_token else "unauthenticated"
    print(f"[Deligo] POST /api/sales/changestatus  sales_id={sales_id}  statusId={status_id} ({status_label})  token={token_source}")

    if driver_token:
        headers = {"Authorization": f"Bearer {driver_token}"}
        url = f"{DELIGO_API_URL}/api/sales/changestatus"
        try:
            r: Optional[httpx.Response] = httpx.post(
                url,
                json={"id": sales_id, "statusId": status_id},
                headers=headers,
                timeout=_HTTP_TIMEOUT,
            )
        except Exception:
            logger.warning("Deligo changestatus request failed with driver token", exc_info=True)
            print(f"[Deligo] changestatus failed (no response) for sales_id={sales_id}")
            return False
    else:
        r = _post_with_retry("/api/sales/changestatus", {"id": sales_id, "statusId": status_id})

    if r is None:
        print(f"[Deligo] changestatus failed (no response) for sales_id={sales_id}")
        return False
    if r.status_code != 200:
        logger.warning(
            "Deligo changestatus returned %s for sales_id=%s statusId=%s",
            r.status_code, sales_id, status_id,
        )
        print(f"[Deligo] changestatus failed HTTP {r.status_code} for sales_id={sales_id}: {r.text[:200]}")
        return False
    print(f"[Deligo] changestatus OK for sales_id={sales_id} statusId={status_id} ({status_label})")
    return True


def get_sales_detail(sales_id: str, *, use_service_auth: bool = False) -> Optional[Dict[str, Any]]:
    """Fetch structured sales detail via POST /api/sales/get.

    The deligo API keys this lookup by sales_id (the `id` field), not by
    sales_number — passing sales_number returns no data.
    """
    if not sales_id:
        return None
    cached = _get_cached_detail(sales_id)
    if cached is not None:
        return cached
    print(f"[Deligo] POST /api/sales/get  id={sales_id}")
    r = _post_with_retry("/api/sales/get", {"id": sales_id}, use_service_auth=use_service_auth)

    # In some deployments service auth may be temporarily unavailable (missing env,
    # expired token, or upstream auth policy changes). Fall back to unauthenticated
    # request once to preserve backwards compatibility where Deligo still allows it.
    if r is None and use_service_auth:
        logger.warning("Deligo sales/get with service auth returned no response; retrying unauthenticated for id=%s", sales_id)
        r = _post_with_retry("/api/sales/get", {"id": sales_id}, use_service_auth=False)

    if r is not None and r.status_code in (401, 403) and use_service_auth:
        logger.warning(
            "Deligo sales/get with service auth returned %s; retrying unauthenticated for id=%s",
            r.status_code,
            sales_id,
        )
        r = _post_with_retry("/api/sales/get", {"id": sales_id}, use_service_auth=False)

    if r is None:
        print(f"[Deligo] sales/get failed for id={sales_id}: no response")
        return None

    if r.status_code != 200:
        response_preview = (r.text or "")[:400]
        if r.status_code not in (401, 404):
            logger.warning(
                "Deligo sales detail returned %s for id=%s: %s",
                r.status_code,
                sales_id,
                response_preview,
            )
        print(
            f"[Deligo] sales/get failed for id={sales_id}: HTTP {r.status_code}"
            + (f" body={response_preview}" if response_preview else "")
        )
        return None
    body = r.json()
    data = body.get("data")
    if not isinstance(data, dict):
        print(f"[Deligo] sales/get unexpected data type for id={sales_id}: {type(data)}")
        return None
    print(f"[Deligo] sales/get OK for id={sales_id}")
    result = structure_sales_detail(data)
    _set_cached_detail(sales_id, result)
    return result


def get_status_description_service(
    sales_id: str, status_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Fetch the proof/description saved for a driver-only status using the service account."""
    payload: Dict[str, Any] = {"id": sales_id}
    if status_id is not None:
        payload["statusId"] = status_id
    r = _post_with_retry("/api/sales/getStatusDescription", payload, use_service_auth=True)
    if r is None or r.status_code != 200:
        return None
    try:
        body = r.json()
        data = body.get("data")
        if isinstance(data, dict):
            # Normalise to { description, file_path } to match the frontend expectation.
            return {
                "description": data.get("description") or data.get("statusDescription") or data.get("note"),
                "file_path": data.get("file_path") or data.get("filePath") or data.get("imageUrl") or data.get("image"),
            }
    except Exception:
        pass
    return None


def push_address_update(
    sales_id: str,
    customer_address: str,
    latitude: float,
    longitude: float,
    extra_notes: Optional[str] = None,
) -> bool:
    """Notify Deligo of an address/coordinate change via POST /api/sales/update/address.

    Best-effort: logs on failure but never raises.
    """
    print(f"[Deligo] POST /api/sales/update/address  id={sales_id}  lat={latitude}  lng={longitude}")
    payload: Dict[str, Any] = {
        "id": sales_id,
        "customerAddress": customer_address,
        "customerLat": str(latitude),
        "customerLng": str(longitude),
    }
    if extra_notes:
        payload["customerAddress"] += f" ({extra_notes})"
    r = _post_with_retry("/api/sales/update/address", payload, use_service_auth=True)
    if r is None or r.status_code != 200:
        status = r.status_code if r is not None else "no response"
        logger.warning(
            "Deligo update/address returned %s for sales_id=%s",
            status,
            sales_id,
        )
        print(f"[Deligo] update/address failed ({status}) for sales_id={sales_id}")
        return False
    print(f"[Deligo] update/address OK for sales_id={sales_id}")
    return True


def structure_sales_detail(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize the raw /api/sales/get payload and attach status info."""
    wfm_raw = raw.get("wfm_status_id")
    wfm_id = int(wfm_raw) if wfm_raw is not None else None
    status_code = STATUS_CODE_MAP.get(wfm_id) if wfm_id is not None else None
    status_label = STATUS_LABEL_MAP.get(wfm_id) if wfm_id is not None else None
    is_closed = 1 if wfm_id in _CLOSED_STATUS_IDS else 0

    detail = dict(raw)
    detail["status_code"] = status_code
    detail["status_name"] = status_label or raw.get("status_name")
    detail["is_closed"] = is_closed
    detail["order_items"] = _normalize_items(raw.get("items") or raw.get("order_items") or [])
    return detail


def _normalize_items(raw_items: Any) -> List[Dict[str, Any]]:
    """Map deligo sales items into the canonical { item_id, name, image_url, quantity, price } shape."""
    if not isinstance(raw_items, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("sales_detail_id") or item.get("item_id")
        name = item.get("item_name") or item.get("main_item_name") or item.get("name") or "—"
        image_url = item.get("default_image") or item.get("image_url")
        quantity = item.get("sales_qty") or item.get("quantity") or 0
        price_raw = (
            item.get("unit_price")
            or item.get("line_total_amount")
            or item.get("line_total_price")
            or item.get("price")
            or 0
        )
        try:
            price = float(price_raw)
        except (TypeError, ValueError):
            price = 0.0
        try:
            quantity_int = int(quantity)
        except (TypeError, ValueError):
            quantity_int = 0
        out.append({
            "item_id": str(item_id) if item_id is not None else "",
            "name": name,
            "image_url": image_url,
            "quantity": quantity_int,
            "price": price,
        })
    return out
