# A단계: 현실적인 데이터 기반과 고객 식별 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 목 JSON을 SQLite 기반의 실제 규모 데이터로 바꾸고, 발신번호로 고객을 식별해 최근 주문을 컨텍스트로 넘긴다.

**Architecture:** `server/db/`(스키마·생성기)와 `server/repo.py`(읽기 전용 저장소)를 새로 두고, `tools.py`는 dict 조회를 Repo 호출로 바꾼다. 파이프라인은 통화별 고객 컨텍스트를 답변기에 넘기고 가드레일 허용 집합에 합친다. 정식 데이터(mockdb.json)는 seed로 그대로 DB에 들어가 기존 평가가 유효하다.

**Tech Stack:** Python 3.12 `sqlite3`(표준), FastAPI, LangGraph. 화면은 기존 HTML/JS.

**Spec:** `docs/superpowers/specs/2026-09-16-realistic-data-and-customer-id-design.md`

## Global Constraints

- `.venv/bin/pytest`, `.venv/bin/python` 만 사용. 모든 테스트는 API 키 없이 통과.
- 기존 129개 테스트는 계속 통과해야 한다(도구 시그니처·반환 dict 모양 유지).
- 정식 데이터 보존: `mockdb.json`의 상품 20(P1001~P6003), 주문 11(O-1001~O-1011), 반품 5(R-2001~R-2005), 재입고 2는 같은 ID·값으로 DB에 있어야 한다.
- 생성기는 결정적(seed 20260916, 기준일 2026-09-16). DB 파일은 git에 넣지 않는다.
- 도구 이름은 기존 9개 + `find_customer`. 새 키는 `domain.json`에서 선택적.
- 코드 식별자 영어, 주석·로그·UI 문구 한국어. 커밋 트레일러 `Co-Authored-By: Claude <모델명> <noreply@anthropic.com>`.

## 현재 코드 요약

- `server/domain.py`: `Domain` frozen dataclass(name, greeting, out_of_scope_message, escalate_message, routes, always_sections, routing_rules, fixed_values, small_numbers_allowed, policy_text, mockdb, path, clarify_message, search). `load_domain(path)`.
- `server/tools.py`: `make_tools(domain)`가 `domain.mockdb`에서 dict를 만들어 클로저 9개를 돌려준다. `search_product`는 products dict를 순회. `clean()`은 `$`키 제거.
- `server/pipeline.py`: `Pipeline.start_call() -> call_id`, `turn(call_id, text)`, `_node_answer`가 `self.answerer.answer(question, route, history=..., feedback=...)` 호출, `_node_guard`가 `guardrail.check(answer, results, domain)`.
- `server/answer.py`: `Answerer.answer(question, route, history=None, feedback=None)`; 시스템 프롬프트는 `build_answer_prompt(domain, route)`.
- `server/app.py`: `create_app(pipeline, domain, tts=None)`; `POST /api/call/start` 인자 없음; `GET /api/domain` → name, greeting, tts_available.
- `web/call.js`: `startCall()`이 `/api/call/start`를 POST. `web/panel.js`: `createPanel(root)` with `clear()`, `addTurn()`.
- `tests/conftest.py`: `modumall_dir` fixture. `tests/test_pipeline.py`: `settings` fixture, `FakeAnswerer(script)` with `.answer(question, route, history=None, feedback=None)` recording `questions`.

---

### Task 1: 스키마와 생성기 (`server/db/`)

**Files:**
- Create: `server/db/__init__.py`(빈 파일), `server/db/schema.sql`, `server/db/generate.py`
- Modify: `server/domain.py`(`db_path` 필드, 자동 생성), `.gitignore`(`domains/*/modumall.db` 대신 `domains/*/*.db`)
- Test: `tests/test_generate.py`

**Interfaces:**
- Produces: `server.db.generate.generate(domain_dir: Path, force: bool = False) -> Path` — `domain_dir/<폴더명>.db`를 만들고 경로를 돌려준다(이미 있고 force가 아니면 그대로). CLI: `python -m server.db.generate --domain modumall [--force]`.
- `Domain.db_path: Path` — `load_domain`이 없으면 `generate()`를 호출해 만든다.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_generate.py`:

```python
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
    assert [r[0] for r in db.execute("select product_id from products order by product_id limit 20")] == CANON_PRODUCTS


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
```

`tests/conftest.py`에 module 범위 fixture 추가:

```python
@pytest.fixture(scope="module")
def modumall_dir_module() -> Path:
    return ROOT / "domains" / "modumall"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_generate.py -q`
Expected: ImportError

- [ ] **Step 3: schema.sql**

```sql
PRAGMA journal_mode = WAL;
CREATE TABLE categories (
  key TEXT PRIMARY KEY, label TEXT NOT NULL, free_shipping_threshold INTEGER, free_shipping_note TEXT,
  return_window_days INTEGER NOT NULL, return_window_basis TEXT NOT NULL, requires_unopened INTEGER NOT NULL DEFAULT 0);
CREATE TABLE same_day_delivery (region TEXT PRIMARY KEY, available INTEGER NOT NULL, fee INTEGER, extra_fee INTEGER, note TEXT);
CREATE TABLE customers (
  customer_id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL UNIQUE,
  address_region TEXT NOT NULL, address TEXT NOT NULL, joined_at TEXT NOT NULL);
CREATE TABLE products (
  product_id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL REFERENCES categories(key),
  price INTEGER NOT NULL, stock INTEGER, is_set INTEGER NOT NULL DEFAULT 0, components TEXT, material TEXT, material_note TEXT,
  origin TEXT, has_quality_cert INTEGER NOT NULL DEFAULT 0, made_to_order INTEGER NOT NULL DEFAULT 0, made_to_order_days INTEGER,
  options TEXT, size_chart TEXT, size_matching TEXT, individual_purchase_allowed INTEGER, individual_purchase_note TEXT,
  individual_prices TEXT, return_allowed INTEGER, return_blocked_reason TEXT, soldout INTEGER NOT NULL DEFAULT 0,
  soldout_note TEXT, stock_note TEXT);
CREATE TABLE orders (
  order_id TEXT PRIMARY KEY, customer_id TEXT REFERENCES customers(customer_id), ordered_at TEXT NOT NULL,
  status TEXT NOT NULL, status_detail TEXT, is_external_channel INTEGER NOT NULL DEFAULT 0, external_channel_name TEXT,
  order_amount INTEGER NOT NULL, shipping_fee INTEGER NOT NULL, free_shipping_applied INTEGER NOT NULL DEFAULT 0,
  address_region TEXT, courier TEXT, tracking_no TEXT, invoice_printed INTEGER NOT NULL DEFAULT 0,
  expected_ship_date TEXT, shipped_at TEXT, expected_delivery TEXT, delivered_at TEXT, delay_days INTEGER, delay_reason TEXT,
  return_id TEXT, note TEXT);
CREATE TABLE order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL REFERENCES orders(order_id),
  product_id TEXT NOT NULL REFERENCES products(product_id), name TEXT NOT NULL, option TEXT, qty INTEGER NOT NULL, price INTEGER NOT NULL);
CREATE TABLE shipment_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL REFERENCES orders(order_id), at TEXT NOT NULL, status TEXT NOT NULL, location TEXT);
CREATE TABLE returns (
  return_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(order_id), type TEXT NOT NULL, return_scope TEXT,
  reason_stated TEXT, requested_at TEXT NOT NULL, stage TEXT NOT NULL, inspection_result TEXT, fault_party TEXT,
  shipping_fee_bearer TEXT, return_fee INTEGER, refund_amount INTEGER, refund_calc TEXT, expected_completion TEXT,
  exchange_target TEXT, exchange_available INTEGER, exchange_blocked_reason TEXT, convert_to_refund INTEGER,
  courier_visit_expected TEXT, note TEXT);
CREATE TABLE return_stage_history (id INTEGER PRIMARY KEY AUTOINCREMENT, return_id TEXT NOT NULL REFERENCES returns(return_id), stage TEXT NOT NULL, date TEXT NOT NULL);
CREATE TABLE restock (
  product_id TEXT PRIMARY KEY REFERENCES products(product_id), name TEXT NOT NULL, is_soldout INTEGER NOT NULL,
  is_confirmed INTEGER NOT NULL, expected_date TEXT, expected_note TEXT, notify_available INTEGER NOT NULL DEFAULT 1);
CREATE TABLE call_logs (call_id TEXT PRIMARY KEY, customer_id TEXT, started_at TEXT NOT NULL, ended_at TEXT, turns TEXT NOT NULL DEFAULT '[]');
CREATE INDEX idx_orders_customer ON orders(customer_id, ordered_at DESC);
CREATE INDEX idx_returns_order ON returns(order_id);
CREATE INDEX idx_items_order ON order_items(order_id);
```

- [ ] **Step 4: generate.py**

```python
# -*- coding: utf-8 -*-
"""도메인 폴더의 mockdb.json(정식 seed)을 그대로 넣고, 그 위에 실제 규모의 합성 데이터를 얹어 SQLite DB 를 만든다.

결정적(seed 고정)이라 언제 만들어도 같은 DB 가 나온다. 정식 seed 의 ID·값은 절대 바꾸지 않는다
(정답셋과 회귀 스위트가 그 값을 기준으로 채점하기 때문).
  python -m server.db.generate --domain modumall [--force]
"""
import argparse
import json
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 20260916
TODAY = date(2026, 9, 16)
SCHEMA = Path(__file__).with_name("schema.sql")

# ── 합성 데이터 재료 ────────────────────────────────────────────────────────
SURNAMES = "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진지엄채원천방공"
GIVEN = ["민준", "서준", "도윤", "예준", "시우", "하준", "지호", "주원", "지우", "서연", "서윤", "지민", "하은", "하윤", "민서",
         "수아", "지아", "윤서", "채원", "지유", "은우", "현우", "지훈", "준서", "유진", "다은", "소율", "예린", "수빈", "지원"]
REGIONS = [("수도권", ["서울특별시 강남구", "서울특별시 마포구", "경기도 성남시 분당구", "경기도 고양시", "인천광역시 연수구"], 0.62),
           ("수도권외", ["부산광역시 해운대구", "대구광역시 수성구", "대전광역시 유성구", "광주광역시 서구", "강원특별자치도 춘천시", "전북특별자치도 전주시"], 0.33),
           ("제주도서산간", ["제주특별자치도 제주시", "제주특별자치도 서귀포시", "경상남도 통영시 욕지면"], 0.05)]
NAMES = {
    "APPAREL": (["린넨 셔츠", "오버핏 후디", "슬랙스", "니트 가디건", "플리츠 스커트", "와이드 데님", "트렌치 코트", "패딩 조끼", "크롭 자켓", "롱 원피스",
                 "맨투맨", "카고 팬츠", "블라우스", "울 코트", "조거 팬츠", "폴로 셔츠"], ["베이직", "프리미엄", "라이트", "코튼", "울 블렌드", "스트레치"]),
    "UNDERWEAR": (["심리스 팬티 3매", "브라렛", "런닝 3매", "보정 속옷", "드로즈 2매", "잠옷 세트", "니플커버", "무봉제 브라"], ["코튼", "모달", "에어", "데일리", "소프트"]),
    "ACCESSORY": (["진주 귀걸이", "체인 팔찌", "레이어드 목걸이", "미니 반지", "헤어핀 세트", "실버 앵클릿", "볼 귀걸이", "커프 링"], ["실버", "14K 도금", "스틸", "미니멀", "빈티지"]),
    "BAG_GOODS": (["토트백", "백팩", "카드 지갑", "클러치", "버킷햇", "머플러", "벨트백", "에코백", "장지갑", "볼캡"], ["가죽", "캔버스", "나일론", "데님", "스웨이드"]),
    "SHOES": (["로퍼", "첼시 부츠", "러닝화", "샌들", "슬립온", "더비 슈즈", "하이탑", "메리제인", "슬리퍼", "워커"], ["클래식", "라이트", "쿠션", "스웨이드", "레더"]),
    "COSMETICS": (["수분 크림", "선크림", "클렌징 오일", "토너", "세럼", "립 틴트", "쿠션 팩트", "핸드크림", "바디로션", "마스크팩 10매"], ["어성초", "히알루론", "비타민", "시카", "티트리"]),
}
PRICE_RANGE = {"APPAREL": (19000, 189000), "UNDERWEAR": (9900, 49000), "ACCESSORY": (12000, 89000),
               "BAG_GOODS": (15000, 159000), "SHOES": (39000, 219000), "COSMETICS": (8900, 59000)}
SIZES = {"APPAREL": ["S", "M", "L", "XL"], "UNDERWEAR": ["S", "M", "L"], "SHOES": ["230", "240", "250", "260", "270"]}
COLORS = ["블랙", "화이트", "베이지", "네이비", "그레이", "브라운", "카키", "버건디"]
MATERIALS = {"APPAREL": ["면 100%", "면 80%, 폴리에스터 20%", "울 70%, 나일론 30%", "린넨 55%, 면 45%", "폴리에스터 100%"],
             "UNDERWEAR": ["면 95%, 폴리우레탄 5%", "모달 92%, 스판덱스 8%", "나일론 80%, 스판덱스 20%"],
             "ACCESSORY": ["925 실버", "황동 14K 도금", "서지컬 스틸"], "BAG_GOODS": ["소가죽", "캔버스", "나일론", "PU"],
             "SHOES": ["천연 가죽", "스웨이드", "메시 + 합성 피혁"], "COSMETICS": ["정제수, 글리세린, 부틸렌글라이콜 외"]}
ORIGINS = ["대한민국", "중국", "베트남", "이탈리아", "인도네시아"]
COURIERS = ["롯데택배"]
RETURN_REASONS = ["사이즈가 맞지 않음", "색상이 상세페이지와 다름", "단순 변심", "배송 중 파손", "오배송", "소재가 기대와 다름", "봉제 불량"]


def dt(d: date, h=0, m=0):
    return datetime(d.year, d.month, d.day, h, m).strftime("%Y-%m-%dT%H:%M:%S")


def jdump(v):
    return None if v is None else json.dumps(v, ensure_ascii=False)


def strip_notes(obj):
    if isinstance(obj, dict):
        return {k: strip_notes(v) for k, v in obj.items() if not k.startswith("$")}
    return obj


class Gen:
    def __init__(self, con: sqlite3.Connection, seed: dict, rnd: random.Random):
        self.con, self.seed, self.rnd = con, seed, rnd
        self.products: dict[str, dict] = {}
        self.customers: list[dict] = []
        self.categories = {k: strip_notes(v) for k, v in seed["categories"].items()}

    # ── 참조 데이터 ─────────────────────────────────────────────
    def refs(self):
        for k, c in self.categories.items():
            self.con.execute("insert into categories values (?,?,?,?,?,?,?)",
                             (k, c["label"], c.get("free_shipping_threshold"), c.get("free_shipping_note"),
                              c["return_window_days"], c["return_window_basis"], int(c["requires_unopened"])))
        for region, s in strip_notes(self.seed["same_day_delivery"]).items():
            self.con.execute("insert into same_day_delivery values (?,?,?,?,?)",
                             (region, int(s.get("available", False)), s.get("fee"), s.get("extra_fee"), s.get("note")))

    # ── 정식 seed 그대로 ────────────────────────────────────────
    def canonical(self):
        for p in self.seed["products"]:
            self.insert_product(strip_notes(p))
        # 정식 주문 11건에 고객 8명 배정 (결정적 순서)
        owners = [self.new_customer() for _ in range(8)]
        for i, o in enumerate(self.seed["orders"]):
            o = strip_notes(o)
            cust = owners[i % 8]
            self.insert_order(o, cust["customer_id"])
        for r in self.seed["returns"]:
            self.insert_return(strip_notes(r))
        for r in self.seed["restock"]:
            r = strip_notes(r)
            self.con.execute("insert into restock values (?,?,?,?,?,?,?)",
                             (r["product_id"], r["name"], int(r["is_soldout"]), int(r["is_confirmed"]),
                              r.get("expected_date"), r.get("expected_note"), int(r.get("notify_available", True))))

    def insert_product(self, p: dict):
        self.products[p["product_id"]] = p
        self.con.execute("""insert into products (product_id,name,category,price,stock,is_set,components,material,material_note,origin,
            has_quality_cert,made_to_order,made_to_order_days,options,size_chart,size_matching,individual_purchase_allowed,
            individual_purchase_note,individual_prices,return_allowed,return_blocked_reason,soldout,soldout_note,stock_note)
            values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["product_id"], p["name"], p["category"], p["price"], p.get("stock"), int(p.get("is_set", False)),
             jdump(p.get("components")), p.get("material"), p.get("material_note"), p.get("origin"),
             int(p.get("has_quality_cert", False)), int(p.get("made_to_order", False)), p.get("made_to_order_days"),
             jdump(p.get("options")), jdump(p.get("size_chart")), p.get("size_matching"),
             None if p.get("individual_purchase_allowed") is None else int(p["individual_purchase_allowed"]),
             p.get("individual_purchase_note"), jdump(p.get("individual_prices")),
             None if p.get("return_allowed") is None else int(p["return_allowed"]), p.get("return_blocked_reason"),
             int(p.get("soldout", False)), p.get("soldout_note"), p.get("stock_note")))

    def insert_order(self, o: dict, customer_id):
        self.con.execute("""insert into orders (order_id,customer_id,ordered_at,status,status_detail,is_external_channel,external_channel_name,
            order_amount,shipping_fee,free_shipping_applied,address_region,courier,tracking_no,invoice_printed,expected_ship_date,
            shipped_at,expected_delivery,delivered_at,delay_days,delay_reason,return_id,note) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (o["order_id"], customer_id, o["ordered_at"], o["status"], o.get("status_detail"), int(o.get("is_external_channel", False)),
             o.get("external_channel_name"), o["order_amount"], o["shipping_fee"], int(o.get("free_shipping_applied", False)),
             o.get("address_region"), o.get("courier"), o.get("tracking_no"), int(o.get("invoice_printed", False)),
             o.get("expected_ship_date"), o.get("shipped_at"), o.get("expected_delivery"), o.get("delivered_at"),
             o.get("delay_days"), o.get("delay_reason"), o.get("return_id"), o.get("note")))
        for it in o["items"]:
            self.con.execute("insert into order_items (order_id,product_id,name,option,qty,price) values (?,?,?,?,?,?)",
                             (o["order_id"], it["product_id"], it["name"], it.get("option"), it["qty"], it["price"]))
        for ev in self.events_for(o):
            self.con.execute("insert into shipment_events (order_id,at,status,location) values (?,?,?,?)", (o["order_id"], *ev))

    def events_for(self, o: dict):
        """주문 상태에 맞는 배송 이력을 만든다 (정식 주문에도 붙인다 — 값 충돌 없음)."""
        evs = [(o["ordered_at"], "주문 접수", "온라인")]
        if o.get("shipped_at"):
            evs.append((o["shipped_at"], "출고", "모두몰 물류센터"))
            evs.append((o["shipped_at"][:10] + "T21:00:00", "간선 상차", "옥천 HUB"))
        if o.get("delivered_at"):
            evs.append((o["delivered_at"], "배송 완료", o.get("address_region") or ""))
        return evs

    def insert_return(self, r: dict):
        self.con.execute("""insert into returns (return_id,order_id,type,return_scope,reason_stated,requested_at,stage,inspection_result,
            fault_party,shipping_fee_bearer,return_fee,refund_amount,refund_calc,expected_completion,exchange_target,exchange_available,
            exchange_blocked_reason,convert_to_refund,courier_visit_expected,note) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["return_id"], r["order_id"], r["type"], r.get("return_scope"), r.get("reason_stated"), r["requested_at"], r["stage"],
             r.get("inspection_result"), r.get("fault_party"), r.get("shipping_fee_bearer"), r.get("return_fee"), r.get("refund_amount"),
             r.get("refund_calc"), r.get("expected_completion"), r.get("exchange_target"),
             None if r.get("exchange_available") is None else int(r["exchange_available"]), r.get("exchange_blocked_reason"),
             None if r.get("convert_to_refund") is None else int(r["convert_to_refund"]), r.get("courier_visit_expected"), r.get("note")))
        for h in r.get("stage_history", []):
            self.con.execute("insert into return_stage_history (return_id,stage,date) values (?,?,?)", (r["return_id"], h["stage"], h["date"]))

    # ── 합성 ───────────────────────────────────────────────────
    def new_customer(self):
        n = len(self.customers) + 1
        region, addrs, _ = self.rnd.choices(REGIONS, weights=[w for _, _, w in REGIONS])[0]
        c = {"customer_id": f"C-{n:04d}", "name": self.rnd.choice(SURNAMES) + self.rnd.choice(GIVEN),
             "phone": f"010-{self.rnd.randint(2000, 9999):04d}-{n:04d}", "address_region": region,
             "address": f"{self.rnd.choice(addrs)} {self.rnd.randint(1, 99)}길 {self.rnd.randint(1, 60)}",
             "joined_at": (TODAY - timedelta(days=self.rnd.randint(30, 900))).isoformat()}
        self.customers.append(c)
        self.con.execute("insert into customers values (?,?,?,?,?,?)", tuple(c.values()))
        return c

    def synth_products(self, total=180):
        counters = {k: 0 for k in NAMES}
        # 정식 상품 다음 번호부터 (P1004, P2004, …). 카테고리 접두 숫자는 mockdb 규칙을 따른다
        prefix = {"UNDERWEAR": 1, "ACCESSORY": 2, "APPAREL": 3, "SHOES": 4, "COSMETICS": 5, "BAG_GOODS": 6}
        existing = {k: max(int(pid[2:]) for pid in self.products if int(pid[1]) == prefix[k]) for k in prefix}
        cats = list(NAMES)
        while len(self.products) < total:
            cat = cats[len(self.products) % len(cats)]
            base, mods = NAMES[cat]
            name = f"{self.rnd.choice(mods)} {self.rnd.choice(base)}"
            if any(p["name"] == name for p in self.products.values()):
                name += f" {self.rnd.choice(['II', '라인', '에디션', '컬렉션'])}"
            existing[cat] += 1
            pid = f"P{prefix[cat]}{existing[cat]:03d}"
            lo, hi = PRICE_RANGE[cat]
            price = int(round(self.rnd.randint(lo, hi) / 100) * 100)
            soldout = self.rnd.random() < 0.06
            sizes = SIZES.get(cat)
            options = {"color": self.rnd.sample(COLORS, self.rnd.randint(1, 3))}
            if sizes:
                options["size"] = sizes
            size_chart = {s: f"{'발길이' if cat == 'SHOES' else '가슴'} {self.rnd.randint(60, 120)}cm" for s in sizes} if sizes else None
            mto = cat == "APPAREL" and self.rnd.random() < 0.05
            p = {"product_id": pid, "name": name, "category": cat, "price": price, "stock": 0 if soldout else self.rnd.randint(3, 300),
                 "is_set": "세트" in name or "매" in name, "components": None, "material": self.rnd.choice(MATERIALS[cat]),
                 "origin": self.rnd.choice(ORIGINS), "has_quality_cert": cat == "ACCESSORY" and self.rnd.random() < 0.6,
                 "made_to_order": mto, "made_to_order_days": 14 if mto else None, "options": options, "size_chart": size_chart,
                 "individual_purchase_allowed": (not ("세트" in name or "매" in name)) or self.rnd.random() < 0.3,
                 "return_allowed": not mto, "return_blocked_reason": "주문 제작 상품은 교환·반품이 불가합니다." if mto else None,
                 "soldout": soldout, "soldout_note": "일시 품절" if soldout else None}
            self.insert_product(p)

    def synth_customers(self, total=120):
        while len(self.customers) < total:
            self.new_customer()

    def synth_orders(self, total=450):
        n = 1011
        pids = [p for p in self.products.values() if not p.get("soldout")]
        returns_pending = []
        while n < 1000 + total:
            n += 1
            oid = f"O-{n}"
            cust = self.rnd.choice(self.customers)
            d = TODAY - timedelta(days=self.rnd.randint(0, 89))
            ordered = dt(d, self.rnd.randint(8, 22), self.rnd.randint(0, 59))
            items = []
            for p in self.rnd.sample(pids, self.rnd.randint(1, 3)):
                opt = " / ".join(x for x in [self.rnd.choice(p["options"].get("size", [""])) if p["options"].get("size") else "",
                                             self.rnd.choice(p["options"]["color"])] if x)
                items.append({"product_id": p["product_id"], "name": p["name"], "option": opt or None,
                              "qty": self.rnd.choices([1, 2, 3], weights=[80, 15, 5])[0], "price": p["price"]})
            amount = sum(i["qty"] * i["price"] for i in items)
            cats = {self.products[i["product_id"]]["category"] for i in items}
            ths = [self.categories[c].get("free_shipping_threshold") for c in cats]
            free = all(t is not None for t in ths) and amount >= max(ths)
            fee = 0 if free else 2500
            extra = 3000 if cust["address_region"] == "제주도서산간" else 0
            age = (TODAY - d).days
            r = self.rnd.random()
            status, detail = "배송완료", "수령 완료"
            shipped = delivered = tracking = None
            invoice = True
            exp_ship = (d + timedelta(days=1 if int(ordered[11:13]) < 11 else 2)).isoformat()
            if age <= 1 or r < 0.10:
                status, detail, invoice = "결제완료", "출고 대기", False
            elif age <= 4 or r < 0.25:
                status, detail = "배송중", "간선 상차" if self.rnd.random() < 0.7 else "배송 지연"
                shipped = dt(date.fromisoformat(exp_ship), 14)
                tracking = f"{self.rnd.randint(10**11, 10**12 - 1)}"
            else:
                shipped = dt(date.fromisoformat(exp_ship), 14)
                delivered = dt(date.fromisoformat(exp_ship) + timedelta(days=1 if cust["address_region"] == "수도권" else 3), 16)
                tracking = f"{self.rnd.randint(10**11, 10**12 - 1)}"
                if r > 0.88:
                    status, detail = ("반품진행", "접수") if r < 0.96 else ("교환진행", "접수")
                    returns_pending.append((oid, status, items, amount, delivered))
            delay = 2 if detail == "배송 지연" else None
            o = {"order_id": oid, "ordered_at": ordered, "status": status, "status_detail": detail, "is_external_channel": False,
                 "items": items, "order_amount": amount, "shipping_fee": fee + extra, "free_shipping_applied": free,
                 "address_region": cust["address_region"], "courier": COURIERS[0] if shipped else None, "tracking_no": tracking,
                 "invoice_printed": invoice, "expected_ship_date": exp_ship, "shipped_at": shipped,
                 "expected_delivery": None if not shipped else (date.fromisoformat(shipped[:10]) + timedelta(days=2)).isoformat(),
                 "delivered_at": delivered, "delay_days": delay, "delay_reason": "물량 증가로 인한 간선 지연" if delay else None}
            self.insert_order(o, cust["customer_id"])
        self.synth_returns(returns_pending)

    def synth_returns(self, pending):
        n = 2005
        for oid, status, items, amount, delivered in pending:
            n += 1
            rid = f"R-{n}"
            req = date.fromisoformat(delivered[:10]) + timedelta(days=self.rnd.randint(1, 6))
            stages = ["접수", "수거대기", "수거완료", "입고완료", "검품중", "승인", "환불완료"]
            upto = self.rnd.randint(1, 6)
            hist = [{"stage": s, "date": (req + timedelta(days=i)).isoformat()} for i, s in enumerate(stages[:upto + 1])]
            stage = hist[-1]["stage"]
            inspected = stage in ("승인", "환불완료")
            fault = self.rnd.choice(["판매자", "고객"]) if inspected else None
            reason = self.rnd.choice(RETURN_REASONS)
            r = {"return_id": rid, "order_id": oid, "type": "교환" if status == "교환진행" else "반품", "return_scope": "전체",
                 "reason_stated": reason, "requested_at": req.isoformat(), "stage": stage,
                 "inspection_result": (("하자" if fault == "판매자" else "정상") if inspected else None),
                 "fault_party": fault, "shipping_fee_bearer": (("판매자" if fault == "판매자" else "고객") if inspected else None),
                 "return_fee": (0 if fault == "판매자" else 5000) if inspected else None,
                 "refund_amount": (amount if fault == "판매자" else amount - 5000) if inspected else None,
                 "expected_completion": (req + timedelta(days=7)).isoformat(), "stage_history": hist,
                 "note": None if inspected else "검품 미완료로 귀책 미확정"}
            self.insert_return(r)
            self.con.execute("update orders set return_id=? where order_id=?", (rid, oid))

    def synth_restock(self, total=8):
        sold = [p for p in self.products.values() if p.get("soldout") and p["product_id"] not in {r["product_id"] for r in self.seed["restock"]}]
        for p in sold[: total - len(self.seed["restock"])]:
            confirmed = self.rnd.random() < 0.5
            exp = (TODAY + timedelta(days=self.rnd.randint(3, 21))).isoformat() if confirmed else None
            self.con.execute("insert into restock values (?,?,?,?,?,?,?)",
                             (p["product_id"], p["name"], 1, int(confirmed), exp,
                              f"{exp[5:7].lstrip('0')}월 {exp[8:10].lstrip('0')}일 입고 예정입니다." if confirmed else "재입고 일정이 확정되지 않았습니다. 알림 신청 시 입고 즉시 연락드립니다.", 1))


def generate(domain_dir: Path, force: bool = False) -> Path:
    domain_dir = Path(domain_dir)
    db_path = domain_dir / f"{domain_dir.name}.db"
    if db_path.exists() and not force:
        return db_path
    seed = json.loads((domain_dir / "mockdb.json").read_text(encoding="utf-8"))
    tmp = db_path.with_suffix(".db.tmp")
    for p in (tmp, tmp.with_suffix(".db.tmp-wal"), tmp.with_suffix(".db.tmp-shm")):
        if p.exists():
            p.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    g = Gen(con, seed, random.Random(SEED))
    g.refs(); g.canonical(); g.synth_customers(); g.synth_products(); g.synth_orders(); g.synth_restock()
    con.commit(); con.close()
    if db_path.exists():
        db_path.unlink()
    tmp.rename(db_path)
    for p in (db_path.with_suffix(".db-wal"), db_path.with_suffix(".db-shm")):
        if p.exists():
            p.unlink()
    return db_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="modumall")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    root = Path(__file__).resolve().parents[2] / "domains" / a.domain
    print("생성:", generate(root, force=a.force))
```

주의: `synth_products`의 `existing` 계산은 정식 상품 ID의 두 번째 자리(카테고리 접두 숫자)를 쓴다. mockdb 규칙: 1=UNDERWEAR, 2=ACCESSORY, 3=APPAREL, 4=SHOES, 5=COSMETICS, 6=BAG_GOODS. 생성 상품은 P1004, P2004, P3007, P4003, P5004, P6004부터 이어진다. 정식 ID 20개는 정렬상 앞에 온다(P1001~P1003 < P1004 …)는 보장이 `test_canonical_products_preserved`의 마지막 단정과 어긋날 수 있다 — P1004는 P2001보다 앞에 정렬되므로 **그 단정은 `set(...) >= set(CANON_PRODUCTS)`로 바꾼다**(구현자는 테스트를 그렇게 수정할 것).

- [ ] **Step 5: domain.py 와 .gitignore**

`Domain`에 `db_path: Path` 필드. `load_domain` 끝에:

```python
    from server.db.generate import generate
    db_path = generate(path)          # 없으면 만들고, 있으면 그대로
```
`Domain(..., db_path=db_path)`. `.gitignore`에 `domains/*/*.db`, `domains/*/*.db-*`, `domains/*/*.db.tmp*` 추가.

- [ ] **Step 6: 통과 확인**

Run: `.venv/bin/pytest tests/test_generate.py -v` → 7 passed. `.venv/bin/pytest -q` → 전체 통과 (기존 129 + 7). `time .venv/bin/python -m server.db.generate --force` → 10초 이내.

- [ ] **Step 7: Commit**

```bash
git add server/db tests/test_generate.py tests/conftest.py server/domain.py .gitignore
git commit -m "feat: SQLite 스키마와 결정적 데이터 생성기 (상품 180·고객 120·주문 450)

Co-Authored-By: Claude <모델명> <noreply@anthropic.com>"
```

---

### Task 2: 저장소 계층 `server/repo.py`

**Files:**
- Create: `server/repo.py`
- Test: `tests/test_repo.py`

**Interfaces:**
- Produces: `Repo(db_path)` with `product(pid) -> dict|None`, `products() -> list[dict]`, `order(oid) -> dict|None`(items 포함, 기존 mock 주문 dict와 같은 키), `return_by_id(rid)`, `return_by_order(oid)`(stage_history 포함), `restock(pid)`, `categories() -> dict`, `same_day() -> dict`, `customer_by_phone(phone) -> dict|None`, `customer(cid)`, `recent_orders(cid, limit=3) -> list[dict]`(order_id, ordered_at, status, status_detail, order_amount, items_summary), `shipment_events(oid) -> list[dict]`, `log_call(call_id, customer_id, started_at)`, `finish_call(call_id, ended_at, turns: list)`. `normalize_phone("01012345678") -> "010-1234-5678"`.
- JSON 열은 파싱해서 돌려주고, 정수 bool 열은 bool 로 바꾼다. `None`은 그대로.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_repo.py`:

```python
import pytest
from server.repo import Repo, normalize_phone
from server.domain import load_domain


@pytest.fixture(scope="module")
def repo(modumall_dir_module):
    return Repo(load_domain(modumall_dir_module).db_path)


def test_product_dict_shape(repo):
    p = repo.product("P1001")
    assert p["name"] == "요일팬티 7종 세트" and p["is_set"] is True
    assert isinstance(p["components"], list) and p["options"]["size"] == ["S", "M", "L"]
    assert repo.product("P9999") is None
    assert len(repo.products()) >= 170


def test_order_with_items_and_bools(repo):
    o = repo.order("O-1006")
    assert o["return_id"] == "R-2001" and o["is_external_channel"] is False
    assert o["items"][0]["product_id"] and o["items"][0]["qty"] >= 1
    assert repo.order("O-1009")["is_external_channel"] is True
    assert repo.order("O-9999") is None


def test_return_with_history(repo):
    r = repo.return_by_order("O-1006")
    assert r["return_id"] == "R-2001" and r["inspection_result"] is None
    assert [h["stage"] for h in r["stage_history"]][:2] == ["접수", "수거대기"]
    assert repo.return_by_id("R-2001")["order_id"] == "O-1006"


def test_restock_and_refs(repo):
    assert repo.restock("P4002")["is_confirmed"] is False
    assert repo.categories()["SHOES"]["free_shipping_threshold"] == 100000
    assert repo.same_day()["수도권"]["available"] is True


def test_customer_lookup(repo):
    o = repo.order("O-1001")
    c = repo.customer(o["customer_id"])
    assert c["phone"].startswith("010-")
    assert repo.customer_by_phone(c["phone"].replace("-", ""))["customer_id"] == c["customer_id"]
    recent = repo.recent_orders(c["customer_id"], limit=3)
    assert 1 <= len(recent) <= 3 and "items_summary" in recent[0]
    assert repo.customer_by_phone("010-0000-0000") is None


def test_normalize_phone():
    assert normalize_phone("01012345678") == "010-1234-5678"
    assert normalize_phone("010 1234 5678") == "010-1234-5678"
    assert normalize_phone("+82 10-1234-5678") == "010-1234-5678"


def test_call_log_roundtrip(repo):
    repo.log_call("test-call", None, "2026-09-16T10:00:00")
    repo.finish_call("test-call", "2026-09-16T10:03:00", [{"q": "배송비", "action": "ANSWER"}])
    row = repo.call(  "test-call")
    assert row["ended_at"] == "2026-09-16T10:03:00" and row["turns"][0]["action"] == "ANSWER"
```

- [ ] **Step 2: 실패 확인** — `ModuleNotFoundError: server.repo`

- [ ] **Step 3: 구현**

```python
# -*- coding: utf-8 -*-
"""SQLite 읽기 저장소. 도구가 쓰던 dict 모양을 그대로 돌려준다 (JSON 열 파싱, 0/1 → bool)."""
import json
import re
import sqlite3
from pathlib import Path
from typing import Optional

JSON_COLS = {"components", "options", "size_chart", "individual_prices", "turns"}
BOOL_COLS = {"is_set", "has_quality_cert", "made_to_order", "individual_purchase_allowed", "return_allowed", "soldout",
             "is_external_channel", "free_shipping_applied", "invoice_printed", "exchange_available", "convert_to_refund",
             "is_soldout", "is_confirmed", "notify_available", "available", "requires_unopened"}


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("82"):
        digits = "0" + digits[2:]
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return raw.strip()


def _row(r: Optional[sqlite3.Row]) -> Optional[dict]:
    if r is None:
        return None
    d = dict(r)
    for k, v in d.items():
        if k in JSON_COLS and isinstance(v, str):
            d[k] = json.loads(v)
        elif k in BOOL_COLS and v is not None:
            d[k] = bool(v)
    return d


class Repo:
    def __init__(self, db_path: Path):
        self.con = sqlite3.connect(str(db_path), check_same_thread=False)
        self.con.row_factory = sqlite3.Row

    def _one(self, sql, *args):
        return _row(self.con.execute(sql, args).fetchone())

    def _all(self, sql, *args):
        return [_row(r) for r in self.con.execute(sql, args).fetchall()]

    def product(self, pid): return self._one("select * from products where product_id=?", pid)
    def products(self): return self._all("select * from products order by product_id")

    def order(self, oid):
        o = self._one("select * from orders where order_id=?", oid)
        if o:
            o["items"] = self._all("select product_id,name,option,qty,price from order_items where order_id=? order by id", oid)
        return o

    def _return(self, r):
        if r:
            r["stage_history"] = self._all("select stage,date from return_stage_history where return_id=? order by id", r["return_id"])
        return r

    def return_by_id(self, rid): return self._return(self._one("select * from returns where return_id=?", rid))
    def return_by_order(self, oid): return self._return(self._one("select * from returns where order_id=? order by requested_at desc limit 1", oid))
    def restock(self, pid): return self._one("select * from restock where product_id=?", pid)
    def categories(self): return {r["key"]: r for r in self._all("select * from categories")}
    def same_day(self): return {r["region"]: r for r in self._all("select * from same_day_delivery")}
    def shipment_events(self, oid): return self._all("select at,status,location from shipment_events where order_id=? order by id", oid)

    def customer(self, cid): return self._one("select * from customers where customer_id=?", cid)
    def customer_by_phone(self, phone): return self._one("select * from customers where phone=?", normalize_phone(phone))

    def recent_orders(self, cid, limit=3):
        out = []
        for o in self._all("select order_id,ordered_at,status,status_detail,order_amount from orders where customer_id=? order by ordered_at desc limit ?", cid, limit):
            items = self._all("select name,qty from order_items where order_id=? order by id", o["order_id"])
            o["items_summary"] = ", ".join(f"{i['name']}×{i['qty']}" if i["qty"] > 1 else i["name"] for i in items)
            out.append(o)
        return out

    def log_call(self, call_id, customer_id, started_at):
        self.con.execute("insert or replace into call_logs (call_id,customer_id,started_at) values (?,?,?)", (call_id, customer_id, started_at))
        self.con.commit()

    def finish_call(self, call_id, ended_at, turns):
        self.con.execute("update call_logs set ended_at=?, turns=? where call_id=?", (ended_at, json.dumps(turns, ensure_ascii=False), call_id))
        self.con.commit()

    def call(self, call_id): return self._one("select * from call_logs where call_id=?", call_id)
```

- [ ] **Step 4: 통과 확인** — `tests/test_repo.py` 7 passed, 전체 통과.

- [ ] **Step 5: Commit** — `feat: SQLite 읽기 저장소 계층`

---

### Task 3: 도구를 Repo 위로, `find_customer` 추가

**Files:**
- Modify: `server/tools.py`, `server/prompts.py`(도구 목록 문구), `tests/test_tools.py`

**Interfaces:**
- `make_tools(domain)` 반환 dict에 `find_customer` 추가(10번째). 기존 9개 시그니처·반환 키 유지.
- `find_customer(phone: Optional[str] = None, order_id: Optional[str] = None) -> dict`: `{customer_id, name, address_region, recent_orders:[...]}` 또는 `{"error": "고객을 찾을 수 없습니다"}`.

- [ ] **Step 1: 테스트 추가** (`tests/test_tools.py`)

```python
def test_tools_now_ten(tools):
    assert list(tools)[-1] == "find_customer" and len(tools) == 10


def test_find_customer_by_phone_and_order(tools, modumall_dir):
    from server.repo import Repo
    from server.domain import load_domain
    repo = Repo(load_domain(modumall_dir).db_path)
    phone = repo.customer(repo.order("O-1001")["customer_id"])["phone"]
    c = tools["find_customer"](phone=phone.replace("-", ""))
    assert c["name"] and any(o["order_id"] == "O-1001" for o in c["recent_orders"]) or c["recent_orders"]
    assert tools["find_customer"](order_id="O-1006")["customer_id"] == repo.order("O-1006")["customer_id"]
    assert "error" in tools["find_customer"](phone="010-0000-0000")
    assert "error" in tools["find_customer"]()


def test_get_order_status_includes_tracking_events(tools):
    o = tools["get_order_status"]("O-1002")
    assert "events" in o and isinstance(o["events"], list)
```

`tests/test_tools.py`의 기존 `test_all_nine_tools_exist`는 이름을 `test_first_nine_tools_unchanged`로 바꾸고 `list(tools)[:9] == TOOL_NAMES`로 완화한다.

- [ ] **Step 2: 실패 확인**

- [ ] **Step 3: 구현 요지** (`server/tools.py`)

`make_tools` 시작부를 Repo 기반으로:

```python
    from server.repo import Repo
    repo = Repo(domain.db_path)
    products = {p["product_id"]: p for p in repo.products()}   # search_product 후보용 메모리 사본
    categories = repo.categories()
    same_day = repo.same_day()
```
- `orders`, `returns`, `returns_by_order`, `restock` dict 를 없애고 각 도구 안에서 `repo.order(...)`, `repo.return_by_order(...)`, `repo.return_by_id(...)`, `repo.restock(...)`를 호출한다. `clean()` 호출은 유지(무해).
- `get_order_status`: 반환 dict에 `"events": repo.shipment_events(order_id)` 추가. 나머지 키 동일.
- `get_return_status`: `stage_history` 포함(repo가 준다).
- `get_restock_info`: `repo.restock(pid)`가 None 이면 기존 재고 있음/오류 분기.
- 새 도구:

```python
    def find_customer(phone: Optional[str] = None, order_id: Optional[str] = None) -> dict:
        """전화번호 또는 주문번호로 고객과 최근 주문 3건을 찾는다. 통화 고객이 '그 주문'처럼 말할 때 주문번호를 알아내는 용도다."""
        c = None
        if phone:
            c = repo.customer_by_phone(phone)
        elif order_id:
            o = repo.order(order_id)
            c = repo.customer(o["customer_id"]) if o and o.get("customer_id") else None
        if not c:
            return {"error": "고객을 찾을 수 없습니다", "phone": phone, "order_id": order_id}
        return {"customer_id": c["customer_id"], "name": c["name"], "address_region": c["address_region"],
                "recent_orders": repo.recent_orders(c["customer_id"], limit=3)}
```
- `prompts.py` ANSWER_RULES 3단계 앞에 0단계 추가:
  "0단계. [통화 고객] 블록이 있으면 그 고객의 최근 주문을 우선 참고한다. 고객이 '그 주문', '어제 산 거'처럼 말하고 최근 주문이 하나뿐이면 그 주문번호로 조회한다. 여럿이면 어느 주문인지 되묻는다. 전화번호를 말하면 find_customer 로 찾는다."

- [ ] **Step 4: 통과 확인** — 전체 pytest 통과. `search_product` 기존 테스트 25개가 그대로 통과해야 한다(후보가 180개로 늘어도 "원피스"→P3002 등 alias·정식 이름 결과는 같아야 한다. 생성 상품 이름이 정식 상품명과 겹치지 않도록 NAMES 풀에는 정식 이름을 넣지 않았다. 만약 "롱 원피스"가 생겨 "원피스" 검색이 모호해지면 alias 규칙이 우선하므로 P3002로 확정된다 — 확인할 것).

- [ ] **Step 5: Commit** — `feat: 도구를 SQLite 저장소 위로 옮기고 find_customer 추가`

---

### Task 4: 통화 고객 컨텍스트 (pipeline · answer · guardrail · app)

**Files:**
- Modify: `server/pipeline.py`, `server/answer.py`, `server/guardrail.py`(없음 — pipeline에서 합침), `server/app.py`, `server/domain.py`(`greeting_known` 선택 키), `domains/modumall/domain.json`
- Test: `tests/test_pipeline.py`, `tests/test_app.py`, `tests/test_answer.py`

**Interfaces:**
- `Pipeline.start_call(phone: str | None = None) -> tuple[str, str, dict | None]` → `(call_id, greeting, customer)`; `customer = {"name", "customer_id", "recent_orders"}`.
- `Pipeline.end_call(call_id)` → call_logs 저장(선택 호출; 화면이 끊기를 누르면 `POST /api/call/end`).
- `Answerer.answer(..., customer: dict | None = None)` — 시스템 프롬프트 끝에 `===== 통화 고객 =====` 블록.
- `POST /api/call/start {phone?}` → `{call_id, greeting, customer}`; `POST /api/call/end {call_id}` → `{ok: true}`; `GET /api/domain` → `sample_customers: [{name, phone}]` 5명 추가.
- `Domain.greeting_known: str` (선택, 기본 `"{name} 고객님, 안녕하세요. {shop} 고객센터입니다. 무엇을 도와드릴까요?"`).

- [ ] **Step 1: 테스트**

`tests/test_pipeline.py` — `FakeAnswerer.answer`에 `customer=None` 인자를 추가하고 `self.customers.append(customer)` 기록. 추가:

```python
def test_start_call_with_known_phone_passes_customer(domain, settings, modumall_dir):
    from server.repo import Repo
    repo = Repo(domain.db_path)
    phone = repo.customer(repo.order("O-1001")["customer_id"])["phone"]
    ans = FakeAnswerer([("네.", {"get_order_status": {}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, greeting, customer = p.start_call(phone=phone)
    assert customer["name"] in greeting and customer["recent_orders"]
    p.turn(cid, "그 주문 언제 와요?")
    assert ans.customers[0]["customer_id"] == customer["customer_id"]


def test_start_call_unknown_phone_is_guest(domain, settings):
    ans = FakeAnswerer([("어떤 상품인가요?", {}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, greeting, customer = p.start_call(phone="010-0000-0000")
    assert customer is None and greeting == domain.greeting
    p.turn(cid, "배송비요")
    assert ans.customers[0] is None


def test_customer_numbers_are_allowed_by_guardrail(domain, settings):
    # 통화 고객 블록에 있는 주문 금액을 답변에 써도 출처 불명이 아니다
    ans = FakeAnswerer([("주문 금액은 25,800원입니다.", {"get_order_status": {"order_id": "O-1001"}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    p.customers[cid] = {"customer_id": "C-0001", "name": "홍길동", "recent_orders": [{"order_id": "O-1001", "order_amount": 25800}]}
    r = p.turn(cid, "그 주문 얼마였죠")
    assert r.action == "ANSWER" and r.guardrail["ok"]


def test_end_call_writes_log(domain, settings):
    from server.repo import Repo
    ans = FakeAnswerer([("네.", {"get_product_detail": {}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "PRODUCT_INFO", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    p.turn(cid, "소재요")
    p.end_call(cid)
    row = Repo(domain.db_path).call(cid)
    assert row and len(row["turns"]) == 1 and row["turns"][0]["action"] == "ANSWER"
```

기존 테스트에서 `cid = p.start_call()`처럼 단일 반환을 가정한 곳은 `cid, _, _ = p.start_call()`로 바꾼다(여러 곳). `tests/test_app.py`의 `FakePipeline.start_call`도 튜플을 돌려주고 `end_call`을 갖게 바꾼다. 추가:

```python
def test_start_call_accepts_phone(client):
    r = client.post("/api/call/start", json={"phone": "010-1111-2222"}).json()
    assert r["call_id"] == "abc" and "customer" in r
    assert client.get("/api/domain").json()["sample_customers"] == []   # FakePipeline 는 샘플 없음


def test_end_call(client):
    assert client.post("/api/call/end", json={"call_id": "abc"}).json() == {"ok": True}
```

`tests/test_answer.py` 추가:

```python
def test_customer_block_in_system_prompt(domain):
    seen = {}
    def capture(messages):
        seen["system"] = [m for m in messages if getattr(m, "type", "") == "system"][0].content
        return AIMessage(content="네.")
    Answerer(domain, llm=RunnableLambda(capture)).answer("그 주문 언제 와요", "SHIPPING",
        customer={"name": "홍길동", "customer_id": "C-0001", "recent_orders": [{"order_id": "O-1001", "ordered_at": "2026-09-15T10:00:00", "status": "결제완료", "items_summary": "캔버스화", "order_amount": 59000}]})
    assert "===== 통화 고객 =====" in seen["system"] and "O-1001" in seen["system"] and "홍길동" in seen["system"]
```

- [ ] **Step 2: 실패 확인**

- [ ] **Step 3: 구현 요지**

`server/answer.py`:
```python
def customer_block(customer: Optional[dict]) -> str:
    if not customer:
        return ""
    lines = [f"이름: {customer['name']} (고객번호 {customer['customer_id']})"]
    for o in customer.get("recent_orders", []):
        lines.append(f"- {o['order_id']} · {o['ordered_at'][:10]} 주문 · {o.get('status')} · {o.get('items_summary', '')} · {o.get('order_amount')}원")
    return "\n===== 통화 고객 =====\n" + "\n".join(lines) + "\n"
```
`build_answer_prompt(domain, route, tool_results=None, customer=None)`가 끝에 `customer_block(customer)`를 붙인다. `answer(..., customer=None)`가 그대로 넘긴다.

`server/pipeline.py`:
- `self.repo = Repo(domain.db_path)`, `self.customers: dict[str, dict | None] = {}`, `self.turn_logs: dict[str, list] = {}`.
- `start_call(phone=None)`: `c = self.repo.customer_by_phone(phone) if phone else None`; customer dict = `{customer_id, name, recent_orders: repo.recent_orders(cid, 3)}`; greeting = `domain.greeting_known.format(name=..., shop=domain.name)` + (최근 14일 내 배송완료가 아닌 첫 주문이 있으면 f" {ordered_at[5:7]}월 {[8:10]}일 주문하신 {items_summary} 건이신가요?"); `repo.log_call(cid, customer_id, now)`. 반환 `(cid, greeting, customer)`.
- `_node_answer`: `customer=self.customers.get(self._current_call)` 전달.
- `_node_guard`: `results = dict(state["results"]); if customer: results["_customer"] = customer` 로 검사(원본 state 는 그대로).
- `turn()`: 결과를 `self.turn_logs[call_id].append({"q": text, "route":..., "action":..., "tools": [...names], "guardrail_ok": ...})`.
- `end_call(call_id)`: `repo.finish_call(call_id, now, self.turn_logs.pop(call_id, []))`.

`server/domain.py`: `greeting_known: str` 필드, `cfg.get("greeting_known")` 기본값 위 문자열. `domains/modumall/domain.json`에 `"greeting_known": "{name} 고객님, 안녕하세요. 모두몰 고객센터입니다. 무엇을 도와드릴까요?"` 추가.

`server/app.py`: `StartRequest(phone: Optional[str] = None)`; `/api/call/start` body 선택; `/api/call/end`; `/api/domain`에 `sample_customers`: `pipeline.sample_customers()`가 있으면 호출(Pipeline에 `sample_customers(n=5)` — 정식 주문 O-1001~O-1005의 고객 이름·전화).

- [ ] **Step 4: 통과 확인** — 전체 통과.

- [ ] **Step 5: Commit** — `feat: 발신번호로 고객을 식별해 최근 주문을 답변 컨텍스트로 전달`

---

### Task 5: 화면 · 매뉴얼 · 평가 스크립트 · README

**Files:**
- Modify: `web/index.html`, `web/call.js`, `web/panel.js`, `web/style.css`, `domains/modumall/policy.md`, `README.md`
- Create: `eval/eval_context.py`

- [ ] **Step 1: 화면**

`index.html` header 아래(컨트롤 위)에:
```html
<div class="caller">
  <label>발신 번호 <input id="phone-input" type="tel" placeholder="비워 두면 비회원" autocomplete="off"></label>
  <select id="sample-select"><option value="">샘플 고객 선택…</option></select>
</div>
```
`call.js`: 초기화에서 `d.sample_customers`로 `sample-select` 채우고 선택 시 `phone-input`에 번호 입력. `startCall()`은 `fetch("/api/call/start", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({phone: $("phone-input").value || null})})`; 응답의 `customer`가 있으면 `panel.setCustomer(customer)`(이름·최근 주문 카드), 없으면 `panel.setCustomer(null)`. `endCall()`에서 `fetch("/api/call/end", ...)`를 호출(응답 무시). `panel.js`에 `setCustomer(c)`: 패널 맨 위 고정 카드 `#customer-card`. `style.css`에 `.caller` 한 줄 레이아웃과 `.customer-card`.

`node --check` 세 파일. `tests/test_app.py::test_index_served` 통과.

- [ ] **Step 2: 매뉴얼** — `policy.md` §8 끝에 문단 추가:
"발신번호로 고객이 확인되면 이름으로 인사하고 최근 주문을 먼저 짚는다(예: "어제 주문하신 캔버스화 건이신가요?"). 확인되지 않으면 주문번호를 묻는다. 통화 고객이 아닌 다른 사람의 주문·개인정보는 안내하지 않는다. 전화번호만으로는 본인 확인이 완전하지 않으므로 환불 계좌 변경 같은 민감 처리는 이관한다."

- [ ] **Step 3: eval_context.py**

```python
# -*- coding: utf-8 -*-
"""⑤ 고객 컨텍스트 효과 측정. 실제 문의 N건을 (a) 비회원 (b) 식별된 고객 두 조건으로 흘려보내 자동화율을 비교한다.

.venv/bin/python -m eval.eval_context [--n 40] [--domain modumall]
문의마다 새 통화. (b) 는 정식 주문 고객 8명 중 하나를 무작위(seed 고정)로 발신자로 붙인다.
"""
import argparse
import random

import pandas as pd

from server.config import load_settings
from server.domain import load_domain
from server.pipeline import Pipeline
from server.repo import Repo


def run(pipeline, questions, phones):
    rows = []
    for q, ph in zip(questions, phones):
        cid, _, cust = pipeline.start_call(phone=ph)
        r = pipeline.turn(cid, q)
        pipeline.end_call(cid)
        rows.append({"q": q, "customer": bool(cust), "action": r.action, "tools": len(r.tools) > 0})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None)
    ap.add_argument("--n", type=int, default=40)
    a = ap.parse_args()
    s = load_settings()
    domain = load_domain(s.domains_root / (a.domain or s.domain))
    inq = pd.read_csv(domain.path / "eval" / "customer_inquiries.csv", encoding="utf-8-sig")
    rnd = random.Random(7)
    qs = rnd.sample(inq["question"].tolist(), a.n)
    repo = Repo(domain.db_path)
    phones = [repo.customer(repo.order(f"O-10{i:02d}")["customer_id"])["phone"] for i in range(1, 9)]
    pipeline = Pipeline(domain, s)
    a_df = run(pipeline, qs, [None] * a.n)
    b_df = run(pipeline, qs, [rnd.choice(phones) for _ in qs])
    for label, df in (("A. 비회원", a_df), ("B. 식별된 고객", b_df)):
        print(f"\n{label}  n={len(df)}")
        print(df["action"].value_counts().to_string())
        answered = df[df["action"] == "ANSWER"]
        print(f"자동화율(답변 도달) {len(answered) / len(df):.3f}  조회율(답변 중 도구 호출) {answered['tools'].mean() if len(answered) else 0:.3f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: README** — 시작하기에 "데이터는 첫 실행 시 자동 생성(`domains/modumall/modumall.db`). 다시 만들려면 `python -m server.db.generate --force`" 추가. 평가 명령에 `eval_context` 추가. 구조 표에 `server/db/`, `server/repo.py`. 한계에서 "체크포인터 메모리" 유지, "전화번호만으로 식별(본인 인증 없음)" 추가.

- [ ] **Step 5: 검증** — `.venv/bin/pytest -q` 전체 통과; `node --check web/*.js`; `.venv/bin/python -m eval.eval_answer --limit 0` 대신 `.venv/bin/python -c "from eval.scoring import load_first_turns, self_check; from pathlib import Path; c=load_first_turns(Path('domains/modumall/eval/answer_goldenset.json')); print(self_check(c))"` → `[]`.

- [ ] **Step 6: Commit** — `feat: 통화 화면 발신번호·고객 카드, 매뉴얼 고객 식별 규정, eval_context`

---

## 자체 검토

- 스펙 3(스키마)→T1, 4(생성기)→T1, 5(Repo)→T2, 6.1(find_customer)→T3, 6.2(통화 시작·프롬프트·가드레일)→T4, 6.3(화면)→T5, 7(매뉴얼·프롬프트)→T3·T5, 8(eval_context)→T5, 9(테스트)→각 태스크, 11(성공 기준)→T1 Step 6, T4 테스트, T5 Step 5.
- 타입: `start_call` 반환이 튜플로 바뀌므로 T4에서 기존 호출처(tests, app)를 모두 갱신한다고 명시. `Answerer.answer(customer=)`·`FakeAnswerer` 시그니처 일치. `Repo.recent_orders` 키(order_id, ordered_at, status, status_detail, order_amount, items_summary)를 T4 customer_block 과 T5 화면이 그대로 쓴다.
- 위험: 생성 상품명이 정식 상품명 검색을 흔들 수 있음(T3 Step 4에 확인 항목). 생성기 실행 시간(T1 Step 6에 10초 기준).
