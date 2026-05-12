from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from schemas.delivery import (
    AddressUpdateRequest,
    DeliveryOrderCreate,
    DeliveryOrderResponse,
    Location,
    MapStatusUpdateRequest,
)
from schemas.database.delivery_db import DeliveryOrder
from src.api.auth_utils import require_api_key
from src.dependencies import get_delivery_repository
from src.repositories.delivery import DeliveryRepository
from src.services.delivery import (
    complete_delivery,
    create_delivery,
    get_delivery,
    update_location,
    update_location_by_address,
)
from src.services.deligo_integration import change_sales_status, get_driver_sales, get_sales_detail, structure_sales_detail
from src.services.middleware_order import get_orders_by_sales_numbers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/delivery", tags=["delivery"])


def _as_str(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_customer_location(detail: dict) -> Optional[dict]:
    # Accept common coordinate field variations returned by third-party payloads.
    lat_raw = (
        detail.get("latitude")
        or detail.get("lat")
        or detail.get("customer_latitude")
        or detail.get("customer_lat")
    )
    lng_raw = (
        detail.get("longitude")
        or detail.get("lng")
        or detail.get("customer_longitude")
        or detail.get("customer_lng")
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
        "formatted_address": _as_str(detail.get("customer_address"))
        or _as_str(detail.get("formatted_address")),
    }


def _upsert_local_delivery_from_detail(repo: DeliveryRepository, detail: dict) -> Optional[DeliveryOrder]:
    sales_number = _as_str(detail.get("sales_number"))
    if not sales_number:
        return None

    sales_id = _as_str(detail.get("sales_id"))
    store_id = _as_str(detail.get("store_id")) or "unknown"
    company_id = _as_str(detail.get("company_id"))
    customer_address = (
        _as_str(detail.get("customer_address"))
        or _as_str(detail.get("address"))
        or "Address not provided"
    )
    customer_location = _extract_customer_location(detail)

    existing = repo.get_by_sales_number(sales_number)
    if existing is None:
        created = DeliveryOrder(
            sales_number=sales_number,
            sales_id=sales_id,
            store_id=store_id,
            company_id=company_id,
            customer_address=customer_address,
            customer_location=customer_location,
            map_status="pending",
        )
        return repo.create(created)

    patch: dict[str, object] = {}
    if not existing.sales_id and sales_id:
        patch["sales_id"] = sales_id
    if (not existing.store_id or existing.store_id == "unknown") and store_id:
        patch["store_id"] = store_id
    if not existing.company_id and company_id:
        patch["company_id"] = company_id
    if (not existing.customer_address or existing.customer_address == "Address not provided") and customer_address:
        patch["customer_address"] = customer_address
    if existing.customer_location is None and customer_location is not None:
        patch["customer_location"] = customer_location

    if patch:
        updated = repo.update_partial(sales_number, patch)
        if updated is not None:
            return updated
    return existing



# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# Map status is determined by the delivery workflow stage.

@router.post("/", response_model=DeliveryOrderResponse, status_code=201)
async def create_delivery_order(
    order: DeliveryOrderCreate,
    repo: DeliveryRepository = Depends(get_delivery_repository),
    api_key: str = Depends(require_api_key),
):
    """Create a new delivery order."""
    try:
        delivery_order = create_delivery(repo, order)
        return delivery_order
    except Exception as e:
        logger.error(f"Error creating delivery order: {e}")
        raise HTTPException(status_code=500, detail="Failed to create delivery order")
    
@router.get("/{sales_number}", response_model=DeliveryOrderResponse)
async def get_delivery_order(
    sales_number: str,
    repo: DeliveryRepository = Depends(get_delivery_repository),
    api_key: str = Depends(require_api_key),
):
    """Get a delivery order by sales number, enriched with deligo sales detail (includes driver)."""
    try:
        delivery_order = get_delivery(repo, sales_number)
        if not delivery_order:
            raise HTTPException(status_code=404, detail="Delivery order not found")
        if delivery_order.sales_id:
            detail = get_sales_detail(delivery_order.sales_id)
            if detail:
                delivery_order.detail = detail
        return delivery_order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching delivery order: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch delivery order")


@router.get("/sales-id/{sales_id}", response_model=DeliveryOrderResponse)
async def get_delivery_order_by_sales_id(
    sales_id: str,
    repo: DeliveryRepository = Depends(get_delivery_repository),
    api_key: str = Depends(require_api_key),
):
    """Get a delivery order by sales_id, enriched with deligo sales detail."""
    try:
        delivery_order = repo.get_by_sales_id(sales_id)
        if not delivery_order:
            raise HTTPException(status_code=404, detail="Delivery order not found")
        if delivery_order.sales_id:
            detail = get_sales_detail(delivery_order.sales_id)
            if detail:
                delivery_order.detail = detail
        return delivery_order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching delivery order by sales_id: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch delivery order")
    
@router.patch("/{sales_number}/location", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
async def update_delivery_location(
    sales_number: str,
    location: Location,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Update delivery location with pre-geocoded coordinates."""
    try:
        updated_order = update_location(repo, sales_number, location)
        if not updated_order:
            raise HTTPException(status_code=404, detail="Delivery order not found or already completed")
        return updated_order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating delivery location: {e}")
        raise HTTPException(status_code=500, detail="Failed to update delivery location")
    
@router.patch("/{sales_number}/address", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
async def update_delivery_address(
    sales_number: str,
    address_update: AddressUpdateRequest,
    is_countryside: bool = Query(False, description="Skip Mongolia/UB suffix in geocoding"),
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Update delivery location by re-geocoding a new address."""
    try:
        updated_order = update_location_by_address(repo, sales_number, address_update.customer_address, is_countryside)
        if not updated_order:
            raise HTTPException(status_code=404, detail="Delivery order not found or already completed")
        return updated_order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating delivery address: {e}")
        raise HTTPException(status_code=500, detail="Failed to update delivery address")
    
@router.post("/{sales_number}/start", dependencies=[Depends(require_api_key)])
async def start_delivery_order(
    sales_number: str,
    repo: DeliveryRepository = Depends(get_delivery_repository),
    x_driver_token: Optional[str] = Header(default=None, alias="X-Driver-Token"),
):
    """Driver marks the delivery as started — sets deligo wfm_status to 8 (salesDriverDone).

    Looks up the local delivery row to resolve `sales_id`, then calls the deligo
    changestatus API using the driver's own Deligo JWT when supplied via the
    X-Driver-Token header (matching the Postman «change_status /Driver/» request),
    otherwise calling deligo without Authorization header.
    """
    delivery = repo.get_by_sales_number(sales_number)
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery order not found")

    sales_id = delivery.sales_id
    if not sales_id:
        raise HTTPException(status_code=422, detail="sales_id not available for this delivery")

    ok = change_sales_status(str(sales_id), 8, driver_token=x_driver_token)
    if not ok:
        raise HTTPException(status_code=502, detail="Failed to update status in deligo")
    return {"status": "ok", "sales_number": sales_number, "wfm_status_id": 8}


@router.post("/{sales_number}/map_edit", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
async def complete_delivery_order(
    sales_number: str,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Mark a delivery order as completed after map editing. This is a placeholder for the actual map editing workflow."""
    try:
        completed_order = complete_delivery(repo, sales_number)
        if not completed_order:
            raise HTTPException(status_code=404, detail="Delivery order not found or already completed")
        return completed_order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing delivery order: {e}")
        raise HTTPException(status_code=500, detail="Failed to complete delivery order")


@router.patch("/{sales_number}/map_status", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
async def set_delivery_map_status(
    sales_number: str,
    payload: MapStatusUpdateRequest,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Set delivery map_status explicitly (pending/completed) from shop workflow."""
    try:
        updated_order = repo.update_partial(sales_number, {"map_status": payload.map_status.value})
        if not updated_order:
            raise HTTPException(status_code=404, detail="Delivery order not found")
        return updated_order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating map status: {e}")
        raise HTTPException(status_code=500, detail="Failed to update map status")
    
# ---------------------------------------------------------------------------
# User View Endpoints 
# ---------------------------------------------------------------------------

"""Public endpoint for customers to track their delivery order by sales number."""
@router.get("/tracking/{sales_number}", response_model=DeliveryOrderResponse)
async def track_delivery_order(
    sales_number: str,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    try:
        delivery_order = get_delivery(repo, sales_number)
        if not delivery_order:
            raise HTTPException(status_code=404, detail="Delivery order not found")
        detail = get_sales_detail(delivery_order.sales_id) if delivery_order.sales_id else None
        if detail:
            delivery_order.detail = detail
            driver_id = detail.get("driver_id")
            if driver_id is not None:
                driver_sales = get_driver_sales(str(driver_id), page_size=200)
                active = [
                    s for s in driver_sales
                    if s.get("sales_number") != sales_number
                    and s.get("wfm_status_id") not in (3, 12, 23)
                ]
                delivery_order.active_deliveries_count = len(active)
        return delivery_order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error tracking delivery order: {e}")
        raise HTTPException(status_code=500, detail="Failed to track delivery order")
    
# ---------------------------------------------------------------------------
# Driver View Endpoints - Placeholder for actual driver dashboard functionality
# ---------------------------------------------------------------------------

"""
        Get a list of delivery orders assigned to a driver.
        have query params for query, limit etc to support pagination and filtering in the future.
"""
@router.get("/driver/{driver_id}", response_model=list[DeliveryOrderResponse])
async def get_driver_deliveries(
    driver_id: str,
    limit: int = Query(50, description="Maximum number of deliveries to return"),
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """List driver orders and auto-sync missing local delivery rows from Deligo payload."""
    try:
        sales = get_driver_sales(driver_id, page_size=limit)
        if not sales:
            return []

        details_by_number: dict[str, dict] = {}
        for s in sales:
            sn = s.get("sales_number")
            if isinstance(sn, str) and sn:
                details_by_number[sn] = structure_sales_detail(s)

        sales_numbers = list(details_by_number.keys())
        local_rows = {d.sales_number: d for d in repo.get_by_sales_numbers(sales_numbers)}

        # Ensure every Deligo order has a local delivery row (for map/location workflows).
        for sales_number in sales_numbers:
            if sales_number not in local_rows:
                created = _upsert_local_delivery_from_detail(repo, details_by_number[sales_number])
                if created is not None:
                    local_rows[sales_number] = created
            else:
                refreshed = _upsert_local_delivery_from_detail(repo, details_by_number[sales_number])
                if refreshed is not None:
                    local_rows[sales_number] = refreshed

        response: list[DeliveryOrderResponse] = []
        for sales_number in sales_numbers:
            local = local_rows.get(sales_number)
            if local is None:
                continue
            item = DeliveryOrderResponse.model_validate(local)
            item.detail = details_by_number[sales_number]
            response.append(item)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching driver deliveries: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch driver deliveries")


# ---------------------------------------------------------------------------
# Shop View Endpoints - Placeholder for actual shop dashboard functionality
# ---------------------------------------------------------------------------

"""
        Get a list of delivery orders assigned to a shop.
        have query params for query, limit etc to support pagination and filtering in the future.
"""

@router.get("/shop/{store_id}/summary")
async def get_shop_summary(
    store_id: str,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Get summary counts by company scope (path param kept as store_id for backward compatibility)."""
    company_id = store_id
    try:
        deliveries = repo.get_by_shop_id_paginated(company_id, cursor=None, limit=10000)
        sales_numbers = [d.sales_number for d in deliveries]
        details_map: dict = {}
        if sales_numbers:
            raw = get_orders_by_sales_numbers(sales_numbers)
            details_map = {d["sales_number"]: d for d in raw}

        counts = {"pending": 0, "in_progress": 0, "completed": 0, "cancelled": 0}
        for delivery in deliveries:
            detail = details_map.get(delivery.sales_number, {})
            status_code = detail.get("status_code")
            wfm_raw = detail.get("wfm_status_id")
            wfm_status_id = str(wfm_raw) if wfm_raw is not None else None

            is_cancelled = status_code == "deliveryCancel" or wfm_status_id == "12"
            is_completed = status_code in {"salesDone", "exchanged"} or wfm_status_id in {"3", "23"}
            is_in_progress = status_code == "salesDriverDone" or wfm_status_id == "8"

            if is_cancelled:
                counts["cancelled"] += 1
            elif is_completed:
                counts["completed"] += 1
            elif is_in_progress:
                counts["in_progress"] += 1
            else:
                counts["pending"] += 1

        return {
            "status": "ok",
            "data": {
                "pending": counts["pending"],
                "in_progress": counts["in_progress"],
                "completed": counts["completed"],
                "cancelled": counts["cancelled"],
                "total": len(deliveries),
            },
        }
    except Exception as e:
        logger.error(f"Error fetching shop summary: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch shop summary")


@router.get("/shop/{store_id}", response_model=list[DeliveryOrderResponse])
async def get_shop_deliveries(
    store_id: str,
    query: Optional[str] = Query(None, description="Search query for filtering deliveries"),
    limit: int = Query(10, description="Maximum number of deliveries to return"),
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Get delivery orders by company scope (path param kept as store_id for backward compatibility)."""
    company_id = store_id
    try:
        deliveries = repo.get_by_shop_id_paginated(company_id, cursor=query, limit=limit)
        # get details for each delivery and merge into response
        response = []
        for delivery in deliveries:
            detail = get_orders_by_sales_numbers([delivery.sales_number])
            delivery_response = DeliveryOrderResponse.model_validate(delivery)
            if detail:
                detail_item = dict(detail[0])
                # Keep both IDs available in payload while company_id remains the main scope.
                detail_item.setdefault("company_id", delivery.store_id)
                delivery_response.detail = detail_item
            response.append(delivery_response)
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching shop deliveries: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch shop deliveries")