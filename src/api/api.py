import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.endpoints.health import router as health_router
from src.api.endpoints.location import router as location_router
from src.api.endpoints.order import router as order_router
from src.api.endpoints.driver import router as driver_router

load_dotenv()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.dependencies import _get_engine
    from schemas.database.delivery_db import Base as DeliveryBase
    from schemas.database.driver_location_db import Base as DriverBase

    engine = _get_engine()
    DeliveryBase.metadata.create_all(engine)
    DriverBase.metadata.create_all(engine)
    logger.info("Database tables ensured")
    yield


app = FastAPI(
    title="Deligo Mapper API",
    version="1.0.0",
    description="Deligo Mapper API",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
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
app.include_router(driver_router)


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
