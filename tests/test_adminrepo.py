import sqlite3

import pytest

from server.adminrepo import PAGE_SIZE, AdminRepo
from server.domain import load_domain


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
    r = rows[0]
    assert r["turn_count"] == len(r["turns"])
    assert isinstance(r["routes"], list)


def test_customers_search_by_name_and_phone(admin):
    rows, _ = admin.customers()
    target = rows[0]

    by_name, total_name = admin.customers(q=target["name"])
    assert total_name >= 1 and any(c["customer_id"] == target["customer_id"] for c in by_name)

    by_phone, _ = admin.customers(q=target["phone"].replace("-", ""))
    assert any(c["customer_id"] == target["customer_id"] for c in by_phone)

    assert admin.customers(q="존재하지않는고객")[1] == 0
