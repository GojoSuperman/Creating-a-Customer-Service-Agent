import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from server.db.generate import generate

ROOT = Path(__file__).resolve().parent.parent


def _domain_dir_copy(tmp_path):
    """공유 DB(domains/modumall/modumall.db)를 건드리지 않도록, 임시 디렉터리에 도메인 파일을
    복사해 그 위에서 generate() 를 돌린다. (test_generation_is_deterministic 의 방식과 동일)"""
    tmp_dom = tmp_path / "modumall"
    shutil.copytree(ROOT / "domains" / "modumall", tmp_dom, ignore=shutil.ignore_patterns("*.db"))
    return tmp_dom


CANON_PRODUCTS = ["P1001", "P1002", "P1003", "P2001", "P2002", "P2003", "P3001", "P3002", "P3003", "P3004",
                  "P3005", "P3006", "P4001", "P4002", "P5001", "P5002", "P5003", "P6001", "P6002", "P6003"]


@pytest.fixture(scope="module")
def db(modumall_dir_module):
    path = generate(modumall_dir_module, force=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    yield con
    con.close()


def count(db, table):
    return db.execute(f"select count(*) from {table}").fetchone()[0]


def test_scale(db):
    assert count(db, "products") >= 170
    assert count(db, "customers") >= 120
    assert count(db, "orders") >= 400
    assert count(db, "returns") >= 50
    assert count(db, "shipment_events") >= 400
    assert count(db, "restock") >= 8


def test_canonical_products_preserved(db, modumall_dir_module):
    seed = json.loads((modumall_dir_module / "mockdb.json").read_text(encoding="utf-8"))
    for p in seed["products"]:
        row = db.execute("select * from products where product_id=?", (p["product_id"],)).fetchone()
        assert row is not None, p["product_id"]
        assert row["name"] == p["name"] and row["price"] == p["price"] and row["category"] == p["category"]
        assert row["stock"] == p.get("stock") and json.loads(row["components"] or "null") == p.get("components")
    assert set(r[0] for r in db.execute("select product_id from products")) >= set(CANON_PRODUCTS)


def test_canonical_orders_and_returns_preserved(db, modumall_dir_module):
    seed = json.loads((modumall_dir_module / "mockdb.json").read_text(encoding="utf-8"))
    for o in seed["orders"]:
        row = db.execute("select * from orders where order_id=?", (o["order_id"],)).fetchone()
        assert row["status"] == o["status"] and row["order_amount"] == o["order_amount"]
        assert bool(row["is_external_channel"]) == bool(o.get("is_external_channel"))
        items = db.execute("select product_id, qty, price from order_items where order_id=? order by rowid", (o["order_id"],)).fetchall()
        assert [(i["product_id"], i["qty"], i["price"]) for i in items] == [(i["product_id"], i["qty"], i["price"]) for i in o["items"]]
        assert row["customer_id"] is not None
    for r in seed["returns"]:
        row = db.execute("select * from returns where return_id=?", (r["return_id"],)).fetchone()
        assert row["stage"] == r["stage"] and row["inspection_result"] == r.get("inspection_result")
        hist = [h["stage"] for h in db.execute("select stage from return_stage_history where return_id=? order by rowid", (r["return_id"],))]
        assert hist == [h["stage"] for h in r["stage_history"]]


def test_order_amount_matches_items(db):
    bad = db.execute("""
        select o.order_id from orders o
        join (select order_id, sum(qty*price) s from order_items group by order_id) i on i.order_id = o.order_id
        where o.order_amount != i.s""").fetchall()
    assert bad == []


def test_free_shipping_consistent_with_category_threshold(db):
    # 단일 카테고리 주문에서 무료배송 적용 여부가 카테고리 기준액과 일치해야 한다 (생성 주문만 검사)
    rows = db.execute("""
        select o.order_id, o.order_amount, o.free_shipping_applied, c.free_shipping_threshold th
        from orders o join order_items i on i.order_id=o.order_id join products p on p.product_id=i.product_id
        join categories c on c.key=p.category
        where o.order_id > 'O-1011' and o.is_external_channel = 0
        group by o.order_id having count(distinct p.category) = 1""").fetchall()
    assert rows
    for r in rows:
        expected = r["th"] is not None and r["order_amount"] >= r["th"]
        assert bool(r["free_shipping_applied"]) == expected, r["order_id"]


def test_customers_have_unique_phones(db):
    assert db.execute("select count(*) from customers").fetchone()[0] == db.execute("select count(distinct phone) from customers").fetchone()[0]
    assert db.execute("select phone from customers limit 1").fetchone()[0].startswith("010-")


def test_generation_is_deterministic(modumall_dir_module, tmp_path):
    import shutil
    a = generate(modumall_dir_module, force=True)
    tmp_dom = tmp_path / "modumall"
    shutil.copytree(modumall_dir_module, tmp_dom, ignore=shutil.ignore_patterns("*.db"))
    b = generate(tmp_dom, force=True)
    ca, cb = sqlite3.connect(a), sqlite3.connect(b)
    for t in ["products", "customers", "orders", "order_items", "returns", "shipment_events", "restock"]:
        assert ca.execute(f"select count(*) from {t}").fetchone() == cb.execute(f"select count(*) from {t}").fetchone()
    assert ca.execute("select order_id, customer_id, order_amount from orders order by order_id").fetchall() == \
           cb.execute("select order_id, customer_id, order_amount from orders order by order_id").fetchall()


def test_made_to_order_never_in_returns(db):
    bad = db.execute("""
        select r.return_id from returns r
        join order_items i on i.order_id = r.order_id
        join products p on p.product_id = i.product_id
        where p.made_to_order = 1""").fetchall()
    assert bad == []


def test_no_duplicate_product_names(db):
    rows = db.execute("select name, count(*) c from products group by name having c > 1").fetchall()
    assert rows == []


def test_paid_orders_are_recent(db):
    # F4: 결제완료는 방금 접수된 주문만 (2일 이내), 배송중은 6일 이내. 정식(canonical) 주문은
    # 값을 바꾸지 않는다는 규칙이 있으므로 합성 주문(O-1011 초과)만 검사한다.
    bad_paid = db.execute("""
        select order_id from orders
        where status = '결제완료' and order_id > 'O-1011'
          and julianday('2026-09-16') - julianday(substr(ordered_at, 1, 10)) > 2
    """).fetchall()
    assert bad_paid == []
    bad_shipping = db.execute("""
        select order_id from orders
        where status = '배송중' and order_id > 'O-1011'
          and julianday('2026-09-16') - julianday(substr(ordered_at, 1, 10)) > 6
    """).fetchall()
    assert bad_shipping == []


def test_no_future_return_dates(db):
    bad = db.execute("select return_id, date from return_stage_history where date > '2026-09-16'").fetchall()
    assert bad == []
    bad2 = db.execute("select return_id from returns where expected_completion > '2026-09-23'").fetchall()
    assert bad2 == []


def test_share_of_recent_orders(db):
    total = count(db, "orders")
    recent = db.execute("""
        select count(*) from orders
        where julianday('2026-09-16') - julianday(substr(ordered_at, 1, 10)) <= 14
    """).fetchone()[0]
    assert recent / total >= 0.25, f"recent share too low: {recent}/{total}"


def test_status_distribution(db):
    rows = db.execute("select order_id from orders where status = '제작중'").fetchall()
    assert len(rows) >= 8
    for (oid,) in rows:
        order = db.execute("select shipped_at from orders where order_id=?", (oid,)).fetchone()
        assert order["shipped_at"] is None
        has_mto_item = db.execute("""
            select 1 from order_items i join products p on p.product_id = i.product_id
            where i.order_id=? and p.made_to_order = 1""", (oid,)).fetchone()
        assert has_mto_item is not None, oid


def test_return_progress_detail_matches_return_stage(tmp_path):
    """반품·교환 진행 주문의 status_detail 은 그 반품의 현재 단계에서 파생돼야 한다."""
    from server.db.generate import generate

    db = generate(_domain_dir_copy(tmp_path))
    con = sqlite3.connect(str(db))
    # 손으로 만든 정식 주문(O-1006~1008)은 그 자체가 정답셋 기준이므로 이 일반 규칙 검사에서 제외한다
    # (예: O-1008 은 품절로 인한 환불 전환 안내문이라 반품 단계 문구를 그대로 담지 않는다).
    rows = con.execute(
        "select o.order_id, o.status, o.status_detail, r.stage from orders o "
        "join returns r on r.order_id = o.order_id "
        "where o.status in ('반품진행','교환진행') and o.order_id > 'O-1011'").fetchall()
    assert rows, "반품 진행 주문이 있어야 한다"
    for order_id, status, detail, stage in rows:
        assert stage in (detail or ""), f"{order_id}: 주문 상세 '{detail}' 가 반품 단계 '{stage}' 를 담지 않는다"


def test_finished_return_is_not_in_progress(tmp_path):
    """환불까지 끝난 반품의 주문이 '반품진행' 으로 남아 있으면 안 된다."""
    from server.db.generate import generate

    db = generate(_domain_dir_copy(tmp_path))
    con = sqlite3.connect(str(db))
    bad = con.execute(
        "select o.order_id from orders o join returns r on r.order_id = o.order_id "
        "where r.stage = '환불완료' and o.status in ('반품진행','교환진행')").fetchall()
    assert bad == [], f"환불완료인데 진행 중으로 남은 주문: {bad}"
