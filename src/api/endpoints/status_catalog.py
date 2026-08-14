"""Delivery-status catalog endpoints — colour + icon + label per wfm status.

Serves the shape `docs/STATUS-CATALOG-API.md` asks Deligo for, from the local
stand-in in `src.services.status_catalog` until they publish theirs. The
frontend already talks to this contract, so the switchover is one env var
(`DELIGO_STATUS_CATALOG_URL`) and no code change.

Resolution order, top wins:

    admin override (Postgres, PUT below)
    Deligo upstream / STATUS_CATALOG_JSON
    local stand-in

Reads are public, like the tracking surface: this is presentation metadata
(which icon, which hex) with no order data in it, and the customer tracking page
and driver view both need it before any authentication step. Writes need the
API key — they change what every driver sees.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from src.api.auth_utils import require_api_key
from src.dependencies import get_status_catalog_override_repository
from src.repositories.status_catalog_override import (
    EDITABLE_FIELDS,
    StatusCatalogOverrideRepository,
)
from src.services import status_catalog
from src.services.deligo_integration import _service_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/status", tags=["status"])

# Material Symbols ligature names are lowercase words joined by underscores.
# Validated because a typo renders as an empty box on the map with no error
# anywhere — the failure is silent and only visible to the driver.
_ICON_RE = re.compile(r"^[a-z0-9_]{2,48}$")
_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _upstream_token() -> Optional[str]:
    """Service token, but only when there is an upstream to send it to.

    `_service_token()` logs into Deligo on a cache miss; the catalog endpoint is
    public and hit on every page load, so asking for a token we would not use
    turns a static payload into an upstream round-trip (and, while the `usermap`
    password is broken, a 401 in the log for every request).
    """
    if not status_catalog.STATUS_CATALOG_URL:
        return None
    return _service_token()


def _resolved(repo: StatusCatalogOverrideRepository, refresh: bool = False) -> Dict[str, Any]:
    """Source catalog with the admin overrides laid on top."""
    catalog = status_catalog.get_catalog(_upstream_token(), refresh=refresh)
    return status_catalog.apply_overrides(catalog, repo.as_dict())


@router.get("/catalog")
def get_status_catalog(
    refresh: bool = Query(False, description="Bypass the upstream cache"),
    repo: StatusCatalogOverrideRepository = Depends(get_status_catalog_override_repository),
):
    """Every delivery status with its colour, icon, label and grouping.

    Always answers 200 with a non-empty `statuses` list — a failed upstream
    fetch degrades to the local catalog rather than erroring, because a driver
    with no legend is worse than a driver with a slightly stale one. Check
    `data.source` to see which copy you got (`deligo` / `env` / `local-dummy`),
    and each entry's `overridden` array for fields an operator has changed.
    """
    return {"status": "ok", "data": _resolved(repo, refresh=refresh)}


@router.get("/catalog/{wfm_status_id}")
def get_one_status(
    wfm_status_id: int,
    status_code: Optional[str] = Query(None),
    repo: StatusCatalogOverrideRepository = Depends(get_status_catalog_override_repository),
):
    """One status, or the `unknown` entry when the id is not in the catalog."""
    catalog = _resolved(repo)
    return {
        "status": "ok",
        "data": {
            "source": catalog["source"],
            "status": status_catalog.find_status(catalog, wfm_status_id, status_code),
        },
    }


@router.get("/catalog/meta/health", dependencies=[Depends(require_api_key)])
def get_catalog_health(
    repo: StatusCatalogOverrideRepository = Depends(get_status_catalog_override_repository),
):
    """Admin view: where the catalog came from and what is missing from it.

    `pending_from_deligo` is the list we are still waiting on — it is what the
    admin tab prints so nobody has to re-read the requirements doc to answer
    "are the colours real yet?".
    """
    catalog = _resolved(repo)
    statuses = catalog.get("statuses", [])
    return {
        "status": "ok",
        "data": {
            "source": catalog["source"],
            "version": catalog["version"],
            "generated_at": catalog["generated_at"],
            "note": catalog.get("note"),
            "upstream_url": status_catalog.STATUS_CATALOG_URL or None,
            "cache_ttl_seconds": status_catalog.STATUS_CATALOG_TTL,
            "status_count": len(statuses),
            "flag_count": len(catalog.get("flags", [])),
            "override_count": catalog.get("override_count", 0),
            "editable_fields": list(EDITABLE_FIELDS),
            "missing_icon": [s["wfm_status_id"] for s in statuses if not s.get("icon")],
            "missing_color": [s["wfm_status_id"] for s in statuses if not s.get("color")],
            "is_placeholder": catalog["source"] != status_catalog.CatalogSource.UPSTREAM,
            "pending_from_deligo": [
                "Төлвийн бүрэн жагсаалт (id + код + монгол нэр + тайлбар)",
                "Төлөв бүрийн icon (нэр эсвэл SVG/PNG файл)",
                "Төлөв бүрийн албан ёсны өнгө (hex)",
                "Шинэ төлөв нэмэгдэхэд мэдэгдэх / каталог endpoint",
            ] if catalog["source"] != status_catalog.CatalogSource.UPSTREAM else [],
        },
    }


class StatusOverrideRequest(BaseModel):
    """One status' presentation, as edited in the admin panel.

    Field semantics are three-way and the distinction matters:
      * absent      → leave whatever is stored alone
      * ``""``      → clear the override, inherit from the source catalog again
      * a value     → override with it

    That is why every field is `Optional` with a `None` default *and* empty
    strings are meaningful: "revert the icon" and "don't touch the icon" are
    different requests.
    """

    icon: Optional[str] = Field(None, description="Material Symbols name, or '' to revert")
    color: Optional[str] = Field(None, description="Hex like #0ea5e9, or '' to revert")
    pin_color: Optional[str] = Field(None, description="Map pin hex, or '' to revert")
    label: Optional[str] = Field(None, max_length=120)
    description: Optional[str] = Field(None, max_length=400)
    updated_by: Optional[str] = Field(None, max_length=80, description="Who is making the change")

    @field_validator("icon")
    @classmethod
    def _check_icon(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value.strip() == "":
            return value
        name = value.strip()
        if not _ICON_RE.match(name):
            raise ValueError(
                "icon must be a Material Symbols name — lowercase letters, digits "
                "and underscores only (e.g. local_shipping)"
            )
        return name

    @field_validator("color", "pin_color")
    @classmethod
    def _check_color(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value.strip() == "":
            return value
        colour = value.strip()
        if not _HEX_RE.match(colour):
            raise ValueError("colour must be a hex value like #0ea5e9")
        return colour


@router.put("/catalog/{wfm_status_id}", dependencies=[Depends(require_api_key)])
def upsert_status_override(
    wfm_status_id: int,
    payload: StatusOverrideRequest,
    repo: StatusCatalogOverrideRepository = Depends(get_status_catalog_override_repository),
):
    """Override how one status is drawn, everywhere, immediately.

    Rejects ids the catalog does not know: an override for a status that will
    never arrive is invisible dead config, and the likeliest cause is a typo in
    the id rather than a status we have not heard of yet.
    """
    catalog = status_catalog.get_catalog(_upstream_token())
    known = {s.get("wfm_status_id") for s in catalog.get("statuses", [])}
    if wfm_status_id not in known:
        raise HTTPException(
            status_code=404,
            detail=f"wfm_status_id {wfm_status_id} is not in the catalog ({sorted(known)})",
        )

    # `exclude_unset` is what preserves the absent-vs-empty distinction: only
    # fields the caller actually sent are touched.
    sent = payload.model_dump(exclude_unset=True)
    updated_by = sent.pop("updated_by", None)
    fields = {k: v for k, v in sent.items() if k in EDITABLE_FIELDS}
    if not fields:
        raise HTTPException(status_code=400, detail="No editable fields in the request")

    repo.upsert(wfm_status_id, fields, updated_by=updated_by)
    logger.info(
        "Status catalog override saved: wfm=%s fields=%s by=%s",
        wfm_status_id, sorted(fields), updated_by or "unknown",
    )

    resolved = status_catalog.apply_overrides(catalog, repo.as_dict())
    return {
        "status": "ok",
        "data": {
            "status": status_catalog.find_status(resolved, wfm_status_id),
            "override_count": resolved.get("override_count", 0),
        },
    }


@router.delete("/catalog/{wfm_status_id}", dependencies=[Depends(require_api_key)])
def delete_status_override(
    wfm_status_id: int,
    repo: StatusCatalogOverrideRepository = Depends(get_status_catalog_override_repository),
):
    """Drop every override for one status — back to whatever the source says."""
    removed = repo.delete(wfm_status_id)
    catalog = status_catalog.get_catalog(_upstream_token())
    resolved = status_catalog.apply_overrides(catalog, repo.as_dict())
    return {
        "status": "ok",
        "data": {
            "removed": removed,
            "status": status_catalog.find_status(resolved, wfm_status_id),
            "override_count": resolved.get("override_count", 0),
        },
    }
