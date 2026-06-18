from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

    def get_all(self) -> list[DriverLocationDB]:
        return self.db_session.query(DriverLocationDB).order_by(DriverLocationDB.updated_at.desc()).all()

    def upsert(self, driver_id: str, latitude: float, longitude: float) -> DriverLocationDB:
        """Create or update driver location atomically (no read-then-insert race)."""
        stmt = (
            pg_insert(DriverLocationDB)
            .values(driver_id=driver_id, latitude=latitude, longitude=longitude)
            .on_conflict_do_update(
                index_elements=[DriverLocationDB.driver_id],
                set_={
                    "latitude": latitude,
                    "longitude": longitude,
                    "updated_at": func.now(),
                },
            )
            .returning(DriverLocationDB)
        )
        result = self.db_session.execute(stmt).scalar_one()
        self.db_session.commit()
        return result
