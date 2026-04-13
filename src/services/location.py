from __future__ import annotations

import logging
import os
import re

import googlemaps
from dotenv import load_dotenv

from schemas.delivery import Building, Location

load_dotenv()

logger = logging.getLogger(__name__)

_gmaps: googlemaps.Client | None = None


def _get_client() -> googlemaps.Client:
    global _gmaps
    if _gmaps is None:
        key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not key:
            raise RuntimeError("GOOGLE_MAPS_API_KEY is not set")
        _gmaps = googlemaps.Client(key=key)
    return _gmaps


def _extract_component(components: list[dict], type_name: str) -> str | None:
    """Extract a value from Google address_components by type."""
    for comp in components:
        if type_name in comp.get("types", []):
            return comp.get("long_name")
    return None


def _extract_khoroo(district: str | None) -> str | None:
    """Extract khoroo number from district string like 'CHD - 4 khoroo'."""
    if not district:
        return None
    match = re.search(r"(\d+)\s*(?:khoroo|хороо)", district, re.IGNORECASE)
    if match:
        return match.group(1)
    # Also try standalone number pattern like "4-р хороо"
    match = re.search(r"(\d+)-?\s*(?:р\s+)?(?:khoroo|хороо)", district, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def parse_geocode_result(result: dict) -> Location:
    """Parse a single Google Geocoding API result into a Location."""
    components = result.get("address_components", [])
    geometry = result.get("geometry", {})
    location = geometry.get("location", {})

    city = (
        _extract_component(components, "locality")
        or _extract_component(components, "administrative_area_level_1")
    )

    # For Mongolia, district info often comes in sublocality or neighborhood
    district = (
        _extract_component(components, "sublocality_level_1")
        or _extract_component(components, "sublocality")
        or _extract_component(components, "neighborhood")
        or _extract_component(components, "administrative_area_level_2")
    )

    khoroo = _extract_khoroo(district)

    # Try to find building/premise info
    premise = _extract_component(components, "premise")
    building = Building(building=premise, entrance=None, floor=None, door=None, extra_notes=None) if premise else None

    street_number = _extract_component(components, "street_number") or ""
    route = _extract_component(components, "route") or ""
    street_address = f"{street_number} {route}".strip() or None

    return Location(
        latitude=location.get("lat", 0.0),
        longitude=location.get("lng", 0.0),
        formatted_address=result.get("formatted_address"),
        street_address=street_address,
        city=city,
        state=_extract_component(components, "administrative_area_level_1"),
        district=district,
        khoroo=khoroo,
        country=_extract_component(components, "country"),
        postal_code=_extract_component(components, "postal_code"),
        building=building,
    )


def parse_frontend_location(data: dict) -> Location:
    """Parse the frontend's pre-parsed Google location object into a Location."""
    coords = data.get("coordinates", {})
    building_name = data.get("building")
    building = Building(building=building_name) if building_name else None # pyright: ignore[reportCallIssue]

    return Location(
        latitude=coords.get("lat", 0.0),
        longitude=coords.get("lng", 0.0),
        formatted_address=data.get("formattedAddress"),
        street_address=data.get("streetAddress"),
        city=data.get("city"),
        state=data.get("state"),
        district=data.get("district"),
        khoroo=data.get("khoroo"),
        country=data.get("country"),
        postal_code=data.get("postalCode"),
        building=building,
    )


def reverse_geocode(lat: float, lng: float) -> Location:
    """Reverse geocode coordinates using Google Maps API."""
    client = _get_client() # type: ignore
    results = client.reverse_geocode((lat, lng)) # type: ignore

    if not results:
        return Location(
            latitude=lat,
            longitude=lng,
            formatted_address=None,
            street_address=None,
            city=None,
            state=None,
            district=None,
            khoroo=None,
            country=None,
            postal_code=None,
            building=None,
        )

    return parse_geocode_result(results[0])


def geocode_address(address: str) -> Location:
    """Geocode an address string using Google Maps API."""
    client = _get_client()
    results = client.geocode(address) # type: ignore

    if not results:
        raise ValueError(f"No results found for address: {address}")

    return parse_geocode_result(results[0])
