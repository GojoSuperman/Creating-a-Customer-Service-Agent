# C단계 쇼핑몰 고객 화면 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전화번호 로그인 → 상품 목록 → 장바구니 → 주문까지 되는 고객 화면을 붙이고, 그렇게 만든 주문이 어드민과 상담 에이전트에 그대로 나타나게 한다.

**Architecture:** 어드민(B단계)과 대칭. 조회·계산·쓰기는 `server/shoprepo.py`(`ShopRepo`), 라우트·쿠키 처리는 `server/shop.py`, 쿠키 서명은 `server/shopcookie.py`, 화면은 `server/templates/shop/` Jinja2 템플릿. 쓰기 SQL 은 `ShopRepo` 안에만 존재하며 `Repo` 의 커넥션과 락을 공유한다.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLite, 표준 라이브러리 `hmac`/`hashlib`/`base64`/`secrets`, pytest + `fastapi.testclient.TestClient`

**Spec:** `docs/superpowers/specs/2026-09-17-shop-frontend-design.md`

## Global Constraints

- **새 파이썬 의존성 금지.** 서명은 표준 라이브러리로 한다.
- **POST 폼은 `Form(...)` 대신 `await request.form()` 으로 읽는다.** FastAPI 의 `Form(...)` 은 `python-multipart` 를 요구하는데 이 환경에는 없다(컨트롤러 실측). Starlette 는 `application/x-www-form-urlencoded` 를 자체 파싱하므로, POST 라우트를 `async def` 로 만들고 `form = await request.form()` → `form.get("phone", "")` 처럼 읽으면 의존성 없이 동작한다. 모든 `<form>` 은 기본 인코딩(urlencoded)을 쓰고 `enctype="multipart/form-data"` 를 쓰지 않는다. 수량 같은 숫자는 `int(form.get("qty") or 1)` 로 직접 변환하고, 숫자가 아니면 기본값으로 처리한다.
- **쓰기 SQL 은 `server/shoprepo.py` 에만.** `server/adminrepo.py`·`server/admin.py` 의 읽기 전용 원칙은 그대로다(그 파일들을 수정하지 말 것. 기존 회귀 테스트가 감시한다).
- **`server/repo.py`·`server/pipeline.py`·`server/tools.py`·`server/router.py` 수정 금지.**
- 모든 값은 `?` 바인딩. 문자열 포매팅으로 값을 SQL 에 넣지 않는다.
- **XSS**: Jinja2 자동 이스케이프를 끄지 않는다. `|safe` 금지.
- **테스트는 공유 DB(`domains/modumall/modumall.db`)에 쓰지 않는다.** 쓰기 테스트는 인메모리 SQLite(`sqlite3.connect(":memory:", check_same_thread=False)` + `server/db/schema.sql` 실행 + 필요한 행만 시드). 공유 DB 를 읽는 테스트는 불변식만 본다.
- 기본 배송비는 `domain.fixed_values["base_shipping_fee"]`(현재 2500). 코드에 숫자를 박지 않는다.
- 주문 상태는 `'결제완료'`, 주문번호 형식은 `O-####`.
- 페이지 크기 24, 페이지 번호 1부터. 날짜 `9월 17일`, 금액 `12,000원`.
- 주석·UI 문구는 한국어, 식별자는 영어. 기존 파일 스타일(`# -*- coding: utf-8 -*-` + 한 줄 docstring).
- `.venv` 는 uv venv 라 pip 이 없다(`uv pip install --python .venv/bin/python`). 이번 플랜은 새 의존성이 없다.
- 기존 테스트 280개가 계속 통과해야 한다: `.venv/bin/python -m pytest -q`
- 커밋 메시지에 Co-Authored-By 트레일러를 붙이지 않는다.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `server/shopcookie.py` (신규) | HMAC 서명·검증, 장바구니 JSON 직렬화·검증 |
| `server/shoprepo.py` (신규) | 상품 조회, 금액 계산(`quote`), 주문 생성(트랜잭션·재고 차감), 내 주문 조회 |
| `server/shop.py` (신규) | `/shop/*` 라우터, 세션·장바구니 쿠키 처리, 템플릿 렌더 |
| `server/templates/shop/*.html` (신규 8개) | base_shop, products, product_detail, cart, checkout, done, orders, order_detail, login |
| `web/shop.css` (신규) | 고객 화면 스타일(어드민과 같은 다크 톤) |
| `server/app.py` (수정) | 어드민 장착부 옆에 `shop_router` 장착 |
| `web/index.html` (수정, Task 5) | 헤더에 `/shop` 링크 |
| `tests/test_shopcookie.py` · `tests/test_shoprepo.py` · `tests/test_shop_routes.py` (신규) | 각 계층 테스트 |

---

### Task 1: 쿠키 서명과 장바구니 직렬화

**Files:**
- Create: `server/shopcookie.py`
- Test: `tests/test_shopcookie.py`

**Interfaces:**
- Produces: `sign(value: str) -> str`, `unsign(token: str | None) -> str | None`(실패 시 None, 예외 없음), `dump_cart(items: list[dict]) -> str`, `load_cart(token: str | None) -> list[dict]`, 상수 `MAX_CART_LINES = 20`, `MAX_QTY = 99`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_shopcookie.py`

```python
import os

import pytest

from server import shopcookie


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch):
    monkeypatch.setenv("SHOP_SECRET", "테스트용-고정-키")


def test_sign_unsign_roundtrip():
    token = shopcookie.sign("C-0001")
    assert token != "C-0001" and "." in token
    assert shopcookie.unsign(token) == "C-0001"


def test_unsign_rejects_tampered_or_garbage():
    token = shopcookie.sign("C-0001")
    payload, mac = token.rsplit(".", 1)
    forged = shopcookie.sign("C-9999").rsplit(".", 1)[0] + "." + mac
    assert shopcookie.unsign(forged) is None          # 값만 바꾼 위조
    assert shopcookie.unsign(payload + ".AAAA") is None
    assert shopcookie.unsign("점없는문자열") is None
    assert shopcookie.unsign(None) is None
    assert shopcookie.unsign("") is None


def test_unsign_fails_with_other_key(monkeypatch):
    token = shopcookie.sign("C-0001")
    monkeypatch.setenv("SHOP_SECRET", "다른-키")
    assert shopcookie.unsign(token) is None


def test_cart_roundtrip_keeps_korean_option():
    items = [{"product_id": "P1001", "option": "사이즈 M", "qty": 2}]
    assert shopcookie.load_cart(shopcookie.dump_cart(items)) == items


def test_load_cart_is_forgiving():
    assert shopcookie.load_cart(None) == []
    assert shopcookie.load_cart("깨진.토큰") == []
    assert shopcookie.load_cart(shopcookie.sign("{잘못된 json")) == []
    assert shopcookie.load_cart(shopcookie.sign('{"not": "a list"}')) == []


def test_load_cart_clamps_and_drops_bad_lines():
    items = [{"product_id": "P1001", "option": None, "qty": 999},
             {"product_id": "P1002", "option": None, "qty": 0},
             {"qty": 1},                                   # product_id 없음 → 버린다
             {"product_id": "P1003", "option": None, "qty": "셋"}]  # 숫자 아님 → 버린다
    loaded = shopcookie.load_cart(shopcookie.dump_cart(items))
    assert [l["product_id"] for l in loaded] == ["P1001", "P1002"]
    assert loaded[0]["qty"] == shopcookie.MAX_QTY
    assert loaded[1]["qty"] == 1


def test_load_cart_limits_line_count():
    items = [{"product_id": f"P{i:04d}", "option": None, "qty": 1} for i in range(40)]
    assert len(shopcookie.load_cart(shopcookie.dump_cart(items))) == shopcookie.MAX_CART_LINES


def test_ephemeral_key_is_used_when_env_missing(monkeypatch):
    monkeypatch.delenv("SHOP_SECRET", raising=False)
    monkeypatch.setattr(shopcookie, "_EPHEMERAL", None)
    token = shopcookie.sign("C-0001")
    assert shopcookie.unsign(token) == "C-0001"      # 같은 프로세스 안에서는 통한다
    assert shopcookie._EPHEMERAL is not None
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_shopcookie.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.shopcookie'`

- [ ] **Step 3: 구현** — `server/shopcookie.py`

```python
# -*- coding: utf-8 -*-
"""쇼핑몰 쿠키 서명. 값은 비밀이 아니고 변조만 막으면 되므로 HMAC 서명만 한다."""
import base64
import hashlib
import hmac
import json
import os
import secrets

MAX_CART_LINES = 20
MAX_QTY = 99

_EPHEMERAL = None


def _secret() -> bytes:
    """SHOP_SECRET 이 없으면 프로세스 임시 키를 쓴다 (재시작하면 로그인·장바구니가 풀린다)."""
    global _EPHEMERAL
    env = os.environ.get("SHOP_SECRET")
    if env:
        return env.encode("utf-8")
    if _EPHEMERAL is None:
        _EPHEMERAL = secrets.token_hex(32)
        print("[shop] SHOP_SECRET 이 없어 임시 서명 키를 만들었습니다 — 재시작하면 로그인이 풀립니다.")
    return _EPHEMERAL.encode("utf-8")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(value: str) -> str:
    payload = _b64e(value.encode("utf-8"))
    mac = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_b64e(mac)}"


def unsign(token):
    """서명이 맞으면 원래 값, 아니면 None. 예외를 던지지 않는다."""
    if not token or "." not in token:
        return None
    payload, mac = token.rsplit(".", 1)
    try:
        expected = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(mac), expected):
            return None
        return _b64d(payload).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def dump_cart(items) -> str:
    return sign(json.dumps(items, ensure_ascii=False, separators=(",", ":")))


def load_cart(token):
    """서명·형식이 조금이라도 이상하면 빈 장바구니로 돌려준다 (오류 화면 대신)."""
    raw = unsign(token)
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    out = []
    for item in items[:MAX_CART_LINES]:
        if not isinstance(item, dict) or not item.get("product_id"):
            continue
        qty = item.get("qty")
        if not isinstance(qty, int) or isinstance(qty, bool):
            continue
        out.append({"product_id": str(item["product_id"]),
                    "option": item.get("option") or None,
                    "qty": max(1, min(MAX_QTY, qty))})
    return out
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_shopcookie.py -q`
Expected: PASS (8개)

- [ ] **Step 5: 커밋**

```bash
git add server/shopcookie.py tests/test_shopcookie.py
git commit -m "feat: 쇼핑몰 쿠키 서명과 장바구니 직렬화"
```

---

### Task 2: ShopRepo 조회와 금액 계산

**Files:**
- Create: `server/shoprepo.py`
- Test: `tests/test_shoprepo.py`

**Interfaces:**
- Consumes: `server.repo.Repo`(`.con`, `._lock`, `.product`, `.categories`, `.customer`), `server.domain.Domain.fixed_values`
- Produces: `ShopRepo(repo, domain)` — 속성 `base_fee: int`; 메서드 `products(*, category=None, q=None, page=1, size=PAGE_SIZE) -> (rows, total)`, `product(pid) -> dict|None`, `category_list() -> list[dict]`, `quote(cart) -> dict`. 상수 `PAGE_SIZE = 24`. `quote` 반환: `{"lines":[{"product_id","name","option","qty","price","amount","soldout","stock","category"}], "subtotal": int, "shipping_fee": int, "free_shipping_applied": bool, "total": int}`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_shoprepo.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_shoprepo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.shoprepo'`

- [ ] **Step 3: 구현** — `server/shoprepo.py`

```python
# -*- coding: utf-8 -*-
"""쇼핑몰 고객 화면 저장소. 이 프로젝트에서 유일하게 주문을 쓰는 곳이다."""
from server.repo import _row

PAGE_SIZE = 24


class ShopRepo:
    def __init__(self, repo, domain):
        self.repo = repo
        self.con = repo.con
        self._lock = repo._lock          # 읽기(Repo·AdminRepo)와 같은 락 아래에서 쓴다
        self.base_fee = int(domain.fixed_values["base_shipping_fee"])

    # ── 내부 헬퍼 ────────────────────────────────────────
    def _all(self, sql, params=()):
        with self._lock:
            return [_row(r) for r in self.con.execute(sql, params).fetchall()]

    def _count(self, sql, params=()):
        with self._lock:
            return self.con.execute(sql, params).fetchone()[0]

    @staticmethod
    def _like_escape(text):
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    # ── 조회 ────────────────────────────────────────────
    def products(self, *, category=None, q=None, page=1, size=PAGE_SIZE):
        cond, params = [], []
        if category:
            cond.append("category = ?"); params.append(category)
        if q:
            cond.append("name like ? escape '\\'"); params.append(f"%{self._like_escape(q)}%")
        where = ("where " + " and ".join(cond)) if cond else ""
        page = max(1, int(page or 1))
        total = self._count(f"select count(*) from products {where}", tuple(params))
        rows = self._all(
            f"select product_id, name, category, price, stock, soldout, soldout_note, options, material "
            f"from products {where} order by product_id limit ? offset ?",
            (*params, size, (page - 1) * size))
        return rows, total

    def product(self, product_id):
        return self.repo.product(product_id)

    def category_list(self):
        cats = self.repo.categories()
        return [{"key": k, "label": v["label"], "free_shipping_threshold": v["free_shipping_threshold"]}
                for k, v in sorted(cats.items())]

    # ── 금액 ────────────────────────────────────────────
    def _shipping(self, subtotal, categories):
        """무료배송 대상이 아닌 카테고리(임계값 NULL, 예: 화장품)가 섞이면 무조건 유료.
        그 외에는 포함된 모든 카테고리의 임계값을 합계가 전부 넘어야 무료."""
        rows = self.repo.categories()
        thresholds = []
        for key in categories:
            row = rows.get(key)
            threshold = row["free_shipping_threshold"] if row else None
            if threshold is None:
                return self.base_fee, False
            thresholds.append(threshold)
        if thresholds and subtotal >= max(thresholds):
            return 0, True
        return self.base_fee, False

    def quote(self, cart):
        lines, subtotal, categories = [], 0, []
        for item in cart:
            p = self.product(item["product_id"])
            if p is None:
                continue
            qty = int(item["qty"])
            amount = p["price"] * qty
            subtotal += amount
            categories.append(p["category"])
            lines.append({"product_id": p["product_id"], "name": p["name"], "option": item.get("option"),
                          "qty": qty, "price": p["price"], "amount": amount,
                          "soldout": bool(p["soldout"]), "stock": p["stock"], "category": p["category"]})
        if not lines:
            return {"lines": [], "subtotal": 0, "shipping_fee": 0, "free_shipping_applied": False, "total": 0}
        fee, free = self._shipping(subtotal, dict.fromkeys(categories))
        return {"lines": lines, "subtotal": subtotal, "shipping_fee": fee,
                "free_shipping_applied": free, "total": subtotal + fee}
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_shoprepo.py -q`
Expected: PASS (8개)

- [ ] **Step 5: 커밋**

```bash
git add server/shoprepo.py tests/test_shoprepo.py
git commit -m "feat: 쇼핑몰 상품 조회와 배송비 계산"
```

---

### Task 3: 주문 생성(트랜잭션·재고 차감)과 내 주문 조회

**Files:**
- Modify: `server/shoprepo.py`
- Test: `tests/test_shoprepo.py`

**Interfaces:**
- Consumes: Task 2 의 `ShopRepo`, `quote`, `_lock`
- Produces: 예외 `OrderError(message, blocked=())` — 속성 `.blocked: list[str]`; 메서드 `create_order(customer_id, cart, now=None) -> str`(주문번호), `orders_of(customer_id) -> list[dict]`, `order_of(customer_id, order_id) -> dict|None`(남의 주문이면 None, `items` 포함)

- [ ] **Step 1: 실패하는 테스트 추가** — `tests/test_shoprepo.py` 끝에

```python
@pytest.fixture
def memory_shop(modumall_dir_module):
    """쓰기 테스트 전용. 공유 DB 를 건드리지 않는다."""
    from server.shoprepo import ShopRepo

    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript((ROOT / "server" / "db" / "schema.sql").read_text(encoding="utf-8"))
    con.executescript("""
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
    """)
    con.commit()

    class FakeRepoDomain:
        fixed_values = {"base_shipping_fee": 2500}

    from server.repo import Repo
    repo = Repo.__new__(Repo)          # 파일 경로 없이 커넥션만 주입
    repo.con = con
    import threading
    repo._lock = threading.RLock()
    return ShopRepo(repo, FakeRepoDomain())


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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_shoprepo.py -q`
Expected: FAIL — `ImportError: cannot import name 'OrderError'` 또는 `AttributeError: 'ShopRepo' object has no attribute 'create_order'`

- [ ] **Step 3: 구현** — `server/shoprepo.py` 에 추가

```python
import datetime


class OrderError(Exception):
    """주문을 만들 수 없는 이유. message 와 blocked 는 화면에 그대로 보여준다."""

    def __init__(self, message, blocked=()):
        super().__init__(message)
        self.message = message
        self.blocked = list(blocked)
```

`ShopRepo` 에 메서드 추가:

```python
    # ── 주문 ────────────────────────────────────────────
    def _next_order_id(self):
        row = self.con.execute(
            "select max(cast(substr(order_id, 3) as integer)) from orders where order_id like 'O-%'").fetchone()
        return f"O-{(row[0] or 1000) + 1}"

    def create_order(self, customer_id, cart, now=None):
        """한 트랜잭션으로 주문·품목을 쓰고 재고를 줄인다. 하나라도 살 수 없으면 전부 취소한다."""
        if not cart:
            raise OrderError("장바구니가 비어 있습니다.")
        now = now or datetime.datetime.now().isoformat()
        with self._lock:
            try:
                self.con.execute("begin immediate")
                blocked, buyable = [], []
                for item in cart:
                    p = self.repo.product(item["product_id"])
                    qty = int(item["qty"])
                    if p is None:
                        blocked.append(f"{item['product_id']} 상품을 찾을 수 없습니다.")
                    elif p["soldout"]:
                        blocked.append(f"{p['name']} 은(는) 품절입니다.")
                    elif p["stock"] is not None and p["stock"] < qty:
                        blocked.append(f"{p['name']} 재고가 부족합니다 (남은 수량 {p['stock']}개).")
                    else:
                        buyable.append((p, item, qty))
                if blocked:
                    raise OrderError("주문할 수 없는 상품이 있습니다.", blocked)

                quote = self.quote(cart)
                customer = self.repo.customer(customer_id)
                order_id = self._next_order_id()
                self.con.execute(
                    "insert into orders (order_id, customer_id, ordered_at, status, is_external_channel, "
                    "order_amount, shipping_fee, free_shipping_applied, address_region, invoice_printed) "
                    "values (?,?,?,?,0,?,?,?,?,0)",
                    (order_id, customer_id, now, "결제완료", quote["subtotal"], quote["shipping_fee"],
                     1 if quote["free_shipping_applied"] else 0,
                     customer["address_region"] if customer else None))
                for p, item, qty in buyable:
                    self.con.execute(
                        "insert into order_items (order_id, product_id, name, option, qty, price) "
                        "values (?,?,?,?,?,?)",
                        (order_id, p["product_id"], p["name"], item.get("option"), qty, p["price"]))
                    if p["stock"] is not None:
                        self.con.execute("update products set stock = stock - ? where product_id = ?",
                                         (qty, p["product_id"]))
                self.con.commit()
                return order_id
            except Exception:
                self.con.rollback()
                raise

    def orders_of(self, customer_id):
        return self._all(
            "select order_id, customer_id, ordered_at, status, status_detail, order_amount, shipping_fee, "
            "courier, tracking_no, expected_delivery, delivered_at from orders "
            "where customer_id = ? order by ordered_at desc, order_id desc", (customer_id,))

    def order_of(self, customer_id, order_id):
        with self._lock:
            o = _row(self.con.execute("select * from orders where order_id = ? and customer_id = ?",
                                      (order_id, customer_id)).fetchone())
        if not o:
            return None
        o["items"] = self._all(
            "select product_id, name, option, qty, price from order_items where order_id = ? order by id",
            (order_id,))
        return o
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_shoprepo.py -q`
Expected: PASS (15개)

- [ ] **Step 5: 전체 스위트**

Run: `.venv/bin/python -m pytest -q`
Expected: 기존 280 + 신규 전부 통과

- [ ] **Step 6: 커밋**

```bash
git add server/shoprepo.py tests/test_shoprepo.py
git commit -m "feat: 쇼핑몰 주문 생성(트랜잭션·재고 차감)과 내 주문 조회"
```

---

### Task 4: 라우터 · 로그인 · 상품 목록/상세

**Files:**
- Create: `server/shop.py`, `server/templates/shop/base_shop.html`, `server/templates/shop/products.html`, `server/templates/shop/product_detail.html`, `server/templates/shop/login.html`, `server/templates/shop/notfound.html`, `web/shop.css`
- Modify: `server/app.py` (어드민 장착부 `server/app.py:94-97` 옆)
- Test: `tests/test_shop_routes.py`

**Interfaces:**
- Consumes: Task 1~3 의 `shopcookie`, `ShopRepo`, `PAGE_SIZE`
- Produces: `server.shop.shop_router(repo, domain) -> APIRouter`(prefix `/shop`), 쿠키 이름 `shop_session`·`shop_cart`, 헬퍼 `current_customer(request)`(라우터 내부)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_shop_routes.py`

```python
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.domain import load_domain
from server.pipeline import TurnResult
from server.repo import Repo


class FakePipelineWithRepo:
    def __init__(self, db_path):
        self.repo = Repo(db_path)

    def start_call(self, phone=None):
        return "abc", "안녕하세요", None

    def end_call(self, call_id):
        pass

    def turn(self, call_id, text):
        return TurnResult(answer="응답", route="SHIPPING", confidence=0.9, action="ANSWER", tools=[],
                          guardrail={"ok": True, "violations": []}, elapsed_ms=1, end_call=False, attempts=1)


@pytest.fixture(scope="module")
def client(modumall_dir_module, monkeypatch_module_secret):
    domain = load_domain(modumall_dir_module)
    return TestClient(create_app(FakePipelineWithRepo(domain.db_path), domain))


@pytest.fixture(scope="module")
def monkeypatch_module_secret():
    import os
    old = os.environ.get("SHOP_SECRET")
    os.environ["SHOP_SECRET"] = "테스트-쇼핑-키"
    yield
    if old is None:
        os.environ.pop("SHOP_SECRET", None)
    else:
        os.environ["SHOP_SECRET"] = old


def test_product_list_renders_with_filter_and_paging(client):
    r = client.get("/shop")
    assert r.status_code == 200 and "<h1>모두몰</h1>" in r.text
    assert "page=2" in r.text

    r = client.get("/shop", params={"category": "COSMETICS"})
    assert r.status_code == 200 and "화장품" in r.text

    r = client.get("/shop", params={"q": "존재하지않는상품"})
    assert "결과 없음" in r.text


def test_product_detail_and_404(client):
    r = client.get("/shop/products/P1001")
    assert r.status_code == 200 and "요일팬티 7종 세트" in r.text and "장바구니" in r.text
    missing = client.get("/shop/products/P9999")
    assert missing.status_code == 404 and "text/html" in missing.headers["content-type"]
    assert "찾을 수 없습니다" in missing.text


def test_login_sets_session_and_logout_clears(client):
    phone = client.app  # 아래에서 DB 에서 직접 뽑는다
    import sqlite3
    from server.domain import load_domain as _load
    con = sqlite3.connect(str(_load(__import__("pathlib").Path("domains/modumall")).db_path))
    name, phone = con.execute("select name, phone from customers order by customer_id limit 1").fetchone()

    with TestClient(client.app) as c:
        bad = c.post("/shop/login", data={"phone": "010-0000-0000"}, follow_redirects=False)
        assert bad.status_code == 200 and "가입 이력이 없는 번호" in bad.text

        ok = c.post("/shop/login", data={"phone": phone}, follow_redirects=False)
        assert ok.status_code == 303 and "shop_session" in ok.cookies
        mine = c.get("/shop/orders")
        assert mine.status_code == 200 and name in mine.text

        out = c.post("/shop/logout", follow_redirects=False)
        assert out.status_code == 303
        assert c.get("/shop/orders", follow_redirects=False).status_code == 303   # 로그인 화면으로


def test_forged_session_is_treated_as_logged_out(client):
    with TestClient(client.app) as c:
        c.cookies.set("shop_session", "위조된.쿠키", path="/shop")
        r = c.get("/shop/orders", follow_redirects=False)
        assert r.status_code == 303 and "/shop/login" in r.headers["location"]


def test_search_is_escaped(client):
    r = client.get("/shop", params={"q": "<script>alert(1)</script>"})
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_shop_routes.py -q`
Expected: FAIL — `/shop` 이 404

- [ ] **Step 3: 라우터 구현** — `server/shop.py`

```python
# -*- coding: utf-8 -*-
"""쇼핑몰 고객 화면 라우터. 주문 쓰기는 ShopRepo 에만 있다."""
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from server import shopcookie
from server.repo import normalize_phone
from server.shoprepo import PAGE_SIZE, OrderError, ShopRepo

TEMPLATES = Path(__file__).resolve().parent / "templates"
SESSION_COOKIE = "shop_session"
CART_COOKIE = "shop_cart"
COOKIE_PATH = "/shop"


def _won(n):
    return "-" if n is None else f"{int(n):,}원"


def _mmdd(iso):
    return f"{int(iso[5:7])}월 {int(iso[8:10])}일" if iso else "-"


def shop_router(repo, domain) -> APIRouter:
    shop = ShopRepo(repo, domain)
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.filters.update(won=_won, mmdd=_mmdd)

    def page_url(path, query, page):
        q = {k: v for k, v in query.items() if k != "page" and v}
        q["page"] = page
        return f"{path}?{urlencode(q)}"

    templates.env.globals["page_url"] = page_url
    router = APIRouter(prefix="/shop")

    # ── 세션·장바구니 ───────────────────────────────────
    def current_customer(request: Request):
        cid = shopcookie.unsign(request.cookies.get(SESSION_COOKIE))
        return repo.customer(cid) if cid else None

    def cart_of(request: Request):
        return shopcookie.load_cart(request.cookies.get(CART_COOKIE))

    def set_cart(response, cart):
        response.set_cookie(CART_COOKIE, shopcookie.dump_cart(cart), httponly=True,
                            samesite="lax", path=COOKIE_PATH)

    def render(request, name, customer, **ctx):
        return templates.TemplateResponse(request, f"shop/{name}",
                                          {"customer": customer, "cart_count": len(cart_of(request)), **ctx})

    def not_found(request, what, customer):
        return templates.TemplateResponse(request, "shop/notfound.html",
                                          {"what": what, "customer": customer, "cart_count": 0}, status_code=404)

    def redirect(path):
        return RedirectResponse(path, status_code=303)

    # ── 상품 ────────────────────────────────────────────
    @router.get("", response_class=HTMLResponse)
    def product_list(request: Request, category: str = "", q: str = "", page: int = 1):
        rows, total = shop.products(category=category or None, q=q or None, page=page)
        return render(request, "products.html", current_customer(request), rows=rows, total=total, page=page,
                      pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                      categories=shop.category_list(), query=dict(request.query_params), path=request.url.path)

    @router.get("/products/{product_id}", response_class=HTMLResponse)
    def product_detail(request: Request, product_id: str):
        customer = current_customer(request)
        p = shop.product(product_id)
        if not p:
            return not_found(request, f"상품 {product_id}", customer)
        return render(request, "product_detail.html", customer, p=p)

    # ── 로그인 ──────────────────────────────────────────
    @router.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "login.html", current_customer(request), error=None,
                      samples=repo.recently_active_customers.__self__ and [])

    @router.post("/login")
    async def login(request: Request):
        form = await request.form()
        phone = (form.get("phone") or "").strip()
        c = repo.customer_by_phone(normalize_phone(phone)) if phone else None
        if not c:
            return templates.TemplateResponse(
                request, "shop/login.html",
                {"customer": None, "cart_count": 0, "error": "가입 이력이 없는 번호입니다.", "samples": []},
                status_code=200)
        response = redirect("/shop")
        response.set_cookie(SESSION_COOKIE, shopcookie.sign(c["customer_id"]), httponly=True,
                            samesite="lax", path=COOKIE_PATH)
        return response

    @router.post("/logout")
    def logout():
        response = redirect("/shop")
        response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH)
        return response

    return router
```

주의:
- `login_form` 의 `samples` 는 위 코드처럼 쓰지 말고, `repo.sample_customers()` 가 `Repo` 에 없다면(파이프라인에만 있음) **빈 리스트로 두거나** `repo` 에서 직접 `select name, phone from customers order by customer_id limit 3` 로 뽑아 쓰세요. 구현자가 `server/repo.py` 를 읽고 실제 있는 메서드로 결정할 것. `server/repo.py` 는 수정 금지.
- 위 코드의 `Form(...)` 은 **쓰지 마세요.** Global Constraints 대로 `async def` + `form = await request.form()` 으로 바꿔 구현합니다(`python-multipart` 미설치, 컨트롤러 실측). 예: `async def login(request: Request): form = await request.form(); phone = (form.get("phone") or "").strip()`.

- [ ] **Step 4: 템플릿과 CSS**

`server/templates/shop/base_shop.html` — 헤더(로고 "모두몰", 검색 폼, 장바구니 링크 `장바구니 ({{ cart_count }})`, 로그인 상태면 `{{ customer.name }} 님`·내 주문·로그아웃 버튼, 아니면 로그인 링크), `{% block body %}`, `<link rel="stylesheet" href="/static/shop.css">`.

`server/templates/shop/products.html` — `<h1>모두몰</h1>`, 카테고리 필터(전체 + `categories` 반복 링크), 검색 폼(`name="q"`), 상품 카드 그리드(이름·가격 `| won`·품절 뱃지·상세 링크), 결과 없으면 `<p class="empty">결과 없음</p>`, 페이지네이션(`page_url(path, query, page-1/page+1)`).

`server/templates/shop/product_detail.html` — 상품명 `<h1>`, 가격, 카테고리, 소재(`p.material`), 재고/품절 안내(`p.soldout` 이면 `soldout_note`), 옵션(`p.options` 가 있으면 `<select name="option">` 로 `size`·`color` 조합을 문자열로), 수량 입력(1~99), `POST /shop/cart/add` 폼(Task 5 에서 라우트가 생기므로 이번 태스크에서는 폼만 두고 눌러도 404 인 상태가 정상).

`server/templates/shop/login.html` — 전화번호 입력 폼(`name="phone"`), `error` 가 있으면 빨간 문구, 데모용 예시 고객 3명(이름 · 전화) 표시.

`server/templates/shop/notfound.html` — "찾을 수 없습니다" + `{{ what }}`.

`web/shop.css` — `web/admin.css` 와 같은 다크 톤(같은 CSS 변수 값을 다시 선언). 상품 그리드는 `display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:12px`. 카드·버튼·폼 최소 규칙만.

- [ ] **Step 5: 앱에 장착** — `server/app.py`

어드민 장착 블록(`server/app.py:94-97`) 바로 아래에 추가한다.

```python
        from server.shop import shop_router
        app.include_router(shop_router(repo, domain))
```

- [ ] **Step 6: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_shop_routes.py -q`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add server/shop.py server/templates/shop web/shop.css server/app.py tests/test_shop_routes.py
git commit -m "feat: 쇼핑몰 상품 목록·상세와 전화번호 로그인"
```

---

### Task 5: 장바구니 · 주문 · 내 주문

**Files:**
- Modify: `server/shop.py`, `web/index.html`, `web/style.css`
- Create: `server/templates/shop/cart.html`, `server/templates/shop/checkout.html`, `server/templates/shop/done.html`, `server/templates/shop/orders.html`, `server/templates/shop/order_detail.html`
- Test: `tests/test_shop_routes.py`

**Interfaces:**
- Consumes: Task 3 의 `create_order`/`OrderError`/`orders_of`/`order_of`, Task 4 의 `render`/`cart_of`/`set_cart`/`current_customer`/`redirect`
- Produces: 경로 `/shop/cart`(GET), `/shop/cart/add`(POST), `/shop/cart/update`(POST), `/shop/checkout`(GET·POST), `/shop/orders`(GET), `/shop/orders/{order_id}`(GET)

- [ ] **Step 1: 실패하는 테스트 추가** — `tests/test_shop_routes.py` 끝에

```python
def _login(c, con):
    phone = con.execute("select phone from customers order by customer_id limit 1").fetchone()[0]
    assert c.post("/shop/login", data={"phone": phone}, follow_redirects=False).status_code == 303


def test_cart_add_update_and_quote(client):
    with TestClient(client.app) as c:
        add = c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 2, "option": "M"},
                     follow_redirects=False)
        assert add.status_code == 303 and add.headers["location"] == "/shop/cart"

        cart = c.get("/shop/cart")
        assert "요일팬티 7종 세트" in cart.text and "49,800원" in cart.text

        c.post("/shop/cart/update", data={"product_id": "P1001", "qty": 1}, follow_redirects=False)
        assert "24,900원" in c.get("/shop/cart").text

        c.post("/shop/cart/update", data={"product_id": "P1001", "qty": 0}, follow_redirects=False)
        assert "장바구니가 비어" in c.get("/shop/cart").text


def test_checkout_requires_login(client):
    with TestClient(client.app) as c:
        c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 1, "option": ""}, follow_redirects=False)
        r = c.get("/shop/checkout", follow_redirects=False)
        assert r.status_code == 303 and "/shop/login" in r.headers["location"]


def test_order_flow_creates_order_visible_in_admin(client, modumall_dir_module):
    import sqlite3
    con = sqlite3.connect(str(load_domain(modumall_dir_module).db_path))
    with TestClient(client.app) as c:
        _login(c, con)
        c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 1, "option": "M"}, follow_redirects=False)
        done = c.post("/shop/checkout", follow_redirects=False)
        assert done.status_code == 303
        location = done.headers["location"]
        assert location.startswith("/shop/orders/O-")
        order_id = location.rsplit("/", 1)[1]

        detail = c.get(location)
        assert detail.status_code == 200 and order_id in detail.text and "요일팬티 7종 세트" in detail.text

        assert "장바구니가 비어" in c.get("/shop/cart").text            # 주문 후 장바구니는 비워진다
        assert order_id in c.get("/shop/orders").text
        assert order_id in c.get(f"/admin/orders/{order_id}").text      # 어드민에도 보인다


def test_order_detail_of_other_customer_is_404(client, modumall_dir_module):
    import sqlite3
    con = sqlite3.connect(str(load_domain(modumall_dir_module).db_path))
    other = con.execute("select order_id from orders where customer_id != (select customer_id from customers "
                        "order by customer_id limit 1) limit 1").fetchone()[0]
    with TestClient(client.app) as c:
        _login(c, con)
        assert c.get(f"/shop/orders/{other}").status_code == 404


def test_checkout_rejects_soldout_product(client, modumall_dir_module):
    import sqlite3
    con = sqlite3.connect(str(load_domain(modumall_dir_module).db_path))
    soldout = con.execute("select product_id from products where soldout=1 limit 1").fetchone()[0]
    with TestClient(client.app) as c:
        _login(c, con)
        c.post("/shop/cart/add", data={"product_id": soldout, "qty": 1, "option": ""}, follow_redirects=False)
        r = c.post("/shop/checkout", follow_redirects=True)
        assert "품절" in r.text
```

주의: 이 테스트들은 **공유 DB 에 주문을 실제로 씁니다.** 다른 테스트가 건수를 하드코딩하지 않으므로 허용하되, 주문 id 는 절대 하드코딩하지 말고 응답에서 뽑아 쓰세요(위 코드처럼).

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_shop_routes.py -q`
Expected: FAIL — `/shop/cart` 404

- [ ] **Step 3: 라우트 구현** — `server/shop.py` 의 `shop_router` 안, `return router` 앞

```python
    # ── 장바구니 ────────────────────────────────────────
    @router.get("/cart", response_class=HTMLResponse)
    def cart_view(request: Request):
        cart = cart_of(request)
        return render(request, "cart.html", current_customer(request), quote=shop.quote(cart), blocked=[])

    @router.post("/cart/add")
    async def cart_add(request: Request):
        form = await request.form()
        product_id = form.get("product_id") or ""
        option = form.get("option") or ""
        try:
            qty = int(form.get("qty") or 1)
        except ValueError:
            qty = 1
        cart = cart_of(request)
        for line in cart:
            if line["product_id"] == product_id and (line["option"] or "") == (option or ""):
                line["qty"] = min(shopcookie.MAX_QTY, line["qty"] + max(1, qty))
                break
        else:
            cart.append({"product_id": product_id, "option": option or None,
                         "qty": max(1, min(shopcookie.MAX_QTY, qty))})
        response = redirect("/shop/cart")
        set_cart(response, cart[:shopcookie.MAX_CART_LINES])
        return response

    @router.post("/cart/update")
    async def cart_update(request: Request):
        form = await request.form()
        product_id = form.get("product_id") or ""
        try:
            qty = int(form.get("qty") or 0)
        except ValueError:
            qty = 0
        cart = [line for line in cart_of(request) if line["product_id"] != product_id or qty > 0]
        for line in cart:
            if line["product_id"] == product_id:
                line["qty"] = max(1, min(shopcookie.MAX_QTY, qty))
        response = redirect("/shop/cart")
        set_cart(response, cart)
        return response

    # ── 주문 ────────────────────────────────────────────
    @router.get("/checkout", response_class=HTMLResponse)
    def checkout_form(request: Request):
        customer = current_customer(request)
        if not customer:
            return redirect("/shop/login")
        cart = cart_of(request)
        if not cart:
            return redirect("/shop/cart")
        return render(request, "checkout.html", customer, quote=shop.quote(cart))

    @router.post("/checkout")
    def checkout(request: Request):
        customer = current_customer(request)
        if not customer:
            return redirect("/shop/login")
        cart = cart_of(request)
        try:
            order_id = shop.create_order(customer["customer_id"], cart)
        except OrderError as e:
            response = templates.TemplateResponse(
                request, "shop/cart.html",
                {"customer": customer, "cart_count": len(cart), "quote": shop.quote(cart),
                 "blocked": [e.message, *e.blocked]}, status_code=200)
            return response
        response = redirect(f"/shop/orders/{order_id}")
        set_cart(response, [])                    # 주문이 끝나면 장바구니를 비운다
        return response

    @router.get("/orders", response_class=HTMLResponse)
    def my_orders(request: Request):
        customer = current_customer(request)
        if not customer:
            return redirect("/shop/login")
        return render(request, "orders.html", customer, rows=shop.orders_of(customer["customer_id"]))

    @router.get("/orders/{order_id}", response_class=HTMLResponse)
    def my_order_detail(request: Request, order_id: str):
        customer = current_customer(request)
        if not customer:
            return redirect("/shop/login")
        o = shop.order_of(customer["customer_id"], order_id)
        if not o:
            return not_found(request, f"주문 {order_id}", customer)
        return render(request, "order_detail.html", customer, o=o)
```

- [ ] **Step 4: 템플릿 작성**

`cart.html` — `blocked` 가 있으면 상단에 빨간 목록으로 사유 표시. `quote.lines` 표(상품명·옵션·단가·수량 폼(`POST /shop/cart/update`, 0 이면 삭제)·금액), 합계(`quote.subtotal | won`), 배송비(`quote.shipping_fee | won`, 무료면 "무료배송"), 총액(`quote.total | won`), "주문하기" 버튼(`/shop/checkout` 링크). 비었으면 "장바구니가 비어 있습니다".

`checkout.html` — 배송지(`customer.address`, `customer.address_region`), 품목 표(cart 와 같은 항목, 수정 폼 없음), 금액 요약, `POST /shop/checkout` 제출 버튼("결제하고 주문하기").

`order_detail.html` — `<h1>주문 {{ o.order_id }}</h1>`, 상태·주문일(`| mmdd`)·금액·배송비·배송지, 품목 표, 그리고 주문 직후에도 같은 화면을 쓰므로 상단에 "주문이 접수되었습니다. 주문번호 {{ o.order_id }} 로 상담원에게 문의하실 수 있습니다." 안내(스펙 2.6). `done.html` 은 별도로 만들지 말고 이 템플릿 하나로 겸한다 — **파일 구조표의 done.html 은 만들지 않는다.**

`orders.html` — 내 주문 목록 표(주문번호 링크·주문일·상태·금액). 없으면 "주문 내역이 없습니다".

- [ ] **Step 5: 통화 화면에 쇼핑몰 링크** — `web/index.html` 헤더의 `.admin-link` 옆에 추가

```html
      <a class="admin-link" href="/shop" target="_blank" rel="noopener">쇼핑몰</a>
```

`web/style.css` 는 기존 `.admin-link` 규칙을 그대로 쓴다(새 규칙 불필요).

- [ ] **Step 6: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_shop_routes.py -q`
Expected: PASS

- [ ] **Step 7: 전체 스위트**

Run: `.venv/bin/python -m pytest -q`
Expected: 전부 통과. 이어서 `.venv/bin/python -m pytest tests/test_generate.py -q && .venv/bin/python -m pytest -q` 도 통과해야 한다(실행 순서 무관).

- [ ] **Step 8: 커밋**

```bash
git add server/shop.py server/templates/shop web/index.html tests/test_shop_routes.py
git commit -m "feat: 쇼핑몰 장바구니·주문 생성·내 주문 화면"
```

---

### Task 6: 통합 확인과 손 확인

**Files:**
- Test: `tests/test_shop_routes.py` (통합 테스트 추가)
- Modify: 필요 시 발견된 결함만

**Interfaces:**
- Consumes: Task 5 까지의 전체 경로

- [ ] **Step 1: 에이전트 도구 연결 테스트 추가**

```python
def test_shop_order_is_visible_to_agent_tool(client, modumall_dir_module):
    """쇼핑몰에서 만든 주문을 상담 에이전트의 조회 도구가 찾을 수 있어야 한다."""
    import sqlite3

    from server.domain import load_domain as _load
    from server.tools import build_tools          # 실제 함수명은 server/tools.py 를 읽고 맞출 것

    con = sqlite3.connect(str(_load(modumall_dir_module).db_path))
    with TestClient(client.app) as c:
        _login(c, con)
        c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 1, "option": "M"}, follow_redirects=False)
        order_id = c.post("/shop/checkout", follow_redirects=False).headers["location"].rsplit("/", 1)[1]

    # get_order_status 도구로 같은 주문을 조회한다
    domain = _load(modumall_dir_module)
    result = <server/tools.py 의 실제 진입점으로 get_order_status(order_id) 호출>
    assert result["order_id"] == order_id and result["status"] == "결제완료"
```

구현자는 `server/tools.py` 를 먼저 읽고 도구를 만드는 실제 함수·시그니처에 맞춰 위 테스트를 완성하세요(위 `build_tools`·호출부는 자리표시가 아니라 **실제 이름으로 바꿔야 하는 부분**입니다). 도구 호출이 LLM 없이 직접 되지 않는 구조라면, 대신 `Repo.order(order_id)` 가 그 주문을 돌려주는지 검증하고 그 이유를 보고서에 쓰세요.

- [ ] **Step 2: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_shop_routes.py -q`
Expected: PASS

- [ ] **Step 3: 손 확인 (서버를 백그라운드로 띄울 것)**

```bash
PORT=8010 .venv/bin/python -m server
```

확인 항목(각각 결과를 보고서에 쓸 것):
1. `/shop` 목록에서 카테고리 필터·검색·페이지 이동이 되는가
2. 상품 상세에서 옵션·수량을 골라 담으면 장바구니에 반영되는가
3. 로그인 없이 주문 시도 → 로그인 화면으로, 로그인 후 주문하면 주문번호가 나오는가
4. 그 주문번호가 `/shop/orders`, `/admin/orders`, 어드민 고객 상세에 모두 보이는가
5. 품절 상품을 담아 주문하면 사유가 표시되고 주문이 생기지 않는가
6. 통화 화면(`/`) 헤더의 "쇼핑몰" 링크가 동작하는가

curl 로 확인해도 되지만, 화면 배치·가독성 판단이 필요한 항목은 "확인 불가"로 명시하세요(추측 금지). **당신이 띄운 서버는 확인 후 반드시 종료하세요.**

- [ ] **Step 4: 전체 스위트 재확인**

Run: `.venv/bin/python -m pytest -q` 그리고 `.venv/bin/python -m pytest tests/test_generate.py -q && .venv/bin/python -m pytest -q`
Expected: 두 경우 모두 통과

- [ ] **Step 5: 커밋**

```bash
git add tests/test_shop_routes.py
git commit -m "test: 쇼핑몰 주문이 에이전트 도구·어드민에 연결되는지 통합 검증"
```

---

## 자기 점검 (플랜 작성자용, 실행자는 건너뛴다)

- 스펙 2.1 라우트 11개 → Task 4(목록·상세·로그인·로그아웃)와 Task 5(장바구니 3·체크아웃 2·내 주문 2). 전부 있음.
- 스펙 2.2 세션(HMAC·쿠키 속성·없는 번호 안내) → Task 1·4. 2.3 장바구니(서명 쿠키·20줄·1~99) → Task 1·5.
- 스펙 2.4 금액(기본 배송비 출처·화장품 예외·혼합 카테고리·`quote` 단일화) → Task 2.
- 스펙 2.5 주문 생성(트랜잭션·채번·전체 거부·재고 차감·롤백) → Task 3.
- 스펙 2.6 연결(통화 화면 링크·주문 완료 안내) → Task 5.
- 스펙 3 테스트 항목 → Task 1·2·3·4·5·6 에 분산. 스펙 4 성공 기준 1·2·4 는 Task 6 이 검증.
- 파일 구조표의 `done.html` 은 Task 5 에서 `order_detail.html` 로 겸하기로 했으므로 만들지 않는다(표와 Task 5 지시가 어긋나면 Task 5 를 따른다).
