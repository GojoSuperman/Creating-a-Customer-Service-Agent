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


def test_orders_filter_by_date_to_with_time_is_not_appended(admin):
    # date_to 에 이미 시각(T)이 있으면 그대로 써야 한다. 날짜만 붙이는 로직이 남아 있으면
    # "...T10:40:00T23:59:59" 가 되어 조용히 그날 전체(그 뒤 시각의 주문)가 포함된다.
    # 하드코딩된 시각 대신, 같은 날짜에 서로 다른 시각의 주문이 2건 이상 있는 날을 실제 데이터에서
    # 찾아 경계로 쓴다 — 생성기가 바뀌어 주문 시각이 달라져도 이 테스트는 깨지지 않는다.
    by_day: dict[str, set[str]] = {}
    for r in admin.con.execute("select ordered_at from orders order by ordered_at"):
        by_day.setdefault(r["ordered_at"][:10], set()).add(r["ordered_at"])
    day, times = next((d, sorted(t)) for d, t in by_day.items() if len(t) >= 2)
    boundary, after = times[0], times[1]

    rows, total = admin.orders(date_from=day, date_to=boundary)
    assert total == expected_count(
        admin, "select count(*) from orders where ordered_at >= ? and ordered_at <= ?", day, boundary)
    assert all(r["ordered_at"] <= boundary for r in rows)
    assert any(r["ordered_at"] == boundary for r in rows)
    assert not any(r["ordered_at"] == after for r in rows)

    # 날짜만 준 경우는 기존처럼 그날 끝(23:59:59)까지 포함되어야 한다
    rows_date_only, _ = admin.orders(date_from=day, date_to=day)
    assert any(r["ordered_at"] == times[-1] for r in rows_date_only)


@pytest.mark.parametrize("method, id_key", [("orders", "order_id"), ("returns", "return_id")])
def test_list_paging_covers_all_rows_without_gap_or_dup_on_ties(admin, method, id_key):
    # orders.ordered_at, returns.requested_at 모두 동점(중복 값) 그룹이 실제로 존재한다.
    # tiebreaker(2차 정렬키)가 없으면 SQLite 가 동점 행 순서를 보장하지 않아
    # 페이지 경계에서 행이 누락되거나 중복될 수 있다. 전체 페이지를 순회해
    # 모은 id 집합이 전체 건수와 일치하고 중복이 없는지로 이를 검증한다.
    fn = getattr(admin, method)
    _, total = fn(page=1)
    seen = []
    page = 1
    while True:
        rows, _ = fn(page=page)
        if not rows:
            break
        seen.extend(r[id_key] for r in rows)
        page += 1
    assert len(seen) == total
    assert len(set(seen)) == total


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


def test_call_detail_parses_turns():
    # 공유 DB 의 call_logs 는 다른 테스트(test_generate 의 재생성 등)가 계속 건드리는
    # 가변 자원이라 "턴이 있는 통화가 존재한다"를 전제로 할 수 없다. 인메모리 DB 에
    # 통화 1건을 직접 시드해 턴 파싱과 customer 조인 여부를 확인한다.
    con = _memory_con()
    turns = [{"q": "질문1", "a": "답변1", "route": "SHIPPING", "confidence": 0.8}]
    con.execute(
        "insert into call_logs (call_id, customer_id, started_at, ended_at, turns) values (?,?,?,?,?)",
        ("call-detail-1", None, "2026-09-01T00:00:00", "2026-09-01T00:05:00", json.dumps(turns, ensure_ascii=False)),
    )
    con.commit()

    admin = AdminRepo(con)
    c = admin.call_detail("call-detail-1")
    assert c["call_id"] == "call-detail-1"
    assert isinstance(c["turns"], list) and len(c["turns"]) == 1
    assert c["turns"][0]["q"] == "질문1"
    assert "customer" in c  # 비회원 통화면 None
    assert c["customer"] is None
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
        "select count(*) from orders where status not in ('배송완료','반품완료','교환완료')").fetchone()[0]
    assert {r["stage"] for r in s["returns_by_stage"]} == {
        r[0] for r in admin.con.execute("select distinct stage from returns").fetchall()}
    assert len(s["recent_calls"]) <= 5
