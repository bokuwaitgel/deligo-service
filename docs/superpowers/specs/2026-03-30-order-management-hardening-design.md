# Deligo Service: Order Management Hardening & Features

**Date:** 2026-03-30
**Approach:** Feature-by-feature (Approach A) — each feature built end-to-end with foundation, validation, error handling, and tests

---

## Phase 1: Foundation Setup

### Dependencies (requirements.txt)
Pin all current and new dependencies:
- `fastapi`, `uvicorn[standard]`, `sqlalchemy`, `pydantic`, `googlemaps`, `python-dotenv`
- New: `alembic`, `psycopg2-binary`, `pytest`, `httpx`

### PostgreSQL + Alembic
- Switch `dependencies.py` to use `DATABASE_URL` env var (Neon PostgreSQL) as primary connection
- Remove SQLite fallback (`sqlite:///products.db`)
- Initialize Alembic with `alembic init`
- Create initial migration from current `OrderDB` schema
- Add `created_at` (server default `now()`) and `updated_at` (auto-update on modify) columns to `OrderDB`

### Error Handling
- Add global exception handler in `api.py` for consistent JSON error responses
- Standardize response format across all endpoints:
  ```json
  {"status": "ok"|"error", "data": ..., "message": ...}
  ```

---

## Phase 2: Order Updates & Status Workflow

### New Endpoints
- `PUT /api/orders/{sales_number}` — Full order update (replace all fields)
- `PATCH /api/orders/{sales_number}` — Partial update (only provided fields)
- `DELETE /api/orders/{sales_number}` — Delete an order

### New Pydantic Schemas
- `OrderCreate` — Strict input schema replacing current `Dict[str, Any]` on POST endpoints
- `OrderUpdate` — All fields optional, used for PATCH

### Status Transitions
- Loose model: any status can transition to any other status
- No validation rules on transitions
- Status changes update `updated_at` automatically

### Geocoding on Update
- If `location_raw` changes during an update, re-geocode automatically (same behavior as create)

### Repository Changes
- Update `update()` method to support partial updates (only modify provided fields)
- Wire `delete()` (already exists) to a new endpoint

### Tests
- Test each endpoint: create, read, update (full + partial), delete
- Test status transitions between all states
- Test geocoding triggers on update when `location_raw` changes
- Test error cases: 404 on missing order, invalid input

---

## Phase 3: Cursor-Based Pagination

### Mechanism
- Cursor = `sales_number` of the last item returned
- Ordering: `created_at` descending, `sales_number` as tiebreaker for stable cursor
- Default limit: 50, max: 200

### Request Format
```
GET /api/orders/driver/{driver_id}?cursor={last_sales_number}&limit=50
```

### Response Format
```json
{
  "status": "ok",
  "data": [...orders],
  "next_cursor": "SN-12345",
  "has_more": true
}
```

### Endpoints with Pagination
- `GET /api/orders/driver/{driver_id}` — orders by driver
- `GET /api/orders/store/{store_id}` — orders by store
- `GET /api/orders/` — new endpoint, all orders (admin/map views)

### Query Parameters
- `cursor` (optional): `sales_number` to start after
- `limit` (optional): number of results, default 50, max 200

---

## Architecture Notes

### Layering (unchanged)
```
Endpoints (src/api/endpoints/) → Services (src/services/) → Repositories (src/repositories/)
```

### Database
- Primary: Neon PostgreSQL via `DATABASE_URL`
- Migrations: Alembic (no auto-create tables)
- ORM: SQLAlchemy

### Authentication
- Unchanged: API key via `X-API-Key` header on order endpoints
- JWT auth is out of scope for this spec

### Location Services
- Unchanged: Google Maps geocoding via `googlemaps` library
- Triggered on create and on update when `location_raw` changes
