import sqlite3
from pathlib import Path

import pytest

from server.domain import load_domain
from server.repo import Repo
from server.shoprepo import PAGE_SIZE, ShopRepo

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def shop(modumall_dir_module):
    domain = load_domain(modumall_dir_module)
    return ShopRepo(Repo(domain.db_path), domain)


def test_products_list_and_filter(shop):
    rows, total = shop.products()
    assert len(rows) == PAGE_SIZE
    assert total == shop.con.execute("select count(*) from products").fetchone()[0]

    rows, total = shop.products(category="COSMETICS")
    assert total == shop.con.execute("select count(*) from products where category=?", ("COSMETICS",)).fetchone()[0]
    assert all(r["category"] == "COSMETICS" for r in rows)


def test_products_search_escapes_wildcards(shop):
    rows, _ = shop.products(q="팬티")
    assert rows and all("팬티" in r["name"] for r in rows)
    assert shop.products(q="%")[1] == 0
    assert shop.products(q="존재하지않는상품이름")[1] == 0


def test_product_and_category_list(shop):
    p = shop.product("P1001")
    assert p["name"] == "요일팬티 7종 세트" and p["price"] > 0
    assert shop.product("P9999") is None
    cats = shop.category_list()
    assert {c["key"] for c in cats} >= {"APPAREL", "COSMETICS"}
    assert all("label" in c for c in cats)


def test_quote_charges_base_fee_below_threshold(shop):
    # UNDERWEAR 임계값 30,000원. P1001 은 24,900원 1개 → 미달
    q = shop.quote([{"product_id": "P1001", "option": None, "qty": 1}])
    assert q["subtotal"] == 24900
    assert q["shipping_fee"] == shop.base_fee and q["free_shipping_applied"] is False
    assert q["total"] == q["subtotal"] + q["shipping_fee"]
    assert q["lines"][0]["name"] == "요일팬티 7종 세트" and q["lines"][0]["amount"] == 24900


def test_quote_free_above_threshold(shop):
    q = shop.quote([{"product_id": "P1001", "option": None, "qty": 2}])   # 49,800원 ≥ 30,000
    assert q["free_shipping_applied"] is True and q["shipping_fee"] == 0
    assert q["total"] == q["subtotal"]


def test_quote_cosmetics_never_free(shop):
    pid = shop.con.execute("select product_id from products where category='COSMETICS' order by price desc limit 1").fetchone()[0]
    q = shop.quote([{"product_id": pid, "option": None, "qty": 10}])      # 금액과 무관하게 유료
    assert q["free_shipping_applied"] is False and q["shipping_fee"] == shop.base_fee


def test_quote_mixed_categories_need_every_threshold(shop):
    # 의류(40,000) + 속옷(30,000) 이 섞이면 합계가 둘 다 넘어야 무료
    apparel = shop.con.execute("select product_id, price from products where category='APPAREL' order by price limit 1").fetchone()
    q = shop.quote([{"product_id": apparel[0], "option": None, "qty": 1},
                    {"product_id": "P1001", "option": None, "qty": 1}])
    expected_free = q["subtotal"] >= 40000
    assert q["free_shipping_applied"] is expected_free
    assert q["shipping_fee"] == (0 if expected_free else shop.base_fee)


def test_quote_empty_and_unknown_product(shop):
    empty = shop.quote([])
    assert empty["lines"] == [] and empty["subtotal"] == 0 and empty["shipping_fee"] == 0 and empty["total"] == 0
    assert shop.quote([{"product_id": "P9999", "option": None, "qty": 1}])["lines"] == []
