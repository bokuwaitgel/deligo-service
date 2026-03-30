from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from schemas.database.order_db import OrderDB

logger = logging.getLogger(__name__)


class OrderRepository:
    def __init__(self, db_session: Session):
        self.db_session = db_session

    def get_by_sales_number(self, sales_number: str) -> Optional[OrderDB]:
        return (
            self.db_session.query(OrderDB)
            .filter(OrderDB.sales_number == sales_number)
            .first()
        )

    def get_by_driver_id(self, driver_id: str) -> List[OrderDB]:
        return (
            self.db_session.query(OrderDB)
            .filter(OrderDB.driver_id == driver_id)
            .all()
        )

    def get_by_store_id(self, store_id: str) -> List[OrderDB]:
        return (
            self.db_session.query(OrderDB)
            .filter(OrderDB.store_id == store_id)
            .all()
        )

    def get_all(self) -> List[OrderDB]:
        return self.db_session.query(OrderDB).all()

    def create(self, order: OrderDB) -> OrderDB:
        self.db_session.add(order)
        self.db_session.commit()
        self.db_session.refresh(order)
        return order

    def create_bulk(self, orders: List[OrderDB]) -> List[OrderDB]:
        self.db_session.add_all(orders)
        self.db_session.commit()
        for order in orders:
            self.db_session.refresh(order)
        return orders

    def update_partial(self, sales_number: str, data: Dict[str, Any]) -> Optional[OrderDB]:
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
