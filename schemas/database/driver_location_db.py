from sqlalchemy import Column, DateTime, Float, String, func
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class DriverLocationDB(Base):
    __tablename__ = "driver_locations"

    driver_id = Column(String, primary_key=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
