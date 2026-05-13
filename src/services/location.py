from __future__ import annotations

import logging
import os
import re
from typing import Any, cast

import googlemaps
from dotenv import load_dotenv

from schemas.delivery import Building, Location

load_dotenv()

logger = logging.getLogger(__name__)

# Deligo currently provisions newer Google APIs in some environments.
# Keep legacy Places Find Place opt-in to avoid REQUEST_DENIED when legacy is disabled.
_ENABLE_LEGACY_FIND_PLACE = os.getenv("ENABLE_LEGACY_FIND_PLACE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

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
    """Extract khoroo number from a string like '1-р хороо', '4 khoroo', 'CHD-4'."""
    if not value:
        return None
    # "1-р хороо", "4-р хороо", "4 хороо"
    match = re.search(r"(\d+)\s*-?\s*(?:р\s+)?(?:khoroo|хороо)", value, re.IGNORECASE)
    if match:
        return match.group(1)
    # Standalone digit at end like "CHD - 4"
    match = re.search(r"-\s*(\d+)\s*$", value)
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

    # In Mongolia: administrative_area_level_2 = дүүрэг, sublocality_level_1 = хороо
    district = (
        _extract_component(components, "administrative_area_level_2")
        or _extract_component(components, "sublocality_level_1")
        or _extract_component(components, "sublocality")
        or _extract_component(components, "neighborhood")
    )

    # khoroo: sublocality_level_1 is usually "N-р хороо" in UB
    khoroo_raw = (
        _extract_component(components, "sublocality_level_1")
        or _extract_component(components, "sublocality_level_2")
        or _extract_component(components, "neighborhood")
    )
    khoroo = _extract_khoroo(khoroo_raw)

    # If district and khoroo_raw are the same string, district should be level_2
    if district == khoroo_raw and _extract_component(components, "administrative_area_level_2"):
        district = _extract_component(components, "administrative_area_level_2")

    # Building / premise
    premise = _extract_component(components, "premise")
    building = Building(building=premise, entrance=None, floor=None, door=None, extra_notes=None) if premise else None

    street_number = _extract_component(components, "street_number") or ""
    route = _extract_component(components, "route") or ""
    street_address = f"{street_number} {route}".strip() or None

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

    # --- Step 1: Optional Find Place (legacy Places API) ---
    if _ENABLE_LEGACY_FIND_PLACE:
        try:
            fp_result = client.find_place(  # type: ignore
                input=query,
                input_type="textquery",
                fields=["geometry", "formatted_address", "place_id"],
                language="mn",
                location_bias=f"rectangle:{_MN_BOUNDS['southwest']['lat']},{_MN_BOUNDS['southwest']['lng']}|{_MN_BOUNDS['northeast']['lat']},{_MN_BOUNDS['northeast']['lng']}",
            )
            candidates = fp_result.get("candidates", [])
            mn_candidates = [c for c in candidates if _is_in_mongolia(c) or (
                _MN_BOUNDS["southwest"]["lat"] <= c.get("geometry", {}).get("location", {}).get("lat", 0) <= _MN_BOUNDS["northeast"]["lat"]
            )]
            if mn_candidates:
                loc = _location_from_find_place(mn_candidates[0], address)
                if loc:
                    return loc
        except Exception as e:
            logger.warning("find_place failed for %r: %s", address, e)

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
