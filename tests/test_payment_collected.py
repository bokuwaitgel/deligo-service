"""POST /api/delivery/{sales_id}/payment_collected — the driver's
"Бэлэн авсан" / "Дансаар авсан" tick that puts the ★ badge on the marker.

No database and no Deligo: the delivery repository is a stub holding one order
and the driver-token check is replaced, so these assert on the contract only.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from schemas.database.delivery_db import DeliveryOrder
from src.api.api import app
from src.api.endpoints import delivery as ep
from src.dependencies import get_delivery_repository
from src.services.driver_identity import DriverAuthError

SALES_ID = "1773113766311954"
DRIVER_ID = "D-1"
GOOD_TOKEN = "good-token"


class _StubRepo:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.row = DeliveryOrder(
            sales_id=SALES_ID,
            sales_number="ORD-77",
            store_id="STORE-1",
            driver_id=DRIVER_ID,
            customer_address="БЗД 4-р хороо",
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
def repo(monkeypatch):
    stub = _StubRepo()
    monkeypatch.setattr(ep, "authorize_driver_for_order", _fake_authorize)
    # An unknown order would otherwise fall through to a live Deligo lookup.
    monkeypatch.setattr(ep, "get_sales_detail", lambda *a, **k: None)
    app.dependency_overrides[get_delivery_repository] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_delivery_repository, None)


def _tick(client, auth_headers, method, sales_id=SALES_ID, token: str | None = GOOD_TOKEN):
    headers = {**auth_headers, **({"X-Driver-Token": token} if token else {})}
    return client.post(
        f"/api/delivery/{sales_id}/payment_collected",
        json={"method": method},
        headers=headers,
    )


@pytest.mark.parametrize("method", ["cash", "bank"])
def test_tick_records_method_and_driver(client, auth_headers, repo, method):
    r = _tick(client, auth_headers, method)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["payment_collected_method"] == method
    assert body["payment_collected_by"] == DRIVER_ID
    assert body["payment_collected_at"] is not None


def test_null_clears_the_tick(client, auth_headers, repo):
    _tick(client, auth_headers, "cash")
    r = _tick(client, auth_headers, None)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["payment_collected_method"] is None
    assert body["payment_collected_at"] is None
    assert body["payment_collected_by"] is None


def test_requires_driver_token(client, auth_headers, repo):
    r = _tick(client, auth_headers, "cash", token=None)
    assert r.status_code == 403
    assert repo.row.payment_collected_method is None


def test_rejects_other_drivers_order(client, auth_headers, repo):
    repo.row.driver_id = "D-2"
    r = _tick(client, auth_headers, "cash")
    assert r.status_code == 403
    assert repo.row.payment_collected_method is None


def test_rejects_unknown_method(client, auth_headers, repo):
    r = _tick(client, auth_headers, "card")
    assert r.status_code == 422


def test_unknown_order_is_404(client, auth_headers, repo):
    r = _tick(client, auth_headers, "cash", sales_id="nope")
    assert r.status_code == 404
