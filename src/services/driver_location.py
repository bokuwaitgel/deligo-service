from __future__ import annotations

import logging
import math
import os
from typing import Optional

from schemas.database.driver_location_db import DriverLocationDB
from src.repositories.delivery import DeliveryRepository
from src.repositories.driver_location import DriverLocationRepository
from src.services.deligo_integration import OPEN_STATUS_CODES
from src.services.events import publish_order_event

logger = logging.getLogger(__name__)

# How close the driver must get before the customer is told "жолооч ойртож байна".
DRIVER_NEARBY_METERS = float(os.getenv("DRIVER_NEARBY_METERS", "1000"))


def _distance_meters(
    lat1: float, lng1: float, lat2: float, lng2: float
) -> float:
    radius = 6371000.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def _format_distance(meters: float) -> str:
    if meters < 1000:
        return f"{int(round(meters / 50.0) * 50)} м"
    return f"{meters / 1000:.1f} км"


def _order_coords(order) -> Optional[tuple[float, float]]:
    location = order.customer_location
    if not isinstance(location, dict):
        return None
    try:
        return float(location["latitude"]), float(location["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


def upsert_driver_location(
    repo: DriverLocationRepository, driver_id: str, latitude: float, longitude: float
) -> DriverLocationDB:
    return repo.upsert(driver_id, latitude, longitude)


def announce_driver_movement(
    delivery_repo: DeliveryRepository,
    driver_id: str,
    previous: Optional[tuple[float, float]],
    latitude: float,
    longitude: float,
) -> None:
    """Notify the customers of this driver's open orders that the van moved.

    Emits ``driver_nearby`` only on the *crossing* of DRIVER_NEARBY_METERS —
    computed from the driver's previously stored coordinates, which live in the
    shared database rather than in process memory. That keeps the "notify once"
    decision identical no matter which API replica handles the update, so a
    customer never gets the same "жолооч ойртож байна" from four workers
    (requirement 3.2.5).

    Never raises: a GPS ping must still be stored if notification fails.
    """
    try:
        orders = [
            order for order in delivery_repo.get_active_by_driver_id(str(driver_id))
            if order.status in OPEN_STATUS_CODES
        ]
    except Exception:
        logger.warning("Could not load driver %s orders for movement events", driver_id, exc_info=True)
        return

    for order in orders:
        coords = _order_coords(order)
        if coords is None:
            continue
        dest_lat, dest_lng = coords
        distance = _distance_meters(latitude, longitude, dest_lat, dest_lng)

        crossed_into_range = (
            distance <= DRIVER_NEARBY_METERS
            and (
                previous is None
                or _distance_meters(previous[0], previous[1], dest_lat, dest_lng) > DRIVER_NEARBY_METERS
            )
        )

        publish_order_event(
            str(order.sales_id),
            "driver_nearby" if crossed_into_range else "driver_location",
            {
                "sales_number": order.sales_number,
                "driver_id": str(driver_id),
                "latitude": latitude,
                "longitude": longitude,
                "distance_meters": round(distance),
                "distance_text": _format_distance(distance),
            },
        )


def get_driver_location(
    repo: DriverLocationRepository, driver_id: str
) -> DriverLocationDB | None:
    return repo.get_by_driver_id(driver_id)
