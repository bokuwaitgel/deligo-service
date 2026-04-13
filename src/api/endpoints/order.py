from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from schemas.delivery import (
    AddressUpdateRequest,
    DeliveryOrderCreate,
    DeliveryOrderResponse,
    Location,
)
from src.api.auth_utils import require_api_key
from src.dependencies import get_delivery_repository
from src.repositories.delivery import DeliveryRepository
from src.services.delivery import (
    complete_delivery,
    create_delivery,
    get_delivery,
    update_location,
    update_location_by_address,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/delivery", tags=["delivery"])

# ---------------------------------------------------------------------------
# Dummy order detail helper
# ---------------------------------------------------------------------------

def _build_dummy_detail(order: DeliveryOrderResponse) -> dict:
    """Attach dummy line-item / customer detail that the real order service would supply."""
    return {
        "sales_number": order.sales_number,
        "sales_id": order.sales_id,
        "store_id": order.store_id,
        "driver_id": order.driver_id,
        "driver_name": order.driver_name,
        "customer_address": order.customer_address,
        "customer_location": order.customer_location,
        "map_status": order.map_status,
        "tracking_url": order.tracking_url,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        # --- dummy fields filled until the real order service is wired ---
        "customer": {
            "name": "Dummy Customer",
            "phone": "+976-9900-0000",
            "email": "customer@example.com",
        },
        "items": [
            {
                "product_id": "PROD-001",
                "name": "Sample Item A",
                "quantity": 2,
                "unit_price": 15000,
                "total_price": 30000,
            },
            {
                "product_id": "PROD-002",
                "name": "Sample Item B",
                "quantity": 1,
                "unit_price": 8500,
                "total_price": 8500,
            },
        ],
        "payment": {
            "method": "cash",
            "total": 38500,
            "currency": "MNT",
            "is_paid": False,
        },
        "notes": "Please call before arrival.",
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
def create_order_endpoint(
    payload: DeliveryOrderCreate,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Create a new delivery order. Auto-creates driver location record if missing."""
    return create_delivery(repo, payload)


@router.get("/{sales_number}", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
def get_order_endpoint(
    sales_number: str,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Fetch a delivery order by sales number."""
    order = get_delivery(repo, sales_number)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.get("/{sales_number}/detail", dependencies=[Depends(require_api_key)])
def get_order_detail_endpoint(
    sales_number: str,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Fetch delivery order with full detail (customer, items, payment). Dummy data for now."""
    order = get_delivery(repo, sales_number)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"status": "ok", "data": _build_dummy_detail(order)}


@router.patch("/{sales_number}/location", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
def update_order_location_endpoint(
    sales_number: str,
    location: Location,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Update customer delivery location with explicit coordinates."""
    updated = update_location(repo, sales_number, location)
    if updated is None:
        raise HTTPException(status_code=404, detail="Order not found or already completed")
    return updated


@router.patch("/{sales_number}/address", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
def update_order_address_endpoint(
    sales_number: str,
    body: AddressUpdateRequest,
    is_countryside: bool = Query(False),
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Re-geocode a new address and update the delivery location."""
    try:
        updated = update_location_by_address(repo, sales_number, body.customer_address, is_countryside)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if updated is None:
        raise HTTPException(status_code=404, detail="Order not found or already completed")
    return updated


@router.post("/{sales_number}/complete", response_model=DeliveryOrderResponse, dependencies=[Depends(require_api_key)])
def complete_order_endpoint(
    sales_number: str,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Mark delivery as completed — no further location edits allowed."""
    completed = complete_delivery(repo, sales_number)
    if completed is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return completed


@router.get("", response_model=list[DeliveryOrderResponse], dependencies=[Depends(require_api_key)])
def list_orders_endpoint(
    driver_id: str | None = Query(None),
    store_id: str | None = Query(None),
    cursor: str | None = Query(None, description="Pagination cursor (last sales_number)"),
    limit: int = Query(20, ge=1, le=100),
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """List orders by driver or store with cursor-based pagination."""
    if driver_id:
        rows = repo.get_by_driver_id_paginated(driver_id, cursor, limit)
    elif store_id:
        rows = repo.get_by_shop_id_paginated(store_id, cursor, limit)
    else:
        raise HTTPException(status_code=400, detail="Provide driver_id or store_id")
    return [DeliveryOrderResponse.model_validate(r) for r in rows[:limit]]


# ---------------------------------------------------------------------------
# Dummy WFM order list
# ---------------------------------------------------------------------------

_DUMMY_ORDER_ITEMS = [
    {
        "item_id": "1",
        "name": "product 1",
        "image_url": "https://lcshelter.org/wp-content/uploads/2024/11/lewis-clark-animal-shelter-lewiston-idaho-cat.png",
        "quantity": 2,
        "price": 10000.0,
    },
    {
        "item_id": "2",
        "name": "product 2",
        "image_url": "https://www.tasteofhome.com/wp-content/uploads/2024/09/Cook-Burgers-on-the-Stove-FT24_277152_EC_0904_1-vert-social.jpg",
        "quantity": 2,
        "price": 7500.0,
    },
]

_DUMMY_WFM_ORDERS = [
    {"sales_number": "26031012808A", "total": "35000.000000", "created_date": "2026-03-10 11:38:20", "status_created_date": "2026-03-10 11:39:10", "sales_id": 1773113900534670, "customer_phone": "99758526", "delivery_date": "2026-03-10 11:38:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113900534670, "customer_address": "Хөгжим бүжигийн сургуулийн зүүн талд Этүгэн дээд сургууль дээр авна.", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1624, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "2603102D34A4", "total": "35000.000000", "created_date": "2026-03-10 11:38:44", "status_created_date": "2026-03-10 11:39:08", "sales_id": 1773113924373386, "customer_phone": "8886 1861", "delivery_date": "2026-03-10 11:38:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113924373386, "customer_address": "Натур Амартүвшин 101 байр 1-р орц 38 тоот код- 3399#", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1622, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "2603102EED8C", "total": "35000.000000", "created_date": "2026-03-10 11:37:55", "status_created_date": "2026-03-10 11:39:12", "sales_id": 1773113875647245, "customer_phone": "80088970", "delivery_date": "2026-03-10 11:37:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113875647245, "customer_address": "Altai hothon Gvrv zahiin 1g dawhart 80088970", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1626, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "26031048CE79", "total": "35000.000000", "created_date": "2026-03-10 11:32:23", "status_created_date": "2026-03-10 11:39:27", "sales_id": 1773113543582426, "customer_phone": "96355445", "delivery_date": "2026-03-10 11:32:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113543582426, "customer_address": "1-р хороолол Хархорин хороолол 51/4 байр 11 давхар 65 тоот 96355445", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1644, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310559D64", "total": "35000.000000", "created_date": "2026-03-10 11:37:17", "status_created_date": "2026-03-10 11:39:14", "sales_id": 1773113837069154, "customer_phone": "88019062", "delivery_date": "2026-03-10 11:37:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113837069154, "customer_address": "Bagshin deed 1r emngin hajud hudalda hugjlin tuw bank", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1629, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "2603105989E7", "total": "35000.000000", "created_date": "2026-03-10 11:35:37", "status_created_date": "2026-03-10 11:39:21", "sales_id": 1773113737341679, "customer_phone": "89380144", "delivery_date": "2026-03-10 11:35:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113737341679, "customer_address": "Баянхошуу хөтөл 89380144", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1637, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "2603106845BB", "total": "35000.000000", "created_date": "2026-03-10 11:36:28", "status_created_date": "2026-03-10 11:39:18", "sales_id": 1773113788153917, "customer_phone": "88083101", "delivery_date": "2026-03-10 11:36:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113788153917, "customer_address": "Bayngol dvvreg 16 horoo gandangin barun talin gudamj orhoni 8/17 google map der garch irdeg", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1633, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310716A66", "total": "35000.000000", "created_date": "2026-03-10 11:36:06", "status_created_date": "2026-03-10 11:39:20", "sales_id": 1773113766311954, "customer_phone": "89170417", "delivery_date": "2026-03-10 11:36:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113766311954, "customer_address": "Бзд-26 хороо элезабэт хотхон 214-3-157 тоот 89170417", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1635, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "26031075BD12", "total": "35000.000000", "created_date": "2026-03-10 11:37:43", "status_created_date": "2026-03-10 11:39:12", "sales_id": 1773113863583451, "customer_phone": "99102147", "delivery_date": "2026-03-10 11:37:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113863583451, "customer_address": "бгд 27хороо 4а-36 хермэсийн замын эсрэг тал", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1627, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "2603107BFD49", "total": "35000.000000", "created_date": "2026-03-10 11:34:52", "status_created_date": "2026-03-10 11:39:25", "sales_id": 1773113692390035, "customer_phone": "88045695", "delivery_date": "2026-03-10 11:34:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113692390035, "customer_address": "Барс захын баруун талд убтз замын 2-р анги ажлын цагаар", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1641, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310862A22", "total": "35000.000000", "created_date": "2026-03-10 11:38:31", "status_created_date": "2026-03-10 11:39:09", "sales_id": 1773113911092116, "customer_phone": "99081239", "delivery_date": "2026-03-10 11:38:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113911092116, "customer_address": "Хөвсгөл Мөрөн 99081239", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1623, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "2603108A5B9E", "total": "35000.000000", "created_date": "2026-03-10 11:38:08", "status_created_date": "2026-03-10 11:39:11", "sales_id": 1773113888235045, "customer_phone": "91113192", "delivery_date": "2026-03-10 11:38:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113888235045, "customer_address": "19-н автобусны буудлын ард Нутгийн буян хотхон 37а байр 1703 тоот 91113192", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1625, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "2603108DDE8B", "total": "35000.000000", "created_date": "2026-03-10 11:36:40", "status_created_date": "2026-03-10 11:39:17", "sales_id": 1773113800731921, "customer_phone": "99124745", "delivery_date": "2026-03-10 11:36:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113800731921, "customer_address": "songino hairhan duureg 17r horoo 57A-2toot harhorin zahin hoid talin bair", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1632, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310911699", "total": "35000.000000", "created_date": "2026-03-10 11:35:13", "status_created_date": "2026-03-10 11:39:23", "sales_id": 1773113713922384, "customer_phone": "89150515", "delivery_date": "2026-03-10 11:35:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113713922384, "customer_address": "Схд 37-р хороо 21хороолол Содонгийн 114 байр 2-р орц 15 давхар 224 тоот, 89150515", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1639, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "2603109360DC", "total": "35000.000000", "created_date": "2026-03-10 11:32:46", "status_created_date": "2026-03-10 11:39:26", "sales_id": 1773113566948487, "customer_phone": "80108082", "delivery_date": "2026-03-10 11:32:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113566948487, "customer_address": "han uul dvvreg yarmag nukht rvv ugsuud shvnshig 3 243r bair 1r orts 7dawhart 31toot", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1642, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310A299A9", "total": "35000.000000", "created_date": "2026-03-10 11:35:03", "status_created_date": "2026-03-10 11:39:24", "sales_id": 1773113703388603, "customer_phone": "89380144", "delivery_date": "2026-03-10 11:35:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113703388603, "customer_address": "Баянхошуу хөтөл 89380144", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1640, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310AB15BB", "total": "35000.000000", "created_date": "2026-03-10 11:31:59", "status_created_date": "2026-03-10 11:39:29", "sales_id": 1773113519043018, "customer_phone": "89150515", "delivery_date": "2026-03-10 11:31:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113519043018, "customer_address": "Схд 37-р хороо 21хороолол Содонгийн 114 байр 2-р орц 15 давхар 224 тоот, 89150515", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1646, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310AD74F3", "total": "35000.000000", "created_date": "2026-03-10 11:37:30", "status_created_date": "2026-03-10 11:39:13", "sales_id": 1773113850902548, "customer_phone": "86114296", "delivery_date": "2026-03-10 11:37:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113850902548, "customer_address": "дорноговь сайншанд руу", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1628, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310B618A1", "total": "35000.000000", "created_date": "2026-03-10 11:32:35", "status_created_date": "2026-03-10 11:39:26", "sales_id": 1773113555172231, "customer_phone": "89170417", "delivery_date": "2026-03-10 11:32:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113555172231, "customer_address": "Бзд-26 хороо элезабэт хотхон 214-3-157 тоот 89170417", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1643, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310C20095", "total": "35000.000000", "created_date": "2026-03-10 11:35:53", "status_created_date": "2026-03-10 11:39:21", "sales_id": 1773113753356961, "customer_phone": "96355445", "delivery_date": "2026-03-10 11:35:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113753356961, "customer_address": "1-р хороолол Хархорин хороолол 51/4 байр 11 давхар 65 тоот 96355445", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1636, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310C5E2D5", "total": "35000.000000", "created_date": "2026-03-10 11:35:25", "status_created_date": "2026-03-10 11:39:22", "sales_id": 1773113725208004, "customer_phone": "89150515", "delivery_date": "2026-03-10 11:35:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113725208004, "customer_address": "Схд 37-р хороо 21хороолол Содонгийн 114 байр 2-р орц 15 давхар 224 тоот, 89150515", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1638, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310CA92BE", "total": "35000.000000", "created_date": "2026-03-10 11:32:10", "status_created_date": "2026-03-10 11:39:28", "sales_id": 1773113530622667, "customer_phone": "89380144", "delivery_date": "2026-03-10 11:32:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113530622667, "customer_address": "Баянхошуу хөтөл 89380144", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1645, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310E3F4A9", "total": "35000.000000", "created_date": "2026-03-10 11:36:52", "status_created_date": "2026-03-10 11:39:16", "sales_id": 1773113812527298, "customer_phone": "88188403", "delivery_date": "2026-03-10 11:36:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113812527298, "customer_address": "Схд 19р хороо 4а байр 2р орц", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1631, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310EA15EB", "total": "35000.000000", "created_date": "2026-03-10 11:37:05", "status_created_date": "2026-03-10 11:39:15", "sales_id": 1773113825341052, "customer_phone": "99266342", "delivery_date": "2026-03-10 11:37:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113825341052, "customer_address": "БЗД 19-р хороо Саруул тэнгэр 2 замын урд байдаг 103-р бар 37 тоот", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1630, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    {"sales_number": "260310F8BD70", "total": "35000.000000", "created_date": "2026-03-10 11:36:17", "status_created_date": "2026-03-10 11:39:19", "sales_id": 1773113777145084, "customer_phone": "80108082", "delivery_date": "2026-03-10 11:36:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "key": 1773113777145084, "customer_address": "han uul dvvreg yarmag nukht rvv ugsuud shvnshig 3 243r bair 1r orts 7dawhart 31toot", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1634, "is_pay": "0", "custom_order": None, "delivery_time": None, "store_id": 1773113445185308, "driver_id": 1773113257387725, "is_three_status": 0, "company_id": 1773113370301785, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
]


_WFM_BY_SALES_NUMBER: dict = {o["sales_number"]: o for o in _DUMMY_WFM_ORDERS}


def _build_user_view(order: DeliveryOrderResponse) -> dict:
    """Merge a DB delivery order with dummy WFM fields into the user-facing shape."""
    wfm = _WFM_BY_SALES_NUMBER.get(order.sales_number, {})
    return {
        "sales_number": order.sales_number,
        "sales_id": order.sales_id,
        "store_id": order.store_id,
        "driver_id": order.driver_id,
        "driver_name": order.driver_name,
        "status": order.map_status,
        "url": order.tracking_url,
        "location_raw": order.customer_address,
        "location": order.customer_location,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        # location confirmation — placeholder until wired to DB
        "location_confirmed": None,
        "location_confirmed_by": None,
        "location_confirmed_at": None,
        # WFM fields
        "delivery_date": wfm.get("delivery_date"),
        "wfm_status_id": str(wfm.get("wfm_status_id", "")),
        "status_name": wfm.get("status_name"),
        "status_color": wfm.get("status_color"),
        "store_name": wfm.get("store_name"),
        "route_number": str(wfm.get("route_number", "")),
        "customer_phone": wfm.get("customer_phone"),
        "total_price": float(wfm.get("total", 0)),
        "is_closed": bool(wfm.get("is_closed", 0)),
        "is_pay": bool(int(wfm.get("is_pay", 0))),
        "is_download": bool(int(wfm.get("is_download", 0))),
        "is_integration": bool(int(wfm.get("is_integration", 0))),
        "is_direct_pay": bool(int(wfm.get("is_direct_pay", 0))),
        "is_start_driver": bool(wfm.get("is_start_driver")) if wfm.get("is_start_driver") is not None else False,
        "is_country": bool(wfm.get("is_country", 0)),
        "is_countryside": bool(wfm.get("is_country", 0)),
        "exchange_sales_id": wfm.get("exchange_sales_id") or None,
        "return_sales_id": wfm.get("return_sales_id"),
        # dummy order items
        "order_items": _DUMMY_ORDER_ITEMS,
    }


@router.post("/seed-dummy", dependencies=[Depends(require_api_key)])
def seed_dummy_orders_endpoint(
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """Create all dummy WFM orders as delivery orders. Skips any that already exist."""
    existing = set(repo.get_existing_sales_numbers([o["sales_number"] for o in _DUMMY_WFM_ORDERS]))

    created = []
    skipped = []
    failed = []

    for wfm in _DUMMY_WFM_ORDERS:
        sn = wfm["sales_number"]
        if sn in existing:
            skipped.append(sn)
            continue
        try:
            payload = DeliveryOrderCreate(
                sales_number=sn,
                sales_id=str(wfm["sales_id"]),
                store_id=str(wfm["store_id"]),
                driver_id=str(wfm["driver_id"]),
                driver_name=wfm.get("driver_name"),
                customer_address=wfm["customer_address"],
                is_countryside=bool(wfm.get("is_country", 0)),
            )
            create_delivery(repo, payload)
            created.append(sn)
        except Exception as exc:
            repo.db_session.rollback()
            logger.warning("Failed to seed order %s: %s", sn, exc)
            failed.append(sn)

    return {
        "status": "ok",
        "created": len(created),
        "skipped": len(skipped),
        "failed": len(failed),
        "created_numbers": created,
        "failed_numbers": failed,
    }


@router.get("/dummy-wfm-orders", dependencies=[Depends(require_api_key)])
def dummy_wfm_orders_endpoint():
    """Return dummy WFM-format order list for testing."""
    return {
        "status": "success",
        "recordsTotal": len(_DUMMY_WFM_ORDERS),
        "data": _DUMMY_WFM_ORDERS,
    }


@router.get("/user-orders", dependencies=[Depends(require_api_key)])
def user_orders_endpoint(
    driver_id: str | None = Query(None),
    store_id: str | None = Query(None),
    cursor: str | None = Query(None, description="Pagination cursor (last sales_number)"),
    limit: int = Query(20, ge=1, le=100),
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """List orders in user-facing format, merging DB data with dummy WFM fields."""
    if driver_id:
        rows = repo.get_by_driver_id_paginated(driver_id, cursor, limit)
    elif store_id:
        rows = repo.get_by_shop_id_paginated(store_id, cursor, limit)
    else:
        raise HTTPException(status_code=400, detail="Provide driver_id or store_id")

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].sales_number if has_more and page else None

    data = [_build_user_view(DeliveryOrderResponse.model_validate(r)) for r in page]
    return {"status": "ok", "data": data, "next_cursor": next_cursor, "has_more": has_more}


# ---------------------------------------------------------------------------
# Batch detail
# ---------------------------------------------------------------------------

class BatchDetailRequest(BaseModel):
    sales_numbers: list[str] = Field(..., min_length=1, max_length=100)


@router.post("/batch-detail", dependencies=[Depends(require_api_key)])
def batch_order_detail_endpoint(
    body: BatchDetailRequest,
    repo: DeliveryRepository = Depends(get_delivery_repository),
):
    """
    Fetch full detail (with dummy customer/items/payment) for up to 100 orders in one request.
    Returns a map of sales_number -> detail. Missing sales_numbers are omitted.
    """
    rows = repo.get_by_sales_numbers(body.sales_numbers)
    result = {}
    for row in rows:
        order = DeliveryOrderResponse.model_validate(row)
        result[order.sales_number] = _build_dummy_detail(order)
    return {"status": "ok", "data": result}
