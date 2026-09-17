# -*- coding: utf-8 -*-
"""쇼핑몰 라우터: 상품 목록/상세, 로그인/로그아웃, 세션 보호."""
import os
import sqlite3

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
def monkeypatch_module_secret():
    old = os.environ.get("SHOP_SECRET")
    os.environ["SHOP_SECRET"] = "테스트-쇼핑-키"
    yield
    if old is None:
        os.environ.pop("SHOP_SECRET", None)
    else:
        os.environ["SHOP_SECRET"] = old


@pytest.fixture(scope="module")
def domain(modumall_dir_module):
    return load_domain(modumall_dir_module)


@pytest.fixture(scope="module")
def app(domain, monkeypatch_module_secret):
    return create_app(FakePipelineWithRepo(domain.db_path), domain)


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app)


@pytest.fixture(scope="module")
def sample_customer(domain):
    """DB 에 실제로 있는 고객 한 명 (이름·전화번호)을 로그인 테스트에 쓴다."""
    con = sqlite3.connect(str(domain.db_path))
    return con.execute("select name, phone from customers order by customer_id limit 1").fetchone()


def test_product_list_renders_with_filter_and_paging(client):
    r = client.get("/shop")
    assert r.status_code == 200 and "<h1>모두몰</h1>" in r.text
    assert "page=2" in r.text

    r = client.get("/shop", params={"category": "COSMETICS"})
    assert r.status_code == 200 and "화장품" in r.text

    r = client.get("/shop", params={"q": "존재하지않는상품이름xyz"})
    assert "결과 없음" in r.text


def test_product_detail_and_404(client):
    r = client.get("/shop/products/P1001")
    assert r.status_code == 200 and "요일팬티 7종 세트" in r.text and "장바구니" in r.text
    missing = client.get("/shop/products/P9999")
    assert missing.status_code == 404 and "text/html" in missing.headers["content-type"]
    assert "찾을 수 없습니다" in missing.text


def test_login_sets_session_and_logout_clears(app, sample_customer):
    name, phone = sample_customer

    with TestClient(app) as c:
        bad = c.post("/shop/login", data={"phone": "010-0000-0000"}, follow_redirects=False)
        assert bad.status_code == 200 and "가입 이력이 없는 번호" in bad.text

        ok = c.post("/shop/login", data={"phone": phone}, follow_redirects=False)
        assert ok.status_code == 303 and "shop_session" in ok.cookies

        mine = c.get("/shop/orders")
        assert mine.status_code == 200 and name in mine.text

        out = c.post("/shop/logout", follow_redirects=False)
        assert out.status_code == 303
        assert c.get("/shop/orders", follow_redirects=False).status_code == 303  # 로그인 화면으로


def test_forged_session_is_treated_as_logged_out(app):
    with TestClient(app) as c:
        c.cookies.set("shop_session", "tampered-value.bad-signature", path="/shop")
        r = c.get("/shop/orders", follow_redirects=False)
        assert r.status_code == 303 and "/shop/login" in r.headers["location"]


def test_search_is_escaped(client):
    r = client.get("/shop", params={"q": "<script>alert(1)</script>"})
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text
