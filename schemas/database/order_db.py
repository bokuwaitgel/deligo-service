from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Index, String, Text, func
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class OrderDB(Base):
    __tablename__ = "orders"

    sales_number = Column(String, primary_key=True)
    store_id = Column(String, nullable=False, index=True)
    driver_id = Column(String, nullable=False, index=True)
    total_price = Column(Float, nullable=False)
    sales_id = Column(String, nullable=False, unique=True)
    delivery_date = Column(String, nullable=True)
    driver_name = Column(String, nullable=True)
    store_name = Column(String, nullable=True)
    route_number = Column(String, nullable=True)
    status = Column(String, nullable=True, default="pending")
    location_raw = Column(Text, nullable=True)
    location = Column(JSON, nullable=True)
    exchange_sales_id = Column(String, nullable=True)
    return_sales_id = Column(String, nullable=True)
    is_pay = Column(Boolean, nullable=True, default=False)
    is_closed = Column(Boolean, nullable=True, default=False)
    is_country = Column(Boolean, nullable=True, default=False)
    is_download = Column(Boolean, nullable=True, default=False)
    is_integration = Column(Boolean, nullable=True, default=False)
    is_start_driver = Column(Boolean, nullable=True, default=False)
    is_countryside = Column(Boolean, nullable=True, default=False)
    is_direct_pay = Column(Boolean, nullable=True, default=False)
    status_name = Column(String, nullable=True)
    status_color = Column(String, nullable=True)
    wfm_status_id = Column(String, nullable=True)
    customer_phone = Column(String, nullable=True)
    url = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_orders_status", "status"),
        Index("ix_orders_delivery_date", "delivery_date"),
    )
