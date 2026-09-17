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


_SEED_SQL = """
    insert into categories (key,label,free_shipping_threshold,free_shipping_note,return_window_days,return_window_basis,requires_unopened)
      values ('UNDERWEAR','속옷',30000,null,7,'수령일',0), ('COSMETICS','화장품',null,'무료배송 대상이 아닙니다',7,'수령일',1);
    insert into customers values ('C-0001','김테스트','010-1111-2222','수도권','서울시 강남구 1','2024-01-01');
    insert into products (product_id,name,category,price,stock,is_set,soldout,has_quality_cert,made_to_order)
      values ('P1','속옷 세트','UNDERWEAR',20000,5,0,0,0,0),
             ('P2','품절 상품','UNDERWEAR',10000,0,0,1,0,0),
             ('P3','재고없는 상품','UNDERWEAR',10000,null,0,0,0,0),
             ('P4','클렌징 오일','COSMETICS',18000,3,0,0,0,0);
    insert into orders (order_id,customer_id,ordered_at,status,order_amount,shipping_fee)
      values ('O-1005','C-0001','2026-09-01T10:00:00','배송완료',10000,2500);
"""


class _FakeRepoDomain:
    fixed_values = {"base_shipping_fee": 2500}


def _shop_repo_over(con):
    """이미 스키마·시드가 된 커넥션 위에 ShopRepo 를 만든다(경로 없이 커넥션만 주입)."""
    from server.repo import Repo
    from server.shoprepo import ShopRepo
    import threading

    repo = Repo.__new__(Repo)
    repo.con = con
    repo._lock = threading.RLock()
    return ShopRepo(repo, _FakeRepoDomain())


@pytest.fixture
def memory_shop(modumall_dir_module):
    """쓰기 테스트 전용. 공유 DB 를 건드리지 않는다."""
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript((ROOT / "server" / "db" / "schema.sql").read_text(encoding="utf-8"))
    con.executescript(_SEED_SQL)
    con.commit()
    return _shop_repo_over(con)


def test_create_order_writes_order_and_items(memory_shop):
    oid = memory_shop.create_order("C-0001", [{"product_id": "P1", "option": "M", "qty": 2}],
                                   now="2026-09-17T12:00:00")
    assert oid == "O-1006"                                   # 기존 최대 O-1005 + 1
    row = memory_shop.con.execute("select * from orders where order_id=?", (oid,)).fetchone()
    assert row["status"] == "결제완료" and row["customer_id"] == "C-0001"
    assert row["ordered_at"] == "2026-09-17T12:00:00"
    assert row["order_amount"] == 40000 and row["shipping_fee"] == 0 and row["free_shipping_applied"] == 1
    assert row["address_region"] == "수도권"
    items = memory_shop.con.execute("select * from order_items where order_id=?", (oid,)).fetchall()
    assert len(items) == 1 and items[0]["name"] == "속옷 세트" and items[0]["option"] == "M"
    assert items[0]["qty"] == 2 and items[0]["price"] == 20000


def test_create_order_decrements_stock_but_not_null_stock(memory_shop):
    memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": 2},
                                        {"product_id": "P3", "option": None, "qty": 1}])
    assert memory_shop.con.execute("select stock from products where product_id='P1'").fetchone()[0] == 3
    assert memory_shop.con.execute("select stock from products where product_id='P3'").fetchone()[0] is None


def test_create_order_rejects_soldout_and_leaves_no_trace(memory_shop):
    from server.shoprepo import OrderError

    before_orders = memory_shop.con.execute("select count(*) from orders").fetchone()[0]
    with pytest.raises(OrderError) as e:
        memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": 1},
                                            {"product_id": "P2", "option": None, "qty": 1}])
    assert any("품절" in m for m in e.value.blocked)
    assert memory_shop.con.execute("select count(*) from orders").fetchone()[0] == before_orders
    assert memory_shop.con.execute("select count(*) from order_items").fetchone()[0] == 0
    assert memory_shop.con.execute("select stock from products where product_id='P1'").fetchone()[0] == 5


def test_create_order_rejects_insufficient_stock(memory_shop):
    from server.shoprepo import OrderError

    with pytest.raises(OrderError) as e:
        memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": 6}])
    assert any("재고" in m for m in e.value.blocked)
    assert memory_shop.con.execute("select count(*) from order_items").fetchone()[0] == 0


def test_create_order_rejects_empty_cart(memory_shop):
    from server.shoprepo import OrderError

    with pytest.raises(OrderError):
        memory_shop.create_order("C-0001", [])


def test_create_order_cosmetics_pays_shipping(memory_shop):
    oid = memory_shop.create_order("C-0001", [{"product_id": "P4", "option": None, "qty": 3}])
    row = memory_shop.con.execute("select * from orders where order_id=?", (oid,)).fetchone()
    assert row["order_amount"] == 54000 and row["shipping_fee"] == 2500 and row["free_shipping_applied"] == 0


def test_orders_of_and_order_of_scope_to_customer(memory_shop):
    memory_shop.con.execute("insert into customers values ('C-0002','남','010-3333-4444','수도권','서울 2','2024-02-02')")
    oid = memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": 1}])
    mine = memory_shop.orders_of("C-0001")
    assert [o["order_id"] for o in mine][0] == oid                  # 최신순
    assert all(o["customer_id"] == "C-0001" for o in mine)
    assert memory_shop.order_of("C-0001", oid)["items"][0]["product_id"] == "P1"
    assert memory_shop.order_of("C-0002", oid) is None              # 남의 주문은 안 보인다
    assert memory_shop.order_of("C-0001", "O-9999") is None


def test_create_order_rolls_back_on_mid_transaction_failure(memory_shop):
    """order insert 는 이미 성공한 뒤 order_items insert 가 실패하는 상황을 흉내 내어,
    rollback 이 실제로 orders 행까지 되돌리는지 확인한다(스텁 제거 시에만 잡히는 테스트)."""
    real_con = memory_shop.con

    class FlakyConnection:
        def __init__(self, real):
            self._real = real
            self.calls = 0

        def execute(self, sql, params=()):
            if sql.startswith("insert into order_items"):
                self.calls += 1
                if self.calls == 1:
                    raise sqlite3.OperationalError("주입된 실패")
            return self._real.execute(sql, params)

        def commit(self):
            self._real.commit()

        def rollback(self):
            self._real.rollback()

        @property
        def in_transaction(self):
            return self._real.in_transaction

    memory_shop.con = FlakyConnection(real_con)
    try:
        with pytest.raises(sqlite3.OperationalError):
            memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": 2}])
    finally:
        memory_shop.con = real_con  # 검증은 진짜 커넥션으로

    assert memory_shop.con.execute("select count(*) from orders").fetchone()[0] == 1  # 기존 O-1005 뿐
    assert memory_shop.con.execute("select count(*) from order_items").fetchone()[0] == 0
    assert memory_shop.con.execute("select stock from products where product_id='P1'").fetchone()[0] == 5


def test_create_order_rejects_negative_and_non_integer_qty(memory_shop):
    """Task 2 리뷰에서 발견된 quote() 의 음수 수량 취약점을 주문 경로에서 막는다."""
    from server.shoprepo import OrderError

    before_orders = memory_shop.con.execute("select count(*) from orders").fetchone()[0]

    with pytest.raises(OrderError):
        memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": -1}])
    with pytest.raises(OrderError):
        memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": 0}])
    with pytest.raises(OrderError):
        memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": "abc"}])

    # 거부된 주문은 흔적을 남기지 않는다.
    assert memory_shop.con.execute("select count(*) from orders").fetchone()[0] == before_orders
    assert memory_shop.con.execute("select stock from products where product_id='P1'").fetchone()[0] == 5


def test_create_order_rejects_unknown_customer(memory_shop):
    """FK 가 강제되지 않는 SQLite 특성상, 존재하지 않는 고객으로도 주문이 만들어지면 안 된다."""
    from server.shoprepo import OrderError

    before_orders = memory_shop.con.execute("select count(*) from orders").fetchone()[0]
    with pytest.raises(OrderError):
        memory_shop.create_order("C-9999", [{"product_id": "P1", "option": None, "qty": 1}])
    assert memory_shop.con.execute("select count(*) from orders").fetchone()[0] == before_orders
    assert memory_shop.con.execute("select stock from products where product_id='P1'").fetchone()[0] == 5


def test_create_order_aggregates_duplicate_lines_against_stock(memory_shop):
    """같은 product_id 가 여러 줄로 나뉘어도 합산 수량으로 재고를 검증해야 한다.
    P1 재고 5인데 3+3=6 줄을 담으면 거부되어야 하고, 재고는 음수가 되면 안 된다."""
    from server.shoprepo import OrderError

    with pytest.raises(OrderError) as e:
        memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": 3},
                                            {"product_id": "P1", "option": None, "qty": 3}])
    assert any("재고" in m for m in e.value.blocked)
    assert memory_shop.con.execute("select stock from products where product_id='P1'").fetchone()[0] == 5
    assert memory_shop.con.execute("select count(*) from order_items").fetchone()[0] == 0


def test_create_order_allows_duplicate_lines_within_combined_stock(memory_shop):
    """합산 수량이 재고 이내면 중복 줄이어도 성공하고, 재고는 정확히 합산만큼 줄어야 한다."""
    oid = memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": 2},
                                              {"product_id": "P1", "option": None, "qty": 3}])
    assert memory_shop.con.execute("select stock from products where product_id='P1'").fetchone()[0] == 0
    items = memory_shop.con.execute("select * from order_items where order_id=?", (oid,)).fetchall()
    assert len(items) == 2 and sum(i["qty"] for i in items) == 5


def test_create_order_rowcount_guard_catches_stale_stock_read(memory_shop):
    """[Critical 2 이중 방어] 조회 시점에는 재고가 넉넉해 보였지만(부풀려진 값을 흉내) 실제 갱신
    시점의 DB 재고는 부족한 상황을 흉내 내어, 합산 사전검증을 속이고도 rowcount 가드가 잡는지 확인한다."""
    from server.shoprepo import OrderError

    real_product = memory_shop.repo.product

    def stale_product(product_id):
        row = real_product(product_id)
        if row is None or product_id != "P1":
            return row
        d = dict(row)
        d["stock"] = 999  # 실제 DB 는 5 인데 조회 시점엔 부풀려서 보고한다고 가정
        return d

    memory_shop.repo.product = stale_product
    try:
        with pytest.raises(OrderError):
            memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": 10}])
    finally:
        memory_shop.repo.product = real_product

    assert memory_shop.con.execute("select stock from products where product_id='P1'").fetchone()[0] == 5
    assert memory_shop.con.execute("select count(*) from order_items").fetchone()[0] == 0


def test_create_order_uses_savepoint_not_forced_commit_of_outer_transaction(memory_shop):
    """[Critical 1] 남의 미커밋 트랜잭션을 임의로 커밋하지 않는지 실측한다.
    외부가 커밋하지 않은 insert 를 남겨둔 채 create_order 를 실패시키고, 외부가 rollback 하면
    그 행도 사라져야 한다(예전 구현은 begin 전에 무조건 commit 해 버려 이 케이스에서 행이 남았다)."""
    from server.shoprepo import OrderError

    memory_shop.con.execute("insert into customers values ('C-EXT','외부','010-9999-0000','수도권','외부 1','2024-03-03')")
    assert memory_shop.con.in_transaction is True  # 아직 커밋 전(암묵적 트랜잭션)

    with pytest.raises(OrderError):
        memory_shop.create_order("C-0001", [{"product_id": "P2", "option": None, "qty": 1}])  # 품절 → 실패

    memory_shop.con.rollback()  # 외부 트랜잭션은 외부가 되돌린다
    assert memory_shop.con.execute("select count(*) from customers where customer_id='C-EXT'").fetchone()[0] == 0


def test_create_order_executes_savepoint_statements(memory_shop):
    """savepoint/release 문이 실제로 실행되는지 SQL 을 가로채 확인한다."""
    real_con = memory_shop.con
    seen = []

    class RecordingConnection:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, params=()):
            seen.append(sql)
            return self._real.execute(sql, params)

        def commit(self):
            self._real.commit()

        def rollback(self):
            self._real.rollback()

        @property
        def in_transaction(self):
            return self._real.in_transaction

    memory_shop.con = RecordingConnection(real_con)
    try:
        memory_shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": 1}])
    finally:
        memory_shop.con = real_con

    assert any(s.startswith("savepoint shop_order") for s in seen)
    assert any(s.startswith("release shop_order") for s in seen)
    assert any(s.startswith("begin immediate") for s in seen)


def test_create_order_is_durable_across_a_second_connection(tmp_path):
    """[Important 2] 최종 commit() 이 실제로 디스크에 반영되는지, 같은 커넥션이 아닌
    별도 커넥션으로 다시 열어서 확인한다(같은 커넥션으로만 읽으면 commit 유무를 구별하지 못한다)."""
    db_path = tmp_path / "shop_durability.db"
    con = sqlite3.connect(str(db_path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript((ROOT / "server" / "db" / "schema.sql").read_text(encoding="utf-8"))
    con.executescript(_SEED_SQL)
    con.commit()
    shop = _shop_repo_over(con)

    oid = shop.create_order("C-0001", [{"product_id": "P1", "option": None, "qty": 1}])
    con.close()

    con2 = sqlite3.connect(str(db_path))
    con2.row_factory = sqlite3.Row
    try:
        row = con2.execute("select * from orders where order_id=?", (oid,)).fetchone()
        assert row is not None and row["status"] == "결제완료"
        assert con2.execute("select stock from products where product_id='P1'").fetchone()[0] == 4
    finally:
        con2.close()
