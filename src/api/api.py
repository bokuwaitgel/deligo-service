import logging
import os
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.endpoints.auth import router as auth_router
from src.api.endpoints.health import router as health_router
from src.api.endpoints.location import router as location_router
from src.api.endpoints.delivery import router as delivery_router
from src.api.endpoints.driver import router as driver_router
from src.api.endpoints.events import router as events_router
from src.api.endpoints.push import router as push_router
from src.api.endpoints.status_catalog import router as status_catalog_router

load_dotenv()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.dependencies import _get_engine
    from schemas.database.delivery_db import Base as DeliveryBase
    from schemas.database.driver_location_db import Base as DriverBase
    from schemas.database.push_subscription_db import Base as PushBase
    from schemas.database.status_catalog_override_db import Base as StatusOverrideBase
    from schemas.database.notification_override_db import Base as NotificationOverrideBase
    from schemas.database.notification_log_db import Base as NotificationLogBase
    from schemas.database.notification_icon_db import Base as NotificationIconBase
    from src.repositories.migrations import apply_schema_patches, ensure_schema
    from src.services.events import shutdown_event_bus, start_event_bus

    engine = _get_engine()
    ensure_schema(
        engine,
        (
            DeliveryBase,
            DriverBase,
            PushBase,
            StatusOverrideBase,
            NotificationOverrideBase,
            NotificationLogBase,
            NotificationIconBase,
        ),
    )
    # create_all adds missing tables but never missing columns — patch those.
    apply_schema_patches(engine)
    logger.info("Database tables ensured")

    await start_event_bus()
    try:
        yield
    finally:
        await shutdown_event_bus()


app = FastAPI(
    title="Deligo Mapper API",
    version="1.0.0",
    description="Deligo Mapper API",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

_cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
if _cors_env:
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
    _cors_regex = None
else:
    # Backward-compat default: allow all. Set CORS_ALLOWED_ORIGINS in production
    # (comma-separated, e.g. "https://map.deligoalpha.mn,https://admin.deligoalpha.mn").
    _cors_origins = ["*"]
    _cors_regex = ".*"
    logger.warning(
        "CORS_ALLOWED_ORIGINS not set — accepting requests from any origin. "
        "Restrict this in production."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_regex,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

app.include_router(auth_router)
app.include_router(health_router)
app.include_router(location_router)
app.include_router(delivery_router)
app.include_router(driver_router)
app.include_router(events_router)
app.include_router(push_router)
app.include_router(status_catalog_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    logger.exception(
        "Unhandled exception on %s %s [request_id=%s]",
        request.method, request.url.path, request_id,
    )
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "data": None,
            "message": "Internal server error",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


@app.get("/", tags=["system"])
async def root():
    return {
        "message": "Welcome to the Deligo Mapper API.",
        "docs": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
    }
