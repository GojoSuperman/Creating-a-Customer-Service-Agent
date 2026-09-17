import json
import os
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.domain import load_domain
from server.pipeline import TurnResult


@pytest.fixture(scope="module", autouse=True)
def _open_admin_env():
    """이 파일은 어드민 화면 콘텐츠 렌더링만 검증한다(인증 자체는 test_admin_auth.py).
    ADMIN_PASSWORD 없이도 열리도록 로컬 개발 옵트인(ALLOW_OPEN_ADMIN)을 켠 채로 돌린다.
    module 스코프 autouse 라 이 모듈의 module 스코프 client 픽스처보다 먼저 적용된다."""
    prev_password = os.environ.pop("ADMIN_PASSWORD", None)
    prev_allow = os.environ.get("ALLOW_OPEN_ADMIN")
    os.environ["ALLOW_OPEN_ADMIN"] = "1"
    yield
    if prev_password is not None:
        os.environ["ADMIN_PASSWORD"] = prev_password
    if prev_allow is None:
        os.environ.pop("ALLOW_OPEN_ADMIN", None)
    else:
        os.environ["ALLOW_OPEN_ADMIN"] = prev_allow


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


def test_nav_call_screen_link_targets_top(client):
    # 통화 화면 링크는 조회 패널 iframe 안에서 눌려도 그 프레임 안에 중첩되지 않도록 target="_top" 이어야 한다.
    # 다른 nav 링크는 iframe 안에서 그대로 탐색되어야 하므로 target 이 없어야 한다.
    r = client.get("/admin")
    assert '<a class="right" href="/" target="_top">통화 화면</a>' in r.text
    assert 'href="/admin/orders" target=' not in r.text
    assert 'href="/admin/customers" target=' not in r.text


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


def test_customer_detail_embed_hides_nav(client):
    # 조회 패널의 "현재 고객" iframe 은 embed=1 로 호출되며, 이때는 nav 를 숨긴다.
    # 기본 호출(embed 파라미터 없음)에는 영향이 없어야 한다.
    listing = client.get("/admin/orders", params={"page": 1})
    m = re.search(r'/admin/customers/(C-\d+)">', listing.text)
    assert m, "주문 목록에 고객 링크가 없음"
    cid = m.group(1)

    normal = client.get(f"/admin/customers/{cid}")
    assert normal.status_code == 200
    assert 'class="nav"' in normal.text

    embedded = client.get(f"/admin/customers/{cid}", params={"embed": "1"})
    assert embedded.status_code == 200
    assert 'class="nav"' not in embedded.text


def test_call_detail_page(modumall_dir):
    # 공유 DB 의 call_logs 는 다른 테스트(test_generate 의 재생성 등)가 계속 건드리는
    # 가변 자원이라 "턴이 있는 통화가 목록에 존재한다"를 전제로 할 수 없다.
    # test_html_escapes_customer_name 과 같은 패턴으로 인메모리 DB 에 통화 1건을
    # 직접 시드해, 목록 -> 상세 흐름에서 턴 수·질문·답변이 그대로 렌더되는지 검증한다.
    domain = load_domain(modumall_dir)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript((modumall_dir.parent.parent / "server" / "db" / "schema.sql").read_text(encoding="utf-8"))
    turns = [
        {"q": "배송이 언제 오나요", "a": "내일 도착 예정입니다", "route": "SHIPPING", "confidence": 0.91},
        {"q": "반품하고 싶어요", "a": "반품 절차를 안내드립니다", "route": "RETURN", "confidence": 0.77},
    ]
    con.execute(
        "insert into call_logs (call_id, customer_id, started_at, ended_at, turns) values (?,?,?,?,?)",
        ("call-detail-page-1", None, "2026-09-01T00:00:00", "2026-09-01T00:05:00",
         json.dumps(turns, ensure_ascii=False)),
    )
    con.commit()

    fake_pipeline = FakePipelineWithRepo.__new__(FakePipelineWithRepo)
    fake_pipeline.repo = type("R", (), {"con": con})()
    seeded_client = TestClient(create_app(fake_pipeline, domain))

    row_pattern = re.compile(
        r'<td><a href="/admin/calls/([^"]+)">.*?</a></td>\s*'
        r'<td>.*?</td>\s*<td>.*?</td>\s*<td>.*?</td>\s*<td>(\d+)</td>', re.S)
    listing = seeded_client.get("/admin/calls")
    rows = row_pattern.findall(listing.text)
    assert rows == [("call-detail-page-1", "2")]
    call_id, turn_count = rows[0]

    r = seeded_client.get(f"/admin/calls/{call_id}")
    assert r.status_code == 200
    detail_turns = re.search(r"턴 수</dt>\s*<dd>(\d+)</dd>", r.text)
    assert detail_turns and detail_turns.group(1) == turn_count
    assert "배송이 언제 오나요" in r.text and "내일 도착 예정입니다" in r.text
    assert "반품하고 싶어요" in r.text and "반품 절차를 안내드립니다" in r.text


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
