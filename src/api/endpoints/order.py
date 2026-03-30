from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from schemas.order import OrderUpdate
from src.api.auth_utils import require_api_key
from src.dependencies import get_order_repository
from src.repositories.order import OrderRepository
from src.services.order import (
    create_order,
    create_orders_bulk,
    delete_order,
    get_order,
    get_orders_by_driver,
    get_orders_by_store,
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
    return {"status": "ok", "data": {"sales_number": order.sales_number}}


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


@router.get("/driver/{driver_id}", dependencies=[Depends(require_api_key)])
def get_orders_by_driver_endpoint(
    driver_id: str,
    repo: OrderRepository = Depends(get_order_repository),
):
    orders = get_orders_by_driver(repo, driver_id)
    return {"status": "ok", "data": orders}


@router.get("/store/{store_id}", dependencies=[Depends(require_api_key)])
def get_orders_by_store_endpoint(
    store_id: str,
    repo: OrderRepository = Depends(get_order_repository),
):
    orders = get_orders_by_store(repo, store_id)
    return {"status": "ok", "data": orders}
