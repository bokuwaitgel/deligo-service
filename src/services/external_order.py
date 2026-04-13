"""External order service client.

This service fetches order details (items, totals, customer info, etc.) from the
dedicated order service. Deligo-service only stores location-related data locally;
everything else comes from here.

Replace the dummy fallback with real URLs once the order service is deployed.
Set ORDER_SERVICE_URL in your .env to point at the order service.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "")


_DUMMY_ORDERS: Dict[str, Dict[str, Any]] = {
    o["sales_number"]: o
    for o in [
        {"sales_number": "26031012808A", "total": "35000.000000", "created_date": "2026-03-10 11:38:20", "status_created_date": "2026-03-10 11:39:10", "sales_id": 1773113900534670, "customer_phone": "99758526", "delivery_date": "2026-03-10 11:38:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Хөгжим бүжигийн сургуулийн зүүн талд Этүгэн дээд сургууль дээр авна.", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1624, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "2603102D34A4", "total": "35000.000000", "created_date": "2026-03-10 11:38:44", "status_created_date": "2026-03-10 11:39:08", "sales_id": 1773113924373386, "customer_phone": "8886 1861", "delivery_date": "2026-03-10 11:38:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Натур Амартүвшин 101 байр 1-р орц 38 тоот код- 3399#", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1622, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "2603102EED8C", "total": "35000.000000", "created_date": "2026-03-10 11:37:55", "status_created_date": "2026-03-10 11:39:12", "sales_id": 1773113875647245, "customer_phone": "80088970", "delivery_date": "2026-03-10 11:37:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Altai hothon Gvrv zahiin 1g dawhart 80088970", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1626, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "26031048CE79", "total": "35000.000000", "created_date": "2026-03-10 11:32:23", "status_created_date": "2026-03-10 11:39:27", "sales_id": 1773113543582426, "customer_phone": "96355445", "delivery_date": "2026-03-10 11:32:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "1-р хороолол Хархорин хороолол 51/4 байр 11 давхар 65 тоот 96355445", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1644, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310559D64", "total": "35000.000000", "created_date": "2026-03-10 11:37:17", "status_created_date": "2026-03-10 11:39:14", "sales_id": 1773113837069154, "customer_phone": "88019062", "delivery_date": "2026-03-10 11:37:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Bagshin deed 1r emngin hajud hudalda hugjlin tuw bank", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1629, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "2603105989E7", "total": "35000.000000", "created_date": "2026-03-10 11:35:37", "status_created_date": "2026-03-10 11:39:21", "sales_id": 1773113737341679, "customer_phone": "89380144", "delivery_date": "2026-03-10 11:35:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Баянхошуу хөтөл 89380144", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1637, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "2603106845BB", "total": "35000.000000", "created_date": "2026-03-10 11:36:28", "status_created_date": "2026-03-10 11:39:18", "sales_id": 1773113788153917, "customer_phone": "88083101", "delivery_date": "2026-03-10 11:36:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Bayngol dvvreg 16 horoo gandangin barun talin gudamj orhoni 8/17 google map der garch irdeg", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1633, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310716A66", "total": "35000.000000", "created_date": "2026-03-10 11:36:06", "status_created_date": "2026-03-10 11:39:20", "sales_id": 1773113766311954, "customer_phone": "89170417", "delivery_date": "2026-03-10 11:36:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Бзд-26 хороо элезабэт хотхон 214-3-157 тоот 89170417", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1635, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "26031075BD12", "total": "35000.000000", "created_date": "2026-03-10 11:37:43", "status_created_date": "2026-03-10 11:39:12", "sales_id": 1773113863583451, "customer_phone": "99102147", "delivery_date": "2026-03-10 11:37:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "бгд 27хороо 4а-36 хермэсийн замын эсрэг тал", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1627, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "2603107BFD49", "total": "35000.000000", "created_date": "2026-03-10 11:34:52", "status_created_date": "2026-03-10 11:39:25", "sales_id": 1773113692390035, "customer_phone": "88045695", "delivery_date": "2026-03-10 11:34:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Барс захын баруун талд убтз замын 2-р анги ажлын цагаар", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1641, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310862A22", "total": "35000.000000", "created_date": "2026-03-10 11:38:31", "status_created_date": "2026-03-10 11:39:09", "sales_id": 1773113911092116, "customer_phone": "99081239", "delivery_date": "2026-03-10 11:38:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Хөвсгөл Мөрөн 99081239", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1623, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "2603108A5B9E", "total": "35000.000000", "created_date": "2026-03-10 11:38:08", "status_created_date": "2026-03-10 11:39:11", "sales_id": 1773113888235045, "customer_phone": "91113192", "delivery_date": "2026-03-10 11:38:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "19-н автобусны буудлын ард Нутгийн буян хотхон 37а байр 1703 тоот 91113192", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1625, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "2603108DDE8B", "total": "35000.000000", "created_date": "2026-03-10 11:36:40", "status_created_date": "2026-03-10 11:39:17", "sales_id": 1773113800731921, "customer_phone": "99124745", "delivery_date": "2026-03-10 11:36:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "songino hairhan duureg 17r horoo 57A-2toot harhorin zahin hoid talin bair", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1632, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310911699", "total": "35000.000000", "created_date": "2026-03-10 11:35:13", "status_created_date": "2026-03-10 11:39:23", "sales_id": 1773113713922384, "customer_phone": "89150515", "delivery_date": "2026-03-10 11:35:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Схд 37-р хороо 21хороолол Содонгийн 114 байр 2-р орц 15 давхар 224 тоот, 89150515", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1639, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "2603109360DC", "total": "35000.000000", "created_date": "2026-03-10 11:32:46", "status_created_date": "2026-03-10 11:39:26", "sales_id": 1773113566948487, "customer_phone": "80108082", "delivery_date": "2026-03-10 11:32:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "han uul dvvreg yarmag nukht rvv ugsuud shvnshig 3 243r bair 1r orts 7dawhart 31toot", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1642, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310A299A9", "total": "35000.000000", "created_date": "2026-03-10 11:35:03", "status_created_date": "2026-03-10 11:39:24", "sales_id": 1773113703388603, "customer_phone": "89380144", "delivery_date": "2026-03-10 11:35:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Баянхошуу хөтөл 89380144", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1640, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310AB15BB", "total": "35000.000000", "created_date": "2026-03-10 11:31:59", "status_created_date": "2026-03-10 11:39:29", "sales_id": 1773113519043018, "customer_phone": "89150515", "delivery_date": "2026-03-10 11:31:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Схд 37-р хороо 21хороолол Содонгийн 114 байр 2-р орц 15 давхар 224 тоот, 89150515", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1646, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310AD74F3", "total": "35000.000000", "created_date": "2026-03-10 11:37:30", "status_created_date": "2026-03-10 11:39:13", "sales_id": 1773113850902548, "customer_phone": "86114296", "delivery_date": "2026-03-10 11:37:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "дорноговь сайншанд руу", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1628, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310B618A1", "total": "35000.000000", "created_date": "2026-03-10 11:32:35", "status_created_date": "2026-03-10 11:39:26", "sales_id": 1773113555172231, "customer_phone": "89170417", "delivery_date": "2026-03-10 11:32:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Бзд-26 хороо элезабэт хотхон 214-3-157 тоот 89170417", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1643, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310C20095", "total": "35000.000000", "created_date": "2026-03-10 11:35:53", "status_created_date": "2026-03-10 11:39:21", "sales_id": 1773113753356961, "customer_phone": "96355445", "delivery_date": "2026-03-10 11:35:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "1-р хороолол Хархорин хороолол 51/4 байр 11 давхар 65 тоот 96355445", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1636, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310C5E2D5", "total": "35000.000000", "created_date": "2026-03-10 11:35:25", "status_created_date": "2026-03-10 11:39:22", "sales_id": 1773113725208004, "customer_phone": "89150515", "delivery_date": "2026-03-10 11:35:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Схд 37-р хороо 21хороолол Содонгийн 114 байр 2-р орц 15 давхар 224 тоот, 89150515", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1638, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310CA92BE", "total": "35000.000000", "created_date": "2026-03-10 11:32:10", "status_created_date": "2026-03-10 11:39:28", "sales_id": 1773113530622667, "customer_phone": "89380144", "delivery_date": "2026-03-10 11:32:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Баянхошуу хөтөл 89380144", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1645, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310E3F4A9", "total": "35000.000000", "created_date": "2026-03-10 11:36:52", "status_created_date": "2026-03-10 11:39:16", "sales_id": 1773113812527298, "customer_phone": "88188403", "delivery_date": "2026-03-10 11:36:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "Схд 19р хороо 4а байр 2р орц", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1631, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310EA15EB", "total": "35000.000000", "created_date": "2026-03-10 11:37:05", "status_created_date": "2026-03-10 11:39:15", "sales_id": 1773113825341052, "customer_phone": "99266342", "delivery_date": "2026-03-10 11:37:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "БЗД 19-р хороо Саруул тэнгэр 2 замын урд байдаг 103-р бар 37 тоот", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1630, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
        {"sales_number": "260310F8BD70", "total": "35000.000000", "created_date": "2026-03-10 11:36:17", "status_created_date": "2026-03-10 11:39:19", "sales_id": 1773113777145084, "customer_phone": "80108082", "delivery_date": "2026-03-10 11:36:00", "wfm_status_id": 5, "status_name": "Хуваарилсан", "status_color": "lime", "driver_name": "Test Map jolooch", "customer_address": "han uul dvvreg yarmag nukht rvv ugsuud shvnshig 3 243r bair 1r orts 7dawhart 31toot", "is_closed": 0, "store_name": "Map test delguur", "route_number": 1634, "is_pay": "0", "store_id": 1773113445185308, "driver_id": 1773113257387725, "exchange_sales_id": 0, "is_country": 0, "is_download": "0", "is_integration": "0", "is_start_driver": None, "is_direct_pay": "0", "return_sales_id": None},
    ]
}

DUMMY_SALES_NUMBERS: List[str] = list(_DUMMY_ORDERS.keys())

_DUMMY_ORDER_ITEMS: List[Dict[str, Any]] = [
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


def get_order_detail(sales_number: str) -> Optional[Dict[str, Any]]:
    """Fetch order details from the external order service.

    Returns None if the order is not found. Falls back to dummy data when
    ORDER_SERVICE_URL is not configured (development / local use).
    """
    if not ORDER_SERVICE_URL:
        logger.debug("ORDER_SERVICE_URL not set — returning dummy order detail for %s", sales_number)
        return _dummy_order_detail(sales_number)

    try:
        url = f"{ORDER_SERVICE_URL}/api/orders/{sales_number}"
        response = httpx.get(url, timeout=5.0)
        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            return None
        logger.warning(
            "Order service returned %s for order %s", response.status_code, sales_number
        )
    except Exception:
        logger.warning("Failed to reach order service for order %s", sales_number, exc_info=True)

    return None


def get_new_sales_numbers(existing: List[str]) -> List[str]:
    """Return dummy sales_numbers not yet in the given existing list."""
    existing_set = set(existing)
    return [sn for sn in DUMMY_SALES_NUMBERS if sn not in existing_set]


def _dummy_order_detail(sales_number: str) -> Optional[Dict[str, Any]]:
    """Return dummy data for a known sales_number, or None if unknown."""
    order = _DUMMY_ORDERS.get(sales_number)
    if order is None:
        return None
    return {**order, "order_items": _DUMMY_ORDER_ITEMS}
