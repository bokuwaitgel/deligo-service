from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum

class OrderStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class Building(BaseModel):
    building: Optional[str] = Field(None, description="Name or number of the building")
    entrance: Optional[str] = Field(None, description="Entrance information for the building")
    floor: Optional[str] = Field(None, description="Floor number of the building")
    door: Optional[str] = Field(None, description="Door number of the building")
    extra_notes: Optional[str] = Field(None, description="Additional notes about the building or delivery instructions")


class Location(BaseModel):
    latitude: float = Field(..., description="Latitude of the location")
    longitude: float = Field(..., description="Longitude of the location")
    formatted_address: Optional[str] = Field(None, description="Full formatted address string")
    street_address: Optional[str] = Field(None, description="Street address or plus code")
    city: Optional[str] = Field(None, description="City of the location")
    state: Optional[str] = Field(None, description="State or province")
    district: Optional[str] = Field(None, description="District of the location")
    khoroo: Optional[str] = Field(None, description="Khoroo of the location")
    country: Optional[str] = Field(None, description="Country name")
    postal_code: Optional[str] = Field(None, description="Postal code")
    building: Optional[Building] = Field(None, description="Building information for the location")


class OrderBase(BaseModel):
    customer_id: str = Field(..., description="ID of the customer placing the order")
    items: List[str] = Field(..., description="List of item IDs included in the order")
    delivery_location: Location = Field(..., description="Delivery location for the order")
    status: OrderStatus = Field(..., description="Current status of the order")
    total_price: float = Field(..., description="Total price of the order")
