import json
import sqlite3
from pathlib import Path

import pytest

from server.adminrepo import PAGE_SIZE, AdminRepo
from server.domain import load_domain

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "server" / "db" / "schema.sql"


@pytest.fixture(scope="module")
def admin(modumall_dir_module):
    con = sqlite3.connect(str(load_domain(modumall_dir_module).db_path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return AdminRepo(con)


def expected_count(admin, sql, *args):
    return admin.con.execute(sql, args).fetchone()[0]


def test_orders_list_default_page(admin):
    rows, total = admin.orders()
    assert total == expected_count(admin, "select count(*) from orders")
    assert len(rows) == PAGE_SIZE
    # 최신순 정렬
    assert [r["ordered_at"] for r in rows] == sorted([r["ordered_at"] for r in rows], reverse=True)
    # 목록에 필요한 열만이 아니라, 고객 이름이 조인돼 있어야 한다
    assert "customer_name" in rows[0] and "order_amount" in rows[0]


def test_orders_filter_by_status_and_customer(admin):
    rows, total = admin.orders(status="배송중")
    assert total == expected_count(admin, "select count(*) from orders where status=?", "배송중")
    assert all(r["status"] == "배송중" for r in rows)

    cid = rows[0]["customer_id"]
    rows2, total2 = admin.orders(customer_id=cid)
    assert total2 == expected_count(admin, "select count(*) from orders where customer_id=?", cid)
    assert all(r["customer_id"] == cid for r in rows2)


def test_orders_filter_by_date_range(admin):
    rows, total = admin.orders(date_from="2026-09-01", date_to="2026-09-30")
    assert total == expected_count(
        admin, "select count(*) from orders where ordered_at >= ? and ordered_at <= ?",
        "2026-09-01", "2026-09-30T23:59:59")
    assert all(r["ordered_at"][:7] == "2026-09" for r in rows)


def test_orders_paging_boundaries(admin):
    _, total = admin.orders()
    last = (total + PAGE_SIZE - 1) // PAGE_SIZE
    rows_last, _ = admin.orders(page=last)
    assert 1 <= len(rows_last) <= PAGE_SIZE
    rows_over, _ = admin.orders(page=last + 5)
    assert rows_over == []
    rows_zero, _ = admin.orders(page=0)          # 0 이하는 1페이지로 취급
    rows_one, _ = admin.orders(page=1)
    assert [r["order_id"] for r in rows_zero] == [r["order_id"] for r in rows_one]


def test_returns_filters(admin):
    rows, total = admin.returns(stage="검품중")
    assert total == expected_count(admin, "select count(*) from returns where stage=?", "검품중")
    assert all(r["stage"] == "검품중" for r in rows)

    rows2, total2 = admin.returns(type="교환")
    assert total2 == expected_count(admin, "select count(*) from returns where type=?", "교환")
    assert all(r["type"] == "교환" for r in rows2)
    assert "customer_name" in rows2[0] and "order_id" in rows2[0]


def test_calls_list_parses_turns(admin):
    rows, total = admin.calls()
    assert total == expected_count(admin, "select count(*) from call_logs")
    assert [r["started_at"] for r in rows] == sorted([r["started_at"] for r in rows], reverse=True)

    # call_id='d64a0e12c1fd' 는 turns 가 비어있지 않은 실제 DB 행이다 (DB 에서 직접 확인).
    # turns == [{"q": "소재요", "route": "PRODUCT_INFO", ...}] 1개.
    target = next(r for r in rows if r["call_id"] == "d64a0e12c1fd")
    assert target["turn_count"] == 1
    assert target["routes"] == ["PRODUCT_INFO"]


def test_calls_route_dedup_preserves_order():
    # 같은 route 가 여러 턴에 반복될 때 중복 제거되는지는 실제 DB 에 그런 데이터가 없으므로
    # 인메모리 SQLite 에 직접 시드해 검증한다. AdminRepo/adminrepo.py 는 읽기만 하고
    # 쓰기(insert)는 이 테스트 픽스처 전용이다.
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    turns = [
        {"q": "1", "route": "PRODUCT_INFO"},
        {"q": "2", "route": "SHIPPING"},
        {"q": "3", "route": "PRODUCT_INFO"},
    ]
    con.execute(
        "insert into call_logs (call_id, customer_id, started_at, ended_at, turns) values (?,?,?,?,?)",
        ("dup-call", None, "2026-09-01T00:00:00", "2026-09-01T00:05:00", json.dumps(turns, ensure_ascii=False)),
    )
    con.commit()

    admin = AdminRepo(con)
    rows, total = admin.calls()
    assert total == 1
    r = rows[0]
    assert r["turn_count"] == 3
    assert r["routes"] == ["PRODUCT_INFO", "SHIPPING"]  # 등장 순서 유지, 중복 제거


def test_customers_search_by_name_and_phone(admin):
    rows, _ = admin.customers()
    target = rows[0]

    by_name, total_name = admin.customers(q=target["name"])
    assert total_name >= 1 and any(c["customer_id"] == target["customer_id"] for c in by_name)

    by_phone, _ = admin.customers(q=target["phone"].replace("-", ""))
    assert any(c["customer_id"] == target["customer_id"] for c in by_phone)

    assert admin.customers(q="존재하지않는고객")[1] == 0


def test_customers_search_escapes_like_wildcards(admin):
    # LIKE 특수문자 %, _ 가 와일드카드로 해석되면 안 된다 (실측: 이스케이프 없으면 q='%' 가 전체건수 반환)
    assert admin.customers(q="%")[1] == 0
    assert admin.customers(q="_")[1] == 0
