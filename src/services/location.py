from __future__ import annotations

import logging
import os
import re
import threading
import time
from functools import lru_cache
from typing import Any, cast

import googlemaps
import googlemaps.exceptions
import httpx
from dotenv import load_dotenv

from schemas.delivery import Building, Location

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# District normalization — mirrors frontend geocoding.ts
# ---------------------------------------------------------------------------
_DISTRICT_MAP_EN: dict[str, str] = {
    "bgd": "Bayangol", "sbd": "Sukhbaatar", "bzd": "Bayanzurkh",
    "chd": "Chingeltei", "khud": "Khan-Uul", "hud": "Khan-Uul",
    "shd": "Songinokhairkhan", "skhd": "Songinokhairkhan",
    "nad": "Nalaikh", "bnd": "Baganuur",
}
_DISTRICT_MAP_MN: dict[str, str] = {
    "бгд": "Bayangol", "сбд": "Sukhbaatar", "бзд": "Bayanzurkh",
    "чд": "Chingeltei", "худ": "Khan-Uul",
    "схд": "Songinokhairkhan", "скд": "Songinokhairkhan",
    "нд": "Nalaikh", "бд": "Baganuur",
}
_DISTRICT_NAMES_MAP: dict[str, str] = {
    "сүхбаатар": "Sukhbaatar", "sukhbaatar": "Sukhbaatar",
    "чингэлтэй": "Chingeltei", "chingeltei": "Chingeltei",
    "баянгол": "Bayangol", "bayangol": "Bayangol",
    "хан-уул": "Khan-Uul", "khan-uul": "Khan-Uul",
    "баянзүрх": "Bayanzurkh", "bayanzurkh": "Bayanzurkh",
    "сонгинохайрхан": "Songinokhairkhan", "songinokhairkhan": "Songinokhairkhan",
    "налайх": "Nalaikh", "nalaikh": "Nalaikh",
    "багахангай": "Bagakhangai", "bagakhangai": "Bagakhangai",
    "багануур": "Baganuur", "baganuur": "Baganuur",
}
_DISTRICT_CANONICAL_TO_MN: dict[str, str] = {
    "Bayangol": "Баянгол", "Sukhbaatar": "Сүхбаатар",
    "Chingeltei": "Чингэлтэй", "Bayanzurkh": "Баянзүрх",
    "Khan-Uul": "Хан-Уул", "Songinokhairkhan": "Сонгинохайрхан",
    "Nalaikh": "Налайх", "Baganuur": "Багануур", "Bagakhangai": "Багахангай",
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


# Approximate centre coords per district for tight Places API bias
_DISTRICT_CENTERS: dict[str, tuple[float, float]] = {
    "Bayangol": (47.9077, 106.8832), "Sukhbaatar": (47.9198, 106.9195),
    "Chingeltei": (47.9318, 106.8921), "Bayanzurkh": (47.9174, 107.0024),
    "Khan-Uul": (47.8654, 106.8850), "Songinokhairkhan": (47.9441, 106.7844),
    "Nalaikh": (47.7572, 107.2964), "Baganuur": (47.7100, 108.2790),
    "Bagakhangai": (47.8483, 106.3600),
}

# ---------------------------------------------------------------------------

# Cost-saving toggle: legacy "Find Place from Text" bills at ~$17/1k.
# Places API (New) Text Search is also ~$17/1k when the field mask contains
# only Basic data fields (places.id, places.location, places.formattedAddress).
# Pro/Advanced fields (displayName, opening_hours, etc.) push it to ~$30–35/1k.
# Both paths are therefore equivalent in cost with our current Basic-only mask.
# Default ON because legacy Find Place is slightly more battle-tested for
# Mongolian addresses; set ENABLE_LEGACY_FIND_PLACE=false to use Places (New).
_ENABLE_LEGACY_FIND_PLACE = False
_gmaps: googlemaps.Client | None = None


def _get_client() -> googlemaps.Client:
    global _gmaps
    if _gmaps is None:
        key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not key:
            raise RuntimeError("GOOGLE_MAPS_API_KEY is not set")
        # retry_over_query_limit=False: on OVER_QUERY_LIMIT the client raises
        # immediately instead of silently retrying the same call ~10x until
        # retry_timeout elapses. Those retries were billed AND counted against
        # quota, turning one capped call into ~10 wasted requests (the storm
        # seen in the logs). queries_per_second throttles burst load.
        _gmaps = googlemaps.Client(
            key=key,
            retry_over_query_limit=False,
            retry_timeout=10,
            queries_per_second=10,
        )
    return _gmaps


# ---------------------------------------------------------------------------
# Quota circuit breaker
# ---------------------------------------------------------------------------
# Once Google returns OVER_QUERY_LIMIT, the daily quota is gone — every further
# call this day fails the same way. Without a breaker, each driver dashboard
# poll re-geocodes the same un-cached/failed addresses across all workers,
# re-hitting Google thousands of times after the cap. We trip a process-wide
# breaker on the first OVER_QUERY_LIMIT and short-circuit all geocode calls for
# a cooldown window so we stop hammering a quota we know is exhausted.
_QUOTA_COOLDOWN_SECONDS = int(os.getenv("GEOCODE_QUOTA_COOLDOWN_SECONDS", "1800"))
_quota_blocked_until: float = 0.0
_quota_lock = threading.Lock()


class GeocodeQuotaExhausted(RuntimeError):
    """Raised when the quota breaker is open — caller should skip geocoding."""


def _quota_is_blocked() -> bool:
    with _quota_lock:
        return time.monotonic() < _quota_blocked_until


def _trip_quota_breaker() -> None:
    global _quota_blocked_until
    with _quota_lock:
        _quota_blocked_until = time.monotonic() + _QUOTA_COOLDOWN_SECONDS
    logger.warning(
        "Geocode quota exhausted (OVER_QUERY_LIMIT) — pausing all geocode "
        "calls for %ds", _QUOTA_COOLDOWN_SECONDS,
    )


def _extract_component(components: list[dict], type_name: str) -> str | None:
    """Extract a value from Google address_components by type."""
    for comp in components:
        if type_name in comp.get("types", []):
            return comp.get("long_name")
    return None


def _extract_khoroo(value: str | None) -> str | None:
    """Extract khoroo number — mirrors frontend parseKhoroo(). Must NOT match хороолол."""
    if not value:
        return None
    # "1-р хороо", "4 хороо", "1 khoroo" — хороо must NOT be followed by Cyrillic (avoids хороолол)
    match = re.search(r"(\d+)(?:-р)?\s*(?:khoroo|хороо)(?![\u0400-\u04FF])", value, re.IGNORECASE)
    if match:
        return match.group(1)
    # "khoroo 15" or "хороо 15"
    match = re.search(r"(?:khoroo|хороо)(?![\u0400-\u04FF])\s*(\d+)", value, re.IGNORECASE)
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
    khoroo_raw = (
        neighborhood
        or _extract_component(components, "sublocality_level_1")
        or _extract_component(components, "sublocality_level_2")
    )
    khoroo = _extract_khoroo(khoroo_raw)
    # Fallback: scan entire formatted_address (mirrors frontend)
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
    """Reverse geocode coordinates using Google Maps API.

    Cost-saving: results are LRU-cached on an ~11m grid cell (4 decimal places)
    so repeated driver-location pings within ~10m don't re-hit Google
    Geocoding API ($5/1k).
    """
    return _reverse_geocode_cached(round(lat, 4), round(lng, 4), lat, lng)


@lru_cache(maxsize=16384)
def _reverse_geocode_cached(
    lat_key: float, lng_key: float, lat: float, lng: float
) -> Location:
    # lat_key/lng_key are the rounded values used as the cache key; lat/lng
    # are the precise input coordinates we forward to Google. This keeps the
    # grid behavior while preserving the precise lat/lng on the returned
    # Location (parse_geocode_result fills coords from the API response).
    _ = (lat_key, lng_key)  # only present to participate in the cache key
    if _quota_is_blocked():
        raise GeocodeQuotaExhausted("reverse_geocode skipped — quota breaker open")
    client = cast(Any, _get_client())
    try:
        # First try: specific types for detailed address
        results = client.reverse_geocode(
            (lat, lng),
            language="mn",
            result_type=["street_address", "premise", "sublocality", "neighborhood"],
        )
        if not results:
            # Fallback: all types
            results = client.reverse_geocode((lat, lng), language="mn")
    except googlemaps.exceptions._OverQueryLimit:
        _trip_quota_breaker()
        raise

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

    loc = parse_geocode_result(_best_result(results))
    # parse_geocode_result fills coords from Google's geometry.location, which is
    # the SNAPPED centroid of the matched feature (street/premise), not the pin
    # the user actually tapped. Restore the raw input coords so the stored point
    # matches the tap — mirrors the frontend reverseGeocode() behavior.
    loc.latitude = lat
    loc.longitude = lng
    return loc


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
    """Convert a Places hit into a minimal Location.

    We deliberately consume only the fields Places already returned in our field
    mask (location + formattedAddress) — no extra reverse_geocode call to fill
    address_components. That extra Geocoding call doubled the per-address cost
    and we don't actually use district/khoroo/street_address on the Places path.
    """
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
    return Location(
        latitude=lat,
        longitude=lng,
        formatted_address=_clean_formatted_address(candidate.get("formatted_address")),
        street_address=None,
        city=None,
        state=None,
        district=None,
        khoroo=None,
        country=None,
        postal_code=None,
        building=None,
    )


def _extract_district_from_query(address: str) -> str | None:
    """Detect canonical district name embedded in a raw address string."""
    lower = address.lower()
    for key, value in _DISTRICT_NAMES_MAP.items():
        if key in lower:
            return value
    for key, value in _DISTRICT_MAP_MN.items():
        if re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", lower):
            return value
    for key, value in _DISTRICT_MAP_EN.items():
        if re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", lower):
            return value
    return None


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


# Addresses that have failed geocoding — short-circuit before any Google calls
# so consistently-bad addresses don't re-burn the full Places+Geocoding budget
# on every driver sync. Bounded to keep memory bounded.
_GEOCODE_FAIL_CACHE: set[str] = set()
_GEOCODE_FAIL_CACHE_MAX = 4096


def _mark_geocode_failed(address: str) -> None:
    if len(_GEOCODE_FAIL_CACHE) >= _GEOCODE_FAIL_CACHE_MAX:
        _GEOCODE_FAIL_CACHE.clear()
    _GEOCODE_FAIL_CACHE.add(address)


@lru_cache(maxsize=2048)
def geocode_address(address: str) -> Location:
    """Geocode an address string using Google Maps API, restricted to Mongolia.

    Successes are cached in-process (LRU, 2048 entries) and failures are
    remembered in _GEOCODE_FAIL_CACHE so the same address string never hits
    Google twice for the lifetime of the process.

    Strategy:
    1. Detect district + khoroo from raw input.
    2. Places API (New) — one call, biased to district circle or Mongolia rect.
    3. Geocoding API fallback (targeted query, then bare address).
    """
    # Normalize the address before any work: collapse whitespace and lowercase
    # so that "1-р хороо" and " 1-р   хороо " share a cache slot. Both Places
    # Text Search (~$17/1k) and Geocoding ($5/1k) are case-insensitive so this
    # is safe and just deduplicates trivial variations.
    normalized = re.sub(r"\s+", " ", address).strip().lower()
    if not normalized:
        raise ValueError("Empty address")
    return _geocode_address_impl(normalized, places=True)



@lru_cache(maxsize=16384)
def _geocode_address_impl(address: str, places: bool = False) -> Location:
    if address in _GEOCODE_FAIL_CACHE:
        raise ValueError(f"Geocoding previously failed for address: {address}")
    if _quota_is_blocked():
        raise GeocodeQuotaExhausted("geocode skipped — quota breaker open")
    client = _get_client()
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    lower = address.lower()

    # Detect district and khoroo from raw input
    district_hint = _extract_district_from_query(address)
    khoroo_hint = _extract_khoroo(address)

    _ub_city_keywords = ["ulaanbaatar", "улаанбаатар"]
    _mn_country_keywords = ["монгол", "mongolia"]
    has_ub_city = any(k in lower for k in _ub_city_keywords)
    has_mn_country = any(k in lower for k in _mn_country_keywords)
    has_ub_district = any(k in lower for k in _ub_district_keywords())

    if has_ub_city:
        base_query = address if has_mn_country else f"{address}, Монгол"
    else:
        base_query = f"{address}, Улаанбаатар, Монгол"

    # Build targeted query: append only context NOT already present
    mn_district = _DISTRICT_CANONICAL_TO_MN.get(district_hint, district_hint) if district_hint else None
    district_in_query = mn_district is not None and mn_district.lower() in lower
    khoroo_in_query = bool(
        khoroo_hint
        and re.search(
            rf"(?<!\d){re.escape(khoroo_hint)}\s*(?:-\s*\u0440\s+)?(?:khoroo|\u0445\u043e\u0440\u043e\u043e)(?![\u0400-\u04FF])",
            lower,
            re.IGNORECASE,
        )
    )
    extra: list[str] = []
    if khoroo_hint and not khoroo_in_query:
        extra.append(f"{khoroo_hint}-р хороо")
    if mn_district and not district_in_query:
        extra.append(f"{mn_district} дүүрэг")
    if not has_ub_city:
        extra.append("Улаанбаатар")
    if not has_mn_country:
        extra.append("Монгол")
    targeted_query = f"{address}, {', '.join(extra)}" if extra else base_query
    # --- Geocoding API fallback ---
    try:
        results = client.geocode(  # type: ignore
            targeted_query,
            region="mn",
            language="mn",
            bounds=_MN_BOUNDS,
        )
        mn_results = [r for r in (results or []) if _is_in_mongolia(r)]

        if not mn_results:
            results = client.geocode(address, region="mn", language="mn", bounds=_MN_BOUNDS)  # type: ignore
            mn_results = [r for r in (results or []) if _is_in_mongolia(r)]
    except googlemaps.exceptions._OverQueryLimit:
        _trip_quota_breaker()
        raise

    if not mn_results:
        # Genuine no-match (not a quota issue): remember it so the same dead
        # address string doesn't re-hit Google on every driver poll. Raise
        # instead of returning a bogus 0,0 Location that would get cached as
        # a "success" and render as a pin in the Gulf of Guinea.
        _mark_geocode_failed(address)
        raise ValueError(f"No geocode result for address: {address}")

    return parse_geocode_result(_best_result(mn_results))


def _build_simplified_query(
    address: str,
    district_hint: str | None,
    khoroo_hint: str | None,
) -> str | None:
    """Build a clean canonical query from the strongest hints.

    Strips postal codes, country suffix, and reorders into
    "<landmark>, <district> дүүрэг, <khoroo>-р хороо, Улаанбаатар, Монгол".
    Returns None if there's nothing distinctive left.
    """
    # Strip the trailing ", Монгол Улс" / ", Монгол" so it doesn't get duplicated.
    cleaned = re.sub(r",\s*монгол(?:\s+улс)?\s*$", "", address, flags=re.IGNORECASE).strip()
    # Strip 5-digit postal codes (UB uses 5 digits, e.g. 17020).
    cleaned = re.sub(r"\b\d{5}\b", "", cleaned)
    # Collapse any leftover double commas / whitespace from the strips.
    cleaned = re.sub(r"\s*,\s*,+", ",", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")

    # First comma-separated segment is usually the most distinctive part
    # (landmark / building / street). Skip any segment that is purely a
    # district abbreviation or khoroo marker so we don't make the query
    # less specific than the targeted_query already was.
    landmark: str | None = None
    for segment in (s.strip() for s in cleaned.split(",")):
        if not segment:
            continue
        seg_lower = segment.lower()
        if seg_lower in _DISTRICT_MAP_MN or seg_lower in _DISTRICT_MAP_EN:
            continue
        if any(name in seg_lower for name in _DISTRICT_NAMES_MAP):
            continue
        if re.fullmatch(r"\d+(?:-р)?\s*(?:khoroo|хороо)", seg_lower):
            continue
        if any(k in seg_lower for k in ("улаанбаатар", "ulaanbaatar")):
            continue
        landmark = segment
        break

    parts: list[str] = []
    if landmark:
        parts.append(landmark)
    if district_hint:
        mn_district = _DISTRICT_CANONICAL_TO_MN.get(district_hint, district_hint)
        parts.append(f"{mn_district} дүүрэг")
    if khoroo_hint:
        parts.append(f"{khoroo_hint}-р хороо")
    parts.append("Улаанбаатар")
    parts.append("Монгол")

    if not landmark and not district_hint and not khoroo_hint:
        return None
    return ", ".join(parts)
