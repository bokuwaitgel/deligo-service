from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from schemas.database.delivery_db import DeliveryAddressChange

logger = logging.getLogger(__name__)


class AddressChangeRepository:
    """Append-only access to the delivery address audit log."""

    def __init__(self, db_session: Session):
        self.db_session = db_session

    def record(self, entry: DeliveryAddressChange) -> DeliveryAddressChange:
        self.db_session.add(entry)
        self.db_session.commit()
        self.db_session.refresh(entry)
        return entry

    def list_for_sales_id(self, sales_id: str, limit: int = 50) -> List[DeliveryAddressChange]:
        return (
            self.db_session.query(DeliveryAddressChange)
            .filter(DeliveryAddressChange.sales_id == str(sales_id))
            .order_by(DeliveryAddressChange.created_at.desc())
            .limit(limit)
            .all()
        )

    def latest_for_sales_id(self, sales_id: str) -> Optional[DeliveryAddressChange]:
        return (
            self.db_session.query(DeliveryAddressChange)
            .filter(DeliveryAddressChange.sales_id == str(sales_id))
            .order_by(DeliveryAddressChange.created_at.desc())
            .first()
        )
