from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DeliveryOrder(Base):
    __tablename__ = "delivery_orders"

    # Composite index for the hot read paths in DeliveryRepository:
    # get_active_by_driver_id and get_active_driver_summary both filter on
    # (driver_id, sync_active, status, map_status). Lead column is driver_id
    # (most selective); map_status is queried with `!= 'deleted'` and so
    # benefits less from being in the index — kept out to keep the index narrow.
    __table_args__ = (
        Index("ix_delivery_active_by_driver", "driver_id", "sync_active", "status"),
    )

    # sales_id is the order identity / primary key. sales_number is a
    # human-facing order code from the order service that CAN repeat across
    # orders, so it is a plain indexed column — never an identity key.
    sales_id: Mapped[str] = mapped_column(String, primary_key=True)
    sales_number: Mapped[str] = mapped_column(String, nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    company_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    driver_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    eta_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    customer_address: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    customer_location: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    sync_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    map_status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    tracking_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
