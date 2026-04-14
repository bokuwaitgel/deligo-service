from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from schemas.delivery import DriverLocation, DriverLocationResponse
from src.api.auth_utils import require_api_key
from src.dependencies import get_driver_location_repository
from src.repositories.driver_location import DriverLocationRepository
from src.services.driver_location import get_driver_location, upsert_driver_location

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/drivers", tags=["drivers"])


@router.post("/{driver_id}/location", dependencies=[Depends(require_api_key)])
def update_driver_location_endpoint(
    driver_id: str,
    location: DriverLocation,
    repo: DriverLocationRepository = Depends(get_driver_location_repository),
):
    """Driver reports current GPS position"""
    driver_loc = upsert_driver_location(repo, driver_id, location.latitude, location.longitude)
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


