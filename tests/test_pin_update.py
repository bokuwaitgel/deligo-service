"""POST /api/delivery/{sales_id}/pin — the driver's "Pin засах".

Moves only the coordinate: every other address field must survive untouched,
locally and in what is pushed to Deligo. No database and no Deligo: the
repository is a stub, the driver-token check is replaced, and the Deligo /
event / audit side effects are captured.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from schemas.database.delivery_db import DeliveryOrder
from src.api.api import app
from src.api.endpoints import delivery as ep
from src.dependencies import get_delivery_repository
from src.services import delivery as svc
from src.services.driver_identity import DriverAuthError

SALES_ID = "1773113766311954"
DRIVER_ID = "D-1"
GOOD_TOKEN = "good-token"
DELIGO_ADDRESS = "БЗД 4-р хороо, Байр: 12, Тоот: 45 (хүрэн хаалга)"

ORIGINAL_LOCATION = {
    "latitude": 47.9,
    "longitude": 106.9,
    "formatted_address": "Натур төв, БЗД",
    "district": "БЗД",
    "khoroo": "4-р хороо",
    "building": {"building": "12", "entrance": "2", "door": "45"},
    "driver_note": "хүрэн хаалга",
}


class _StubRepo:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.row = DeliveryOrder(
            sales_id=SALES_ID,
            sales_number="ORD-77",
            store_id="STORE-1",
            driver_id=DRIVER_ID,
            customer_address="БЗД 4-р хороо",
            customer_location=copy.deepcopy(ORIGINAL_LOCATION),
            sync_active=True,
            map_status="pending",
            created_at=now,
            updated_at=now,
        )

    def get_by_sales_id(self, sales_id: str):
        return self.row if sales_id == SALES_ID else None

    def update_partial(self, sales_id: str, data):
        if sales_id != SALES_ID:
            return None
        for key, value in data.items():
            setattr(self.row, key, value)
        return self.row


def _fake_authorize(token, order_driver_id):
    if token != GOOD_TOKEN:
        raise DriverAuthError("Жолоочийн эрх хүчингүй. Дахин нэвтэрнэ үү.")
    if order_driver_id != DRIVER_ID:
        raise DriverAuthError("Энэ захиалга танд хуваарилагдаагүй байна.")
    return DRIVER_ID, "Bold"


@pytest.fixture
def env(monkeypatch):
    """Stub repo + captured Deligo pushes and audit rows."""
    stub = _StubRepo()
    captured = {"pushes": [], "audits": [], "detail": {"customer_address": DELIGO_ADDRESS}}

    def fake_push(sales_id, customer_address, latitude, longitude, extra_notes=None, driver_note=None):
        captured["pushes"].append(
            {
                "sales_id": sales_id,
                "customer_address": customer_address,
                "latitude": latitude,
                "longitude": longitude,
                "extra_notes": extra_notes,
                "driver_note": driver_note,
            }
        )
        return True

    def fake_audit(repo, order, previous_location, new_location, **kwargs):
        captured["audits"].append({"previous": previous_location, "new": new_location, **kwargs})

    monkeypatch.setattr(ep, "authorize_driver_for_order", _fake_authorize)
    # An unknown order would otherwise fall through to a live Deligo lookup.
    monkeypatch.setattr(ep, "get_sales_detail", lambda *a, **k: None)
    monkeypatch.setattr(svc, "get_sales_detail", lambda *a, **k: captured["detail"])
    monkeypatch.setattr(svc, "push_address_update", fake_push)
    monkeypatch.setattr(svc, "publish_order_event", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_record_address_change", fake_audit)
    app.dependency_overrides[get_delivery_repository] = lambda: stub
    captured["repo"] = stub
    yield captured
    app.dependency_overrides.pop(get_delivery_repository, None)


def _move(client, auth_headers, lat=47.91, lng=106.92, sales_id=SALES_ID, token: str | None = GOOD_TOKEN):
    headers = {**auth_headers, **({"X-Driver-Token": token} if token else {})}
    return client.post(
        f"/api/delivery/{sales_id}/pin",
        json={"latitude": lat, "longitude": lng},
        headers=headers,
    )


def test_moves_only_the_coordinate(client, auth_headers, env):
    r = _move(client, auth_headers)
    assert r.status_code == 200, r.text

    saved = env["repo"].row.customer_location
    assert saved["latitude"] == 47.91
    assert saved["longitude"] == 106.92
    for key in ("formatted_address", "district", "khoroo", "building", "driver_note"):
        assert saved[key] == ORIGINAL_LOCATION[key], key

    body = r.json()
    assert body["customer_location"]["formatted_address"] == ORIGINAL_LOCATION["formatted_address"]
    # Pin moved by a person → the marker ring goes green, attributed to the driver.
    assert body["location_updated_by"] == DRIVER_ID
    assert body["location_updated_by_name"] == "Bold"
    assert body["location_updated_at"] is not None
    assert env["repo"].row.customer_address == "БЗД 4-р хороо"

    assert env["audits"] and env["audits"][0]["changed_by_role"] == "driver"


def test_deligo_gets_its_own_address_back_verbatim(client, auth_headers, env):
    _move(client, auth_headers)
    assert env["pushes"] == [
        {
            "sales_id": SALES_ID,
            "customer_address": DELIGO_ADDRESS,
            "latitude": 47.91,
            "longitude": 106.92,
            "extra_notes": None,
            "driver_note": None,
        }
    ]


def test_deligo_push_skipped_when_its_address_is_unavailable(client, auth_headers, env):
    env["detail"] = None
    r = _move(client, auth_headers)
    assert r.status_code == 200, r.text
    assert env["repo"].row.customer_location["latitude"] == 47.91
    assert env["pushes"] == []


def test_requires_driver_token(client, auth_headers, env):
    r = _move(client, auth_headers, token=None)
    assert r.status_code == 403
    assert env["repo"].row.customer_location == ORIGINAL_LOCATION


def test_rejects_other_drivers_order(client, auth_headers, env):
    env["repo"].row.driver_id = "D-2"
    r = _move(client, auth_headers)
    assert r.status_code == 403
    assert env["repo"].row.customer_location == ORIGINAL_LOCATION


def test_locked_location_is_409(client, auth_headers, env):
    env["repo"].row.map_status = "completed"
    r = _move(client, auth_headers)
    assert r.status_code == 409
    assert env["repo"].row.customer_location == ORIGINAL_LOCATION


@pytest.mark.parametrize("lat,lng", [(91, 106.9), (47.9, 181)])
def test_rejects_out_of_range_coordinates(client, auth_headers, env, lat, lng):
    r = _move(client, auth_headers, lat=lat, lng=lng)
    assert r.status_code == 422


def test_unknown_order_is_404(client, auth_headers, env):
    r = _move(client, auth_headers, sales_id="nope")
    assert r.status_code == 404
