from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from schemas.order import Location
from src.services.location import (
    geocode_address,
    parse_frontend_location,
    reverse_geocode,
)

router = APIRouter(prefix="/api/location", tags=["location"])


@router.get("/reverse", response_model=Location)
def reverse_geocode_endpoint(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
):
    """Reverse geocode coordinates to get address details."""
    try:
        return reverse_geocode(lat, lng)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/geocode", response_model=Location)
def geocode_endpoint(
    address: str = Query(..., description="Address string to geocode"),
):
    """Geocode an address string to get coordinates and details."""
    try:
        return geocode_address(address)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class FrontendLocationInput(BaseModel):
    formattedAddress: str | None = None
    streetAddress: str | None = None
    city: str | None = None
    state: str | None = None
    district: str | None = None
    khoroo: str | None = None
    country: str | None = None
    postalCode: str | None = None
    building: str | None = None
    coordinates: dict | None = None  # {lat: float, lng: float}


@router.post("/parse", response_model=Location)
def parse_frontend_location_endpoint(data: FrontendLocationInput):
    """Parse a frontend Google location object into a standardized Location."""
    return parse_frontend_location(data.model_dump())
