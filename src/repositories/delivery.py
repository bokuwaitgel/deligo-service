from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from schemas.database.delivery_db import DeliveryOrder
from src.services.deligo_integration import ACTIVE_STATUS_CODES

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

    def find_location_by_address(self, customer_address: str) -> Optional[Dict[str, Any]]:
        """Return any previously geocoded location stored for this exact address.

        Acts as a free DB-level geocode cache: if any past delivery row has the same
        customer_address and a non-null customer_location, reuse it instead of
        calling Google. customer_address is indexed so the lookup is cheap.
        """
        if not customer_address:
            return None
        row = (
            self.db_session.query(DeliveryOrder.customer_location)
            .filter(
                DeliveryOrder.customer_address == customer_address,
                DeliveryOrder.customer_location.isnot(None),
            )
            .first()
        )
        return row.customer_location if row is not None else None

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

    def set_driver_sort_orders(self, driver_id: str, sales_numbers: List[str]) -> int:
        """Set per-driver sort_order from a positional list.

        Loads ALL of the driver's rows (not just ones in the payload) so any row
        omitted from sales_numbers has its sort_order cleared to NULL. Without
        this clearing step, a partial reorder would leave stale indexes from a
        previous larger ranking, creating duplicate sort_orders and gaps.

        Returns the count of sales_numbers from the payload that matched a row.
        """
        rows = (
            self.db_session.query(DeliveryOrder)
            .filter(DeliveryOrder.driver_id == driver_id)
            .all()
        )
        desired: Dict[str, int] = {sn: idx for idx, sn in enumerate(sales_numbers)}

        changed = False
        matched = 0
        for row in rows:
            new_order = desired.get(row.sales_number)
            if row.sort_order != new_order:
                row.sort_order = new_order
                changed = True
            if row.sales_number in desired:
                matched += 1

        if changed:
            self.db_session.commit()

        return matched

    def get_active_by_driver_id(self, driver_id: str) -> List[DeliveryOrder]:
        """Return in-progress rows for a driver: in the driver's current sync AND
        the WFM status code marks the order as open/in-progress."""
        return (
            self.db_session.query(DeliveryOrder)
            .filter(
                DeliveryOrder.driver_id == driver_id,
                DeliveryOrder.sync_active.is_(True),
                DeliveryOrder.status.in_(ACTIVE_STATUS_CODES),
                DeliveryOrder.map_status != 'deleted',
            )
            .all()
        )

    def sync_driver_active_status(
        self,
        driver_id: str,
        active_sales_numbers: set,
        status_by_sn: Dict[str, Optional[str]],
    ) -> None:
        """After a Deligo sync: set sync_active for each row, and persist the
        latest WFM status code returned for it.

        - sync_active = True iff the row's sales_number is in active_sales_numbers
          (i.e. it's still on the driver's list in the latest Deligo response,
          regardless of WFM status).
        - status = status_by_sn[sales_number] when present (the latest WFM status
          code from Deligo, e.g. "salesDelivery"). Rows not in the dict keep their
          previous status (the order is no longer in the driver's sync).
        """
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
            desired_active = row.sales_number in active_sales_numbers
            if row.sync_active != desired_active:
                row.sync_active = desired_active
                changed = True
            if row.sales_number in status_by_sn:
                desired_status = status_by_sn[row.sales_number]
                if row.status != desired_status:
                    row.status = desired_status
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

    def get_active_driver_summary(self) -> List[Dict[str, Any]]:
        """Return one row per driver that currently has active (non-deleted) deliveries.

        Each row: { driver_id, active_count }
        """
        rows = (
            self.db_session.query(
                DeliveryOrder.driver_id,
                func.count(DeliveryOrder.sales_number).label("active_count"),
            )
            .filter(
                DeliveryOrder.driver_id.isnot(None),
                DeliveryOrder.sync_active.is_(True),
                DeliveryOrder.status.in_(ACTIVE_STATUS_CODES),
                DeliveryOrder.map_status != "deleted",
            )
            .group_by(DeliveryOrder.driver_id)
            .all()
        )
        return [{"driver_id": r.driver_id, "active_count": r.active_count} for r in rows]
