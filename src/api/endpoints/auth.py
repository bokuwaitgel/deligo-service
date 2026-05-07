"""Auth proxy endpoints — log drivers into the deligo platform via our backend
so the frontend never talks to api.deligo.mn directly with raw credentials.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

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
    login as deligo_login,
    pick_company_id,
    pick_driver_id,
    sales_detail,
    shop_orders,
    user_info,
)
from src.services.delivery import _geocode, _build_tracking_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


class DriverOrderSortRequest(BaseModel):
    sales_numbers: List[str] = Field(min_length=1)
    scope_id: str | None = None


class ChangeStatusRequest(BaseModel):
    sales_id: str
    status_id: int
    status_description: str | None = None
    proof: Dict[str, Any] | None = None


def _build_status_description(payload: ChangeStatusRequest) -> str | None:
    # Prefer explicit status_description when provided by caller.
    if payload.status_description and payload.status_description.strip():
        return payload.status_description.strip()

    if not payload.proof:
        return None

    note = str(payload.proof.get("note") or "").strip()
    image_base64 = str(payload.proof.get("imageDataUrl") or payload.proof.get("image_base64") or "").strip()

    if note and image_base64:
        return f"{note}\nimage_base64:{image_base64}"
    if note:
        return note
    if image_base64:
        return f"image_base64:{image_base64}"
    return None


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
        if sales_id:
            detail = sales_detail(token, sales_id)
            if detail:
                merged = {**merged, **detail}

        local = local_rows.get(sales_number)

        company_id_val = scope_company_id or _as_str(merged.get("company_id"))
        legacy_store_id = _as_str(merged.get("store_id"))
        persisted_shop_id = company_id_val or legacy_store_id or "unknown"

        if company_id_val and not _as_str(merged.get("company_id")):
            merged["company_id"] = company_id_val

        if local:
            patch: Dict[str, Any] = {}
            if scope_driver_id and not local.driver_id:
                patch["driver_id"] = scope_driver_id
            # Company scope is the main shop identifier now.
            if scope_company_id and local.company_id != scope_company_id:
                patch["company_id"] = scope_company_id

            if patch:
                local = repo.update_partial(sales_number, patch) or local
                local_rows[sales_number] = local

            if local.customer_location:
                merged["customer_location"] = local.customer_location
        else:
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
            location_data = None
            if customer_address and customer_address != "Address not provided":
                print(f"[Geocode] Geocoding {sales_number}: {customer_address}")
                location_data = _geocode(customer_address)
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
        items: List[Dict[str, Any]] = driver_orders(
            token, driver_id, offset=payload.offset, page_size=payload.page_size
        )
        items = _enrich_with_detail_and_location(token, items, repo, scope_driver_id=driver_id)
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
            token, company_id, offset=payload.offset, page_size=payload.page_size
        )
        items = _enrich_with_detail_and_location(token, items, repo, scope_company_id=company_id)
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
        status_description = _build_status_description(payload)
        change_status(token, payload.sales_id, payload.status_id, status_description=status_description)
    except DeligoApiError as exc:
        raise _handle_deligo_error(exc) from exc
    return {"status": "ok"}
