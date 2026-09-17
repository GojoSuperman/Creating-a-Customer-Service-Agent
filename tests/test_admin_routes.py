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


@pytest.mark.parametrize("path,text", [
    ("/admin", "요약"),
    ("/admin/orders", "주문"),
    ("/admin/returns", "반품·교환"),
    ("/admin/calls", "통화 로그"),
    ("/admin/customers", "고객"),
])
def test_list_pages_render(client, path, text):
    r = client.get(path)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert text in r.text


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
    assert "접수" in r.text and "/admin/orders/O-1006" in r.text


def test_customer_detail_page(client):
    # 목록 응답에서 실제 존재하는 고객 ID 를 뽑아 상세를 연다 (하드코딩 금지)
    listing = client.get("/admin/customers", params={"page": 1})
    cid = re.search(r"C-\d+", listing.text).group()
    r = client.get(f"/admin/customers/{cid}")
    assert r.status_code == 200
    assert "주문" in r.text and "통화" in r.text


def test_call_detail_page(client):
    # 통화 목록에서 실제 링크된 통화 ID 를 뽑아 상세를 연다 (call_id 하드코딩 금지 — 공유 DB 는 가변)
    listing = client.get("/admin/calls", params={"page": 1})
    m = re.search(r'/admin/calls/([^"]+)"', listing.text)
    assert m, "통화 목록에 상세 링크가 없음"
    r = client.get(f"/admin/calls/{m.group(1)}")
    assert r.status_code == 200


@pytest.mark.parametrize("path", [
    "/admin/orders/O-9999", "/admin/returns/R-9999",
    "/admin/calls/없는통화", "/admin/customers/C-9999"])
def test_missing_id_returns_html_404(client, path):
    r = client.get(path)
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "찾을 수 없습니다" in r.text
