from __future__ import annotations

import logging
import os
from typing import Optional

from schemas.database.delivery_db import DeliveryOrder
from schemas.delivery import DeliveryOrderCreate, DeliveryOrderResponse, Location, MapStatus
from src.repositories.delivery import DeliveryRepository
from src.services.deligo_integration import get_sales_detail, push_address_update
from src.services.geocode_quota import consume_geocode_quota
from src.services.location import geocode_address, reverse_geocode

logger = logging.getLogger(__name__)


class OrderNotEditableError(Exception):
    """Raised when an address edit is attempted on an order whose live Deligo
    status no longer allows it (assigned to a driver, or past 'Шинэ')."""


def _is_address_editable(
    wfm_status_id: Optional[object],
    status_code: Optional[str],
    driver_id: Optional[str],
) -> bool:
    """Server-side mirror of the frontend ``isOrderAddressEditable`` rule.

    Editable only while the order is still brand-new ("Шинэ" / wfm 1 / salesNew)
    AND not yet assigned to a driver. Unknown status fails safe (not editable).
    """
    if driver_id:
        return False
    if wfm_status_id is not None and str(wfm_status_id).strip():
        return str(wfm_status_id).strip() == "1"
    if status_code:
        return status_code == "salesNew"
    return False


def _build_tracking_url(sales_number: str) -> Optional[str]:
    return f"/track/{sales_number}"

def _geocode(address: str, is_countryside: bool = False) -> Optional[dict]:
    try:
        # `geocode_address` already detects whether the raw string contains
        # "Улаанбаатар" / "Монгол" and appends the right city/country context
        # itself, so we hand it the user's string verbatim. Previously we
        # blindly prepended "монгол улс, улаанбаатар хот, " which produced
        # noisy duplicated queries like
        #   "монгол улс, улаанбаатар хот, SEVEN STAR residence, ХУД - 11 хороо, Улаанбаатар 17020, Монгол Улс"
        # that Google's Places + Geocoding API both returned no results for.
        # `is_countryside` is left as a no-op flag for API compatibility — the
        # underlying geocoder no longer needs it because UB/MN detection is
        # automatic and harmless for non-UB queries.
        _ = is_countryside
        print(f"Geocoding address: {address}")
        loc: Location = geocode_address(address)
        return loc.model_dump(mode="json")
    except Exception:
        logger.warning("Geocoding failed for address: %s", address, exc_info=True)
        return None


def _reverse_geocode(latitude: float, longitude: float, fallback_address: str | None = None) -> Optional[dict]:
    try:
        loc: Location = reverse_geocode(latitude, longitude)
        payload = loc.model_dump(mode="json")
        if fallback_address and not payload.get("formatted_address"):
            payload["formatted_address"] = fallback_address
        return payload
    except Exception:
        logger.warning(
            "Reverse geocoding failed for coordinates: (%s, %s)",
            latitude,
            longitude,
            exc_info=True,
        )
        return {
            "latitude": latitude,
            "longitude": longitude,
            "formatted_address": fallback_address,
            "street_address": None,
            "city": None,
            "state": None,
            "district": None,
            "khoroo": None,
            "country": None,
            "postal_code": None,
            "building": None,
        }


def create_delivery(repo: DeliveryRepository, payload: DeliveryOrderCreate) -> DeliveryOrderResponse:
    """
        Create a new delivery order. Geocodes the address immediately, 
        but allows null location if geocoding fails.
    """
    explicit_location = None
    if payload.latitude is not None and payload.longitude is not None:
        explicit_location = _reverse_geocode(
            payload.latitude,
            payload.longitude,
            fallback_address=payload.customer_address,
        )

    order = DeliveryOrder(
        sales_number=payload.sales_number,
        sales_id=payload.sales_id,
        store_id=payload.store_id,
        company_id=payload.company_id,
        customer_address=payload.customer_address,
        customer_location=explicit_location or _geocode(payload.customer_address, payload.is_countryside),
        tracking_url=_build_tracking_url(payload.sales_number),
        map_status=MapStatus.PENDING,
    )

    #before creating, check if sales_number already exists to avoid duplicates
    existing = repo.get_by_sales_number(payload.sales_number)
    if existing:
        # return existing order if already exists
        order = repo.get_by_sales_number(payload.sales_number)
        return DeliveryOrderResponse.model_validate(order)

    return DeliveryOrderResponse.model_validate(repo.create(order))


def get_delivery(repo: DeliveryRepository, sales_number: str) -> Optional[DeliveryOrderResponse]:
    order = repo.get_by_sales_number(sales_number)
    return DeliveryOrderResponse.model_validate(order) if order else None


def _compose_customer_address(location: Location, fallback: str = "") -> str:
    """Build the customerAddress string pushed to Deligo.

    Starts from the geocoded ``formatted_address`` and appends the manually
    entered building details (street/khoroolol name, байр, орц, давхар, тоот)
    that aren't part of the geocoded address. Empty fields are skipped, and a
    value already contained in the address (e.g. a street the geocoder also
    returned) isn't repeated. ``extra_notes`` is appended separately by
    ``push_address_update``.
    """
    base = (location.formatted_address or fallback or "").strip()
    parts: list[str] = [base] if base else []

    def add(value: Optional[str], label: str = "") -> None:
        if not value:
            return
        v = value.strip()
        if not v:
            return
        # Don't repeat an unlabeled value already present in the address.
        if not label and any(v.lower() in p.lower() for p in parts):
            return
        parts.append(f"{label}{v}" if label else v)

    add(location.street_address)
    b = location.building
    if b:
        add(b.building, "Байр: ")
        add(b.entrance, "Орц: ")
        add(b.floor, "Давхар: ")
        add(b.door, "Тоот: ")
    return ", ".join(parts)


def update_location(
    repo: DeliveryRepository,
    sales_number: str,
    location: Location,
    *,
    is_driver: bool = False,
) -> Optional[DeliveryOrderResponse]:
    """Update location with pre-built coordinates. Returns None if not found or already completed.

    ``is_driver`` skips the "still editable" gate: a driver may correct the pin
    of an order already assigned to them, whereas customers/shops may only edit
    while it is still "Шинэ" and unassigned.
    """
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None
    if order.map_status == MapStatus.COMPLETED:
        return None

    # Re-verify editability against the LIVE Deligo status before writing. The
    # frontend gate uses the order it last fetched, which goes stale if the
    # customer never refreshed — by the time they hit save the order may already
    # be assigned to a driver. Drivers are exempt.
    if order.sales_id and not is_driver:
        # Local row already knows it's assigned / past "Шинэ" — reject without
        # the Deligo round-trip.
        if order.driver_id or (order.status and order.status != "salesNew"):
            logger.info(
                "Rejecting address edit for sales_id=%s — local row not editable "
                "(status=%s driver_id=%s)",
                order.sales_id, order.status, order.driver_id,
            )
            raise OrderNotEditableError(
                "Захиалга хуваарилагдсан тул хаягийг өөрчлөх боломжгүй."
            )

        # skip_cache: the shared detail cache holds entries up to 5 min old that
        # may predate the driver assignment — the gate must see the live status.
        detail = get_sales_detail(str(order.sales_id), use_service_auth=False, skip_cache=True)
        if detail is None:
            # Fail CLOSED. Falling back to the (stale) local row here let a
            # just-assigned order through whenever Deligo flaked: first save
            # attempt got the live 409, the retry hit the fallback and saved.
            logger.warning(
                "Rejecting address edit for sales_id=%s — live status unavailable",
                order.sales_id,
            )
            raise OrderNotEditableError(
                "Захиалгын төлөв шалгагдсангүй тул хаягийг өөрчлөх боломжгүй. "
                "Хэсэг хугацааны дараа дахин оролдоно уу."
            )
        wfm_status_id = detail.get("wfm_status_id")
        status_code = detail.get("status_code")
        driver_id = detail.get("driver_id")
        if not _is_address_editable(wfm_status_id, status_code, driver_id):
            logger.info(
                "Rejecting address edit for sales_id=%s — not editable "
                "(wfm=%s status_code=%s driver_id=%s)",
                order.sales_id, wfm_status_id, status_code, driver_id,
            )
            raise OrderNotEditableError(
                "Захиалга хуваарилагдсан тул хаягийг өөрчлөх боломжгүй."
            )

    updated = repo.update_partial(sales_number, {"customer_location": location.model_dump(mode="json")})
    if updated and order.sales_id:
        address = _compose_customer_address(location, fallback=order.customer_address or "")
        extra_notes = location.building.extra_notes if location.building else None
        push_address_update(
            str(order.sales_id),
            address,
            location.latitude,
            location.longitude,
            extra_notes=extra_notes,
            driver_note=location.driver_note,
        )
    return DeliveryOrderResponse.model_validate(updated)


def update_location_by_address(
    repo: DeliveryRepository, sales_number: str, address: str, is_countryside: bool = False
) -> Optional[DeliveryOrderResponse]:
    """Re-geocode a new address string and update. Returns None if not found, completed, or geocoding fails."""
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None
    if order.map_status == MapStatus.COMPLETED:
        return None
    # DB-cache: ижил хаягийг сүүлд геокод хийсэн бол давтан Google дуудалгүй ашиглана.
    location_data = repo.find_location_by_address(address)
    if location_data is None:
        # Жолооч / захиалгад тус бүрд өдрийн квот хэтрээгүй эсэхийг шалгана.
        # Driver_id байхгүй (захиалга шинэ) үед sales_number-аар лимит барина.
        quota_key = order.driver_id or order.sales_number
        if not consume_geocode_quota(quota_key):
            raise ValueError(
                f"Geocode quota exhausted for {quota_key}; try again later"
            )
        location_data = _geocode(address, is_countryside)
    if location_data is None:
        raise ValueError(f"Geocoding failed for address: {address}")
    updated = repo.update_partial(sales_number, {
        "customer_address": address,
        "customer_location": location_data,
    })
    if updated and order.sales_id:
        lat = location_data.get("latitude")
        lng = location_data.get("longitude")
        if lat is not None and lng is not None:
            push_address_update(str(order.sales_id), address, float(lat), float(lng))
    return DeliveryOrderResponse.model_validate(updated)


def complete_delivery(repo: DeliveryRepository, sales_number: str) -> Optional[DeliveryOrderResponse]:
    """Lock the delivery location — no further edits allowed after this."""
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None
    updated = repo.update_partial(sales_number, {"map_status": MapStatus.COMPLETED})
    return DeliveryOrderResponse.model_validate(updated)
