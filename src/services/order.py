from __future__ import annotations

import logging
from typing import Any, Dict, List

from schemas.database.order_db import OrderDB
from schemas.order import Location
from src.repositories.order import OrderRepository
from src.services.location import geocode_address

logger = logging.getLogger(__name__)


def _parse_bool(value: Any) -> bool:
    """Parse various bool representations from raw data."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str):
        return value.strip() not in ("0", "", "false", "False")
    return False


def _map_raw_to_order(raw: Dict[str, Any]) -> OrderDB:
    """Map raw order data to an OrderDB instance (location left as None)."""
    return OrderDB(
        sales_number=str(raw["sales_number"]),
        store_id=str(raw["store_id"]),
        driver_id=str(raw["driver_id"]),
        total_price=float(raw.get("total", 0)),
        sales_id=str(raw["sales_id"]),
        delivery_date=raw.get("delivery_date"),
        driver_name=raw.get("driver_name"),
        store_name=raw.get("store_name"),
        route_number=str(raw["route_number"]) if raw.get("route_number") is not None else None,
        status="pending",
        location_raw=raw.get("customer_address"),
        location=None,
        exchange_sales_id=str(raw["exchange_sales_id"]) if raw.get("exchange_sales_id") else None,
        return_sales_id=str(raw["return_sales_id"]) if raw.get("return_sales_id") else None,
        is_pay=_parse_bool(raw.get("is_pay")),
        is_closed=_parse_bool(raw.get("is_closed")),
        is_country=_parse_bool(raw.get("is_country")),
        is_download=_parse_bool(raw.get("is_download")),
        is_integration=_parse_bool(raw.get("is_integration")),
        is_start_driver=_parse_bool(raw.get("is_start_driver")),
        is_direct_pay=_parse_bool(raw.get("is_direct_pay")),
        status_name=raw.get("status_name"),
        status_color=raw.get("status_color"),
        wfm_status_id=str(raw["wfm_status_id"]) if raw.get("wfm_status_id") is not None else None,
        customer_phone=raw.get("customer_phone"),
    )


def _geocode_and_attach(order: OrderDB) -> None:
    """Geocode location_raw and attach the result as JSONB."""
    if order.location_raw is None:
        return
    try:
        loc: Location = geocode_address(str(order.location_raw))
        order.location = loc.model_dump(mode='json')
    except Exception:
        logger.warning("Geocoding failed for order %s: %s", order.sales_number, order.location_raw, exc_info=True)


def create_order(repo: OrderRepository, raw: Dict[str, Any]) -> OrderDB:
    """Create a single order from raw data, geocoding the address."""
    order = _map_raw_to_order(raw)
    _geocode_and_attach(order)
    return repo.create(order)


def create_orders_bulk(repo: OrderRepository, raw_list: List[Dict[str, Any]]) -> List[OrderDB]:
    """Create multiple orders from raw data, geocoding each address."""
    orders: List[OrderDB] = []
    for raw in raw_list:
        order = _map_raw_to_order(raw)
        _geocode_and_attach(order)
        orders.append(order)
    return repo.create_bulk(orders)


def get_order(repo: OrderRepository, sales_number: str) -> OrderDB | None:
    return repo.get_by_sales_number(sales_number)


def get_orders_by_driver(repo: OrderRepository, driver_id: str) -> List[OrderDB]:
    return repo.get_by_driver_id(driver_id)


def get_orders_by_store(repo: OrderRepository, store_id: str) -> List[OrderDB]:
    return repo.get_by_store_id(store_id)


def update_order(repo: OrderRepository, sales_number: str, data: Dict[str, Any]) -> OrderDB | None:
    """Update an order. Re-geocodes if location_raw changed."""
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None

    old_location_raw = order.location_raw
    new_location_raw = data.get("location_raw")

    updated = repo.update_partial(sales_number, data)

    if updated and new_location_raw and new_location_raw != old_location_raw:
        _geocode_and_attach(updated)
        repo.update_partial(sales_number, {"location": updated.location})

    return updated


def delete_order(repo: OrderRepository, sales_number: str) -> bool:
    """Delete an order by sales_number. Returns True if deleted, False if not found."""
    return repo.delete(sales_number)


def get_orders_by_driver_paginated(
    repo: OrderRepository, driver_id: str, cursor: str | None, limit: int
) -> tuple[List[OrderDB], str | None, bool]:
    """Returns (orders, next_cursor, has_more)."""
    rows = repo.get_by_driver_id_paginated(driver_id, cursor, limit)
    has_more = len(rows) > limit
    orders = rows[:limit]
    next_cursor = str(orders[-1].sales_number) if has_more and orders else None
    return orders, next_cursor, has_more


def get_orders_by_store_paginated(
    repo: OrderRepository, store_id: str, cursor: str | None, limit: int
) -> tuple[List[OrderDB], str | None, bool]:
    """Returns (orders, next_cursor, has_more)."""
    rows = repo.get_by_store_id_paginated(store_id, cursor, limit)
    has_more = len(rows) > limit
    orders = rows[:limit]
    next_cursor = str(orders[-1].sales_number) if has_more and orders else None
    return orders, next_cursor, has_more


def get_all_orders_paginated(
    repo: OrderRepository, cursor: str | None, limit: int
) -> tuple[List[OrderDB], str | None, bool]:
    """Returns (orders, next_cursor, has_more)."""
    rows = repo.get_all_paginated(cursor, limit)
    has_more = len(rows) > limit
    orders = rows[:limit]
    next_cursor = str(orders[-1].sales_number) if has_more and orders else None
    return orders, next_cursor, has_more
