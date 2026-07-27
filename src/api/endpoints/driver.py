from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from schemas.delivery import DriverLocation, DriverLocationResponse
from src.api.auth_utils import require_api_key
from src.dependencies import get_delivery_repository, get_driver_location_repository
from src.repositories.delivery import DeliveryRepository
from src.repositories.driver_location import DriverLocationRepository
from src.services.driver_location import (
    announce_driver_movement,
    get_driver_location,
    upsert_driver_location,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/drivers", tags=["drivers"])


@router.get("/locations", dependencies=[Depends(require_api_key)])
def get_all_driver_locations(
    repo: DriverLocationRepository = Depends(get_driver_location_repository),
):
    """Get all known driver locations"""
    locs = repo.get_all()
    return {
        "status": "ok",
        "data": [
            {
                "driver_id": loc.driver_id,
                "latitude": loc.latitude,
                "longitude": loc.longitude,
                "updated_at": loc.updated_at.isoformat(),
            }
            for loc in locs
        ],
    }


@router.post("/{driver_id}/location", dependencies=[Depends(require_api_key)])
def update_driver_location_endpoint(
    driver_id: str,
    location: DriverLocation,
    repo: DriverLocationRepository = Depends(get_driver_location_repository),
    delivery_repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Driver reports current GPS position"""
    # Read the previous fix BEFORE the upsert overwrites it — the nearby/far
    # crossing test needs both points.
    existing = get_driver_location(repo, driver_id)
    previous = (existing.latitude, existing.longitude) if existing else None

    driver_loc = upsert_driver_location(repo, driver_id, location.latitude, location.longitude)
    announce_driver_movement(
        delivery_repo, driver_id, previous, location.latitude, location.longitude,
    )
    return {
        "status": "ok",
        "data": {
            "driver_id": driver_loc.driver_id,
            "latitude": driver_loc.latitude,
            "longitude": driver_loc.longitude,
            "updated_at": driver_loc.updated_at.isoformat()
        }
    }


@router.get("/{driver_id}/location", dependencies=[Depends(require_api_key)])
def get_driver_location_endpoint(
    driver_id: str,
    repo: DriverLocationRepository = Depends(get_driver_location_repository),
):
    """Get driver's last known location"""
    driver_loc = get_driver_location(repo, driver_id)
    if not driver_loc:
        raise HTTPException(status_code=404, detail="Driver location not found")

    return {
        "status": "ok",
        "data": {
            "driver_id": driver_loc.driver_id,
            "latitude": driver_loc.latitude,
            "longitude": driver_loc.longitude,
            "updated_at": driver_loc.updated_at.isoformat()
        }
    }


@router.get("/{driver_id}/location/public")
def get_driver_location_public(
    driver_id: str,
    repo: DriverLocationRepository = Depends(get_driver_location_repository),
):
    """Get driver's last known location (public, no authentication required)"""
    driver_loc = get_driver_location(repo, driver_id)
    if not driver_loc:
        raise HTTPException(status_code=404, detail="Driver location not found")

    return {
        "status": "ok",
        "data": {
            "driver_id": driver_loc.driver_id,
            "latitude": driver_loc.latitude,
            "longitude": driver_loc.longitude,
            "updated_at": driver_loc.updated_at.isoformat()
        }
    }


