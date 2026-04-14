"""
Seed script — wipe all delivery orders and recreate the 25 test orders.

Usage:
    python seed.py                        # uses API at http://localhost:8000
    python seed.py --api http://host:8000
    python seed.py --dry-run              # print payloads without calling API
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

import os
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# 25 test orders (mirrors _DUMMY_ORDERS in middleware_order.py)
# ---------------------------------------------------------------------------
ORDERS = [
    {"sales_number": "26031012808A", "sales_id": "1773113900534670", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Хөгжим бүжигийн сургуулийн зүүн талд Этүгэн дээд сургууль дээр авна.", "is_countryside": False},
    {"sales_number": "2603102D34A4", "sales_id": "1773113924373386", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Натур Амартүвшин 101 байр 1-р орц 38 тоот код- 3399#", "is_countryside": False},
    {"sales_number": "2603102EED8C", "sales_id": "1773113875647245", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Altai hothon Gvrv zahiin 1g dawhart 80088970", "is_countryside": False},
    {"sales_number": "26031048CE79", "sales_id": "1773113543582426", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "1-р хороолол Хархорин хороолол 51/4 байр 11 давхар 65 тоот 96355445", "is_countryside": False},
    {"sales_number": "260310559D64", "sales_id": "1773113837069154", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Bagshin deed 1r emngin hajud hudalda hugjlin tuw bank", "is_countryside": False},
    {"sales_number": "2603105989E7", "sales_id": "1773113737341679", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Баянхошуу хөтөл 89380144", "is_countryside": False},
    {"sales_number": "2603106845BB", "sales_id": "1773113788153917", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Bayngol dvvreg 16 horoo gandangin barun talin gudamj orhoni 8/17 google map der garch irdeg", "is_countryside": False},
    {"sales_number": "260310716A66", "sales_id": "1773113766311954", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Бзд-26 хороо элезабэт хотхон 214-3-157 тоот 89170417", "is_countryside": False},
    {"sales_number": "26031075BD12", "sales_id": "1773113863583451", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "бгд 27хороо 4а-36 хермэсийн замын эсрэг тал", "is_countryside": False},
    {"sales_number": "2603107BFD49", "sales_id": "1773113692390035", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Барс захын баруун талд убтз замын 2-р анги ажлын цагаар", "is_countryside": False},
    {"sales_number": "260310862A22", "sales_id": "1773113911092116", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Хөвсгөл Мөрөн 99081239", "is_countryside": True},
    {"sales_number": "2603108A5B9E", "sales_id": "1773113888235045", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "19-н автобусны буудлын ард Нутгийн буян хотхон 37а байр 1703 тоот 91113192", "is_countryside": False},
    {"sales_number": "2603108DDE8B", "sales_id": "1773113800731921", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "songino hairhan duureg 17r horoo 57A-2toot harhorin zahin hoid talin bair", "is_countryside": False},
    {"sales_number": "260310911699", "sales_id": "1773113713922384", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Схд 37-р хороо 21хороолол Содонгийн 114 байр 2-р орц 15 давхар 224 тоот, 89150515", "is_countryside": False},
    {"sales_number": "2603109360DC", "sales_id": "1773113566948487", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "han uul dvvreg yarmag nukht rvv ugsuud shvnshig 3 243r bair 1r orts 7dawhart 31toot", "is_countryside": False},
    {"sales_number": "260310A299A9", "sales_id": "1773113703388603", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Баянхошуу хөтөл 89380144", "is_countryside": False},
    {"sales_number": "260310AB15BB", "sales_id": "1773113519043018", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Схд 37-р хороо 21хороолол Содонгийн 114 байр 2-р орц 15 давхар 224 тоот, 89150515", "is_countryside": False},
    {"sales_number": "260310AD74F3", "sales_id": "1773113850902548", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "дорноговь сайншанд руу", "is_countryside": True},
    {"sales_number": "260310B618A1", "sales_id": "1773113555172231", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Бзд-26 хороо элезабэт хотхон 214-3-157 тоот 89170417", "is_countryside": False},
    {"sales_number": "260310C20095", "sales_id": "1773113753356961", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "1-р хороолол Хархорин хороолол 51/4 байр 11 давхар 65 тоот 96355445", "is_countryside": False},
    {"sales_number": "260310C5E2D5", "sales_id": "1773113725208004", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Схд 37-р хороо 21хороолол Содонгийн 114 байр 2-р орц 15 давхар 224 тоот, 89150515", "is_countryside": False},
    {"sales_number": "260310CA92BE", "sales_id": "1773113530622667", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Баянхошуу хөтөл 89380144", "is_countryside": False},
    {"sales_number": "260310E3F4A9", "sales_id": "1773113812527298", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "Схд 19р хороо 4а байр 2р орц", "is_countryside": False},
    {"sales_number": "260310EA15EB", "sales_id": "1773113825341052", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "БЗД 19-р хороо Саруул тэнгэр 2 замын урд байдаг 103-р бар 37 тоот", "is_countryside": False},
    {"sales_number": "260310F8BD70", "sales_id": "1773113777145084", "store_id": "1773113445185308", "driver_id": "1773113257387725", "driver_name": "Test Map jolooch", "customer_address": "han uul dvvreg yarmag nukht rvv ugsuud shvnshig 3 243r bair 1r orts 7dawhart 31toot", "is_countryside": False},
]


def clear_db() -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)

    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM delivery_orders"))
    print(f"  DB: deleted all rows from delivery_orders")


def create_orders(api: str, api_key: str, dry_run: bool) -> None:
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    ok = 0
    fail = 0

    for order in ORDERS:
        if dry_run:
            print(f"  DRY  {order['sales_number']}  {order['customer_address'][:50]}")
            continue

        try:
            r = httpx.post(f"{api}/api/delivery/", json=order, headers=headers, timeout=30)
            if r.status_code == 201:
                data = r.json()
                print(f"  OK   {order['sales_number']}  url={data.get('tracking_url') or '—'}")
                ok += 1
            else:
                print(f"  FAIL {order['sales_number']}  status={r.status_code}  {r.text[:120]}")
                fail += 1
        except Exception as e:
            print(f"  ERR  {order['sales_number']}  {e}")
            fail += 1

        time.sleep(0.3)  # avoid hammering geocoding API

    if not dry_run:
        print(f"\nDone — {ok} created, {fail} failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000", help="Base API URL")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads without calling API")
    args = parser.parse_args()

    api_key = os.getenv("API_KEY", "test")

    if not args.dry_run:
        print("Step 1 — clearing delivery_orders table...")
        clear_db()
        print()

    print(f"Step 2 — creating {len(ORDERS)} orders via {args.api} ...")
    create_orders(args.api, api_key, args.dry_run)


if __name__ == "__main__":
    main()
