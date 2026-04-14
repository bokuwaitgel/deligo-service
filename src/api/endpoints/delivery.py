from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from schemas.delivery import (
    AddressUpdateRequest,
    DeliveryOrderCreate,
    DeliveryOrderResponse,
    Location,
)
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
from src.services.middleware_order import get_order_detail, get_orders_by_sales_numbers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/delivery", tags=["delivery"])



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
    """Get a delivery order by sales number."""
    try:
        delivery_order = get_delivery(repo, sales_number)
        if not delivery_order:
            raise HTTPException(status_code=404, detail="Delivery order not found")
        detail = get_order_detail(delivery_order.sales_number)
        if detail:
            delivery_order.detail = detail
        return delivery_order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching delivery order: {e}")
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
        detail = get_order_detail(delivery_order.sales_number)
        if detail:
            delivery_order.detail = detail
        #get driver orders where 
        driver_deliveries = await get_driver_deliveries(driver_id=delivery_order.driver_id, query=None, limit=100, repo=repo)
        #where detail is_closed is not 1 or True, and sales_number is not the current one, and map_status is not completed
        active_deliveries = [d for d in driver_deliveries if d.detail and d.detail.get("is_closed") not in [1, True]]
        delivery_order.active_deliveries_count = len(active_deliveries)
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
    query: Optional[str] = Query(None, description="Search query for filtering deliveries"),
    limit: int = Query(10, description="Maximum number of deliveries to return"),
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Get delivery orders assigned to a driver. This is a placeholder for the actual driver dashboard functionality."""
    try:
        deliveries = repo.get_by_driver_id_paginated(driver_id, cursor=query, limit=limit)
        #get details for each delivery and merge into response
        response = []
        for delivery in deliveries:
            detail = get_orders_by_sales_numbers([delivery.sales_number])
            delivery_response = DeliveryOrderResponse.model_validate(delivery)
            if detail:
                delivery_response.detail = detail[0]
            response.append(delivery_response)
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
    """Get summary counts for a shop's deliveries, deriving status from order service detail."""
    try:
        deliveries = repo.get_by_shop_id_paginated(store_id, cursor=None, limit=10000)
        sales_numbers = [d.sales_number for d in deliveries]
        details_map: dict = {}
        if sales_numbers:
            raw = get_orders_by_sales_numbers(sales_numbers)
            details_map = {d["sales_number"]: d for d in raw}

        counts = {"pending": 0, "in_progress": 0, "completed": 0, "cancelled": 0}
        for delivery in deliveries:
            detail = details_map.get(delivery.sales_number, {})
            is_closed = detail.get("is_closed", 0)
            is_start_driver = detail.get("is_start_driver")
            if is_closed == 1 or is_closed is True:
                counts["completed"] += 1
            elif is_start_driver is not None and is_start_driver is not False and is_start_driver != 0:
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
    """Get delivery orders assigned to a shop. This is a placeholder for the actual shop dashboard functionality."""
    try:
        deliveries = repo.get_by_shop_id_paginated(store_id, cursor=query, limit=limit)
        #get details for each delivery and merge into response
        response = []
        for delivery in deliveries:
            detail = get_orders_by_sales_numbers([delivery.sales_number])
            delivery_response = DeliveryOrderResponse.model_validate(delivery)
            if detail:
                delivery_response.detail = detail[0]
            response.append(delivery_response)
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching shop deliveries: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch shop deliveries")