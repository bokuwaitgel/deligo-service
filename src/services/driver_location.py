from __future__ import annotations

import logging
from typing import Optional

from schemas.database.driver_location_db import DriverLocationDB
from src.repositories.driver_location import DriverLocationRepository

logger = logging.getLogger(__name__)


def upsert_driver_location(
    repo: DriverLocationRepository,
    driver_id: str,
    latitude: float,
    longitude: float
) -> DriverLocationDB:
    """Create or update driver location."""
    return repo.upsert(driver_id, latitude, longitude)


def get_driver_location(
    repo: DriverLocationRepository,
    driver_id: str
) -> Optional[DriverLocationDB]:
    """Get driver's last known location."""
    return repo.get_by_driver_id(driver_id)
