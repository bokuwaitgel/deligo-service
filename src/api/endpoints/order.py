from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from schemas.order import OrderUpdate
from src.api.auth_utils import require_api_key
from src.dependencies import get_order_repository
from src.repositories.order import OrderRepository
from src.services.order import (
    create_order,
    create_orders_bulk,
    delete_order,
    get_all_orders_paginated,
    get_order,
    get_orders_by_driver_paginated,
    get_orders_by_store_paginated,
    update_order,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("/", dependencies=[Depends(require_api_key)])
def create_order_endpoint(
    raw: Dict[str, Any],
    repo: OrderRepository = Depends(get_order_repository),
):
    order = create_order(repo, raw)
    return {"status": "ok", "data": {"sales_number": order.sales_number, 'url': order.url}}


@router.post("/bulk", dependencies=[Depends(require_api_key)])
def create_orders_bulk_endpoint(
    raw_list: List[Dict[str, Any]],
    repo: OrderRepository = Depends(get_order_repository),
):
    orders = create_orders_bulk(repo, raw_list)
    return {
        "status": "ok",
        "data": {
            "count": len(orders),
            "sales_numbers": [o.sales_number for o in orders],
        },
    }


@router.get("/", dependencies=[Depends(require_api_key)])
def list_orders_endpoint(
    cursor: Optional[str] = None,
    limit: int = 50,
    repo: OrderRepository = Depends(get_order_repository),
):
    limit = min(limit, 200)
    orders, next_cursor, has_more = get_all_orders_paginated(repo, cursor, limit)
    return {"status": "ok", "data": orders, "next_cursor": next_cursor, "has_more": has_more}


@router.get("/driver/{driver_id}", dependencies=[Depends(require_api_key)])
def get_orders_by_driver_endpoint(
    driver_id: str,
    cursor: Optional[str] = None,
    limit: int = 50,
    repo: OrderRepository = Depends(get_order_repository),
):
    limit = min(limit, 200)
    orders, next_cursor, has_more = get_orders_by_driver_paginated(repo, driver_id, cursor, limit)
    return {"status": "ok", "data": orders, "next_cursor": next_cursor, "has_more": has_more}


@router.get("/store/{store_id}", dependencies=[Depends(require_api_key)])
def get_orders_by_store_endpoint(
    store_id: str,
    cursor: Optional[str] = None,
    limit: int = 50,
    repo: OrderRepository = Depends(get_order_repository),
):
    limit = min(limit, 200)
    orders, next_cursor, has_more = get_orders_by_store_paginated(repo, store_id, cursor, limit)
    return {"status": "ok", "data": orders, "next_cursor": next_cursor, "has_more": has_more}


@router.get("/track/{sales_number}")
def track_order_endpoint(
    sales_number: str,
    repo: OrderRepository = Depends(get_order_repository),
):
    """Public endpoint for customer order tracking - no API key required."""
    order = get_order(repo, sales_number)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": order}


@router.patch("/track/{sales_number}/location")
def track_update_location_endpoint(
    sales_number: str,
    body: Dict[str, Any],
    repo: OrderRepository = Depends(get_order_repository),
):
    """Public endpoint: customer updates their delivery location (only if editable)."""
    order = get_order(repo, sales_number)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    # Only allow if location hasn't been confirmed yet
    if order.location_confirmed:
        raise HTTPException(status_code=403, detail="Location is no longer editable")
    location_data = body.get("location")
    if not location_data:
        raise HTTPException(status_code=400, detail="Location data required")
    updated = update_order(repo, sales_number, {"location": location_data})
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": updated}


@router.get("/{sales_number}", dependencies=[Depends(require_api_key)])
def get_order_endpoint(
    sales_number: str,
    repo: OrderRepository = Depends(get_order_repository),
):
    order = get_order(repo, sales_number)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": order}


@router.put("/{sales_number}", dependencies=[Depends(require_api_key)])
def update_order_full_endpoint(
    sales_number: str,
    body: OrderUpdate,
    repo: OrderRepository = Depends(get_order_repository),
):
    data = body.model_dump(exclude_unset=False, exclude_none=True)
    updated = update_order(repo, sales_number, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": updated}


@router.patch("/{sales_number}", dependencies=[Depends(require_api_key)])
def update_order_partial_endpoint(
    sales_number: str,
    body: OrderUpdate,
    repo: OrderRepository = Depends(get_order_repository),
):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    updated = update_order(repo, sales_number, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": updated}


@router.delete("/{sales_number}", dependencies=[Depends(require_api_key)])
def delete_order_endpoint(
    sales_number: str,
    repo: OrderRepository = Depends(get_order_repository),
):
    deleted = delete_order(repo, sales_number)
    if not deleted:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "message": "Order deleted"}


@router.patch("/{sales_number}/assign", dependencies=[Depends(require_api_key)])
def assign_driver_endpoint(
    sales_number: str,
    body: Any,
    repo: OrderRepository = Depends(get_order_repository),
):
    """Assign a driver to an order"""
    from schemas.order import DriverAssign
    from src.services.order import assign_driver_to_order
    
    assign_data = DriverAssign(**body)
    updated = assign_driver_to_order(repo, sales_number, assign_data.driver_id, assign_data.driver_name)
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": updated}


@router.patch("/{sales_number}/status", dependencies=[Depends(require_api_key)])
def update_status_endpoint(
    sales_number: str,
    body: Any,
    repo: OrderRepository = Depends(get_order_repository),
):
    """Update order status"""
    from schemas.order import StatusUpdate
    from src.services.order import update_order_status
    
    status_data = StatusUpdate(**body)
    updated = update_order_status(repo, sales_number, status_data.status.value)
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": updated}


@router.patch("/{sales_number}/confirm-location", dependencies=[Depends(require_api_key)])
def confirm_location_endpoint(
    sales_number: str,
    body: Any,
    repo: OrderRepository = Depends(get_order_repository),
):
    """Confirm order location by shop or driver"""
    from schemas.order import LocationConfirm
    from src.services.order import confirm_order_location
    
    confirm_data = LocationConfirm(**body)
    updated = confirm_order_location(repo, sales_number, confirm_data.confirmed_by, confirm_data.location)
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": updated}


@router.get("/store/{store_id}/summary", dependencies=[Depends(require_api_key)])
def get_store_summary_endpoint(
    store_id: str,
    repo: OrderRepository = Depends(get_order_repository),
):
    """Get order count summary for a store"""
    from src.services.order import get_store_summary
    
    summary = get_store_summary(repo, store_id)
    return {"status": "ok", "data": summary}
