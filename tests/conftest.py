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
