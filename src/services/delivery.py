from __future__ import annotations

import logging
import os
from typing import Optional

from schemas.database.delivery_db import DeliveryOrder
from schemas.delivery import DeliveryOrderCreate, DeliveryOrderResponse, Location, MapStatus
from src.repositories.delivery import DeliveryRepository
from src.repositories.driver_location import DriverLocationRepository
from src.services.location import geocode_address

logger = logging.getLogger(__name__)


def _build_tracking_url(sales_number: str) -> Optional[str]:
    prefix = os.getenv("TRACKING_URL_PREFIX")
    return f"{prefix}{sales_number}" if prefix else None


def _geocode(address: str, is_countryside: bool = False) -> Optional[dict]:
    try:
        full_address = address if is_countryside else f"{address}, Mongolia, Ulaanbaatar"
        loc: Location = geocode_address(full_address)
        return loc.model_dump(mode="json")
    except Exception:
        logger.warning("Geocoding failed for address: %s", address, exc_info=True)
        return None


def create_delivery(repo: DeliveryRepository, payload: DeliveryOrderCreate) -> DeliveryOrderResponse:
    """
        Create a new delivery order. Geocodes the address immediately, 
        but allows null location if geocoding fails.
    """
    order = DeliveryOrder(
        sales_number=payload.sales_number,
        sales_id=payload.sales_id,
        store_id=payload.store_id,
        driver_id=payload.driver_id,
        driver_name=payload.driver_name,
        customer_address=payload.customer_address,
        customer_location=_geocode(payload.customer_address, payload.is_countryside),
        tracking_url=_build_tracking_url(payload.sales_number),
        map_status=MapStatus.PENDING,
    )

    if payload.driver_id:
        driver_loc = DriverLocationRepository(repo.db_session)
        if driver_loc.get_by_driver_id(payload.driver_id) is None:
            driver_loc.upsert(payload.driver_id, 47.9184, 106.9175)  # Default to UB center if driver location not found


    return DeliveryOrderResponse.model_validate(repo.create(order))


def get_delivery(repo: DeliveryRepository, sales_number: str) -> Optional[DeliveryOrderResponse]:
    order = repo.get_by_sales_number(sales_number)
    return DeliveryOrderResponse.model_validate(order) if order else None


def update_location(
    repo: DeliveryRepository, sales_number: str, location: Location
) -> Optional[DeliveryOrderResponse]:
    """Update location with pre-built coordinates. Returns None if not found or already completed."""
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None
    if order.map_status == MapStatus.COMPLETED:
        return None
    updated = repo.update_partial(sales_number, {"customer_location": location.model_dump(mode="json")})
    return DeliveryOrderResponse.model_validate(updated)


def update_location_by_address(
    repo: DeliveryRepository, sales_number: str, address: str, is_countryside: bool = False
) -> Optional[DeliveryOrderResponse]:
    """Re-geocode a new address string and update. Returns None if not found, completed, or geocoding fails."""
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None
    if order.map_status == MapStatus.COMPLETED:
        return None
    location_data = _geocode(address, is_countryside)
    if location_data is None:
        raise ValueError(f"Geocoding failed for address: {address}")
    updated = repo.update_partial(sales_number, {
        "customer_address": address,
        "customer_location": location_data,
    })
    return DeliveryOrderResponse.model_validate(updated)


def complete_delivery(repo: DeliveryRepository, sales_number: str) -> Optional[DeliveryOrderResponse]:
    """Lock the delivery location — no further edits allowed after this."""
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None
    updated = repo.update_partial(sales_number, {"map_status": MapStatus.COMPLETED})
    return DeliveryOrderResponse.model_validate(updated)
