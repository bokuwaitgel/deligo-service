"""Deligo external API client (api.deligo.mn).

Handles login, token caching, and the two calls we actually need:

- POST /api/sales/integration  — list sales for a driver (by driver_id)
- POST /api/sales/get          — fetch full detail for one sales_number

Driver-related fields come from this service at read time; we no longer
store driver_id/driver_name locally.

Configure via env:
    DELIGO_API_URL       (default https://api.deligo.mn)
    DELIGO_API_EMAIL
    DELIGO_API_PASSWORD
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

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
_CLOSED_STATUS_IDS = {3, 12, 23}


class _TokenCache:
    token: Optional[str] = None
    expires_at: float = 0.0


_cache = _TokenCache()


def _login() -> Optional[str]:
    if not DELIGO_API_EMAIL or not DELIGO_API_PASSWORD:
        logger.warning("DELIGO_API_EMAIL / DELIGO_API_PASSWORD not set — skipping deligo integration")
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
            _cache.token = token
            # Renew slightly before the token actually expires.
            _cache.expires_at = time.time() + max(expires_in - 60, 60)
        return token
    except Exception:
        logger.warning("Deligo login request failed", exc_info=True)
        return None


def _token() -> Optional[str]:
    if _cache.token and time.time() < _cache.expires_at:
        return _cache.token
    return _login()


def _auth_headers() -> Optional[Dict[str, str]]:
    tok = _token()
    return {"Authorization": f"Bearer {tok}"} if tok else None


def _post_with_retry(path: str, payload: Dict[str, Any]) -> Optional[httpx.Response]:
    headers = _auth_headers()
    if headers is None:
        return None
    url = f"{DELIGO_API_URL}{path}"
    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT)
        if r.status_code == 401:
            # Token rejected — force refresh and retry once.
            _cache.token = None
            headers = _auth_headers()
            if headers is None:
                return None
            r = httpx.post(url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT)
        return r
    except Exception:
        logger.warning("Deligo request failed: %s", path, exc_info=True)
        return None


def get_driver_sales(
    driver_id: str, offset: int = 0, page_size: int = 50
) -> List[Dict[str, Any]]:
    """List sales assigned to a driver via POST /api/sales/integration."""
    payload = {
        "criteria": {
            "es.driver_id": {
                "value": driver_id,
                "operator": "=",
                "dataType": "integer",
            }
        },
        "paging": {"offset": offset, "pageSize": page_size},
    }
    r = _post_with_retry("/api/sales/integration", payload)
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
    JWT (matching the Postman «change_status /Driver/» request).  Falls back to
    the service-account token when not supplied.

    Returns True on HTTP 200, False otherwise.
    """
    status_label = STATUS_LABEL_MAP.get(status_id, str(status_id))
    token_source = "driver" if driver_token else "service-account"
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


def get_sales_detail(sales_id: str) -> Optional[Dict[str, Any]]:
    """Fetch structured sales detail via POST /api/sales/get.

    The deligo API keys this lookup by sales_id (the `id` field), not by
    sales_number — passing sales_number returns no data.
    """
    if not sales_id:
        return None
    print(f"[Deligo] POST /api/sales/get  id={sales_id}")
    r = _post_with_retry("/api/sales/get", {"id": sales_id})
    if r is None or r.status_code != 200:
        if r is not None and r.status_code not in (401, 404):
            logger.warning("Deligo sales detail returned %s for id=%s", r.status_code, sales_id)
        print(f"[Deligo] sales/get failed for id={sales_id}: {r.status_code if r else 'None'}")
        return None
    body = r.json()
    data = body.get("data")
    if not isinstance(data, dict):
        print(f"[Deligo] sales/get unexpected data type for id={sales_id}: {type(data)}")
        return None
    print(f"[Deligo] sales/get OK for id={sales_id}")
    return structure_sales_detail(data)


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
