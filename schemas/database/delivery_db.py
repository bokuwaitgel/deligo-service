from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text, func
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
    # When the "your order is N deliveries away" notification went out for this
    # order. Persisted rather than kept in memory because the alert must fire
    # exactly once and every API replica evaluates the same queue — an
    # in-process flag would send one notification per worker.
    queue_alert_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Denormalized "who last moved the pin, and when". The full history lives in
    # DeliveryAddressChange; these three columns exist so the order detail can
    # show the attribution without a second query on every read.
    location_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    location_updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location_updated_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DeliveryAddressChange(Base):
    """Append-only audit log of delivery address / pin edits.

    Written on every accepted address change (driver re-pin, customer edit,
    shop edit) so a moved pin can always be traced back to who moved it, when,
    and from which coordinates — the delivery_orders row only keeps the current
    value. Never updated or deleted; a correction is a new row.
    """

    __tablename__ = "delivery_address_changes"

    __table_args__ = (
        Index("ix_address_change_sales_id_created", "sales_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sales_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sales_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # 'driver' | 'customer' | 'shop' | 'system'
    changed_by_role: Mapped[str] = mapped_column(String, nullable=False, default="system")
    changed_by_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    changed_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    previous_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    previous_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    previous_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    new_longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    new_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Distance the pin moved, in metres — lets an operator spot suspicious edits
    # without recomputing haversine over the whole log.
    moved_meters: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
