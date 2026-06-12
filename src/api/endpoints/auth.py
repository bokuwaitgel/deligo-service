"""Auth proxy endpoints — log drivers into the deligo platform via our backend
so the frontend never talks to api.deligo.mn directly with raw credentials.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from schemas.database.delivery_db import DeliveryOrder
from src.api.auth_utils import get_bearer_token
from src.dependencies import get_delivery_repository
from src.repositories.delivery import DeliveryRepository
from src.services.deligo_user_proxy import (
    DeligoApiError,
    change_status,
    driver_orders,
    get_status_description,
    login as deligo_login,
    pick_company_id,
    pick_driver_id,
    sales_detail,
    shop_orders,
    user_info,
)
from src.services.blacklist import is_driver_blacklisted
from src.services.deligo_integration import CLOSED_WFM_STATUS_IDS, STATUS_CODE_MAP, get_status_description_service
from src.services.geocode_quota import consume_geocode_quota
from src.services.delivery import _geocode, _build_tracking_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_DRIVER_ONLY_WITH_PROOF = {12, 13, 14, 15, 16, 17, 23}


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _extract_customer_location(item: Dict[str, Any]) -> Dict[str, Any] | None:
    lat_raw = (
        item.get("latitude")
        or item.get("lat")
        or item.get("customer_latitude")
        or item.get("customer_lat")
    )
    lng_raw = (
        item.get("longitude")
        or item.get("lng")
        or item.get("customer_longitude")
        or item.get("customer_lng")
    )
    if lat_raw is None or lng_raw is None:
        return None

    try:
        latitude = float(lat_raw)
        longitude = float(lng_raw)
    except (TypeError, ValueError):
        return None

    return {
        "latitude": latitude,
        "longitude": longitude,
        "formatted_address": _as_str(item.get("customer_address"))
        or _as_str(item.get("formatted_address")),
    }


def _is_countryside_flag(value: object) -> bool:
    if value is True or value == 1:
        return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"1", "true"}
    return False


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
    scope_id: str | None = None
    include_detail: bool = False
    include_status_desc: bool = False
    created_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Filter orders by created date (YYYY-MM-DD); maps to TO_CHAR(t6.created_date, 'YYYY-MM-DD') in the Deligo criteria.",
    )


class DriverOrderSortRequest(BaseModel):
    sales_numbers: List[str] = Field(min_length=1)
    scope_id: str | None = None


class ChangeStatusRequest(BaseModel):
    sales_id: str
    status_id: int
    status_description: str | None = None
    image_base64: str | None = None
    proof: Dict[str, Any] | None = None
    status_publish_date: str | None = None


def _sort_driver_items(
    items: List[Dict[str, Any]],
    local_rows: Dict[str, DeliveryOrder],
) -> List[Dict[str, Any]]:
    indexed_items: List[tuple[bool, int, int, Dict[str, Any]]] = []
    for original_index, item in enumerate(items):
        sales_number = _as_str(item.get("sales_number"))
        local = local_rows.get(sales_number) if sales_number else None
        sort_order = local.sort_order if local and local.sort_order is not None else None
        indexed_items.append(
            (
                sort_order is None,
                sort_order if sort_order is not None else original_index,
                original_index,
                item,
            )
        )

    indexed_items.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return [entry[3] for entry in indexed_items]


def _enrich_with_detail_and_location(
    token: str,
    items: List[Dict[str, Any]],
    repo: DeliveryRepository,
    scope_driver_id: str | None = None,
    scope_company_id: str | None = None,
    include_detail: bool = True,
    include_status_desc: bool = False,
    geocode_new: bool = True,
) -> List[Dict[str, Any]]:
    """For each item in the Deligo list:
    1. Fetch full detail (items, status, etc.) via get_sales_detail.
    2. Look up customer_location from our local DB.
    3. Upsert the row if missing, and patch location if we got one from detail.
    Returns a merged list ready for the frontend.
    """
    sales_numbers = [str(it.get("sales_number", "")) for it in items if it.get("sales_number")]
    local_rows = {row.sales_number: row for row in repo.get_by_sales_numbers(sales_numbers)}

    driver_sort_orders = [
        row.sort_order
        for row in local_rows.values()
        if row.driver_id == scope_driver_id and row.sort_order is not None
    ] if scope_driver_id else []
    has_saved_driver_sort = bool(driver_sort_orders)
    next_sort_order = (max(driver_sort_orders) + 1) if driver_sort_orders else 0

    enriched: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        sales_number = _as_str(item.get("sales_number"))
        if not sales_number:
            continue

        merged = {**item}
        sales_id = _as_str(merged.get("sales_id"))
        if include_detail and sales_id:
            detail = sales_detail(sales_id)
            if detail:
                merged = {**merged, **detail}

            # Driver-only statuses can carry a courier note + image proof.
            wfm_status_id = _as_int(merged.get("wfm_status_id"))
            if include_status_desc and wfm_status_id in _DRIVER_ONLY_WITH_PROOF:
                try:
                    status_desc = get_status_description(token, sales_id, status_id=wfm_status_id)
                    if isinstance(status_desc, dict):
                        has_description = bool(_as_str(status_desc.get("description")))
                        has_file = bool(_as_str(status_desc.get("file_path")))
                        if has_description or has_file:
                            merged["status_description"] = status_desc
                except DeligoApiError:
                    # Best-effort enrichment; don't fail order listing if proof fetch fails.
                    pass

        local = local_rows.get(sales_number)

        company_id_val = scope_company_id or _as_str(merged.get("company_id"))
        legacy_store_id = _as_str(merged.get("store_id"))
        persisted_shop_id = company_id_val or legacy_store_id or "unknown"

        if company_id_val and not _as_str(merged.get("company_id")):
            merged["company_id"] = company_id_val

        if local:
            # Orders soft-deleted via the delivery API must not appear in any list.
            if local.map_status == "deleted":
                continue

            patch: Dict[str, Any] = {}
            # Deligo is source of truth for driver assignment — overwrite on reassignment.
            if scope_driver_id and local.driver_id != scope_driver_id:
                patch["driver_id"] = scope_driver_id
            # Company scope is the main shop identifier now.
            if scope_company_id and local.company_id != scope_company_id:
                patch["company_id"] = scope_company_id

            if patch:
                local = repo.update_partial(sales_number, patch) or local
                local_rows[sales_number] = local

            # Combine local (authoritative coords from customer-adjusted pin)
            # with whatever Deligo returns (often the only source of structured
            # fields like street_address/district/khoroo/building). Overwriting
            # with the local row alone would silently hide structured fields
            # that the deligo payload exposes for the driver.
            if local.customer_location or isinstance(merged.get("customer_location"), dict):
                deligo_loc = merged.get("customer_location") if isinstance(merged.get("customer_location"), dict) else None
                local_loc = local.customer_location if isinstance(local.customer_location, dict) else None
                combined: Dict[str, Any] = {}
                if deligo_loc:
                    combined.update(deligo_loc)
                if local_loc:
                    # Local row wins for coords + formatted_address (the customer
                    # may have moved the pin), but only when those fields are populated.
                    for key in ("latitude", "longitude", "formatted_address"):
                        if local_loc.get(key):
                            combined[key] = local_loc[key]
                    # For structured address fields, prefer whichever source has a value.
                    for key in (
                        "street_address", "city", "state", "district", "khoroo",
                        "country", "postal_code", "building",
                    ):
                        if combined.get(key) in (None, "") and local_loc.get(key) not in (None, ""):
                            combined[key] = local_loc[key]
                if combined:
                    merged["customer_location"] = combined

                # Persist any structured fields the local row was missing so
                # later requests don't have to re-derive them from Deligo.
                if local_loc is not None and deligo_loc:
                    persisted_patch: Dict[str, Any] = {}
                    structured_keys = (
                        "street_address", "city", "state", "district", "khoroo",
                        "country", "postal_code", "building",
                    )
                    if any(
                        local_loc.get(k) in (None, "") and deligo_loc.get(k) not in (None, "")
                        for k in structured_keys
                    ):
                        persisted_patch["customer_location"] = combined
                    if persisted_patch:
                        repo.update_partial(sales_number, persisted_patch)
        else:
            # No local row yet — create one so the order enters our DB and shows
            # up in shop/map lists (sync_active defaults to True). Geocoding is the
            # only Google-billed step and stays opt-in via geocode_new; the shop
            # list passes geocode_new=False but still gets its rows created here.
            driver_id_val = scope_driver_id or _as_str(item.get("driver_id"))
            sort_order = None
            if driver_id_val:
                if has_saved_driver_sort:
                    sort_order = next_sort_order
                    next_sort_order += 1
                else:
                    sort_order = index
            customer_address = (
                _as_str(merged.get("customer_address"))
                or _as_str(merged.get("address"))
                or "Address not provided"
            )
            # Skip Google calls entirely for closed orders (delivered/cancelled/exchanged) —
            # they don't get rendered on the map.
            wfm_id = _as_int(merged.get("wfm_status_id"))
            is_closed = wfm_id in CLOSED_WFM_STATUS_IDS
            location_data = None
            if not is_closed and customer_address and customer_address != "Address not provided":
                # DB-level cache: reuse any prior geocode for this exact address (free).
                location_data = repo.find_location_by_address(customer_address)
                # Live Google geocode is opt-in (cost) — only when geocode_new is set.
                if location_data is None and geocode_new and consume_geocode_quota(driver_id_val):
                    is_countryside = _is_countryside_flag(merged.get("is_country"))
                    print(f"[Geocode] Geocoding {sales_number}: {customer_address}")
                    location_data = _geocode(customer_address, is_countryside=is_countryside)
                if location_data:
                    merged["customer_location"] = location_data
            created = repo.create(DeliveryOrder(
                sales_number=sales_number,
                sales_id=sales_id,
                store_id=legacy_store_id or company_id_val or "unknown",
                company_id=company_id_val,
                driver_id=driver_id_val,
                sort_order=sort_order,
                customer_address=customer_address,
                customer_location=location_data,
                tracking_url=_build_tracking_url(sales_number),
                map_status="pending",
            ))
            local_rows[sales_number] = created

        enriched.append(merged)

    if scope_driver_id:
        return _sort_driver_items(enriched, local_rows)

    return enriched


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
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    _require_token(token)
    try:
        if payload.scope_id:
            driver_id = payload.scope_id
        else:
            info = user_info(token)
            driver_id = pick_driver_id(info)
        if not driver_id:
            raise HTTPException(status_code=403, detail="Жолоочийн ID олдсонгүй")
        if is_driver_blacklisted(driver_id):
            return {"status": "ok", "data": [], "scope_id": driver_id}
        items: List[Dict[str, Any]] = driver_orders(
            token,
            driver_id,
            offset=payload.offset,
            page_size=payload.page_size,
            created_date=payload.created_date,
        )
        items = _enrich_with_detail_and_location(
            token, items, repo,
            scope_driver_id=driver_id,
            include_detail=payload.include_detail,
            include_status_desc=payload.include_status_desc,
        )

        # Persist sync membership (any order in the driver's list) + latest WFM
        # status code. The "open/in-progress" filter is applied at read time
        # against the status column, not here.
        status_by_sn: Dict[str, Optional[str]] = {}
        for it in items:
            sn = it.get("sales_number")
            if not sn:
                continue
            sn_str = str(sn)
            wfm_id = _as_int(it.get("wfm_status_id"))
            status_by_sn[sn_str] = STATUS_CODE_MAP.get(wfm_id) if wfm_id is not None else None
        active_sns: set = set(status_by_sn.keys())
        repo.sync_driver_active_status(driver_id, active_sns, status_by_sn)

        #if customer_location is missing add location with geocoding or from detail
        for item in items:
            if not item.get("customer_location"):
                sales_number = _as_str(item.get("sales_number"))
                if sales_number:
                    local = repo.get_by_sales_number(sales_number)
                    if local and local.customer_location:
                        item["customer_location"] = local.customer_location
                    elif payload.include_detail:
                        # If we already fetched detail, try extracting location from it before geocoding.
                        location_data = _extract_customer_location(item)
                        if location_data:
                            item["customer_location"] = location_data
                            repo.update_partial(sales_number, {"customer_location": location_data})
                        else:
                            # As a last resort, geocode the address if quota allows.
                            customer_address = _as_str(item.get("customer_address")) or "Address not provided"
                            if customer_address and customer_address != "Address not provided":
                                company_id_val = _as_str(item.get("company_id"))
                                driver_id_val = driver_id
                                is_countryside = _is_countryside_flag(item.get("is_country"))
                                if consume_geocode_quota(driver_id_val):
                                    print(f"[Geocode] Geocoding {sales_number}: {customer_address}")
                                    geocoded = _geocode(customer_address, is_countryside=is_countryside)
                                    if geocoded:
                                        item["customer_location"] = geocoded
                                        repo.update_partial(sales_number, {"customer_location": geocoded})

    except DeligoApiError as exc:
        raise _handle_deligo_error(exc) from exc
    return {"status": "ok", "data": items, "scope_id": driver_id}


@router.post("/orders/driver/sort")
def save_driver_order_sort_endpoint(
    payload: DriverOrderSortRequest,
    token: str = Depends(get_bearer_token),
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    _require_token(token)

    cleaned_sales_numbers = [sales_number.strip() for sales_number in payload.sales_numbers if sales_number.strip()]
    if not cleaned_sales_numbers:
        raise HTTPException(status_code=400, detail="sales_numbers is required")
    if len(cleaned_sales_numbers) != len(set(cleaned_sales_numbers)):
        raise HTTPException(status_code=400, detail="sales_numbers must be unique")

    try:
        if payload.scope_id:
            driver_id = payload.scope_id
        else:
            info = user_info(token)
            driver_id = pick_driver_id(info)
        if not driver_id:
            raise HTTPException(status_code=403, detail="Жолоочийн ID олдсонгүй")
        if is_driver_blacklisted(driver_id):
            return {"status": "ok", "scope_id": driver_id, "matched_count": 0}
        matched_count = repo.set_driver_sort_orders(driver_id, cleaned_sales_numbers)
    except DeligoApiError as exc:
        raise _handle_deligo_error(exc) from exc

    return {"status": "ok", "scope_id": driver_id, "matched_count": matched_count}


@router.post("/orders/shop")
def shop_orders_endpoint(
    payload: OrderListRequest,
    token: str = Depends(get_bearer_token),
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    _require_token(token)
    try:
        if payload.scope_id:
            company_id = payload.scope_id
        else:
            info = user_info(token)
            company_id = pick_company_id(info)
        if not company_id:
            raise HTTPException(status_code=403, detail="Компанийн ID олдсонгүй")
        items: List[Dict[str, Any]] = shop_orders(
            token,
            company_id,
            offset=payload.offset,
            page_size=payload.page_size,
            created_date=payload.created_date,
        )
        items = _enrich_with_detail_and_location(
            token, items, repo,
            scope_company_id=company_id,
            include_detail=payload.include_detail,
            include_status_desc=payload.include_status_desc,
            geocode_new=False,
        )
        # Only include orders where sync_active is True in the local DB
        sales_numbers = [str(it.get("sales_number", "")) for it in items if it.get("sales_number")]
        local_rows = {row.sales_number: row for row in repo.get_by_sales_numbers(sales_numbers)}
        items = [it for it in items if local_rows.get(str(it.get("sales_number"))) and local_rows[str(it.get("sales_number"))].sync_active]
    except DeligoApiError as exc:
        raise _handle_deligo_error(exc) from exc
    return {"status": "ok", "data": items, "scope_id": company_id}


@router.post("/orders/changestatus")
def change_status_endpoint(
    payload: ChangeStatusRequest,
    token: str = Depends(get_bearer_token),
):
    _require_token(token)
    try:
        # Prefer explicit status_description; fall back to the note inside proof
        effective_description = payload.status_description or (
            (payload.proof or {}).get("note") or None
        )
        result = change_status(
            token,
            payload.sales_id,
            payload.status_id,
            status_description=effective_description,
            proof=payload.proof,
            status_publish_date=payload.status_publish_date,
        )
    except DeligoApiError as exc:
        raise _handle_deligo_error(exc) from exc

    warning = result.get("warning") if isinstance(result, dict) else None
    response: Dict[str, Any] = {"status": "ok"}
    if isinstance(warning, str) and warning.strip():
        response["warning"] = warning.strip()
    return response


@router.get("/orders/status-description/{sales_id}")
def get_status_description_endpoint(
    sales_id: str,
    status_id: Optional[int] = None,
):
    # Public: uses the service account, no user token required. Customer tracking
    # page fetches handoff proof for countryside completed orders without auth.
    data = get_status_description_service(sales_id, status_id=status_id)
    if data is None:
        raise HTTPException(status_code=404, detail="No status description found")
    return {"status": "ok", "data": data}


@router.get("/orders/detail/{sales_id}")
def get_order_detail_endpoint(
    sales_id: str,
    token: str = Depends(get_bearer_token),
):
    """Fetch full Deligo detail for a single order on demand (items, status, etc.)."""
    _require_token(token)
    try:
        data = sales_detail(sales_id)
    except DeligoApiError as exc:
        raise _handle_deligo_error(exc) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="Order detail not found")
    return {"status": "ok", "data": data}
