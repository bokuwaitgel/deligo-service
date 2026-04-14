# Order Service Integration Contract
## Overview

Deligo Mapper handles delivery location tracking and map editing. It does **not** store order details (items, totals, customer info, payment, etc.) — those live in your service.

Deligo calls your API to fetch that detail and merges it into delivery responses shown to drivers, shops, and customers.

Right now Deligo falls back to **hardcoded dummy data** when `ORDER_SERVICE_URL` is not set. Once you expose the two endpoints below and we set that env var, the integration is complete.

---

## Configuration

Set this env var in Deligo's `.env`:

```
ORDER_SERVICE_URL=https://your-order-service.example.com
```

No auth header is needed from Deligo's side right now. Let us know if you require one.

---

## Endpoints We Call

### 1. Get Single Order

```
GET {ORDER_SERVICE_URL}/api/orders/{sales_number}
```

Called when a driver, shop, or customer fetches a delivery order.

**Path param**

| Param | Type | Example |
|---|---|---|
| `sales_number` | string | `26031012808A` |

**Expected response — `200 OK`**

```json
{
  "sales_number": "26031012808A",
  "sales_id": 1773113900534670,
  "total": "35000.000000",
  "created_date": "2026-03-10 11:38:20",
  "status_created_date": "2026-03-10 11:39:10",
  "delivery_date": "2026-03-10 11:38:00",
  "customer_phone": "99758526",
  "customer_address": "Хөгжим бүжигийн сургуулийн зүүн талд...",
  "wfm_status_id": 5,
  "status_name": "Хуваарилсан",
  "status_color": "lime",
  "driver_name": "Test Map jolooch",
  "driver_id": 1773113257387725,
  "store_id": 1773113445185308,
  "store_name": "Map test delguur",
  "route_number": 1624,
  "is_pay": "0",
  "is_closed": 0,
  "is_country": 0,
  "is_download": "0",
  "is_integration": "0",
  "is_direct_pay": "0",
  "is_start_driver": null,
  "exchange_sales_id": 0,
  "return_sales_id": null,
  "order_items": [
    {
      "item_id": "1",
      "name": "product 1",
      "image_url": "https://...",
      "quantity": 2,
      "price": 10000.0
    },
    {
      "item_id": "2",
      "name": "product 2",
      "image_url": "https://...",
      "quantity": 2,
      "price": 7500.0
    }
  ]
}
```

**Error responses**

| Status | Meaning |
|---|---|
| `404` | Order not found — Deligo will show the delivery without detail |
| any other | Deligo logs a warning and shows the delivery without detail |

---

### 2. Get Multiple Orders (Batch)

```
POST {ORDER_SERVICE_URL}/api/orders/batch
```

Called when fetching a paginated list of deliveries (driver dashboard, shop dashboard). Batches all sales numbers in one request instead of N individual calls.

**Request body**

```json
{
  "sales_numbers": [
    "26031012808A",
    "2603102D34A4",
    "2603102EED8C"
  ]
}
```

**Expected response — `200 OK`**

Array of order objects in the same shape as the single-order response above. Orders not found can be omitted from the array — Deligo will just show those deliveries without detail.

```json
[
  { "sales_number": "26031012808A", "total": "35000.000000", ... },
  { "sales_number": "2603102D34A4", "total": "35000.000000", ... }
]
```

**Error responses**

| Status | Meaning |
|---|---|
| `404` | None found — Deligo treats as empty list |
| any other | Deligo logs a warning and returns deliveries without detail |

---

## Field Reference

These are the fields Deligo currently uses from the dummy data. Fields marked **required** will cause missing data in the UI if absent.

| Field | Type | Required | Notes |
|---|---|---|---|
| `sales_number` | string | Yes | Primary key used to match with Deligo's record |
| `sales_id` | integer | Yes | Internal order ID |
| `total` | string | Yes | Order total as decimal string e.g. `"35000.000000"` |
| `created_date` | string | Yes | `"YYYY-MM-DD HH:MM:SS"` |
| `delivery_date` | string | Yes | `"YYYY-MM-DD HH:MM:SS"` |
| `status_created_date` | string | No | When the current status was set |
| `customer_phone` | string | Yes | Shown to driver |
| `customer_address` | string | Yes | Raw address string |
| `wfm_status_id` | integer | No | WFM status code |
| `status_name` | string | Yes | Human-readable status e.g. `"Хуваарилсан"` |
| `status_color` | string | No | UI color hint e.g. `"lime"` |
| `driver_name` | string | No | |
| `driver_id` | integer | Yes | |
| `store_id` | integer | Yes | |
| `store_name` | string | Yes | |
| `route_number` | integer | No | |
| `is_pay` | string | Yes | `"0"` or `"1"` |
| `is_closed` | integer | No | `0` or `1` |
| `is_country` | integer | No | `0` = UB, `1` = countryside |
| `is_download` | string | No | `"0"` or `"1"` |
| `is_integration` | string | No | |
| `is_direct_pay` | string | No | |
| `is_start_driver` | string / null | No | |
| `exchange_sales_id` | integer | No | `0` if none |
| `return_sales_id` | integer / null | No | |
| `order_items` | array | Yes | See below |

**`order_items` object**

| Field | Type | Required |
|---|---|---|
| `item_id` | string | Yes |
| `name` | string | Yes |
| `image_url` | string | No |
| `quantity` | integer | Yes |
| `price` | float | Yes |

---

## Behavior When Order Service Is Unavailable

Deligo is designed to degrade gracefully:

- If `ORDER_SERVICE_URL` is **not set** → returns dummy data (current dev behavior)
- If the order service returns **non-200/404** → logs warning, returns delivery without `detail`
- The delivery location data is always returned regardless of order service status

---

## Questions / Contact

Reach out to the Deligo team to align on:

- Auth strategy (API key, JWT, internal network only?)
- Whether the batch endpoint is feasible or if individual calls are preferred
- Pagination needs for the batch endpoint if order counts are large
