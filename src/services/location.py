from __future__ import annotations

import logging
import os
import re
from typing import Any, cast

import googlemaps
import httpx
from dotenv import load_dotenv

from schemas.delivery import Building, Location

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# District normalization — mirrors frontend geocoding.ts
# ---------------------------------------------------------------------------
_DISTRICT_MAP_EN: dict[str, str] = {
    "bgd": "Bayangol",
    "sbd": "Sukhbaatar",
    "bzd": "Bayanzurkh",
    "chd": "Chingeltei",
    "khud": "Khan-Uul",
    "hud": "Khan-Uul",
    "shd": "Songinokhairkhan",
    "skhd": "Songinokhairkhan",
    "nad": "Nalaikh",
    "bnd": "Baganuur",
}
_DISTRICT_MAP_MN: dict[str, str] = {
    "бгд": "Bayangol",
    "сбд": "Sukhbaatar",
    "бзд": "Bayanzurkh",
    "чд": "Chingeltei",
    "худ": "Khan-Uul",
    "схд": "Songinokhairkhan",
    "скд": "Songinokhairkhan",
    "нд": "Nalaikh",
    "бд": "Baganuur",
}
_DISTRICT_NAMES_MAP: dict[str, str] = {
    "сүхбаатар": "Sukhbaatar",
    "sukhbaatar": "Sukhbaatar",
    "чингэлтэй": "Chingeltei",
    "chingeltei": "Chingeltei",
    "баянгол": "Bayangol",
    "bayangol": "Bayangol",
    "хан-уул": "Khan-Uul",
    "khan-uul": "Khan-Uul",
    "баянзүрх": "Bayanzurkh",
    "bayanzurkh": "Bayanzurkh",
    "сонгинохайрхан": "Songinokhairkhan",
    "songinokhairkhan": "Songinokhairkhan",
    "налайх": "Nalaikh",
    "nalaikh": "Nalaikh",
    "багахангай": "Bagakhangai",
    "bagakhangai": "Bagakhangai",
    "багануур": "Baganuur",
    "baganuur": "Baganuur",
}


def _normalize_district(raw: str) -> str:
    """Normalize a raw district string to canonical English — mirrors frontend normalizeDistrict()."""
    lower = raw.lower().strip()
    if lower in _DISTRICT_MAP_EN:
        return _DISTRICT_MAP_EN[lower]
    if lower in _DISTRICT_MAP_MN:
        return _DISTRICT_MAP_MN[lower]
    for key, value in _DISTRICT_NAMES_MAP.items():
        if key in lower:
            return value
    return raw[0].upper() + raw[1:] if raw else raw

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


def _extract_khoroo(value: str | None) -> str | None:
    """Extract khoroo number from a string — mirrors frontend parseKhoroo()."""
    if not value:
        return None
    # "1-р хороо", "4 хороо", "1 khoroo"
    match = re.search(r"(\d+)(?:-р)?\s*(?:khoroo|хороо)", value, re.IGNORECASE)
    if match:
        return match.group(1)
    # "khoroo 15" or "хороо 15"
    match = re.search(r"(?:khoroo|хороо)\s*(\d+)", value, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _best_result(results: list[dict]) -> dict:
    """Pick the most detailed result (most address_components)."""
    if not results:
        return {}
    return max(results, key=lambda r: len(r.get("address_components", [])))


def _clean_formatted_address(addr: str | None) -> str | None:
    """Remove country suffix from formatted address for display."""
    if not addr:
        return addr
    for suffix in (", Mongolia", ", Монгол Улс", ", Монгол"):
        if addr.endswith(suffix):
            addr = addr[: -len(suffix)]
    return addr.strip().rstrip(",").strip()


def parse_geocode_result(result: dict) -> Location:
    """Parse a single Google Geocoding API result into a Location."""
    components = result.get("address_components", [])
    geometry = result.get("geometry", {})
    location = geometry.get("location", {})

    # city: locality (Ulaanbaatar) or fallback to level_1
    city = (
        _extract_component(components, "locality")
        or _extract_component(components, "administrative_area_level_1")
    )

    # District: sublocality first (same priority as frontend), then administrative_area_level_2
    sublocality = (
        _extract_component(components, "sublocality")
        or _extract_component(components, "sublocality_level_1")
    )
    admin_level_2 = _extract_component(components, "administrative_area_level_2")

    district_raw = sublocality or admin_level_2 or None
    district = _normalize_district(district_raw) if district_raw else None

    # khoroo: neighborhood first (same priority as frontend), then sublocality fields
    neighborhood = _extract_component(components, "neighborhood")
    khoroo_raw = neighborhood or _extract_component(components, "sublocality_level_1") or _extract_component(components, "sublocality_level_2")
    khoroo = _extract_khoroo(khoroo_raw)
    # Fallback: scan the entire formatted_address (mirrors frontend)
    if not khoroo:
        khoroo = _extract_khoroo(result.get("formatted_address", ""))

    # Building / premise
    premise = _extract_component(components, "premise")
    building = Building(building=premise, entrance=None, floor=None, door=None, extra_notes=None) if premise else None

    # Street address: route first, then street_number — mirrors frontend (route + streetNumber)
    route = _extract_component(components, "route") or ""
    street_number = _extract_component(components, "street_number") or ""
    if route:
        street_address: str | None = f"{route} {street_number}".strip() if street_number else route
    elif premise:
        street_address = premise
    else:
        street_address = None

    return Location(
        latitude=location.get("lat", 0.0),
        longitude=location.get("lng", 0.0),
        formatted_address=_clean_formatted_address(result.get("formatted_address")),
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
    client = cast(Any, _get_client())
    # First try: specific types for detailed address
    results = client.reverse_geocode(
        (lat, lng),
        language="mn",
        result_type=["street_address", "premise", "sublocality", "neighborhood"],
    )
    if not results:
        # Fallback: all types
        results = client.reverse_geocode((lat, lng), language="mn")

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

    return parse_geocode_result(_best_result(results))


_MN_BOUNDS = {
    "southwest": {"lat": 41.5, "lng": 87.7},
    "northeast": {"lat": 52.2, "lng": 119.9},
}


def _is_in_mongolia(result: dict) -> bool:
    """Check that a geocode result is within Mongolia."""
    components = result.get("address_components", [])
    for comp in components:
        if "country" in comp.get("types", []):
            code = comp.get("short_name", "").upper()
            return code == "MN"
    loc = result.get("geometry", {}).get("location", {})
    lat, lng = loc.get("lat", 0), loc.get("lng", 0)
    return (
        _MN_BOUNDS["southwest"]["lat"] <= lat <= _MN_BOUNDS["northeast"]["lat"]
        and _MN_BOUNDS["southwest"]["lng"] <= lng <= _MN_BOUNDS["northeast"]["lng"]
    )


def _location_from_find_place(candidate: dict, original_address: str) -> Location | None:
    """Convert a find_place candidate into a Location via reverse geocode for full details."""
    loc = candidate.get("geometry", {}).get("location", {})
    lat, lng = loc.get("lat"), loc.get("lng")
    if not lat or not lng:
        return None
    if not _is_in_mongolia(candidate):
        # fallback check by coords
        if not (
            _MN_BOUNDS["southwest"]["lat"] <= lat <= _MN_BOUNDS["northeast"]["lat"]
            and _MN_BOUNDS["southwest"]["lng"] <= lng <= _MN_BOUNDS["northeast"]["lng"]
        ):
            return None
    # Use reverse geocode to get full address_components
    try:
        loc_obj = reverse_geocode(lat, lng)
        # Override formatted_address with the original query result if better
        fa = _clean_formatted_address(candidate.get("formatted_address")) or loc_obj.formatted_address
        return loc_obj.model_copy(update={"formatted_address": fa})
    except Exception:
        return Location(
            latitude=lat,
            longitude=lng,
            formatted_address=_clean_formatted_address(candidate.get("formatted_address")),
            street_address=None, city=None, state=None, district=None,
            khoroo=None, country=None, postal_code=None, building=None,
        )


def _ub_district_keywords() -> list[str]:
    return [
        "хан-уул", "khan-uul", "khan uul",
        "баянгол", "bayangol",
        "сүхбаатар", "sukhbaatar",
        "чингэлтэй", "chingeltei",
        "баянзүрх", "bayanzurkh",
        "сонгинохайрхан", "songinokhairkhan",
        "налайх", "nalaikh",
        "багануур", "baganuur",
    ]


def geocode_address(address: str) -> Location:
    """Geocode an address string using Google Maps API, restricted to Mongolia.

    Strategy:
    1. (Optional) Find Place (Places Legacy API) – best for named buildings
    2. Geocoding API with UB hint – primary and fallback for structured addresses
    """
    client = _get_client()
    lower = address.lower()

    # Build a clean query with appropriate location hints
    _ub_city_keywords = ["ulaanbaatar", "улаанбаатар"]
    _mn_country_keywords = ["монгол", "mongolia"]
    has_ub_city = any(k in lower for k in _ub_city_keywords)
    has_mn_country = any(k in lower for k in _mn_country_keywords)
    has_ub_district = any(k in lower for k in _ub_district_keywords())

    if has_ub_city:
        # Already has city name, just ensure country context
        query = address if has_mn_country else f"{address}, Монгол"
    elif has_ub_district:
        # Has a UB district name but no city — append city so Google stays in UB
        query = f"{address}, Улаанбаатар, Монгол"
    else:
        query = f"{address}, Улаанбаатар, Монгол"

    # --- Step 1: Places API (New) Text Search ---
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    try:
        resp = httpx.post(
            "https://places.googleapis.com/v1/places:searchText",
            json={
                "textQuery": query,
                "languageCode": "mn",
                "locationRestriction": {
                    "rectangle": {
                        "low": {"latitude": _MN_BOUNDS["southwest"]["lat"], "longitude": _MN_BOUNDS["southwest"]["lng"]},
                        "high": {"latitude": _MN_BOUNDS["northeast"]["lat"], "longitude": _MN_BOUNDS["northeast"]["lng"]},
                    }
                },
            },
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "places.location,places.formattedAddress,places.id",
                "Content-Type": "application/json",
            },
            timeout=5,
        )
        if resp.ok:
            places = resp.json().get("places", [])
            if places:
                first = places[0]
                loc_data = first.get("location", {})
                lat = loc_data.get("latitude")
                lng = loc_data.get("longitude")
                if lat and lng and _MN_BOUNDS["southwest"]["lat"] <= lat <= _MN_BOUNDS["northeast"]["lat"]:
                    loc = _location_from_find_place(
                        {
                            "geometry": {"location": {"lat": lat, "lng": lng}},
                            "formatted_address": first.get("formattedAddress", ""),
                            "address_components": [],
                        },
                        address,
                    )
                    if loc:
                        return loc
        else:
            logger.warning("Places API (New) returned %s for %r", resp.status_code, query)
    except Exception as e:
        logger.warning("Places API (New) text search failed for %r: %s", address, e)

    # --- Step 2: Geocoding API ---
    results = client.geocode(  # type: ignore
        query,
        region="mn",
        language="mn",
        bounds=_MN_BOUNDS,
    )
    mn_results = [r for r in (results or []) if _is_in_mongolia(r)]

    if not mn_results:
        results = client.geocode(address, region="mn", language="mn", bounds=_MN_BOUNDS)  # type: ignore
        mn_results = [r for r in (results or []) if _is_in_mongolia(r)]

    if not mn_results:
        raise ValueError(f"No results found in Mongolia for address: {address}")

    return parse_geocode_result(_best_result(mn_results))
