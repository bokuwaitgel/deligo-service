import logging

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.endpoints.health import router as health_router
from src.api.endpoints.location import router as location_router

load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Deligo Mapper API",
    version="1.0.0",
    description="Deligo Mapper API ",
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

# Register routers
app.include_router(health_router)
app.include_router(location_router)

@app.get("/", tags=["system"])
async def root():
    return {
        "message": "Welcome to the Deligo Mapper API.",
        "docs": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
    }
