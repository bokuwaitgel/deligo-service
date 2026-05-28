from __future__ import annotations

import logging
import os
from typing import Optional

from schemas.database.delivery_db import DeliveryOrder
from schemas.delivery import DeliveryOrderCreate, DeliveryOrderResponse, Location, MapStatus
from src.repositories.delivery import DeliveryRepository
from src.services.deligo_integration import push_address_update
from src.services.geocode_quota import consume_geocode_quota
from src.services.location import geocode_address, reverse_geocode

logger = logging.getLogger(__name__)


def _build_tracking_url(sales_number: str) -> Optional[str]:
    return f"/track/{sales_number}"

def _geocode(address: str, is_countryside: bool = False) -> Optional[dict]:
    try:
        full_address = address if is_countryside else f"монгол улс, улаанбаатар хот, {address}"
        print(f"Geocoding address: {full_address}")
        loc: Location = geocode_address(full_address)
        return loc.model_dump(mode="json")
    except Exception:
        logger.warning("Geocoding failed for address: %s", address, exc_info=True)
        return None


def _reverse_geocode(latitude: float, longitude: float, fallback_address: str | None = None) -> Optional[dict]:
    try:
        loc: Location = reverse_geocode(latitude, longitude)
        payload = loc.model_dump(mode="json")
        if fallback_address and not payload.get("formatted_address"):
            payload["formatted_address"] = fallback_address
        return payload
    except Exception:
        logger.warning(
            "Reverse geocoding failed for coordinates: (%s, %s)",
            latitude,
            longitude,
            exc_info=True,
        )
        return {
            "latitude": latitude,
            "longitude": longitude,
            "formatted_address": fallback_address,
            "street_address": None,
            "city": None,
            "state": None,
            "district": None,
            "khoroo": None,
            "country": None,
            "postal_code": None,
            "building": None,
        }


def create_delivery(repo: DeliveryRepository, payload: DeliveryOrderCreate) -> DeliveryOrderResponse:
    """
        Create a new delivery order. Geocodes the address immediately, 
        but allows null location if geocoding fails.
    """
    explicit_location = None
    if payload.latitude is not None and payload.longitude is not None:
        explicit_location = _reverse_geocode(
            payload.latitude,
            payload.longitude,
            fallback_address=payload.customer_address,
        )

    order = DeliveryOrder(
        sales_number=payload.sales_number,
        sales_id=payload.sales_id,
        store_id=payload.store_id,
        company_id=payload.company_id,
        customer_address=payload.customer_address,
        customer_location=explicit_location or _geocode(payload.customer_address, payload.is_countryside),
        tracking_url=_build_tracking_url(payload.sales_number),
        map_status=MapStatus.PENDING,
    )

    #before creating, check if sales_number already exists to avoid duplicates
    existing = repo.get_by_sales_number(payload.sales_number)
    if existing:
        # return existing order if already exists
        order = repo.get_by_sales_number(payload.sales_number)
        return DeliveryOrderResponse.model_validate(order)

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
    if updated and order.sales_id:
        address = location.formatted_address or order.customer_address or ""
        extra_notes = location.building.extra_notes if location.building else None
        push_address_update(str(order.sales_id), address, location.latitude, location.longitude, extra_notes=extra_notes)
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
    # DB-cache: ижил хаягийг сүүлд геокод хийсэн бол давтан Google дуудалгүй ашиглана.
    location_data = repo.find_location_by_address(address)
    if location_data is None:
        # Жолооч / захиалгад тус бүрд өдрийн квот хэтрээгүй эсэхийг шалгана.
        # Driver_id байхгүй (захиалга шинэ) үед sales_number-аар лимит барина.
        quota_key = order.driver_id or order.sales_number
        if not consume_geocode_quota(quota_key):
            raise ValueError(
                f"Geocode quota exhausted for {quota_key}; try again later"
            )
        location_data = _geocode(address, is_countryside)
    if location_data is None:
        raise ValueError(f"Geocoding failed for address: {address}")
    updated = repo.update_partial(sales_number, {
        "customer_address": address,
        "customer_location": location_data,
    })
    if updated and order.sales_id:
        lat = location_data.get("latitude")
        lng = location_data.get("longitude")
        if lat is not None and lng is not None:
            push_address_update(str(order.sales_id), address, float(lat), float(lng))
    return DeliveryOrderResponse.model_validate(updated)


def complete_delivery(repo: DeliveryRepository, sales_number: str) -> Optional[DeliveryOrderResponse]:
    """Lock the delivery location — no further edits allowed after this."""
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None
    updated = repo.update_partial(sales_number, {"map_status": MapStatus.COMPLETED})
    return DeliveryOrderResponse.model_validate(updated)
