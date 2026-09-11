import os

import pytest
from fastapi.testclient import TestClient

# Must be set before the app module is imported: `require_api_key` reads it at
# request time, but other modules read their env at import.
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from src.api.api import app  # noqa: E402


@pytest.fixture
def client():
    # No `with`: the lifespan needs a live Postgres (schema + event bus). The
    # contract tests stub every repository they touch instead.
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def api_key():
    return os.environ["API_KEY"]


@pytest.fixture
def auth_headers(api_key):
    return {"X-API-Key": api_key}
