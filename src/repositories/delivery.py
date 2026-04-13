from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from schemas.database.delivery_db import DeliveryOrder

logger = logging.getLogger(__name__)


class DeliveryRepository:
    def __init__(self, db_session: Session):
        self.db_session = db_session

    def get_by_sales_number(self, sales_number: str) -> Optional[DeliveryOrder]:
        return (
            self.db_session.query(DeliveryOrder)
            .filter(DeliveryOrder.sales_number == sales_number)
            .first()
        )

    def create(self, order: DeliveryOrder) -> DeliveryOrder:
        self.db_session.add(order)
        self.db_session.commit()
        self.db_session.refresh(order)
        return order

    def update_partial(self, sales_number: str, data: Dict[str, Any]) -> Optional[DeliveryOrder]:
        order = self.get_by_sales_number(sales_number)
        if order is None:
            return None
        for key, value in data.items():
            if hasattr(order, key):
                setattr(order, key, value)
        self.db_session.commit()
        self.db_session.refresh(order)
        return order

    def delete(self, sales_number: str) -> bool:
        order = self.get_by_sales_number(sales_number)
        if order is None:
            return False
        self.db_session.delete(order)
        self.db_session.commit()
        return True

    def get_by_driver_id_paginated(
        self, driver_id: str, cursor: Optional[str], limit: int
    ) -> List[DeliveryOrder]:
        query = (
            self.db_session.query(DeliveryOrder)
            .filter(DeliveryOrder.driver_id == driver_id)
            .order_by(DeliveryOrder.created_at.desc(), DeliveryOrder.sales_number.desc())
        )
        if cursor:
            anchor = self.get_by_sales_number(cursor)
            if anchor is not None and anchor.created_at is not None:
                query = query.filter(
                    (DeliveryOrder.created_at < anchor.created_at)
                    | (
                        (DeliveryOrder.created_at == anchor.created_at)
                        & (DeliveryOrder.sales_number < cursor)
                    )
                )
        return query.limit(limit + 1).all()

    def get_by_sales_numbers(self, sales_numbers: List[str]) -> List[DeliveryOrder]:
        """Fetch full order rows for a list of sales numbers in one query."""
        return (
            self.db_session.query(DeliveryOrder)
            .filter(DeliveryOrder.sales_number.in_(sales_numbers))
            .all()
        )

    def get_existing_sales_numbers(self, sales_numbers: List[str]) -> List[str]:
        """Return which of the given sales_numbers already exist in the DB."""
        rows = (
            self.db_session.query(DeliveryOrder.sales_number)
            .filter(DeliveryOrder.sales_number.in_(sales_numbers))
            .all()
        )
        return [r.sales_number for r in rows]

    def get_by_shop_id_paginated(
        self, store_id: str, cursor: Optional[str], limit: int
    ) -> List[DeliveryOrder]:
        query = (
            self.db_session.query(DeliveryOrder)
            .filter(DeliveryOrder.store_id == store_id)
            .order_by(DeliveryOrder.created_at.desc(), DeliveryOrder.sales_number.desc())
        )
        if cursor:
            anchor = self.get_by_sales_number(cursor)
            if anchor is not None and anchor.created_at is not None:
                query = query.filter(
                    (DeliveryOrder.created_at < anchor.created_at)
                    | (
                        (DeliveryOrder.created_at == anchor.created_at)
                        & (DeliveryOrder.sales_number < cursor)
                    )
                )
        return query.limit(limit + 1).all()
