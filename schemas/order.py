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

class OrderItem(BaseModel):
    item_id: str = Field(..., description="Unique identifier for the item")
    name: str = Field(..., description="Name of the item")
    image_url: Optional[str] = Field(None, description="URL of the item's image")
    quantity: int = Field(..., description="Quantity of the item in the order")
    price: float = Field(..., description="Price of a single unit of the item")


class OrderBase(BaseModel):
    sales_number: str = Field(..., description="Unique sales number for the order")
    store_id: str = Field(..., description="Unique store ID for the order")
    driver_id: str = Field(..., description="Unique driver ID for the order")
    total_price: float = Field(..., description="Total price of the order")
    sales_id: str = Field(..., description="Unique sales ID for the order")
    delivery_date: Optional[str] = Field(None, description="Scheduled delivery date for the order")
    driver_name: Optional[str] = Field(None, description="Name of the driver assigned to the order")
    store_name: Optional[str] = Field(None, description="Name of the store from which the order is being delivered")
    route_number: Optional[str] = Field(None, description="Route number for the delivery")
    status: Optional[OrderStatus] = Field(OrderStatus.PENDING, description="Current status of the order")
    location_raw: Optional[str] = Field(None, description="Raw location string as provided in the order data")
    location: Optional[Location] = Field(None, description="Geocoded location information for the delivery address")
    location_editable: bool = Field(True, description="Indicates whether the location can be edited by the driver or store")
    location_confirmed: bool = Field(False, description="Whether the location has been confirmed by shop or driver")
    location_confirmed_by: Optional[str] = Field(None, description="Who confirmed: 'shop' or 'driver'")
    location_confirmed_at: Optional[str] = Field(None, description="When location was confirmed")
    exchange_sales_id: Optional[str] = Field(None, description="Unique sales ID for the exchange order, if applicable")
    return_sales_id: Optional[str] = Field(None, description="Unique sales ID for the return order, if applicable")
    is_pay: Optional[bool] = Field(False, description="Indicates whether the order has been paid for")
    is_closed: Optional[bool] = Field(False, description="Indicates whether the order has been closed")
    is_country: Optional[bool] = Field(False, description="Indicates whether the order is for a country delivery")
    is_download: Optional[bool] = Field(False, description="Indicates whether the order has been downloaded")
    is_integration: Optional[bool] = Field(False, description="Indicates whether the order is integrated with an external system")
    is_start_driver: Optional[bool] = Field(False, description="Indicates whether the driver has started the delivery")
    is_countryside: Optional[bool] = Field(False, description="Indicates whether the delivery is to a countryside area")
    is_direct_pay: Optional[bool] = Field(False, description="Indicates whether the payment is made directly to the driver")
    order_items: Optional[List[OrderItem]] = Field(None, description="List of items included in the order, if available")
    status_name: Optional[str] = Field(None, description="Human-readable name for the current status of the order")
    status_color: Optional[str] = Field(None, description="Color code representing the current status of the order")
    wfm_status_id: Optional[str] = Field(None, description="ID for the order status in the WFM system")
    customer_phone: Optional[str] = Field(None, description="Phone number of the customer for the order")
    url: Optional[str] = Field(None, description="Tracking URL for the order")


class OrderCreate(BaseModel):
    sales_number: str = Field(..., description="Unique sales number for the order")
    store_id: str = Field(..., description="Unique store ID for the order")
    driver_id: str = Field(..., description="Unique driver ID for the order")
    total_price: float = Field(..., description="Total price of the order", alias="total")
    sales_id: str = Field(..., description="Unique sales ID for the order")
    delivery_date: Optional[str] = Field(None, description="Scheduled delivery date")
    driver_name: Optional[str] = Field(None, description="Name of the driver")
    store_name: Optional[str] = Field(None, description="Name of the store")
    route_number: Optional[str] = Field(None, description="Route number for the delivery")
    customer_address: Optional[str] = Field(None, description="Raw customer address for geocoding")
    exchange_sales_id: Optional[str] = Field(None, description="Exchange order sales ID")
    return_sales_id: Optional[str] = Field(None, description="Return order sales ID")
    is_pay: Optional[bool] = Field(False)
    is_closed: Optional[bool] = Field(False)
    is_country: Optional[bool] = Field(False)
    is_download: Optional[bool] = Field(False)
    is_integration: Optional[bool] = Field(False)
    is_start_driver: Optional[bool] = Field(False)
    is_countryside: Optional[bool] = Field(False)
    is_direct_pay: Optional[bool] = Field(False)
    order_items: Optional[List[OrderItem]] = Field(None)
    status_name: Optional[str] = Field(None)
    status_color: Optional[str] = Field(None)
    wfm_status_id: Optional[str] = Field(None)
    customer_phone: Optional[str] = Field(None)

    model_config = {"populate_by_name": True}


class OrderUpdate(BaseModel):
    store_id: Optional[str] = None
    driver_id: Optional[str] = None
    total_price: Optional[float] = None
    delivery_date: Optional[str] = None
    driver_name: Optional[str] = None
    store_name: Optional[str] = None
    route_number: Optional[str] = None
    status: Optional[OrderStatus] = None
    location_raw: Optional[str] = None
    exchange_sales_id: Optional[str] = None
    return_sales_id: Optional[str] = None
    is_pay: Optional[bool] = None
    is_closed: Optional[bool] = None
    is_country: Optional[bool] = None
    is_download: Optional[bool] = None
    is_integration: Optional[bool] = None
    is_start_driver: Optional[bool] = None
    is_countryside: Optional[bool] = None
    is_direct_pay: Optional[bool] = None
    order_items: Optional[List[OrderItem]] = None
    status_name: Optional[str] = None
    status_color: Optional[str] = None
    wfm_status_id: Optional[str] = None
    customer_phone: Optional[str] = None
    url: Optional[str] = None
    location_confirmed: Optional[bool] = None
    location_confirmed_by: Optional[str] = None
    location_confirmed_at: Optional[str] = None

# New schemas for additional operations
class DriverAssign(BaseModel):
    driver_id: str = Field(..., description="Unique driver ID to assign")
    driver_name: str = Field(..., description="Name of the driver")


class StatusUpdate(BaseModel):
    status: OrderStatus = Field(..., description="New order status")


class LocationConfirm(BaseModel):
    confirmed_by: str = Field(..., description="Who confirmed: 'shop' or 'driver'")
    location: Optional[Location] = Field(None, description="Updated location if provided")


class DriverLocation(BaseModel):
    latitude: float = Field(..., description="Current latitude of the driver")
    longitude: float = Field(..., description="Current longitude of the driver")


class DriverLocationResponse(BaseModel):
    driver_id: str = Field(..., description="Unique driver ID")
    latitude: float = Field(..., description="Last known latitude")
    longitude: float = Field(..., description="Last known longitude")
    updated_at: str = Field(..., description="Timestamp of last update")


class StoreSummary(BaseModel):
    pending: int = Field(0, description="Number of pending orders")
    in_progress: int = Field(0, description="Number of in-progress orders")
    completed: int = Field(0, description="Number of completed orders")
    cancelled: int = Field(0, description="Number of cancelled orders")
    total: int = Field(0, description="Total number of orders")
