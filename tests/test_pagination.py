def _make_order(num):
    return {
        "sales_number": f"SN-{num:03d}",
        "store_id": "STORE-1",
        "driver_id": "DRIVER-1",
        "total": 1000.0,
        "sales_id": f"SALE-{num:03d}",
        "customer_phone": "99001122",
    }


def test_list_all_orders_paginated(client, auth_headers):
    for i in range(5):
        client.post("/api/orders/", json=_make_order(i), headers=auth_headers)

    response = client.get("/api/orders/?limit=3", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 3
    assert body["has_more"] is True
    assert body["next_cursor"] is not None

    response2 = client.get(
        f"/api/orders/?cursor={body['next_cursor']}&limit=3", headers=auth_headers
    )
    body2 = response2.json()
    assert len(body2["data"]) == 2
    assert body2["has_more"] is False
    assert body2["next_cursor"] is None


def test_driver_orders_paginated(client, auth_headers):
    for i in range(4):
        client.post("/api/orders/", json=_make_order(i), headers=auth_headers)

    response = client.get("/api/orders/driver/DRIVER-1?limit=2", headers=auth_headers)
    body = response.json()
    assert len(body["data"]) == 2
    assert body["has_more"] is True

    response2 = client.get(
        f"/api/orders/driver/DRIVER-1?cursor={body['next_cursor']}&limit=2",
        headers=auth_headers,
    )
    body2 = response2.json()
    assert len(body2["data"]) == 2
    assert body2["has_more"] is False


def test_store_orders_paginated(client, auth_headers):
    for i in range(3):
        client.post("/api/orders/", json=_make_order(i), headers=auth_headers)

    response = client.get("/api/orders/store/STORE-1?limit=2", headers=auth_headers)
    body = response.json()
    assert len(body["data"]) == 2
    assert body["has_more"] is True


def test_limit_capped_at_200(client, auth_headers):
    client.post("/api/orders/", json=_make_order(0), headers=auth_headers)
    response = client.get("/api/orders/?limit=999", headers=auth_headers)
    assert response.status_code == 200


def test_empty_results(client, auth_headers):
    response = client.get("/api/orders/driver/NONEXISTENT", headers=auth_headers)
    body = response.json()
    assert body["data"] == []
    assert body["has_more"] is False
    assert body["next_cursor"] is None
