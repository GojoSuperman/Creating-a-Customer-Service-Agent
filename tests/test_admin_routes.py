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
