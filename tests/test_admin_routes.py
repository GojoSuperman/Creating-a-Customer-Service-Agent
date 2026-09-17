import re
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


@pytest.mark.parametrize("path,h1", [
    ("/admin", "<h1>요약</h1>"),
    ("/admin/orders", "<h1>주문</h1>"),
    ("/admin/returns", "<h1>반품·교환</h1>"),
    ("/admin/calls", "<h1>통화 로그</h1>"),
    ("/admin/customers", "<h1>고객</h1>"),
])
def test_list_pages_render(client, path, h1):
    # nav 가 모든 페이지에 "주문"/"반품·교환"/"통화 로그"/"고객" 을 링크 텍스트로 렌더하므로
    # 단순 substring 검사는 상시-참이 된다. 각 화면 고유의 <h1> 마크업으로 검증한다.
    r = client.get(path)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert h1 in r.text


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


def test_order_detail_page(client):
    r = client.get("/admin/orders/O-1006")
    assert r.status_code == 200
    assert "O-1006" in r.text and "R-2001" in r.text          # 연결된 반품 링크
    assert "/admin/returns/R-2001" in r.text


def test_return_detail_page(client):
    r = client.get("/admin/returns/R-2001")
    assert r.status_code == 200
    assert "/admin/orders/O-1006" in r.text
    # "접수" 는 <dt>접수일</dt> 라벨 때문에 상시-참이 된다. 단계 이력 섹션에서만 나오는
    # 실제 이력 데이터(수거대기/입고완료)로 렌더링 여부를 검증한다.
    assert "수거대기" in r.text and "입고완료" in r.text


def test_customer_detail_page(client):
    # 주문 목록에서 (고객 ID, 고객명) 쌍을 함께 뽑는다 — 주문이 있는 고객이라는 보장도 된다
    # (id·이름 하드코딩 금지, 공유 DB 는 가변 자원).
    listing = client.get("/admin/orders", params={"page": 1})
    m = re.search(r'/admin/customers/(C-\d+)">([^<]+)</a>', listing.text)
    assert m, "주문 목록에 고객 링크가 없음"
    cid, name = m.group(1), m.group(2)
    r = client.get(f"/admin/customers/{cid}")
    assert r.status_code == 200
    assert f"<h1>{name}</h1>" in r.text
    assert "/admin/orders/" in r.text


def test_call_detail_page(client):
    # 통화 목록에서 실제 링크된 통화 ID 와 그 행의 턴 수를 함께 뽑는다
    # (call_id 하드코딩 금지 — 공유 DB 의 call_logs 는 다른 테스트가 쓰는 가변 자원).
    # 턴 수 0 인 행은 뮤테이션 테스트로 고정값과 구분이 안 되므로 0 이 아닌 행을 고른다.
    row_pattern = re.compile(
        r'<td><a href="/admin/calls/([^"]+)">.*?</a></td>\s*'
        r'<td>.*?</td>\s*<td>.*?</td>\s*<td>.*?</td>\s*<td>(\d+)</td>', re.S)
    call_id = turn_count = None
    for page in range(1, 10):
        listing = client.get("/admin/calls", params={"page": page})
        rows = row_pattern.findall(listing.text)
        if not rows:
            break
        nonzero = next((row for row in rows if row[1] != "0"), None)
        if nonzero:
            call_id, turn_count = nonzero
            break
    assert call_id, "턴이 있는 통화를 찾지 못함"
    r = client.get(f"/admin/calls/{call_id}")
    assert r.status_code == 200
    detail_turns = re.search(r"턴 수</dt>\s*<dd>(\d+)</dd>", r.text)
    assert detail_turns and detail_turns.group(1) == turn_count


@pytest.mark.parametrize("path", [
    "/admin/orders/O-9999", "/admin/returns/R-9999",
    "/admin/calls/없는통화", "/admin/customers/C-9999"])
def test_missing_id_returns_html_404(client, path):
    r = client.get(path)
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "찾을 수 없습니다" in r.text


def test_html_escapes_customer_name(modumall_dir):
    """고객 이름에 스크립트가 들어 있어도 그대로 렌더되지 않는다 (인메모리 DB, 공유 DB 미사용)."""
    domain = load_domain(modumall_dir)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript((modumall_dir.parent.parent / "server" / "db" / "schema.sql").read_text(encoding="utf-8"))
    con.execute("insert into customers values (?,?,?,?,?,?)",
                ("C-XSS", "<script>alert(1)</script>", "010-0000-0000", "서울", "서울시", "2026-01-01"))
    con.commit()

    fake_pipeline = FakePipelineWithRepo.__new__(FakePipelineWithRepo)
    fake_pipeline.repo = type("R", (), {"con": con})()
    xss_client = TestClient(create_app(fake_pipeline, domain))
    r = xss_client.get("/admin/customers")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_admin_modules_have_no_write_sql():
    """어드민은 읽기 전용이다 — 쓰기 SQL 키워드가 없어야 한다."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for name in ("server/adminrepo.py", "server/admin.py"):
        src = (root / name).read_text(encoding="utf-8").lower()
        assert not re.search(r"\b(insert|update|delete|drop|alter)\s+", src), name
