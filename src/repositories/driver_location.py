from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from schemas.database.driver_location_db import DriverLocationDB

logger = logging.getLogger(__name__)


class DriverLocationRepository:
    def __init__(self, db_session: Session):
        self.db_session = db_session

    def get_by_driver_id(self, driver_id: str) -> Optional[DriverLocationDB]:
        return (
            self.db_session.query(DriverLocationDB)
            .filter(DriverLocationDB.driver_id == driver_id)
            .first()
        )

    def upsert(self, driver_id: str, latitude: float, longitude: float) -> DriverLocationDB:
        """Create or update driver location"""
        location = self.get_by_driver_id(driver_id)
        if location:
            location.latitude = latitude
            location.longitude = longitude
        else:
            location = DriverLocationDB(
                driver_id=driver_id,
                latitude=latitude,
                longitude=longitude
            )
            self.db_session.add(location)
        
        self.db_session.commit()
        self.db_session.refresh(location)
        return location
