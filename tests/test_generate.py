import json
import sqlite3
import pytest
from server.db.generate import generate

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
