from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from schemas.delivery import (
    AddressUpdateRequest,
    DeliveryOrderCreate,
    DeliveryOrderResponse,
    EtaUpdateRequest,
    Location,
    LocationUpdateRequest,
    MapStatusUpdateRequest,
)
from schemas.database.delivery_db import DeliveryOrder
from src.api.auth_utils import require_api_key
from src.dependencies import get_delivery_repository
from src.repositories.delivery import DeliveryRepository
from src.services.delivery import (
    _build_tracking_url,
    _geocode,
    complete_delivery,
    create_delivery,
    get_delivery,
    update_location,
    update_location_by_address,
)
from src.services.deligo_integration import ACTIVE_STATUS_CODES, change_sales_status, get_driver_sales, get_sales_detail, get_status_description_service, structure_sales_detail
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


def _as_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _route_position(detail: dict) -> Optional[int]:
    return (
        _as_int(detail.get("route_number"))
        or _as_int(detail.get("route_no"))
        or _as_int(detail.get("sort_order"))
    )


def _upsert_local_delivery_from_detail(
    repo: DeliveryRepository,
    detail: dict,
    driver_id: Optional[str] = None,
) -> Optional[DeliveryOrder]:
    sales_number = _as_str(detail.get("sales_number"))
    if not sales_number:
        return None

    sales_id = _as_str(detail.get("sales_id"))
    store_id = _as_str(detail.get("store_id")) or "unknown"
    company_id = _as_str(detail.get("company_id"))
    resolved_driver_id = driver_id or _as_str(detail.get("driver_id"))
    customer_address = (
        _as_str(detail.get("customer_address"))
        or _as_str(detail.get("address"))
        or "Address not provided"
    )
    customer_location = _extract_customer_location(detail)

    existing = repo.get_by_sales_number(sales_number)

    # Geocode only if Deligo API gave no coordinates AND DB has no location yet.
    if customer_location is None and customer_address and customer_address != "Address not provided":
        if existing is None or existing.customer_location is None:
            is_countryside = bool(detail.get("is_country") in {1, True, "1", "true"})
            customer_location = _geocode(customer_address, is_countryside=is_countryside)

    if existing is None:
        created = DeliveryOrder(
            sales_number=sales_number,
            sales_id=sales_id,
            store_id=store_id,
            company_id=company_id,
            driver_id=resolved_driver_id,
            customer_address=customer_address,
            customer_location=customer_location,
            tracking_url=_build_tracking_url(sales_number),
            map_status="pending",
        )
        try:
            return repo.create(created)
        except IntegrityError:
            # Row was inserted concurrently (or hidden from the earlier bulk fetch by
            # a stale session). Recover instead of 500-ing — re-fetch and patch below.
            repo.db_session.rollback()
            existing = repo.get_by_sales_number(sales_number)
            if existing is None:
                raise

    patch: dict[str, object] = {}
    if not existing.sales_id and sales_id:
        patch["sales_id"] = sales_id
    if (not existing.store_id or existing.store_id == "unknown") and store_id:
        patch["store_id"] = store_id
    if not existing.company_id and company_id:
        patch["company_id"] = company_id
    # Deligo is source of truth for driver assignment — overwrite on reassignment.
    if resolved_driver_id and existing.driver_id != resolved_driver_id:
        patch["driver_id"] = resolved_driver_id
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


@router.get("/active-drivers", dependencies=[Depends(require_api_key)])
async def get_active_drivers(
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Return drivers that currently have active deliveries, joined with their last known location.

    Response: list of { driver_id, active_count, latitude?, longitude?, location_updated_at? }
    Used by the admin panel to build the driver sidebar without fetching every driver's orders.
    """
    from schemas.database.driver_location_db import DriverLocationDB

    try:
        summaries = repo.get_active_driver_summary()
        if not summaries:
            return []

        driver_ids = [s["driver_id"] for s in summaries]
        loc_rows: list[DriverLocationDB] = (
            repo.db_session.query(DriverLocationDB)  # type: ignore[attr-defined]
            .filter(DriverLocationDB.driver_id.in_(driver_ids))
            .all()
        )
        loc_map = {r.driver_id: r for r in loc_rows}

        result = []
        for s in summaries:
            loc = loc_map.get(s["driver_id"])
            result.append({
                "driver_id": s["driver_id"],
                "active_count": s["active_count"],
                "latitude": loc.latitude if loc else None,
                "longitude": loc.longitude if loc else None,
                "location_updated_at": loc.updated_at.isoformat() if loc else None,
            })
        return result
    except Exception as e:
        logger.error("Error fetching active drivers: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch active drivers")


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
            detail = get_sales_detail(delivery_order.sales_id, use_service_auth=True)
            if detail:
                delivery_order.detail = detail  # type: ignore[attr-defined]
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
            detail = get_sales_detail(delivery_order.sales_id, use_service_auth=True)
            if detail:
                delivery_order.detail = detail  # type: ignore[attr-defined]
        return delivery_order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching delivery order by sales_id: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch delivery order")
    
@router.post("/location", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
async def update_delivery_location(
    body: LocationUpdateRequest,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Update delivery location with pre-geocoded coordinates."""
    existing = repo.get_by_sales_id(body.sales_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Delivery order not found")
    location = Location(**body.model_dump(exclude={"sales_id"}))
    try:
        updated_order = update_location(repo, existing.sales_number, location)
        if not updated_order:
            raise HTTPException(status_code=404, detail="Delivery order not found or already completed")
        return updated_order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating delivery location: {e}")
        raise HTTPException(status_code=500, detail="Failed to update delivery location")

@router.post("/address", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
async def update_delivery_address(
    address_update: AddressUpdateRequest,
    is_countryside: bool = Query(False, description="Skip Mongolia/UB suffix in geocoding"),
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Update delivery location by re-geocoding a new address."""
    existing = repo.get_by_sales_id(address_update.sales_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Delivery order not found")
    try:
        updated_order = update_location_by_address(repo, existing.sales_number, address_update.customer_address, is_countryside)
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


@router.post("/map_status", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
async def set_delivery_map_status(
    payload: MapStatusUpdateRequest,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Set delivery map_status explicitly (pending/completed) from shop workflow."""
    existing = repo.get_by_sales_id(payload.sales_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Delivery order not found")
    try:
        updated_order = repo.update_partial(existing.sales_number, {"map_status": payload.map_status.value})
        if not updated_order:
            raise HTTPException(status_code=404, detail="Delivery order not found")
        return updated_order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating map status: {e}")
        raise HTTPException(status_code=500, detail="Failed to update map status")


@router.post("/eta", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
async def update_delivery_eta(
    payload: EtaUpdateRequest,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Driver view calls this to persist the calculated ETA (in minutes) for a delivery."""
    existing = repo.get_by_sales_id(payload.sales_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Delivery order not found")
    updated = repo.update_partial(existing.sales_number, {"eta_minutes": payload.eta_minutes})
    if not updated:
        raise HTTPException(status_code=404, detail="Delivery order not found")
    return updated


class _DeleteDeliveryRequest(BaseModel):
    sales_id: str = Field(..., description="Sales ID of the delivery order")


@router.post("/delete", dependencies=[Depends(require_api_key)])
async def delete_delivery_order(
    payload: _DeleteDeliveryRequest,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Soft-delete a delivery order by setting map_status to 'deleted'."""
    existing = repo.get_by_sales_id(payload.sales_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Delivery order not found")
    updated = repo.update_partial(existing.sales_number, {"map_status": "deleted"})
    if not updated:
        raise HTTPException(status_code=404, detail="Delivery order not found")
    return {"status": "deleted", "sales_number": existing.sales_number, "sales_id": payload.sales_id}

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
        if delivery_order.map_status == "deleted":
            raise HTTPException(status_code=404, detail="Delivery order not found")
        detail = get_sales_detail(delivery_order.sales_id, use_service_auth=True) if delivery_order.sales_id else None
        if detail:
            delivery_order.detail = detail  # type: ignore[attr-defined]

        # If location is missing, geocode and persist it now.
        if delivery_order.customer_location is None and delivery_order.customer_address and delivery_order.customer_address != "Address not provided":
            is_countryside = bool((detail or {}).get("is_country") in {1, True, "1", "true"})
            geocoded = _geocode(delivery_order.customer_address, is_countryside=is_countryside)
            if geocoded:
                repo.update_partial(sales_number, {"customer_location": geocoded})
                delivery_order = delivery_order.model_copy(update={"customer_location": geocoded})

        # For driver-only sub-statuses fetch the saved proof (text + image) so the
        # customer can see why the delivery wasn't completed on this attempt.
        _DRIVER_ONLY_WITH_PROOF = {13, 14, 15, 16, 17, 23}
        wfm_id = _as_int((detail or {}).get("wfm_status_id"))
        if wfm_id in _DRIVER_ONLY_WITH_PROOF and delivery_order.sales_id:
            try:
                status_desc = get_status_description_service(str(delivery_order.sales_id), wfm_id)
                if status_desc and (status_desc.get("description") or status_desc.get("file_path")):
                    merged = dict(getattr(delivery_order, "detail", None) or {})
                    merged["status_description"] = status_desc
                    delivery_order.detail = merged  # type: ignore[attr-defined]
            except Exception as _sd_err:
                logger.warning("Failed to fetch status description for tracking: %s", _sd_err)

        # Use local DeliveryOrder as source of truth for queue position and active count.
        # sort_order (1-indexed) is the driver's confirmed delivery sequence.
        driver_id = _as_str((detail or {}).get("driver_id")) or _as_str(getattr(delivery_order, "driver_id", None))
        if delivery_order.sort_order is not None:
            delivery_order.deliveries_before_mine = delivery_order.sort_order - 1
        if driver_id is not None:
            active_rows = repo.get_active_by_driver_id(driver_id)
            delivery_order.active_deliveries_count = len(active_rows)
        logger.info(
            "[Track] sort_order=%s deliveries_before_mine=%s active_deliveries_count=%s",
            delivery_order.sort_order,
            delivery_order.deliveries_before_mine,
            delivery_order.active_deliveries_count,
        )
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
        sales = get_driver_sales(driver_id, page_size=limit, use_service_auth=True)
        if not sales:
            return []

        details_by_number: dict[str, dict] = {}
        for s in sales:
            sn = s.get("sales_number")
            if isinstance(sn, str) and sn:
                details_by_number[sn] = structure_sales_detail(s)

        sales_numbers = list(details_by_number.keys())
        # Build deleted set first so we don't re-surface soft-deleted orders.
        all_local_rows = {d.sales_number: d for d in repo.get_by_sales_numbers(sales_numbers)}
        deleted_numbers = {sn for sn, row in all_local_rows.items() if row.map_status == "deleted"}
        local_rows = {sn: row for sn, row in all_local_rows.items() if row.map_status != "deleted"}

        # Ensure every non-deleted Deligo order has a local delivery row (for map/location workflows).
        for sales_number in sales_numbers:
            if sales_number in deleted_numbers:
                continue
            existing = local_rows.get(sales_number)
            if existing is None:
                # New order — always upsert (may geocode).
                created = _upsert_local_delivery_from_detail(repo, details_by_number[sales_number], driver_id=driver_id)
                if created is not None:
                    local_rows[sales_number] = created
            else:
                # Existing order — only patch if something meaningful is missing.
                needs_patch = (
                    (not existing.driver_id and driver_id) or
                    existing.customer_location is None
                )
                if needs_patch:
                    refreshed = _upsert_local_delivery_from_detail(repo, details_by_number[sales_number], driver_id=driver_id)
                    if refreshed is not None:
                        local_rows[sales_number] = refreshed

        # Persist sync membership + latest WFM status code per sales_number.
        status_by_sn: dict[str, Optional[str]] = {
            sn: d.get("status_code") for sn, d in details_by_number.items()
        }
        active_sns: set[str] = set(status_by_sn.keys())
        repo.sync_driver_active_status(driver_id, active_sns, status_by_sn)

        response: list[DeliveryOrderResponse] = []
        for sales_number in sales_numbers:
            local = local_rows.get(sales_number)
            if local is None:
                continue
            item = DeliveryOrderResponse.model_validate(local)
            item = item.model_copy(update={"detail": details_by_number[sales_number]})
            response.append(item)
        response.sort(key=lambda r: (
            r.status in ACTIVE_STATUS_CODES,
            r.sort_order is None,
            r.sort_order if r.sort_order is not None else 0,
        ))
        return [r for r in response if r.sync_active]
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
                delivery_response = delivery_response.model_copy(update={"detail": detail_item})
            response.append(delivery_response)
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching shop deliveries: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch shop deliveries")