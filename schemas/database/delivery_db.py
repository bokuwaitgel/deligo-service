from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DeliveryOrder(Base):
    __tablename__ = "delivery_orders"

    sales_number: Mapped[str] = mapped_column(String, primary_key=True)
    sales_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    store_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    driver_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    driver_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    customer_address: Mapped[str] = mapped_column(Text, nullable=False)
    customer_location: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    map_status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    tracking_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
