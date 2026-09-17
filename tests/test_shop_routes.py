# -*- coding: utf-8 -*-
"""쇼핑몰 라우터: 상품 목록/상세, 로그인/로그아웃, 세션 보호."""
import os
import re
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


def _total_count(html: str) -> int:
    m = re.search(r"총 (\d+)건", html)
    assert m, "페이지네이션 총 건수 표시를 찾지 못함"
    return int(m.group(1))


def test_product_list_renders_with_filter_and_paging(client):
    r = client.get("/shop")
    assert r.status_code == 200 and "<h1>모두몰</h1>" in r.text
    assert "page=2" in r.text
    total_all = _total_count(r.text)

    r = client.get("/shop", params={"category": "COSMETICS"})
    assert r.status_code == 200
    total_cosmetics = _total_count(r.text)
    assert 0 < total_cosmetics < total_all  # 실제로 좁혀졌는지 — 카테고리 nav 라벨 존재만으론 부족

    r = client.get("/shop", params={"q": "존재하지않는상품이름xyz"})
    assert "결과 없음" in r.text


def test_product_list_page_out_of_range_clamps_to_last(client):
    r = client.get("/shop", params={"page": 999})
    assert r.status_code == 200
    assert "결과 없음" not in r.text  # 마지막 페이지로 당겨져 실제 상품이 보여야 한다
    m = re.search(r"(\d+) / (\d+) · 총 (\d+)건", r.text)
    assert m and m.group(1) == m.group(2)  # 현재 페이지 == 마지막 페이지


def test_product_list_invalid_page_falls_back_to_html(client):
    r = client.get("/shop", params={"page": "abc"})
    assert r.status_code == 200  # 422 JSON 이 아니라 1페이지 HTML
    assert "<h1>모두몰</h1>" in r.text


def test_product_detail_and_404(client):
    r = client.get("/shop/products/P1001")
    assert r.status_code == 200 and "요일팬티 7종 세트" in r.text
    assert 'action="/shop/cart/add"' in r.text and 'name="qty"' in r.text  # 담기 폼 자체를 확인

    missing = client.get("/shop/products/P9999")
    assert missing.status_code == 404 and "text/html" in missing.headers["content-type"]
    assert "찾을 수 없습니다" in missing.text


def test_login_with_non_utf8_body_does_not_500(client):
    # 인증 없이 누구나 두드릴 수 있는 엔드포인트 — 깨진 바이트가 섞여도 500 이 아니라 정상 안내여야 한다
    r = client.post("/shop/login", content=b"phone=\xff\xfe\x00",
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and "가입 이력이 없는 번호" in r.text


def test_login_with_oversized_body_is_rejected(client):
    from server.shop import MAX_FORM_BODY_BYTES
    huge = b"phone=" + b"9" * (MAX_FORM_BODY_BYTES + 1024)
    r = client.post("/shop/login", content=huge,
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413


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
