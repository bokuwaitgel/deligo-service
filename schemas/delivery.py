from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MapStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"


class Building(BaseModel):
    building: Optional[str] = None
    entrance: Optional[str] = None
    floor: Optional[str] = None
    door: Optional[str] = None
    extra_notes: Optional[str] = None


class Location(BaseModel):
    latitude: float
    longitude: float
    formatted_address: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    khoroo: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    building: Optional[Building] = None


class DriverLocation(BaseModel):
    latitude: float = Field(..., description="Current latitude of the driver")
    longitude: float = Field(..., description="Current longitude of the driver")


class DriverLocationResponse(BaseModel):
    driver_id: str
    latitude: float
    longitude: float
    updated_at: str


class DeliveryOrderCreate(BaseModel):
    sales_number: str = Field(..., description="Unique sales number from the order service")
    sales_id: Optional[str] = Field(None, description="Sales ID from the order service")
    store_id: str = Field(..., description="Store/shop ID")
    customer_address: str = Field(..., description="Raw address string to geocode")
    is_countryside: bool = Field(False, description="Skip Mongolia/UB suffix in geocoding")


class AddressUpdateRequest(BaseModel):
    customer_address: str = Field(..., description="New address string to re-geocode")


class DeliveryOrderResponse(BaseModel):
    model_config = {"from_attributes": True}

    sales_number: str
    sales_id: Optional[str] = None
    store_id: str
    customer_address: str
    customer_location: Optional[Location] = None
    map_status: MapStatus
    detail: Optional[dict] = None  # Merged order detail from deligo integration
    tracking_url: Optional[str] = None
    active_deliveries_count: Optional[int] = None
    created_at: datetime
    updated_at: datetime
