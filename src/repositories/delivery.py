from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func
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

    def get_by_sales_id(self, sales_id: str) -> Optional[DeliveryOrder]:
        return (
            self.db_session.query(DeliveryOrder)
            .filter(DeliveryOrder.sales_id == sales_id)
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

    def get_by_sales_numbers(self, sales_numbers: List[str], exclude_deleted: bool = False) -> List[DeliveryOrder]:
        """Fetch full order rows for a list of sales numbers in one query."""
        query = (
            self.db_session.query(DeliveryOrder)
            .filter(DeliveryOrder.sales_number.in_(sales_numbers))
        )
        if exclude_deleted:
            query = query.filter(DeliveryOrder.map_status != "deleted")
        return query.all()

    def get_existing_sales_numbers(self, sales_numbers: List[str]) -> List[str]:
        """Return which of the given sales_numbers already exist in the DB."""
        rows = (
            self.db_session.query(DeliveryOrder.sales_number)
            .filter(DeliveryOrder.sales_number.in_(sales_numbers))
            .all()
        )
        return [r.sales_number for r in rows]

    def get_max_sort_order_for_driver(self, driver_id: str) -> Optional[int]:
        return (
            self.db_session.query(func.max(DeliveryOrder.sort_order))
            .filter(DeliveryOrder.driver_id == driver_id)
            .scalar()
        )

    def set_driver_sort_orders(self, driver_id: str, sales_numbers: List[str]) -> int:
        rows = (
            self.db_session.query(DeliveryOrder)
            .filter(
                DeliveryOrder.driver_id == driver_id,
                DeliveryOrder.sales_number.in_(sales_numbers),
            )
            .all()
        )
        rows_by_sales_number = {row.sales_number: row for row in rows}

        updated_count = 0
        for index, sales_number in enumerate(sales_numbers):
            row = rows_by_sales_number.get(sales_number)
            if row is None:
                continue
            if row.sort_order != index:
                row.sort_order = index
                updated_count += 1

        if updated_count:
            self.db_session.commit()

        return len(rows_by_sales_number)

    def get_active_by_driver_id(self, driver_id: str) -> List[DeliveryOrder]:
        """Return all status='active' rows for a driver (excluding deleted map entries)."""
        return (
            self.db_session.query(DeliveryOrder)
            .filter(
                DeliveryOrder.driver_id == driver_id,
                DeliveryOrder.status == 'active',
                DeliveryOrder.map_status != 'deleted',
            )
            .all()
        )

    def sync_driver_active_status(self, driver_id: str, active_sales_numbers: set) -> None:
        """After a Deligo sync: mark rows in active_sales_numbers as 'active',
        mark all other rows for this driver as 'inactive'."""
        rows = (
            self.db_session.query(DeliveryOrder)
            .filter(
                DeliveryOrder.driver_id == driver_id,
                DeliveryOrder.map_status != 'deleted',
            )
            .all()
        )
        changed = False
        for row in rows:
            desired = 'active' if row.sales_number in active_sales_numbers else 'inactive'
            if row.status != desired:
                row.status = desired
                changed = True
        if changed:
            self.db_session.commit()
        logger.debug(
            "sync_driver_active_status: driver=%s active=%d total_rows=%d",
            driver_id, len(active_sales_numbers), len(rows),
        )

    def get_by_shop_id_paginated(
        self, store_id: str, cursor: Optional[str], limit: int
    ) -> List[DeliveryOrder]:
        query = (
            self.db_session.query(DeliveryOrder)
            .filter(
                DeliveryOrder.company_id == store_id,
                DeliveryOrder.map_status != "deleted",
            )
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
