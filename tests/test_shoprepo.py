import pytest

from server.domain import load_domain
from server.repo import Repo
from server.shoprepo import PAGE_SIZE, ShopRepo


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


def test_quote_cosmetics_mixed_with_apparel_still_charges_even_over_threshold(shop):
    # 화장품(임계값 NULL)이 하나라도 섞이면, 의류(40,000) 임계값을 합계가 넘어도 무조건 유료여야 한다.
    # (규칙 1) _shipping 의 "return self.base_fee, False" 를 "continue" 로 바꾸는 돌연변이는
    # thresholds 를 [40000] 만 모아 합계가 넘으면 무료로 판단해버려 이 케이스에서만 걸린다.
    apparel = shop.con.execute(
        "select product_id, price from products where category='APPAREL' order by price limit 1").fetchone()
    cosmetics = shop.con.execute(
        "select product_id, price from products where category='COSMETICS' order by price limit 1").fetchone()
    apparel_id, apparel_price = apparel
    cosmetics_id, _ = cosmetics
    # 의류만으로 40,000원을 확실히 넘기도록 수량을 넉넉히 잡는다.
    qty = (40000 // apparel_price) + 1
    q = shop.quote([{"product_id": apparel_id, "option": None, "qty": qty},
                    {"product_id": cosmetics_id, "option": None, "qty": 1}])
    assert q["subtotal"] >= 40000
    assert q["free_shipping_applied"] is False
    assert q["shipping_fee"] == shop.base_fee


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


def test_products_paging_is_ordered_and_non_overlapping(shop):
    # order by product_id 가 없으면(돌연변이) 페이지 간 순서가 정렬 순서와 어긋나거나 겹칠 수 있다.
    page1, total = shop.products(page=1)
    page2, _ = shop.products(page=2)
    ids1 = [r["product_id"] for r in page1]
    ids2 = [r["product_id"] for r in page2]

    assert len(ids1) == PAGE_SIZE and len(ids2) == PAGE_SIZE
    assert set(ids1).isdisjoint(ids2)

    expected_first_48 = [r[0] for r in shop.con.execute(
        "select product_id from products order by product_id limit 48").fetchall()]
    assert ids1 + ids2 == expected_first_48

    # 마지막 페이지: 나머지 개수만큼만 반환한다.
    last_page_no = (total + PAGE_SIZE - 1) // PAGE_SIZE
    last_page, _ = shop.products(page=last_page_no)
    remainder = total - (last_page_no - 1) * PAGE_SIZE
    assert len(last_page) == remainder

    # 범위를 넘는 페이지는 빈 목록이지만 total 은 그대로다.
    beyond, total_beyond = shop.products(page=last_page_no + 1)
    assert beyond == [] and total_beyond == total


def test_shipping_free_exactly_at_threshold(shop):
    # >= 를 > 로 바꾸는 돌연변이를 잡기 위해 _shipping 을 직접 호출해 경계값을 확인한다.
    threshold = shop.repo.categories()["UNDERWEAR"]["free_shipping_threshold"]
    fee_at, free_at = shop._shipping(threshold, ["UNDERWEAR"])
    assert free_at is True and fee_at == 0

    fee_below, free_below = shop._shipping(threshold - 1, ["UNDERWEAR"])
    assert free_below is False and fee_below == shop.base_fee
