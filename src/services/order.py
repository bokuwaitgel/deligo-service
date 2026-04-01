from __future__ import annotations

import logging
import os
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
        is_countryside=_parse_bool(raw.get("is_countryside")),
        is_direct_pay=_parse_bool(raw.get("is_direct_pay")),
        status_name=raw.get("status_name"),
        status_color=raw.get("status_color"),
        wfm_status_id=str(raw["wfm_status_id"]) if raw.get("wfm_status_id") is not None else None,
        customer_phone=raw.get("customer_phone"),
        order_items=[item if isinstance(item, dict) else item.model_dump() for item in raw["order_items"]] if raw.get("order_items") else None,
    )


def _geocode_and_attach(order: OrderDB) -> None:
    """Geocode location_raw and attach the result as JSONB."""
    if order.location_raw is None:
        return
    try:
        location = str(order.location_raw)
        if not getattr(order, "is_countryside", False) :
            location += ", Mongolia, Ulaanbaatar"

        loc: Location = geocode_address(location)
        order.location = loc.model_dump(mode='json') #type: ignore
    except Exception:
        logger.warning("Geocoding failed for order %s: %s", order.sales_number, order.location_raw, exc_info=True)


def _build_tracking_url(sales_number: str) -> str | None:
    """Build a tracking URL from the TRACKING_URL_PREFIX env var."""
    prefix = os.getenv("TRACKING_URL_PREFIX")
    if not prefix:
        return None
    return f"{prefix}{sales_number}"


def create_order(repo: OrderRepository, raw: Dict[str, Any]) -> OrderDB:
    """Create a single order from raw data, geocoding the address."""
    order = _map_raw_to_order(raw)
    _geocode_and_attach(order)
    order.url = _build_tracking_url(str(order.sales_number))  # type: ignore
    print(f"Creating order {order.sales_number} with geocoded location: {order.location}")  # Debug log
    print(f"Tracking URL for order {order.sales_number}: {order.url}")  # Debug log
    return repo.create(order)


def create_orders_bulk(repo: OrderRepository, raw_list: List[Dict[str, Any]]) -> List[OrderDB]:
    """Create multiple orders from raw data, geocoding each address."""
    orders: List[OrderDB] = []
    for raw in raw_list:
        order = _map_raw_to_order(raw)
        _geocode_and_attach(order)
        order.url = _build_tracking_url(str(order.sales_number))  # type: ignore
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
    print(f"Updating order {sales_number} with data: {data}")  # Debug log

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


def assign_driver_to_order(repo: OrderRepository, sales_number: str, driver_id: str, driver_name: str) -> OrderDB | None:
    """Assign a driver to an order and set status to in_progress."""
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None
    
    data = {
        'driver_id': driver_id,
        'driver_name': driver_name,
        'status': 'in_progress'
    }
    return repo.update_partial(sales_number, data)


def update_order_status(repo: OrderRepository, sales_number: str, status: str) -> OrderDB | None:
    """Update order status."""
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None
    
    return repo.update_partial(sales_number, {'status': status})


def confirm_order_location(repo: OrderRepository, sales_number: str, confirmed_by: str, location: Location | None = None) -> OrderDB | None:
    """Confirm order location by shop or driver."""
    from datetime import datetime, timezone as tz
    
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None
    
    data = {
        'location_confirmed': True,
        'location_confirmed_by': confirmed_by,
        'location_confirmed_at': datetime.now(tz.utc)
    }
    
    # If location is provided, update it
    if location:
        data['location'] = location.model_dump(mode='json')
    else:
        # No new location provided — ensure existing location has coordinates
        existing = order.location
        has_coords = (
            isinstance(existing, dict)
            and existing.get('latitude') is not None
            and existing.get('longitude') is not None
        )
        if not has_coords:
            # Try to geocode from location_raw
            _geocode_and_attach(order)
            if order.location:
                data['location'] = order.location
    
    return repo.update_partial(sales_number, data)


def get_store_summary(repo: OrderRepository, store_id: str) -> Dict[str, int]:
    """Get count summary of orders by status for a store."""
    pending = repo.count_by_store_and_status(store_id, 'pending')
    in_progress = repo.count_by_store_and_status(store_id, 'in_progress')
    completed = repo.count_by_store_and_status(store_id, 'completed')
    cancelled = repo.count_by_store_and_status(store_id, 'cancelled')
    
    return {
        'pending': pending,
        'in_progress': in_progress,
        'completed': completed,
        'cancelled': cancelled,
        'total': pending + in_progress + completed + cancelled
    }
