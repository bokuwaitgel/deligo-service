# Order Management Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the Deligo Service with proper dependencies, PostgreSQL/Alembic migrations, timestamps, error handling, order CRUD endpoints, and cursor-based pagination.

**Architecture:** Feature-by-feature approach — each phase builds end-to-end with validation, error handling, and tests. Three phases: foundation (deps, DB, migrations, error handling), order updates/status workflow, cursor-based pagination.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL (Neon), Pydantic, pytest, httpx

---

## File Structure

### New files
- `alembic.ini` — Alembic configuration
- `alembic/env.py` — Alembic environment with SQLAlchemy metadata
- `alembic/script.py.mako` — Migration template
- `alembic/versions/` — Migration scripts directory
- `schemas/responses.py` — Standardized API response models
- `tests/__init__.py` — Test package
- `tests/conftest.py` — Shared fixtures (test DB, client, repo)
- `tests/test_orders.py` — Order endpoint tests

### Modified files
- `requirements.txt` — Add all dependencies
- `schemas/database/order_db.py` — Add `created_at`, `updated_at` columns
- `schemas/order.py` — Add `OrderCreate`, `OrderUpdate`, response models
- `src/dependencies.py` — Use `DATABASE_URL`, remove SQLite fallback, remove auto-create tables
- `src/api/api.py` — Add global exception handler
- `src/repositories/order.py` — Add partial update, cursor-based queries
- `src/services/order.py` — Add update, delete, paginated query functions
- `src/api/endpoints/order.py` — Add PUT, PATCH, DELETE, list-all endpoints; add cursor pagination

---

## Phase 1: Foundation

### Task 1: Populate requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Write requirements.txt**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sqlalchemy>=2.0.0
pydantic>=2.0.0
googlemaps>=4.10.0
python-dotenv>=1.0.0
alembic>=1.13.0
psycopg2-binary>=2.9.0
pytest>=8.0.0
httpx>=0.27.0
```

- [ ] **Step 2: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: All packages install successfully

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: populate requirements.txt with all dependencies"
```

---

### Task 2: Add created_at and updated_at to OrderDB

**Files:**
- Modify: `schemas/database/order_db.py`

- [ ] **Step 1: Add timestamp columns**

Add imports for `DateTime` and `func`, then add two columns before `__table_args__`:

```python
from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Index, String, Text, func
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class OrderDB(Base):
    __tablename__ = "orders"

    sales_number = Column(String, primary_key=True)
    store_id = Column(String, nullable=False, index=True)
    driver_id = Column(String, nullable=False, index=True)
    total_price = Column(Float, nullable=False)
    sales_id = Column(String, nullable=False, unique=True)
    delivery_date = Column(String, nullable=True)
    driver_name = Column(String, nullable=True)
    store_name = Column(String, nullable=True)
    route_number = Column(String, nullable=True)
    status = Column(String, nullable=True, default="pending")
    location_raw = Column(Text, nullable=True)
    location = Column(JSON, nullable=True)
    exchange_sales_id = Column(String, nullable=True)
    return_sales_id = Column(String, nullable=True)
    is_pay = Column(Boolean, nullable=True, default=False)
    is_closed = Column(Boolean, nullable=True, default=False)
    is_country = Column(Boolean, nullable=True, default=False)
    is_download = Column(Boolean, nullable=True, default=False)
    is_integration = Column(Boolean, nullable=True, default=False)
    is_start_driver = Column(Boolean, nullable=True, default=False)
    is_direct_pay = Column(Boolean, nullable=True, default=False)
    status_name = Column(String, nullable=True)
    status_color = Column(String, nullable=True)
    wfm_status_id = Column(String, nullable=True)
    customer_phone = Column(String, nullable=True)
    url = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_orders_status", "status"),
        Index("ix_orders_delivery_date", "delivery_date"),
    )
```

- [ ] **Step 2: Commit**

```bash
git add schemas/database/order_db.py
git commit -m "feat: add created_at and updated_at timestamps to OrderDB"
```

---

### Task 3: Switch to PostgreSQL and initialize Alembic

**Files:**
- Modify: `src/dependencies.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`

- [ ] **Step 1: Initialize Alembic**

Run: `cd c:/Users/ItgelOyunbold/Documents/itgl/idk/deligo-service && alembic init alembic`
Expected: Creates `alembic.ini` and `alembic/` directory

- [ ] **Step 2: Configure alembic.ini**

In `alembic.ini`, change the `sqlalchemy.url` line to be empty (we'll set it from env):

```ini
sqlalchemy.url =
```

- [ ] **Step 3: Configure alembic/env.py**

Replace `alembic/env.py` with:

```python
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from schemas.database.order_db import Base

load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", os.getenv("DATABASE_URL", ""))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Update src/dependencies.py to use DATABASE_URL**

Replace `src/dependencies.py` with:

```python
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

logger = logging.getLogger(__name__)

_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def _get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        conn_str = os.getenv("DATABASE_URL", "")
        if not conn_str:
            raise RuntimeError("DATABASE_URL environment variable is not set")
        _ENGINE = create_engine(
            conn_str,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=300,
        )
        logger.info("SQLAlchemy engine created: %s", conn_str.split("@")[-1] if "@" in conn_str else conn_str[:40])
    return _ENGINE


def _get_session_factory() -> sessionmaker[Session]:
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(bind=_get_engine())
    return _SESSION_FACTORY


def get_order_repository():
    from src.repositories.order import OrderRepository

    session = _get_session_factory()()
    try:
        yield OrderRepository(session)
    finally:
        session.close()
```

- [ ] **Step 5: Generate initial migration**

Run: `alembic revision --autogenerate -m "initial schema with timestamps"`
Expected: Creates a migration file in `alembic/versions/`

- [ ] **Step 6: Run migration**

Run: `alembic upgrade head`
Expected: Tables created in Neon PostgreSQL

- [ ] **Step 7: Commit**

```bash
git add alembic.ini alembic/ src/dependencies.py
git commit -m "feat: switch to PostgreSQL with Alembic migrations"
```

---

### Task 4: Standardized error handling and response format

**Files:**
- Create: `schemas/responses.py`
- Modify: `src/api/api.py`

- [ ] **Step 1: Create schemas/responses.py**

```python
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ApiResponse(BaseModel):
    status: str
    data: Any = None
    message: Optional[str] = None


class PaginatedResponse(BaseModel):
    status: str
    data: Any = None
    next_cursor: Optional[str] = None
    has_more: bool = False
```

- [ ] **Step 2: Add global exception handler to src/api/api.py**

Replace `src/api/api.py` with:

```python
import logging

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.endpoints.health import router as health_router
from src.api.endpoints.location import router as location_router
from src.api.endpoints.order import router as order_router

load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Deligo Mapper API",
    version="1.0.0",
    description="Deligo Mapper API",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(location_router)
app.include_router(order_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "data": None, "message": "Internal server error"},
    )


@app.get("/", tags=["system"])
async def root():
    return {
        "message": "Welcome to the Deligo Mapper API.",
        "docs": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
    }
```

- [ ] **Step 3: Commit**

```bash
git add schemas/responses.py src/api/api.py
git commit -m "feat: add standardized API response models and global exception handler"
```

---

### Task 5: Test infrastructure and foundation tests

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_health.py`

- [ ] **Step 1: Create tests/__init__.py**

Empty file.

- [ ] **Step 2: Create tests/conftest.py**

```python
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from schemas.database.order_db import Base
from src.api.api import app
from src.dependencies import get_order_repository
from src.repositories.order import OrderRepository

TEST_DATABASE_URL = os.getenv("DATABASE_URL", "")

engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
TestSessionLocal = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def order_repo(db_session):
    return OrderRepository(db_session)


def _override_get_order_repository(db_session):
    def override():
        repo = OrderRepository(db_session)
        try:
            yield repo
        finally:
            pass
    return override


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_order_repository] = _override_get_order_repository(db_session)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def api_key():
    return os.getenv("API_KEY", "your-api-key-change-in-production")


@pytest.fixture
def auth_headers(api_key):
    return {"X-API-Key": api_key}
```

- [ ] **Step 3: Create tests/test_health.py**

```python
def test_health_check(client):
    response = client.get("/api/health/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Welcome" in response.json()["message"]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_health.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: add test infrastructure and health check tests"
```

---

## Phase 2: Order Updates & Status Workflow

### Task 6: Add OrderCreate and OrderUpdate Pydantic schemas

**Files:**
- Modify: `schemas/order.py`

- [ ] **Step 1: Add new schemas to schemas/order.py**

Append after the `OrderBase` class:

```python
class OrderCreate(BaseModel):
    sales_number: str = Field(..., description="Unique sales number for the order")
    store_id: str = Field(..., description="Unique store ID for the order")
    driver_id: str = Field(..., description="Unique driver ID for the order")
    total_price: float = Field(..., description="Total price of the order", alias="total")
    sales_id: str = Field(..., description="Unique sales ID for the order")
    delivery_date: Optional[str] = Field(None, description="Scheduled delivery date")
    driver_name: Optional[str] = Field(None, description="Name of the driver")
    store_name: Optional[str] = Field(None, description="Name of the store")
    route_number: Optional[str] = Field(None, description="Route number for the delivery")
    customer_address: Optional[str] = Field(None, description="Raw customer address for geocoding")
    exchange_sales_id: Optional[str] = Field(None, description="Exchange order sales ID")
    return_sales_id: Optional[str] = Field(None, description="Return order sales ID")
    is_pay: Optional[bool] = Field(False)
    is_closed: Optional[bool] = Field(False)
    is_country: Optional[bool] = Field(False)
    is_download: Optional[bool] = Field(False)
    is_integration: Optional[bool] = Field(False)
    is_start_driver: Optional[bool] = Field(False)
    is_direct_pay: Optional[bool] = Field(False)
    status_name: Optional[str] = Field(None)
    status_color: Optional[str] = Field(None)
    wfm_status_id: Optional[str] = Field(None)
    customer_phone: Optional[str] = Field(None)

    model_config = {"populate_by_name": True}


class OrderUpdate(BaseModel):
    store_id: Optional[str] = None
    driver_id: Optional[str] = None
    total_price: Optional[float] = None
    delivery_date: Optional[str] = None
    driver_name: Optional[str] = None
    store_name: Optional[str] = None
    route_number: Optional[str] = None
    status: Optional[OrderStatus] = None
    location_raw: Optional[str] = None
    exchange_sales_id: Optional[str] = None
    return_sales_id: Optional[str] = None
    is_pay: Optional[bool] = None
    is_closed: Optional[bool] = None
    is_country: Optional[bool] = None
    is_download: Optional[bool] = None
    is_integration: Optional[bool] = None
    is_start_driver: Optional[bool] = None
    is_direct_pay: Optional[bool] = None
    status_name: Optional[str] = None
    status_color: Optional[str] = None
    wfm_status_id: Optional[str] = None
    customer_phone: Optional[str] = None
    url: Optional[str] = None
```

- [ ] **Step 2: Commit**

```bash
git add schemas/order.py
git commit -m "feat: add OrderCreate and OrderUpdate Pydantic schemas"
```

---

### Task 7: Add partial update and delete to repository

**Files:**
- Modify: `src/repositories/order.py`

- [ ] **Step 1: Update repository with partial update method**

Replace `src/repositories/order.py` with:

```python
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from schemas.database.order_db import OrderDB

logger = logging.getLogger(__name__)


class OrderRepository:
    def __init__(self, db_session: Session):
        self.db_session = db_session

    def get_by_sales_number(self, sales_number: str) -> Optional[OrderDB]:
        return (
            self.db_session.query(OrderDB)
            .filter(OrderDB.sales_number == sales_number)
            .first()
        )

    def get_by_driver_id(self, driver_id: str) -> List[OrderDB]:
        return (
            self.db_session.query(OrderDB)
            .filter(OrderDB.driver_id == driver_id)
            .all()
        )

    def get_by_store_id(self, store_id: str) -> List[OrderDB]:
        return (
            self.db_session.query(OrderDB)
            .filter(OrderDB.store_id == store_id)
            .all()
        )

    def get_all(self) -> List[OrderDB]:
        return self.db_session.query(OrderDB).all()

    def create(self, order: OrderDB) -> OrderDB:
        self.db_session.add(order)
        self.db_session.commit()
        self.db_session.refresh(order)
        return order

    def create_bulk(self, orders: List[OrderDB]) -> List[OrderDB]:
        self.db_session.add_all(orders)
        self.db_session.commit()
        for order in orders:
            self.db_session.refresh(order)
        return orders

    def update_partial(self, sales_number: str, data: Dict[str, Any]) -> Optional[OrderDB]:
        order = self.get_by_sales_number(sales_number)
        if order is None:
            return None
        for key, value in data.items():
            if hasattr(order, key):
                setattr(order, key, value)
        self.db_session.commit()
        self.db_session.refresh(order)
        return order

    def delete(self, sales_number: str) -> bool:
        order = self.get_by_sales_number(sales_number)
        if order is None:
            return False
        self.db_session.delete(order)
        self.db_session.commit()
        return True
```

- [ ] **Step 2: Commit**

```bash
git add src/repositories/order.py
git commit -m "feat: add partial update and improved delete to OrderRepository"
```

---

### Task 8: Add update, delete service functions

**Files:**
- Modify: `src/services/order.py`

- [ ] **Step 1: Add update and delete functions**

Add these functions at the end of `src/services/order.py`:

```python
def update_order(repo: OrderRepository, sales_number: str, data: Dict[str, Any]) -> OrderDB | None:
    """Update an order. Re-geocodes if location_raw changed."""
    order = repo.get_by_sales_number(sales_number)
    if order is None:
        return None

    old_location_raw = order.location_raw
    new_location_raw = data.get("location_raw")

    updated = repo.update_partial(sales_number, data)

    if updated and new_location_raw and new_location_raw != old_location_raw:
        _geocode_and_attach(updated)
        repo.update_partial(sales_number, {"location": updated.location})

    return updated


def delete_order(repo: OrderRepository, sales_number: str) -> bool:
    """Delete an order by sales_number. Returns True if deleted, False if not found."""
    return repo.delete(sales_number)
```

- [ ] **Step 2: Commit**

```bash
git add src/services/order.py
git commit -m "feat: add update_order and delete_order service functions"
```

---

### Task 9: Add PUT, PATCH, DELETE endpoints

**Files:**
- Modify: `src/api/endpoints/order.py`

- [ ] **Step 1: Update order endpoints**

Replace `src/api/endpoints/order.py` with:

```python
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from schemas.order import OrderUpdate
from src.api.auth_utils import require_api_key
from src.dependencies import get_order_repository
from src.repositories.order import OrderRepository
from src.services.order import (
    create_order,
    create_orders_bulk,
    delete_order,
    get_order,
    get_orders_by_driver,
    get_orders_by_store,
    update_order,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("/", dependencies=[Depends(require_api_key)])
def create_order_endpoint(
    raw: Dict[str, Any],
    repo: OrderRepository = Depends(get_order_repository),
):
    order = create_order(repo, raw)
    return {"status": "ok", "data": {"sales_number": order.sales_number}}


@router.post("/bulk", dependencies=[Depends(require_api_key)])
def create_orders_bulk_endpoint(
    raw_list: List[Dict[str, Any]],
    repo: OrderRepository = Depends(get_order_repository),
):
    orders = create_orders_bulk(repo, raw_list)
    return {
        "status": "ok",
        "data": {
            "count": len(orders),
            "sales_numbers": [o.sales_number for o in orders],
        },
    }


@router.get("/{sales_number}", dependencies=[Depends(require_api_key)])
def get_order_endpoint(
    sales_number: str,
    repo: OrderRepository = Depends(get_order_repository),
):
    order = get_order(repo, sales_number)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": order}


@router.put("/{sales_number}", dependencies=[Depends(require_api_key)])
def update_order_full_endpoint(
    sales_number: str,
    body: OrderUpdate,
    repo: OrderRepository = Depends(get_order_repository),
):
    data = body.model_dump(exclude_unset=False, exclude_none=True)
    updated = update_order(repo, sales_number, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": updated}


@router.patch("/{sales_number}", dependencies=[Depends(require_api_key)])
def update_order_partial_endpoint(
    sales_number: str,
    body: OrderUpdate,
    repo: OrderRepository = Depends(get_order_repository),
):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    updated = update_order(repo, sales_number, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": updated}


@router.delete("/{sales_number}", dependencies=[Depends(require_api_key)])
def delete_order_endpoint(
    sales_number: str,
    repo: OrderRepository = Depends(get_order_repository),
):
    deleted = delete_order(repo, sales_number)
    if not deleted:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "message": "Order deleted"}


@router.get("/driver/{driver_id}", dependencies=[Depends(require_api_key)])
def get_orders_by_driver_endpoint(
    driver_id: str,
    repo: OrderRepository = Depends(get_order_repository),
):
    orders = get_orders_by_driver(repo, driver_id)
    return {"status": "ok", "data": orders}


@router.get("/store/{store_id}", dependencies=[Depends(require_api_key)])
def get_orders_by_store_endpoint(
    store_id: str,
    repo: OrderRepository = Depends(get_order_repository),
):
    orders = get_orders_by_store(repo, store_id)
    return {"status": "ok", "data": orders}
```

- [ ] **Step 2: Commit**

```bash
git add src/api/endpoints/order.py
git commit -m "feat: add PUT, PATCH, DELETE order endpoints with standardized responses"
```

---

### Task 10: Write tests for order CRUD

**Files:**
- Create: `tests/test_orders.py`

- [ ] **Step 1: Write order CRUD tests**

```python
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
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_orders.py
git commit -m "test: add comprehensive order CRUD and status transition tests"
```

---

## Phase 3: Cursor-Based Pagination

### Task 11: Add cursor-based query methods to repository

**Files:**
- Modify: `src/repositories/order.py`

- [ ] **Step 1: Add paginated query methods**

Add these methods to the `OrderRepository` class:

```python
    def get_by_driver_id_paginated(
        self, driver_id: str, cursor: str | None, limit: int
    ) -> List[OrderDB]:
        query = (
            self.db_session.query(OrderDB)
            .filter(OrderDB.driver_id == driver_id)
            .order_by(OrderDB.created_at.desc(), OrderDB.sales_number.desc())
        )
        if cursor:
            cursor_order = self.get_by_sales_number(cursor)
            if cursor_order and cursor_order.created_at:
                query = query.filter(
                    (OrderDB.created_at < cursor_order.created_at)
                    | (
                        (OrderDB.created_at == cursor_order.created_at)
                        & (OrderDB.sales_number < cursor)
                    )
                )
        return query.limit(limit + 1).all()

    def get_by_store_id_paginated(
        self, store_id: str, cursor: str | None, limit: int
    ) -> List[OrderDB]:
        query = (
            self.db_session.query(OrderDB)
            .filter(OrderDB.store_id == store_id)
            .order_by(OrderDB.created_at.desc(), OrderDB.sales_number.desc())
        )
        if cursor:
            cursor_order = self.get_by_sales_number(cursor)
            if cursor_order and cursor_order.created_at:
                query = query.filter(
                    (OrderDB.created_at < cursor_order.created_at)
                    | (
                        (OrderDB.created_at == cursor_order.created_at)
                        & (OrderDB.sales_number < cursor)
                    )
                )
        return query.limit(limit + 1).all()

    def get_all_paginated(self, cursor: str | None, limit: int) -> List[OrderDB]:
        query = self.db_session.query(OrderDB).order_by(
            OrderDB.created_at.desc(), OrderDB.sales_number.desc()
        )
        if cursor:
            cursor_order = self.get_by_sales_number(cursor)
            if cursor_order and cursor_order.created_at:
                query = query.filter(
                    (OrderDB.created_at < cursor_order.created_at)
                    | (
                        (OrderDB.created_at == cursor_order.created_at)
                        & (OrderDB.sales_number < cursor)
                    )
                )
        return query.limit(limit + 1).all()
```

Note: We fetch `limit + 1` rows. If we get more than `limit`, there are more results — we return only `limit` rows and set `next_cursor` to the last returned row's `sales_number`.

- [ ] **Step 2: Commit**

```bash
git add src/repositories/order.py
git commit -m "feat: add cursor-based paginated queries to OrderRepository"
```

---

### Task 12: Add paginated service functions

**Files:**
- Modify: `src/services/order.py`

- [ ] **Step 1: Add paginated service functions**

Add these functions at the end of `src/services/order.py`:

```python
def get_orders_by_driver_paginated(
    repo: OrderRepository, driver_id: str, cursor: str | None, limit: int
) -> tuple[List[OrderDB], str | None, bool]:
    """Returns (orders, next_cursor, has_more)."""
    rows = repo.get_by_driver_id_paginated(driver_id, cursor, limit)
    has_more = len(rows) > limit
    orders = rows[:limit]
    next_cursor = orders[-1].sales_number if has_more and orders else None
    return orders, next_cursor, has_more


def get_orders_by_store_paginated(
    repo: OrderRepository, store_id: str, cursor: str | None, limit: int
) -> tuple[List[OrderDB], str | None, bool]:
    """Returns (orders, next_cursor, has_more)."""
    rows = repo.get_by_store_id_paginated(store_id, cursor, limit)
    has_more = len(rows) > limit
    orders = rows[:limit]
    next_cursor = orders[-1].sales_number if has_more and orders else None
    return orders, next_cursor, has_more


def get_all_orders_paginated(
    repo: OrderRepository, cursor: str | None, limit: int
) -> tuple[List[OrderDB], str | None, bool]:
    """Returns (orders, next_cursor, has_more)."""
    rows = repo.get_all_paginated(cursor, limit)
    has_more = len(rows) > limit
    orders = rows[:limit]
    next_cursor = orders[-1].sales_number if has_more and orders else None
    return orders, next_cursor, has_more
```

- [ ] **Step 2: Commit**

```bash
git add src/services/order.py
git commit -m "feat: add paginated service functions for orders"
```

---

### Task 13: Update endpoints with cursor pagination

**Files:**
- Modify: `src/api/endpoints/order.py`

- [ ] **Step 1: Update list endpoints with pagination**

Add these imports to the top of `src/api/endpoints/order.py`:

```python
from typing import Any, Dict, List, Optional
```

Add these imports from the service layer:

```python
from src.services.order import (
    create_order,
    create_orders_bulk,
    delete_order,
    get_all_orders_paginated,
    get_order,
    get_orders_by_driver_paginated,
    get_orders_by_store_paginated,
    update_order,
)
```

Replace the three GET list endpoints with:

```python
@router.get("/", dependencies=[Depends(require_api_key)])
def list_orders_endpoint(
    cursor: Optional[str] = None,
    limit: int = 50,
    repo: OrderRepository = Depends(get_order_repository),
):
    limit = min(limit, 200)
    orders, next_cursor, has_more = get_all_orders_paginated(repo, cursor, limit)
    return {"status": "ok", "data": orders, "next_cursor": next_cursor, "has_more": has_more}


@router.get("/driver/{driver_id}", dependencies=[Depends(require_api_key)])
def get_orders_by_driver_endpoint(
    driver_id: str,
    cursor: Optional[str] = None,
    limit: int = 50,
    repo: OrderRepository = Depends(get_order_repository),
):
    limit = min(limit, 200)
    orders, next_cursor, has_more = get_orders_by_driver_paginated(repo, driver_id, cursor, limit)
    return {"status": "ok", "data": orders, "next_cursor": next_cursor, "has_more": has_more}


@router.get("/store/{store_id}", dependencies=[Depends(require_api_key)])
def get_orders_by_store_endpoint(
    store_id: str,
    cursor: Optional[str] = None,
    limit: int = 50,
    repo: OrderRepository = Depends(get_order_repository),
):
    limit = min(limit, 200)
    orders, next_cursor, has_more = get_orders_by_store_paginated(repo, store_id, cursor, limit)
    return {"status": "ok", "data": orders, "next_cursor": next_cursor, "has_more": has_more}
```

**Important:** The `GET /` (list all) endpoint must be registered BEFORE `GET /{sales_number}` in the router, otherwise FastAPI will match "driver" or "store" as a `sales_number`. Reorder the endpoints so that static paths come before parameterized paths. Final order:

1. `POST /` (create)
2. `POST /bulk` (bulk create)
3. `GET /` (list all — new)
4. `GET /driver/{driver_id}` (by driver)
5. `GET /store/{store_id}` (by store)
6. `GET /{sales_number}` (single order)
7. `PUT /{sales_number}` (full update)
8. `PATCH /{sales_number}` (partial update)
9. `DELETE /{sales_number}` (delete)

- [ ] **Step 2: Commit**

```bash
git add src/api/endpoints/order.py
git commit -m "feat: add cursor-based pagination to list endpoints"
```

---

### Task 14: Write pagination tests

**Files:**
- Create: `tests/test_pagination.py`

- [ ] **Step 1: Write pagination tests**

```python
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
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_pagination.py
git commit -m "test: add cursor-based pagination tests"
```

---

### Task 15: Generate Alembic migration for timestamps

- [ ] **Step 1: Generate migration**

Run: `alembic revision --autogenerate -m "add created_at and updated_at to orders"`
Expected: New migration file created

- [ ] **Step 2: Run migration**

Run: `alembic upgrade head`
Expected: Migration applies successfully

- [ ] **Step 3: Commit**

```bash
git add alembic/
git commit -m "chore: add migration for created_at and updated_at columns"
```
