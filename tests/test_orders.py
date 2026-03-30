from schemas.database.order_db import OrderDB


def _sample_order_raw():
    return {
        "sales_number": "SN-001",
        "store_id": "STORE-1",
        "driver_id": "DRIVER-1",
        "total": 15000.0,
        "sales_id": "SALE-001",
        "delivery_date": "2026-03-30",
        "driver_name": "Bold",
        "store_name": "Test Store",
        "route_number": "R1",
        "customer_address": None,
        "customer_phone": "99001122",
    }


def test_create_order(client, auth_headers):
    response = client.post("/api/orders/", json=_sample_order_raw(), headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["data"]["sales_number"] == "SN-001"


def test_get_order(client, auth_headers):
    client.post("/api/orders/", json=_sample_order_raw(), headers=auth_headers)
    response = client.get("/api/orders/SN-001", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_get_order_not_found(client, auth_headers):
    response = client.get("/api/orders/NONEXISTENT", headers=auth_headers)
    assert response.status_code == 404


def test_patch_order(client, auth_headers):
    client.post("/api/orders/", json=_sample_order_raw(), headers=auth_headers)
    response = client.patch(
        "/api/orders/SN-001",
        json={"status": "in_progress", "driver_name": "Bat"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


def test_patch_order_empty_body(client, auth_headers):
    client.post("/api/orders/", json=_sample_order_raw(), headers=auth_headers)
    response = client.patch("/api/orders/SN-001", json={}, headers=auth_headers)
    assert response.status_code == 400


def test_put_order(client, auth_headers):
    client.post("/api/orders/", json=_sample_order_raw(), headers=auth_headers)
    response = client.put(
        "/api/orders/SN-001",
        json={"status": "completed", "driver_name": "Dorj", "store_name": "Updated Store"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_delete_order(client, auth_headers):
    client.post("/api/orders/", json=_sample_order_raw(), headers=auth_headers)
    response = client.delete("/api/orders/SN-001", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    response = client.get("/api/orders/SN-001", headers=auth_headers)
    assert response.status_code == 404


def test_delete_order_not_found(client, auth_headers):
    response = client.delete("/api/orders/NONEXISTENT", headers=auth_headers)
    assert response.status_code == 404


def test_create_bulk(client, auth_headers):
    orders = [
        {**_sample_order_raw(), "sales_number": "SN-B1", "sales_id": "SALE-B1"},
        {**_sample_order_raw(), "sales_number": "SN-B2", "sales_id": "SALE-B2"},
    ]
    response = client.post("/api/orders/bulk", json=orders, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["count"] == 2


def test_get_orders_by_driver(client, auth_headers):
    client.post("/api/orders/", json=_sample_order_raw(), headers=auth_headers)
    response = client.get("/api/orders/driver/DRIVER-1", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_get_orders_by_store(client, auth_headers):
    client.post("/api/orders/", json=_sample_order_raw(), headers=auth_headers)
    response = client.get("/api/orders/store/STORE-1", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_transition_any_to_any(client, auth_headers):
    client.post("/api/orders/", json=_sample_order_raw(), headers=auth_headers)

    for new_status in ["in_progress", "completed", "cancelled", "pending"]:
        response = client.patch(
            "/api/orders/SN-001",
            json={"status": new_status},
            headers=auth_headers,
        )
        assert response.status_code == 200


def test_auth_required(client):
    response = client.get("/api/orders/SN-001")
    assert response.status_code == 422  # missing header
