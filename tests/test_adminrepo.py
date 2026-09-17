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
    # 공유 DB(다른 테스트가 call_logs 를 계속 기록해 내용이 바뀐다)는 불변식만 본다.
    # turns 의 구체적인 내용·특정 call_id 는 하드코딩하지 않는다 — 값 검증은 인메모리 테스트에서.
    rows, total = admin.calls()
    assert total == expected_count(admin, "select count(*) from call_logs")
    assert [r["started_at"] for r in rows] == sorted([r["started_at"] for r in rows], reverse=True)
    for r in rows:
        assert r["turn_count"] == len(r["turns"])
        assert isinstance(r["routes"], list)
        assert len(r["routes"]) == len(set(r["routes"]))  # 중복 없음


def _memory_con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return con


def test_calls_parses_turns_and_dedups_routes_in_memory():
    # turn_count·routes 의 구체적인 값과, 같은 route 가 반복될 때 중복 제거되는지는
    # 내용이 계속 바뀌는 공유 DB 로는 검증할 수 없으므로 인메모리 SQLite 에 직접 시드해 확인한다.
    # AdminRepo/server/adminrepo.py 자체는 읽기만 하고, insert 는 이 테스트 픽스처 전용이다.
    con = _memory_con()
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


def test_order_detail_joins_items_events_return_customer(admin):
    o = admin.order_detail("O-1006")
    assert o["order_id"] == "O-1006"
    assert o["items"] and o["items"][0]["qty"] >= 1
    assert isinstance(o["events"], list)
    assert o["return_"]["return_id"] == "R-2001"
    assert o["return_"]["stage_history"][0]["stage"] == "접수"
    assert o["customer"]["customer_id"] == o["customer_id"]
    assert admin.order_detail("O-9999") is None


def test_return_detail_has_history_and_order(admin):
    r = admin.return_detail("R-2001")
    assert r["order_id"] == "O-1006"
    assert [h["stage"] for h in r["stage_history"]][:2] == ["접수", "수거대기"]
    assert r["order"]["order_id"] == "O-1006"
    assert admin.return_detail("R-9999") is None


def test_call_detail_parses_turns(admin):
    rows, _ = admin.calls()
    c = admin.call_detail(rows[0]["call_id"])
    assert c["call_id"] == rows[0]["call_id"]
    assert isinstance(c["turns"], list)
    assert "customer" in c  # 비회원 통화면 None
    assert admin.call_detail("없는통화") is None


def test_customer_detail_has_orders_and_calls(admin):
    rows, _ = admin.customers()
    cid = next(c["customer_id"] for c in rows
               if admin.con.execute("select count(*) from orders where customer_id=?", (c["customer_id"],)).fetchone()[0] > 0)
    d = admin.customer_detail(cid)
    assert d["customer_id"] == cid
    assert d["orders"] and all(o["customer_id"] == cid for o in d["orders"])
    assert isinstance(d["calls"], list)
    assert admin.customer_detail("C-9999") is None


def test_summary_counts(admin):
    s = admin.summary("2026-09-17T10:00:00")
    assert s["calls_today"] == admin.con.execute(
        "select count(*) from call_logs where substr(started_at,1,10)=?", ("2026-09-17",)).fetchone()[0]
    assert s["orders_in_progress"] == admin.con.execute(
        "select count(*) from orders where status not in ('배송완료')").fetchone()[0]
    assert {r["stage"] for r in s["returns_by_stage"]} == {
        r[0] for r in admin.con.execute("select distinct stage from returns").fetchall()}
    assert len(s["recent_calls"]) <= 5
