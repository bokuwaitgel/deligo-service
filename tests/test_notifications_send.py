"""POST /api/notifications/send — the one endpoint Deligo integrates with.

No database: the push-subscription repository is replaced with a stub and
``publish_order_event`` is captured, so these assert on the contract (modes,
validation, echoed copy) and on what would have been published.
"""
from __future__ import annotations

import pytest

from src.api.api import app
from src.api.endpoints import notifications as ep
from src.dependencies import get_delivery_repository, get_push_subscription_repository
from src.services import notifications as svc

SALES_ID = "1773113766311954"


class _StubRepo:
    def __init__(self, devices: int = 2):
        self.devices = devices

    def count_for_sales_id(self, sales_id: str) -> int:
        return self.devices


class _Order:
    sales_number = "ORD-77"


class _StubOrders:
    """Knows one order: SALES_ID → ORD-77."""

    def get_by_sales_id(self, sales_id: str):
        return _Order() if sales_id == SALES_ID else None


@pytest.fixture
def published(monkeypatch):
    """Capture publishes from both the new endpoint and the /api/push/send alias."""
    calls = []

    def fake_publish(sales_id, event_type, payload=None, event_id=None):
        calls.append(
            {"sales_id": sales_id, "event_type": event_type, "payload": payload, "event_id": event_id}
        )

    monkeypatch.setattr(ep, "publish_order_event", fake_publish)
    # Overrides come from the database; pin the compiled-in defaults instead.
    monkeypatch.setattr(svc, "_overrides", lambda: ({}, {}))
    app.dependency_overrides[get_push_subscription_repository] = lambda: _StubRepo(2)
    app.dependency_overrides[get_delivery_repository] = lambda: _StubOrders()
    yield calls
    app.dependency_overrides.pop(get_push_subscription_repository, None)
    app.dependency_overrides.pop(get_delivery_repository, None)


def _send(client, auth_headers, **body):
    return client.post("/api/notifications/send", json={"sales_id": SALES_ID, **body}, headers=auth_headers)


# ── auth ────────────────────────────────────────────────────────────────────


def test_requires_api_key(client, published):
    r = client.post("/api/notifications/send", json={"sales_id": SALES_ID, "event_type": "delivery_completed"})
    assert r.status_code == 422  # header missing entirely
    r = client.post(
        "/api/notifications/send",
        json={"sales_id": SALES_ID, "event_type": "delivery_completed"},
        headers={"X-API-Key": "wrong"},
    )
    assert r.status_code == 403
    assert published == []


# ── status mode ─────────────────────────────────────────────────────────────


def test_status_mode_resolves_template_like_changestatus(client, auth_headers, published):
    r = _send(client, auth_headers, status_id=14)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    tpl = svc.default_templates()["delivery_no_answer"]
    assert d["event_type"] == "delivery_no_answer"
    assert d["title"] == tpl["title"]
    assert d["body"] == tpl["body"]
    # sales_number looked up from the order when not sent
    assert d["sales_number"] == "ORD-77"
    assert d["tracking_url"].endswith("/ORD-77")
    p = published[0]["payload"]
    assert p["wfm_status_id"] == 14
    assert p["status_label"] == "Утсаа аваагүй"
    assert p["status_description_line"] == ""


def test_status_mode_generic_status_prints_label_and_note(client, auth_headers, published):
    r = _send(client, auth_headers, status_id=16, status_description="Хаалга нээгээгүй")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["event_type"] == "delivery_failed"
    assert "Хаягаар очсон" in d["body"]
    assert "Тайлбар: Хаалга нээгээгүй" in d["body"]


def test_status_mode_unknown_status_falls_back_to_status_changed(client, auth_headers, published):
    r = _send(client, auth_headers, status_id=99)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["event_type"] == "status_changed"
    assert "Шинэчлэгдсэн" in d["body"]


def test_status_mode_honours_mute(client, auth_headers, published, monkeypatch):
    monkeypatch.setattr(svc, "_overrides", lambda: ({}, {14: {"muted": True}}))
    r = _send(client, auth_headers, status_id=14)
    assert r.status_code == 409
    assert published == []


def test_status_mode_uses_operator_rule(client, auth_headers, published, monkeypatch):
    monkeypatch.setattr(svc, "_overrides", lambda: ({}, {16: {"event_type": "delivery_later"}}))
    r = _send(client, auth_headers, status_id=16)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["event_type"] == "delivery_later"


def test_status_mode_unknown_order_falls_back_to_sales_id(client, auth_headers, published):
    r = client.post(
        "/api/notifications/send",
        json={"sales_id": "0000", "status_id": 3},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["sales_number"] == "0000"


def test_status_mode_rejects_other_modes(client, auth_headers, published):
    assert _send(client, auth_headers, status_id=3, event_type="delivery_completed").status_code == 422
    assert _send(client, auth_headers, status_id=3, title="t", body="b").status_code == 422
    assert _send(client, auth_headers, event_type="delivery_completed", status_description="x").status_code == 422


# ── template mode ───────────────────────────────────────────────────────────


def test_template_mode_echoes_rendered_copy(client, auth_headers, published):
    r = _send(client, auth_headers, event_type="delivery_completed", sales_number="ORD-77")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    tpl = svc.default_templates()["delivery_completed"]
    assert d["title"] == tpl["title"]
    assert d["body"] == tpl["body"]
    assert d["icon"] == tpl["icon"]
    assert d["urgency"] == tpl["urgency"]
    assert d["event_type"] == "delivery_completed"
    assert d["sales_number"] == "ORD-77"
    assert d["tracking_url"].endswith("/ORD-77")
    assert d["push_devices"] == 2
    assert len(d["event_id"]) == 32
    assert d["sent_at"].endswith("+00:00")

    assert len(published) == 1
    p = published[0]
    assert p["event_id"] == d["event_id"]
    assert p["event_type"] == "delivery_completed"
    assert p["payload"]["sales_number"] == "ORD-77"


def test_template_mode_derives_status_description_line(client, auth_headers, published):
    r = _send(
        client,
        auth_headers,
        event_type="delivery_failed",
        params={"wfm_status_id": 16, "status_description": "Хаалга нээгээгүй"},
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]["body"]
    assert "Хаягаар очсон" in body  # status_label from wfm_status_id
    assert "Тайлбар: Хаалга нээгээгүй" in body


def test_template_mode_missing_placeholder_is_422(client, auth_headers, published):
    r = _send(client, auth_headers, event_type="delivery_queue_near")
    assert r.status_code == 422
    assert "queue_position_text" in r.json()["detail"]
    assert published == []


def test_template_mode_queue_position_derives_text(client, auth_headers, published):
    r = _send(client, auth_headers, event_type="delivery_queue_near", params={"queue_position": 2})
    assert r.status_code == 200, r.text
    assert "2 хүргэлтийн дараа" in r.json()["data"]["body"]


def test_unknown_event_type_is_404_with_list(client, auth_headers, published):
    r = _send(client, auth_headers, event_type="nope")
    assert r.status_code == 404
    assert "delivery_completed" in r.json()["detail"]
    assert "admin_message" not in r.json()["detail"]


def test_unknown_params_key_is_422(client, auth_headers, published):
    r = _send(client, auth_headers, event_type="delivery_completed", params={"price": 1})
    assert r.status_code == 422
    assert published == []


def test_icon_and_urgency_override_template(client, auth_headers, published):
    r = _send(client, auth_headers, event_type="delivery_tomorrow", icon="campaign", urgency="high")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["icon"] == "campaign"
    assert d["urgency"] == "high"


# ── custom mode ─────────────────────────────────────────────────────────────


def test_custom_mode(client, auth_headers, published):
    r = _send(client, auth_headers, title="Хүргэлт хойшиллоо", body="Жолооч 30 минутын дараа очно.")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["event_type"] == "admin_message"
    assert d["title"] == "Хүргэлт хойшиллоо"
    assert d["body"] == "Жолооч 30 минутын дараа очно."
    assert d["icon"] == "campaign"
    assert d["urgency"] == "high"
    assert published[0]["payload"]["admin_title"] == "Хүргэлт хойшиллоо"


def test_custom_mode_needs_both_fields(client, auth_headers, published):
    assert _send(client, auth_headers, title="only title").status_code == 422
    assert _send(client, auth_headers, body="only body").status_code == 422


def test_custom_mode_length_limits(client, auth_headers, published):
    assert _send(client, auth_headers, title="x" * 121, body="b").status_code == 422
    assert _send(client, auth_headers, title="t", body="x" * 401).status_code == 422


# ── mode exclusivity ────────────────────────────────────────────────────────


def test_both_modes_is_422(client, auth_headers, published):
    r = _send(client, auth_headers, event_type="delivery_completed", title="t", body="b")
    assert r.status_code == 422
    assert published == []


def test_neither_mode_is_422(client, auth_headers, published):
    r = _send(client, auth_headers)
    assert r.status_code == 422


def test_params_without_event_type_is_422(client, auth_headers, published):
    r = _send(client, auth_headers, title="t", body="b", params={"status_description": "x"})
    assert r.status_code == 422


def test_bad_urgency_and_icon(client, auth_headers, published):
    assert _send(client, auth_headers, title="t", body="b", urgency="urgent").status_code == 422
    assert _send(client, auth_headers, title="t", body="b", icon="Bad Icon").status_code == 422


# ── /api/push/send alias (admin panel) ──────────────────────────────────────


def test_push_send_alias_keeps_old_shape(client, auth_headers, published):
    r = client.post(
        "/api/push/send",
        json={"sales_id": SALES_ID, "title": "t", "body": "b"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert set(r.json()["data"]) == {"sales_id", "push_devices", "push_enabled"}
    assert published[0]["event_type"] == "admin_message"
    assert published[0]["payload"]["notification_icon"] == "campaign"
