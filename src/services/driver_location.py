from __future__ import annotations

from schemas.database.driver_location_db import DriverLocationDB
from src.repositories.driver_location import DriverLocationRepository


def upsert_driver_location(
    repo: DriverLocationRepository, driver_id: str, latitude: float, longitude: float
) -> DriverLocationDB:
    return repo.upsert(driver_id, latitude, longitude)


def get_driver_location(
    repo: DriverLocationRepository, driver_id: str
) -> DriverLocationDB | None:
    return repo.get_by_driver_id(driver_id)
