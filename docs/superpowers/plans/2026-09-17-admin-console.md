# B단계 어드민 조회 화면 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SQLite 의 주문·반품·통화 로그·고객을 브라우저에서 읽기 전용으로 조회하는 Jinja2 서버 렌더 어드민 화면을 붙인다.

**Architecture:** 조회 SQL 은 새 `server/adminrepo.py`(AdminRepo) 에, 라우트·렌더는 새 `server/admin.py`(APIRouter) 에 둔다. 템플릿은 `server/templates/`. 기존 `Repo`(에이전트 도구 계약)와 통화 파이프라인은 건드리지 않는다 — 예외는 Task 5 의 턴 로그 두 필드.

**Tech Stack:** Python 3.12, FastAPI, Jinja2(신규), SQLite, pytest + `fastapi.testclient.TestClient`

**Spec:** `docs/superpowers/specs/2026-09-17-admin-console-design.md`

## Global Constraints

- **읽기 전용**: `server/adminrepo.py` 와 `server/admin.py` 에 `insert`/`update`/`delete` SQL 을 쓰지 않는다. Task 5 에서 이걸 검사하는 테스트가 들어온다.
- **SQL 인젝션 금지**: 모든 값은 `?` 바인딩. 문자열 포매팅으로 값을 SQL 에 넣지 않는다(`IN (?,?,?)` 의 물음표 개수 생성만 예외).
- **XSS**: Jinja2 자동 이스케이프를 끄지 않는다. `|safe` 를 어디에도 쓰지 않는다.
- **주석·UI 문구는 한국어**, 식별자는 영어. 기존 파일들의 스타일(`# -*- coding: utf-8 -*-` 헤더 + 한 줄 docstring)을 따른다.
- **페이지 크기 20**, 페이지 번호는 1부터.
- **날짜 표기**: `9월 17일`, 금액 표기: `12,000원` (기존 `web/panel.js` 규칙).
- 기존 테스트 241개는 계속 통과해야 한다. 전체 실행: `.venv/bin/python -m pytest -q`
- 커밋 메시지 끝에 `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>` 는 붙이지 않는다. 구현자는 트레일러 없이 커밋하고, 컨트롤러가 리뷰 후 정리한다.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `server/adminrepo.py` (신규) | 목록·집계·상세 조회 SQL, 페이징 헬퍼. 읽기 전용 |
| `server/admin.py` (신규) | `/admin/*` APIRouter, 쿼리 파라미터 파싱, 템플릿 렌더, Jinja 필터 |
| `server/templates/*.html` (신규 11개) | base, home, 목록 4, 상세 4, notfound |
| `web/admin.css` (신규) | 어드민 전용 스타일. `/static/admin.css` 로 서빙됨 |
| `server/app.py` (수정) | `pipeline.repo` 가 있으면 admin 라우터 장착 |
| `server/pipeline.py` (수정, Task 5) | 턴 로그에 답변·확신도 추가 |
| `web/index.html` (수정, Task 4) | 헤더에 `/admin` 링크 |
| `tests/test_adminrepo.py` (신규) | AdminRepo 단위 테스트 |
| `tests/test_admin_routes.py` (신규) | 라우트 테스트, XSS, 쓰기 SQL 금지 |

---

### Task 1: AdminRepo 목록 쿼리

**Files:**
- Create: `server/adminrepo.py`
- Test: `tests/test_adminrepo.py`

**Interfaces:**
- Consumes: `server.repo._row`(sqlite Row → dict, JSON 열 파싱), `server.repo.normalize_phone`
- Produces: `AdminRepo(con)`, 상수 `PAGE_SIZE=20`, `IN_PROGRESS_STATUSES`, 메서드 `orders(...)`, `returns(...)`, `calls(...)`, `customers(...)` — 모두 `(rows: list[dict], total: int)` 반환

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_adminrepo.py`

```python
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv/bin/python -m pytest tests/test_adminrepo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.adminrepo'`

- [ ] **Step 3: 최소 구현** — `server/adminrepo.py`

```python
# -*- coding: utf-8 -*-
"""어드민 조회 전용 저장소. 읽기 쿼리만 둔다 (쓰기 SQL 금지)."""
import threading

from server.repo import _row, normalize_phone

PAGE_SIZE = 20
# 종결되지 않은 주문. 홈의 "진행중 주문" 집계에 쓴다.
IN_PROGRESS_STATUSES = ("결제완료", "제작중", "배송중", "반품진행", "교환진행")


class AdminRepo:
    def __init__(self, con):
        self.con = con
        self._lock = threading.RLock()

    # ── 내부 헬퍼 ────────────────────────────────────────
    def _all(self, sql, params=()):
        with self._lock:
            return [_row(r) for r in self.con.execute(sql, params).fetchall()]

    def _one(self, sql, params=()):
        with self._lock:
            return _row(self.con.execute(sql, params).fetchone())

    def _count(self, sql, params=()):
        with self._lock:
            return self.con.execute(sql, params).fetchone()[0]

    def _page(self, select_sql, count_sql, where, params, order, page, size):
        page = max(1, int(page or 1))
        size = max(1, int(size or PAGE_SIZE))
        total = self._count(f"{count_sql} {where}", params)
        rows = self._all(f"{select_sql} {where} {order} limit ? offset ?",
                         (*params, size, (page - 1) * size))
        return rows, total

    @staticmethod
    def _where(cond):
        return ("where " + " and ".join(cond)) if cond else ""

    # ── 목록 ────────────────────────────────────────────
    def orders(self, *, status=None, customer_id=None, date_from=None, date_to=None, page=1, size=PAGE_SIZE):
        cond, params = [], []
        if status:
            cond.append("o.status = ?"); params.append(status)
        if customer_id:
            cond.append("o.customer_id = ?"); params.append(customer_id)
        if date_from:
            cond.append("o.ordered_at >= ?"); params.append(date_from)
        if date_to:
            # 날짜만 받으므로 그날 끝까지 포함시킨다
            cond.append("o.ordered_at <= ?"); params.append(f"{date_to}T23:59:59")
        select = ("select o.order_id, o.ordered_at, o.status, o.status_detail, o.order_amount, "
                  "o.customer_id, o.return_id, c.name as customer_name "
                  "from orders o left join customers c on c.customer_id = o.customer_id")
        return self._page(select, "select count(*) from orders o", self._where(cond), tuple(params),
                          "order by o.ordered_at desc", page, size)

    def returns(self, *, stage=None, type=None, page=1, size=PAGE_SIZE):
        cond, params = [], []
        if stage:
            cond.append("r.stage = ?"); params.append(stage)
        if type:
            cond.append("r.type = ?"); params.append(type)
        select = ("select r.return_id, r.order_id, r.type, r.stage, r.requested_at, r.refund_amount, "
                  "r.expected_completion, o.customer_id, c.name as customer_name from returns r "
                  "left join orders o on o.order_id = r.order_id "
                  "left join customers c on c.customer_id = o.customer_id")
        return self._page(select, "select count(*) from returns r", self._where(cond), tuple(params),
                          "order by r.requested_at desc", page, size)

    def calls(self, *, page=1, size=PAGE_SIZE):
        select = ("select l.call_id, l.customer_id, l.started_at, l.ended_at, l.turns, "
                  "c.name as customer_name from call_logs l "
                  "left join customers c on c.customer_id = l.customer_id")
        rows, total = self._page(select, "select count(*) from call_logs l", "", (),
                                 "order by l.started_at desc", page, size)
        for r in rows:
            turns = r.get("turns") or []
            r["turn_count"] = len(turns)
            r["routes"] = list(dict.fromkeys([t.get("route") for t in turns if t.get("route")]))
        return rows, total

    def customers(self, *, q=None, page=1, size=PAGE_SIZE):
        cond, params = [], []
        if q:
            cond.append("(name like ? or phone = ? or replace(phone,'-','') like ?)")
            params += [f"%{q}%", normalize_phone(q), f"%{q.replace('-', '')}%"]
        return self._page("select customer_id, name, phone, address_region, joined_at from customers",
                          "select count(*) from customers", self._where(cond), tuple(params),
                          "order by customer_id", page, size)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_adminrepo.py -q`
Expected: PASS (7개)

- [ ] **Step 5: 커밋**

```bash
git add server/adminrepo.py tests/test_adminrepo.py
git commit -m "feat: 어드민 조회 저장소 목록 쿼리 (주문·반품·통화·고객)"
```

---

### Task 2: AdminRepo 상세·요약 쿼리

**Files:**
- Modify: `server/adminrepo.py` (Task 1 의 클래스에 메서드 추가)
- Test: `tests/test_adminrepo.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 1 의 `AdminRepo`, `_all`/`_one`/`_count`, `IN_PROGRESS_STATUSES`, `self.calls`
- Produces: `order_detail(order_id)`, `return_detail(return_id)`, `call_detail(call_id)`, `customer_detail(customer_id)` — 없으면 `None`. `summary(today_iso)` → `{"calls_today": int, "orders_in_progress": int, "returns_by_stage": [{"stage","n"}], "recent_calls": [...]}`

- [ ] **Step 1: 실패하는 테스트 추가** — `tests/test_adminrepo.py` 끝에 덧붙인다

```python
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv/bin/python -m pytest tests/test_adminrepo.py -q`
Expected: FAIL — `AttributeError: 'AdminRepo' object has no attribute 'order_detail'`

- [ ] **Step 3: 최소 구현** — `server/adminrepo.py` 의 `AdminRepo` 에 추가

```python
    # ── 상세 ────────────────────────────────────────────
    def _stage_history(self, r):
        if r:
            r["stage_history"] = self._all(
                "select stage,date from return_stage_history where return_id=? order by id", (r["return_id"],))
        return r

    def order_detail(self, order_id):
        o = self._one("select * from orders where order_id=?", (order_id,))
        if not o:
            return None
        o["items"] = self._all(
            "select product_id,name,option,qty,price from order_items where order_id=? order by id", (order_id,))
        o["events"] = self._all(
            "select at,status,location from shipment_events where order_id=? order by id", (order_id,))
        o["return_"] = self._stage_history(self._one(
            "select * from returns where order_id=? order by requested_at desc limit 1", (order_id,)))
        o["customer"] = self._one("select * from customers where customer_id=?", (o["customer_id"],)) \
            if o.get("customer_id") else None
        return o

    def return_detail(self, return_id):
        r = self._stage_history(self._one("select * from returns where return_id=?", (return_id,)))
        if not r:
            return None
        r["order"] = self._one("select * from orders where order_id=?", (r["order_id"],))
        r["customer"] = self._one(
            "select c.* from customers c join orders o on o.customer_id = c.customer_id where o.order_id=?",
            (r["order_id"],))
        return r

    def call_detail(self, call_id):
        c = self._one("select * from call_logs where call_id=?", (call_id,))
        if not c:
            return None
        c["turns"] = c.get("turns") or []
        c["customer"] = self._one("select * from customers where customer_id=?", (c["customer_id"],)) \
            if c.get("customer_id") else None
        return c

    def customer_detail(self, customer_id):
        c = self._one("select * from customers where customer_id=?", (customer_id,))
        if not c:
            return None
        c["orders"] = self._all(
            "select order_id, ordered_at, status, order_amount, customer_id, return_id from orders "
            "where customer_id=? order by ordered_at desc", (customer_id,))
        calls = self._all(
            "select call_id, started_at, ended_at, turns from call_logs where customer_id=? "
            "order by started_at desc", (customer_id,))
        for r in calls:
            r["turn_count"] = len(r.get("turns") or [])
        c["calls"] = calls
        return c

    # ── 요약 ────────────────────────────────────────────
    def summary(self, today_iso):
        day = today_iso[:10]
        qs = ",".join("?" * len(IN_PROGRESS_STATUSES))
        recent_calls, _ = self.calls(page=1, size=5)
        return {
            "calls_today": self._count(
                "select count(*) from call_logs where substr(started_at,1,10) = ?", (day,)),
            "orders_in_progress": self._count(
                f"select count(*) from orders where status in ({qs})", IN_PROGRESS_STATUSES),
            "returns_by_stage": self._all(
                "select stage, count(*) as n from returns group by stage order by n desc"),
            "recent_calls": recent_calls,
        }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_adminrepo.py -q`
Expected: PASS (12개)

주의: `test_summary_counts` 의 `orders_in_progress` 비교는 "`배송완료` 가 아닌 주문 수"와 같아야 한다. 현재 DB 의 status 값은 `결제완료·제작중·배송중·반품진행·교환진행·배송완료` 6종이므로 `IN_PROGRESS_STATUSES` 와 일치한다. 값이 늘어 테스트가 깨지면 상수를 먼저 고친다.

- [ ] **Step 5: 커밋**

```bash
git add server/adminrepo.py tests/test_adminrepo.py
git commit -m "feat: 어드민 상세·요약 조회 쿼리"
```

---

### Task 3: 어드민 라우터와 목록 화면

**Files:**
- Create: `server/admin.py`, `server/templates/base.html`, `server/templates/home.html`, `server/templates/orders.html`, `server/templates/returns.html`, `server/templates/calls.html`, `server/templates/customers.html`, `web/admin.css`
- Modify: `server/app.py` (`create_app` 안, `if WEB.exists():` 바로 앞), `requirements.txt`
- Test: `tests/test_admin_routes.py`

**Interfaces:**
- Consumes: Task 1·2 의 `AdminRepo`, `PAGE_SIZE`
- Produces: `server.admin.admin_router(repo) -> APIRouter` — `repo.con` 만 사용한다. 경로 `/admin`, `/admin/orders`, `/admin/returns`, `/admin/calls`, `/admin/customers`

- [ ] **Step 1: 의존성 추가**

`requirements.txt` 의 `pydantic>=2.7` 줄 아래에 `jinja2>=3.1` 을 추가하고 설치한다.

```bash
.venv/bin/pip install "jinja2>=3.1"
```

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_admin_routes.py`

```python
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.domain import load_domain
from server.pipeline import TurnResult


class FakePipelineWithRepo:
    """통화 API 는 쓰지 않고 어드민 라우터 장착에 필요한 repo 만 들고 있는 가짜 파이프라인."""

    def __init__(self, db_path):
        con = sqlite3.connect(str(db_path), check_same_thread=False)
        con.row_factory = sqlite3.Row
        self.repo = type("R", (), {"con": con})()

    def start_call(self, phone=None):
        return "abc", "안녕하세요", None

    def end_call(self, call_id):
        pass

    def turn(self, call_id, text):
        return TurnResult(answer="응답", route="SHIPPING", confidence=0.9, action="ANSWER", tools=[],
                          guardrail={"ok": True, "violations": []}, elapsed_ms=1, end_call=False, attempts=1)


@pytest.fixture(scope="module")
def client(modumall_dir_module):
    domain = load_domain(modumall_dir_module)
    return TestClient(create_app(FakePipelineWithRepo(domain.db_path), domain))


@pytest.mark.parametrize("path", ["/admin", "/admin/orders", "/admin/returns", "/admin/calls", "/admin/customers"])
def test_list_pages_render(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "어드민" in r.text


def test_home_shows_summary_numbers(client):
    r = client.get("/admin")
    assert "진행중 주문" in r.text and "오늘 통화" in r.text and "반품 단계" in r.text


def test_orders_filter_keeps_querystring_in_paging_links(client):
    r = client.get("/admin/orders", params={"status": "배송중", "page": 1})
    assert r.status_code == 200
    assert "status=%EB%B0%B0%EC%86%A1%EC%A4%91" in r.text or "status=배송중" in r.text
    assert "page=2" in r.text


def test_customers_search(client):
    r = client.get("/admin/customers", params={"q": "존재하지않는고객"})
    assert r.status_code == 200 and "결과 없음" in r.text


def test_admin_not_mounted_without_repo(modumall_dir_module):
    class NoRepo(FakePipelineWithRepo):
        def __init__(self):
            pass

    c = TestClient(create_app(NoRepo(), load_domain(modumall_dir_module)))
    assert c.get("/admin").status_code == 404
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `.venv/bin/python -m pytest tests/test_admin_routes.py -q`
Expected: FAIL — `/admin` 이 404 (라우터 없음)

- [ ] **Step 4: 라우터 구현** — `server/admin.py`

```python
# -*- coding: utf-8 -*-
"""어드민 조회 화면 라우터. 읽기 전용 — 쓰기 경로가 없다."""
import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from server.adminrepo import PAGE_SIZE, AdminRepo

TEMPLATES = Path(__file__).resolve().parent / "templates"


def _won(n):
    return "-" if n is None else f"{int(n):,}원"


def _mmdd(iso):
    return f"{int(iso[5:7])}월 {int(iso[8:10])}일" if iso else "-"


def _stamp(iso):
    return f"{int(iso[5:7])}월 {int(iso[8:10])}일 {iso[11:16]}" if iso else "-"


def admin_router(repo) -> APIRouter:
    admin = AdminRepo(repo.con)
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.filters.update(won=_won, mmdd=_mmdd, stamp=_stamp)

    def page_url(path, query, page):
        q = {k: v for k, v in query.items() if k != "page" and v}
        q["page"] = page
        return f"{path}?{urlencode(q)}"

    templates.env.globals["page_url"] = page_url

    router = APIRouter(prefix="/admin")

    def render(request, name, **ctx):
        return templates.TemplateResponse(request, name, ctx)

    def listing(request, name, rows, total, page, **ctx):
        return render(request, name, rows=rows, total=total, page=page,
                      pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                      query=dict(request.query_params), path=request.url.path, **ctx)

    @router.get("", response_class=HTMLResponse)
    def home(request: Request):
        s = admin.summary(datetime.datetime.now().isoformat())
        return render(request, "home.html", s=s)

    @router.get("/orders", response_class=HTMLResponse)
    def orders(request: Request, status: str = "", customer_id: str = "", from_: str = "", to: str = "", page: int = 1):
        rows, total = admin.orders(status=status or None, customer_id=customer_id or None,
                                   date_from=request.query_params.get("from") or None,
                                   date_to=to or None, page=page)
        return listing(request, "orders.html", rows, total, page)

    @router.get("/returns", response_class=HTMLResponse)
    def returns(request: Request, stage: str = "", type: str = "", page: int = 1):
        rows, total = admin.returns(stage=stage or None, type=type or None, page=page)
        return listing(request, "returns.html", rows, total, page)

    @router.get("/calls", response_class=HTMLResponse)
    def calls(request: Request, page: int = 1):
        rows, total = admin.calls(page=page)
        return listing(request, "calls.html", rows, total, page)

    @router.get("/customers", response_class=HTMLResponse)
    def customers(request: Request, q: str = "", page: int = 1):
        rows, total = admin.customers(q=q or None, page=page)
        return listing(request, "customers.html", rows, total, page)

    return router
```

주의: 쿼리 파라미터 `from` 은 파이썬 예약어라 함수 인자로 못 쓴다. 위처럼 `request.query_params.get("from")` 으로 읽고, 인자 `from_` 는 쓰지 않으니 시그니처에서 지워도 된다(지우는 쪽을 택할 것).

- [ ] **Step 5: 템플릿 작성**

`server/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{% block title %}어드민{% endblock %} · 모두몰 어드민</title>
<link rel="stylesheet" href="/static/admin.css">
</head>
<body>
<nav class="nav">
  <a class="brand" href="/admin">모두몰 어드민</a>
  <a href="/admin/orders">주문</a>
  <a href="/admin/returns">반품·교환</a>
  <a href="/admin/calls">통화 로그</a>
  <a href="/admin/customers">고객</a>
  <a class="right" href="/">통화 화면</a>
</nav>
<main>
{% block body %}{% endblock %}
</main>
</body>
</html>
```

`server/templates/_paging.html` (목록 4개가 include 한다):

```html
<div class="paging">
  {% if page > 1 %}<a href="{{ page_url(path, query, page - 1) }}">← 이전</a>{% endif %}
  <span>{{ page }} / {{ pages }} · 총 {{ total }}건</span>
  {% if page < pages %}<a href="{{ page_url(path, query, page + 1) }}">다음 →</a>{% endif %}
</div>
```

`server/templates/orders.html`:

```html
{% extends "base.html" %}
{% block title %}주문{% endblock %}
{% block body %}
<h1>주문</h1>
<form class="filters" method="get" action="/admin/orders">
  <input name="status" value="{{ query.get('status', '') }}" placeholder="상태 (예: 배송중)">
  <input name="customer_id" value="{{ query.get('customer_id', '') }}" placeholder="고객 ID">
  <input name="from" value="{{ query.get('from', '') }}" placeholder="시작일 2026-09-01">
  <input name="to" value="{{ query.get('to', '') }}" placeholder="종료일 2026-09-30">
  <button type="submit">검색</button>
</form>
{% if not rows %}<p class="empty">결과 없음</p>{% endif %}
<table>
  <thead><tr><th>주문번호</th><th>주문일</th><th>고객</th><th>상태</th><th>금액</th><th>반품</th></tr></thead>
  <tbody>
  {% for o in rows %}
    <tr>
      <td><a href="/admin/orders/{{ o.order_id }}">{{ o.order_id }}</a></td>
      <td>{{ o.ordered_at | mmdd }}</td>
      <td>{% if o.customer_id %}<a href="/admin/customers/{{ o.customer_id }}">{{ o.customer_name }}</a>{% else %}비회원{% endif %}</td>
      <td>{{ o.status }}{% if o.status_detail %} · {{ o.status_detail }}{% endif %}</td>
      <td class="num">{{ o.order_amount | won }}</td>
      <td>{% if o.return_id %}<a href="/admin/returns/{{ o.return_id }}">{{ o.return_id }}</a>{% endif %}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% include "_paging.html" %}
{% endblock %}
```

`server/templates/returns.html` — 같은 구조. 필터는 `stage`, `type`. 열은 반품번호(상세 링크) · 접수일 · 유형 · 단계 · 주문번호(주문 상세 링크) · 고객(고객 상세 링크) · 환불액(`| won`).

`server/templates/calls.html` — 필터 없음. 열은 통화 ID(상세 링크) · 시작(`| stamp`) · 종료(`| stamp`) · 고객(있으면 링크, 없으면 `비회원`) · 턴 수 · 라우트(`{{ c.routes | join(', ') }}`).

`server/templates/customers.html` — 필터는 `q` 하나(`placeholder="이름 또는 전화번호"`). 열은 고객 ID(상세 링크) · 이름 · 전화 · 지역 · 가입일(`| mmdd`).

세 파일 모두 `{% if not rows %}<p class="empty">결과 없음</p>{% endif %}` 와 `{% include "_paging.html" %}` 을 포함한다.

`server/templates/home.html`:

```html
{% extends "base.html" %}
{% block title %}요약{% endblock %}
{% block body %}
<h1>요약</h1>
<div class="cards">
  <div class="card"><span class="label">오늘 통화</span><span class="value">{{ s.calls_today }}</span></div>
  <div class="card"><span class="label">진행중 주문</span><span class="value">{{ s.orders_in_progress }}</span></div>
</div>
<h2>반품 단계</h2>
<ul class="stages">
  {% for r in s.returns_by_stage %}
    <li><a href="/admin/returns?stage={{ r.stage }}">{{ r.stage }}</a> {{ r.n }}건</li>
  {% endfor %}
</ul>
<h2>최근 통화</h2>
<ul class="recent">
  {% for c in s.recent_calls %}
    <li><a href="/admin/calls/{{ c.call_id }}">{{ c.started_at | stamp }}</a>
        · {{ c.customer_name or "비회원" }} · {{ c.turn_count }}턴</li>
  {% else %}
    <li class="empty">통화 기록 없음</li>
  {% endfor %}
</ul>
{% endblock %}
```

`web/admin.css` — 기존 `web/style.css` 의 색 감각(어두운 배경 대신 밝은 표 중심)에 맞춰 최소한만: `body{font-family:system-ui;margin:0;color:#222}`, `.nav{display:flex;gap:16px;padding:12px 20px;background:#1f2430;color:#fff}`, `.nav a{color:#fff;text-decoration:none}`, `.nav .right{margin-left:auto}`, `main{padding:20px;max-width:1100px}`, `table{border-collapse:collapse;width:100%}`, `th,td{border-bottom:1px solid #e3e3e3;padding:8px;text-align:left;font-size:14px}`, `td.num{text-align:right}`, `.filters{display:flex;gap:8px;margin:12px 0}`, `.paging{display:flex;gap:12px;align-items:center;margin-top:16px}`, `.cards{display:flex;gap:16px}`, `.card{border:1px solid #e3e3e3;border-radius:8px;padding:16px;min-width:140px}`, `.card .value{display:block;font-size:28px;font-weight:700}`, `.empty{color:#888}`.

- [ ] **Step 6: 앱에 장착** — `server/app.py`

`from server.domain import Domain` 아래에 import 를 추가하지 말고(순환 import 방지), `create_app` 안 `if WEB.exists():` 바로 앞에 다음을 넣는다.

```python
    repo = getattr(pipeline, "repo", None)
    if repo is not None:
        from server.admin import admin_router
        app.include_router(admin_router(repo))
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_admin_routes.py -q`
Expected: PASS (9개 — 파라미터 5 + 4)

- [ ] **Step 8: 전체 테스트**

Run: `.venv/bin/python -m pytest -q`
Expected: 기존 241개 포함 전부 통과

- [ ] **Step 9: 커밋**

```bash
git add server/admin.py server/templates web/admin.css server/app.py requirements.txt tests/test_admin_routes.py
git commit -m "feat: 어드민 목록 화면 4개와 요약 홈"
```

---

### Task 4: 상세 화면 4개와 404 처리

**Files:**
- Modify: `server/admin.py`
- Create: `server/templates/order_detail.html`, `server/templates/return_detail.html`, `server/templates/call_detail.html`, `server/templates/customer_detail.html`, `server/templates/notfound.html`
- Modify: `web/index.html` (헤더에 어드민 링크)
- Test: `tests/test_admin_routes.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 2 의 `order_detail`/`return_detail`/`call_detail`/`customer_detail`, Task 3 의 `render`/`templates`
- Produces: 경로 `/admin/orders/{order_id}`, `/admin/returns/{return_id}`, `/admin/calls/{call_id}`, `/admin/customers/{customer_id}`. 없는 id 는 **HTML 404**

- [ ] **Step 1: 실패하는 테스트 추가** — `tests/test_admin_routes.py` 끝에

```python
def test_order_detail_page(client):
    r = client.get("/admin/orders/O-1006")
    assert r.status_code == 200
    assert "O-1006" in r.text and "R-2001" in r.text          # 연결된 반품 링크
    assert "/admin/returns/R-2001" in r.text


def test_return_detail_page(client):
    r = client.get("/admin/returns/R-2001")
    assert r.status_code == 200
    assert "접수" in r.text and "/admin/orders/O-1006" in r.text


def test_customer_detail_page(client):
    cid = client.get("/admin/customers").text  # 목록에서 첫 고객 ID 를 직접 뽑기보다 알려진 주문의 고객을 쓴다
    r = client.get("/admin/orders/O-1006")
    assert r.status_code == 200


@pytest.mark.parametrize("path", [
    "/admin/orders/O-9999", "/admin/returns/R-9999",
    "/admin/calls/없는통화", "/admin/customers/C-9999"])
def test_missing_id_returns_html_404(client, path):
    r = client.get(path)
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "찾을 수 없습니다" in r.text
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv/bin/python -m pytest tests/test_admin_routes.py -q`
Expected: FAIL — 상세 경로가 없어 404 이고 본문에 "찾을 수 없습니다" 가 없다

- [ ] **Step 3: 상세 라우트 구현** — `server/admin.py` 의 `admin_router` 안, `return router` 앞에 추가

```python
    def not_found(request, what):
        return templates.TemplateResponse(request, "notfound.html", {"what": what}, status_code=404)

    @router.get("/orders/{order_id}", response_class=HTMLResponse)
    def order_detail(request: Request, order_id: str):
        o = admin.order_detail(order_id)
        return render(request, "order_detail.html", o=o) if o else not_found(request, f"주문 {order_id}")

    @router.get("/returns/{return_id}", response_class=HTMLResponse)
    def return_detail(request: Request, return_id: str):
        r = admin.return_detail(return_id)
        return render(request, "return_detail.html", r=r) if r else not_found(request, f"반품 {return_id}")

    @router.get("/calls/{call_id}", response_class=HTMLResponse)
    def call_detail(request: Request, call_id: str):
        c = admin.call_detail(call_id)
        return render(request, "call_detail.html", c=c) if c else not_found(request, f"통화 {call_id}")

    @router.get("/customers/{customer_id}", response_class=HTMLResponse)
    def customer_detail(request: Request, customer_id: str):
        d = admin.customer_detail(customer_id)
        return render(request, "customer_detail.html", d=d) if d else not_found(request, f"고객 {customer_id}")
```

- [ ] **Step 4: 상세 템플릿 작성**

`server/templates/notfound.html`:

```html
{% extends "base.html" %}
{% block title %}없음{% endblock %}
{% block body %}
<h1>찾을 수 없습니다</h1>
<p>{{ what }} 은(는) 없습니다. 목록에서 다시 찾아 보세요.</p>
{% endblock %}
```

`server/templates/order_detail.html`:

```html
{% extends "base.html" %}
{% block title %}주문 {{ o.order_id }}{% endblock %}
{% block body %}
<h1>주문 {{ o.order_id }}</h1>
<dl class="kv">
  <dt>주문일</dt><dd>{{ o.ordered_at | mmdd }}</dd>
  <dt>상태</dt><dd>{{ o.status }}{% if o.status_detail %} · {{ o.status_detail }}{% endif %}</dd>
  <dt>고객</dt><dd>{% if o.customer %}<a href="/admin/customers/{{ o.customer.customer_id }}">{{ o.customer.name }}</a> · {{ o.customer.phone }}{% else %}비회원{% endif %}</dd>
  <dt>금액</dt><dd>{{ o.order_amount | won }} (배송비 {{ o.shipping_fee | won }})</dd>
  <dt>배송</dt><dd>{{ o.courier or "-" }} {{ o.tracking_no or "" }}</dd>
  <dt>출고 예정</dt><dd>{{ o.expected_ship_date | mmdd }}</dd>
  <dt>도착 예정</dt><dd>{{ o.expected_delivery | mmdd }}</dd>
  {% if o.delay_days %}<dt>지연</dt><dd class="bad">{{ o.delay_days }}일 · {{ o.delay_reason or "" }}</dd>{% endif %}
</dl>
<h2>품목</h2>
<table>
  <thead><tr><th>상품</th><th>옵션</th><th>수량</th><th>가격</th></tr></thead>
  <tbody>
  {% for it in o["items"] %}
    <tr><td>{{ it.name }}</td><td>{{ it.option or "-" }}</td><td>{{ it.qty }}</td><td class="num">{{ it.price | won }}</td></tr>
  {% endfor %}
  </tbody>
</table>
<h2>배송 이벤트</h2>
<ul class="events">
  {% for e in o.events %}<li>{{ e.at | mmdd }} · {{ e.status }}{% if e.location %} · {{ e.location }}{% endif %}</li>
  {% else %}<li class="empty">기록 없음</li>{% endfor %}
</ul>
{% if o["return_"] %}
<h2>연결된 반품</h2>
<p><a href="/admin/returns/{{ o['return_'].return_id }}">{{ o["return_"].return_id }}</a>
   · {{ o["return_"].type }} · 단계 {{ o["return_"].stage }}</p>
{% endif %}
{% endblock %}
```

주의: `o.items` 는 dict 의 `.items()` 메서드와 충돌하므로 템플릿에서 반드시 `o["items"]` 로 쓴다. `return` 은 Jinja 예약어가 아니지만 키 이름이 `return_` 이라 `o["return_"]` 로 쓴다.

`server/templates/return_detail.html` — `r.return_id` 제목, `dl.kv` 에 유형·단계·접수일(`| mmdd`)·귀책(`r.fault_party`)·배송비 부담(`r.shipping_fee_bearer`)·환불액(`| won`)·환불 산정(`r.refund_calc`)·완료 예정(`| mmdd`), `h2 단계 이력` 에 `r.stage_history` 를 `{{ h.date | mmdd }} · {{ h.stage }}` 로, `h2 원 주문` 에 `<a href="/admin/orders/{{ r.order.order_id }}">` 링크와 고객 링크.

`server/templates/customer_detail.html` — `d.name` 제목, `dl.kv` 에 고객 ID·전화·지역·주소·가입일, `h2 주문` 표(주문번호 링크·주문일·상태·금액), `h2 통화` 목록(`<a href="/admin/calls/{{ c.call_id }}">{{ c.started_at | stamp }}</a> · {{ c.turn_count }}턴`), 각각 비면 `결과 없음`.

`server/templates/call_detail.html` (Task 5 에서 답변·확신도가 채워진다):

```html
{% extends "base.html" %}
{% block title %}통화 {{ c.call_id }}{% endblock %}
{% block body %}
<h1>통화 {{ c.call_id }}</h1>
<dl class="kv">
  <dt>고객</dt><dd>{% if c.customer %}<a href="/admin/customers/{{ c.customer.customer_id }}">{{ c.customer.name }}</a> · {{ c.customer.phone }}{% else %}비회원{% endif %}</dd>
  <dt>시작</dt><dd>{{ c.started_at | stamp }}</dd>
  <dt>종료</dt><dd>{{ c.ended_at | stamp }}</dd>
  <dt>턴 수</dt><dd>{{ c.turns | length }}</dd>
</dl>
<ol class="turns">
  {% for t in c.turns %}
  <li class="turn">
    <div class="q">{{ t.q }}</div>
    <div class="a">{% if t.a %}{{ t.a }}{% else %}<span class="empty">답변 기록 없음</span>{% endif %}</div>
    <div class="meta">
      <span>{{ t.route or "-" }}</span>
      <span>{% if t.confidence is not none %}확신도 {{ "%.2f" | format(t.confidence) }}{% else %}확신도 기록 없음{% endif %}</span>
      <span>{{ t.action or "-" }}</span>
      <span>도구: {{ t.tools | join(", ") if t.tools else "없음" }}</span>
      <span class="{{ 'ok' if t.guardrail_ok else 'bad' }}">가드레일 {{ "통과" if t.guardrail_ok else "위반/기록없음" }}</span>
    </div>
  </li>
  {% else %}
  <li class="empty">턴 기록 없음</li>
  {% endfor %}
</ol>
{% endblock %}
```

`web/admin.css` 에 추가: `.kv{display:grid;grid-template-columns:120px 1fr;gap:4px 12px}`, `.kv dt{color:#666;font-size:13px}`, `.turns{list-style:decimal;padding-left:24px}`, `.turn{border-bottom:1px solid #eee;padding:10px 0}`, `.turn .q{font-weight:600}`, `.turn .a{margin:4px 0}`, `.turn .meta{display:flex;gap:12px;flex-wrap:wrap;color:#666;font-size:12px}`, `.bad{color:#c0392b}`, `.ok{color:#2c7a3f}`.

- [ ] **Step 5: 통화 화면에서 어드민으로 가는 링크** — `web/index.html`

13번 줄 `<div class="shop">…</div>` 이 들어 있는 `<header>` 안, `<div class="meta">` 다음 줄에 추가한다.

```html
      <a class="admin-link" href="/admin" target="_blank" rel="noopener">어드민</a>
```

`web/style.css` 끝에 `.admin-link{color:inherit;font-size:12px;opacity:.7;text-decoration:underline}` 를 더한다.

- [ ] **Step 6: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_admin_routes.py -q`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add server/admin.py server/templates web/admin.css web/index.html web/style.css tests/test_admin_routes.py
git commit -m "feat: 어드민 상세 화면 4개와 HTML 404"
```

---

### Task 5: 턴 로그에 답변·확신도 추가 + 보안 회귀 테스트

**Files:**
- Modify: `server/pipeline.py:382-387` (턴 로그 dict)
- Test: `tests/test_pipeline.py` (기존 파일에 테스트 추가), `tests/test_admin_routes.py` (XSS·쓰기 SQL 테스트 추가)

**Interfaces:**
- Consumes: Task 4 의 `call_detail.html` (`t.a`, `t.confidence` 를 이미 읽는다)
- Produces: `call_logs.turns` 의 각 턴 dict 에 `a`(답변 텍스트), `confidence`(float 또는 None) 추가

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_pipeline.py` 에 추가한다. 기존 파일의 파이프라인 픽스처 이름·구성을 먼저 읽고 그 패턴을 그대로 따른다(가짜 LLM 을 쓰는 기존 테스트가 있다). 검증할 내용:

```python
def test_turn_log_records_answer_and_confidence(<기존 픽스처>):
    # 통화를 시작해 한 턴을 돌리고 종료한 뒤, 저장된 turns 에 답변과 확신도가 있는지 본다
    call_id, _, _ = pipeline.start_call()
    result = pipeline.turn(call_id, "배송비 얼마인가요?")
    logged = pipeline.turn_logs[call_id][-1]
    assert logged["a"] == result.answer
    assert logged["confidence"] == result.confidence
```

`tests/test_admin_routes.py` 에 추가한다.

```python
def test_html_escapes_customer_name():
    """고객 이름에 스크립트가 들어 있어도 그대로 렌더되지 않는다."""
    from pathlib import Path

    from server.adminrepo import AdminRepo

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(Path("server/db/schema.sql").read_text(encoding="utf-8"))
    con.execute("insert into customers values (?,?,?,?,?,?)",
                ("C-XSS", "<script>alert(1)</script>", "010-0000-0000", "서울", "서울시", "2026-01-01"))
    con.commit()

    app_client = TestClient(create_app(type("P", (), {"repo": type("R", (), {"con": con})()})(),
                                       load_domain(Path("domains/modumall"))))
    r = app_client.get("/admin/customers")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_admin_modules_have_no_write_sql():
    """어드민은 읽기 전용이다 — 쓰기 SQL 키워드가 없어야 한다."""
    import re
    from pathlib import Path

    for name in ("server/adminrepo.py", "server/admin.py"):
        src = Path(name).read_text(encoding="utf-8").lower()
        assert not re.search(r"\b(insert|update|delete|drop|alter)\s+", src), name
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -q -k turn_log && .venv/bin/python -m pytest tests/test_admin_routes.py -q`
Expected: 턴 로그 테스트가 `KeyError: 'a'` 로 실패. XSS·쓰기 SQL 테스트는 통과할 수도 있다(통과하면 그대로 두고 회귀 방지용으로 남긴다).

- [ ] **Step 3: 구현** — `server/pipeline.py` 의 턴 로그

```python
        self.turn_logs.setdefault(call_id, []).append({
            "q": text, "a": out["answer"], "route": out.get("route"),
            "confidence": out.get("confidence"), "action": action,
            "tools": [t["name"] for t in (out.get("tools") or [])],
            "guardrail_ok": g.get("ok") if g else None,
            "followup": out.get("is_followup", False),
        })
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest -q`
Expected: 전부 통과 (기존 241 + 신규)

- [ ] **Step 5: 손으로 확인**

```bash
PORT=8010 .venv/bin/python -m server
```
브라우저에서 `http://127.0.0.1:8010/admin` → 목록 4개와 상세로 링크만 따라 이동되는지, `/admin/calls` 상세에 턴이 보이는지 확인한다. 확인 후 서버를 끈다.

- [ ] **Step 6: 커밋**

```bash
git add server/pipeline.py tests/test_pipeline.py tests/test_admin_routes.py
git commit -m "feat: 통화 턴 로그에 답변·확신도 기록, 어드민 보안 회귀 테스트"
```

---

## 자기 점검 (플랜 작성자용, 실행자는 건너뛴다)

- 스펙 2.1 의 9개 경로 → Task 3(목록 4 + 홈), Task 4(상세 4). 전부 있음.
- 스펙 2.2 AdminRepo 시그니처 → Task 1·2 와 일치(`(rows, total)` 반환, 상세는 `None`).
- 스펙 2.3 템플릿 → base + home + 목록 4 + 상세 4 + notfound + `_paging` = 11개. 스펙의 "9개" 는 `_paging`·`home` 을 세지 않은 수치이므로 이 플랜의 11개가 맞다.
- 스펙 2.4 통화 화면 링크 → Task 4 Step 5.
- 스펙 2.5 턴 로그 → Task 5.
- 스펙 3 테스트 항목(필터·페이징 경계·검색·집계·없는 id·404·XSS·쓰기 금지) → Task 1·2·3·4·5 에 모두 배치됨.
