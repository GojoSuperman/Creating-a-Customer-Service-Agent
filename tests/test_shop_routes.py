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
def monkeypatch_module_open_admin():
    # test_order_flow_creates_order_visible_in_admin 이 /admin/orders 를 확인한다.
    # ADMIN_PASSWORD 없이도(fail-closed 503 대신) 열리도록 로컬 개발 옵트인을 켠다.
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


@pytest.fixture(scope="module")
def app(domain, monkeypatch_module_secret, monkeypatch_module_open_admin):
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


def _login(c, con):
    phone = con.execute("select phone from customers order by customer_id limit 1").fetchone()[0]
    assert c.post("/shop/login", data={"phone": phone}, follow_redirects=False).status_code == 303


def _subtotal_line(html: str) -> str:
    """상품 합계 <dd> 를 그대로 뽑는다 — 단가 열은 수량과 무관하게 항상 같은 문자열을 담고 있어
    "24,900원 in text" 같은 단언은 수량이 실제로 바뀌었는지 전혀 검증하지 못한다(실측)."""
    m = re.search(r"<dt>상품 합계</dt><dd>([^<]+)</dd>", html)
    assert m, "상품 합계 표시를 찾지 못함"
    return m.group(1)


def test_cart_add_update_and_quote(client):
    with TestClient(client.app) as c:
        add = c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 2, "option": "M"},
                     follow_redirects=False)
        assert add.status_code == 303 and add.headers["location"] == "/shop/cart"

        cart = c.get("/shop/cart")
        assert "요일팬티 7종 세트" in cart.text
        assert _subtotal_line(cart.text) == "49,800원"

        c.post("/shop/cart/update", data={"product_id": "P1001", "qty": 1}, follow_redirects=False)
        # 수량을 2 -> 1 로 줄였으니 상품 합계도 실제로 절반(24,900원)이 되어야 한다
        assert _subtotal_line(c.get("/shop/cart").text) == "24,900원"

        c.post("/shop/cart/update", data={"product_id": "P1001", "qty": 0}, follow_redirects=False)
        assert "장바구니가 비어" in c.get("/shop/cart").text


def test_cart_add_merges_same_product_and_option(client):
    with TestClient(client.app) as c:
        c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 1, "option": "M"}, follow_redirects=False)
        c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 1, "option": "M"}, follow_redirects=False)
        cart = c.get("/shop/cart")
        # 두 번 담아도 한 줄로 합쳐져 수량 2, 단가*2 총액만 보여야 한다 (24,900*2=49,800)
        assert "49,800원" in cart.text
        assert cart.text.count("요일팬티 7종 세트") == 1


def test_cart_update_only_touches_matching_line(client):
    with TestClient(client.app) as c:
        c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 1, "option": "M"}, follow_redirects=False)
        c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 1, "option": "L"}, follow_redirects=False)
        before = _subtotal_line(c.get("/shop/cart").text)
        assert before == "49,800원"  # 두 줄(M, L) 합계
        # M 옵션 줄만 삭제해도 L 옵션 줄은 남아야 한다 — no-op 돌연변이라면 두 줄 다 남아 합계가 그대로일 것
        c.post("/shop/cart/update", data={"product_id": "P1001", "option": "M", "qty": 0}, follow_redirects=False)
        cart_text = c.get("/shop/cart").text
        assert "장바구니가 비어" not in cart_text
        assert _subtotal_line(cart_text) == "24,900원"  # L 옵션 한 줄만 남아야 한다
        assert cart_text.count('name="option" value="M"') == 0  # M 옵션 줄 자체가 사라져야 한다


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
        path = location.split("?", 1)[0]
        assert path.startswith("/shop/orders/O-")
        order_id = path.rsplit("/", 1)[1]

        detail = c.get(location)
        assert detail.status_code == 200 and order_id in detail.text and "요일팬티 7종 세트" in detail.text
        assert "주문이 접수되었습니다" in detail.text            # 방금 주문한 직후에는 접수 안내가 보인다

        assert "장바구니가 비어" in c.get("/shop/cart").text            # 주문 후 장바구니는 비워진다
        assert order_id in c.get("/shop/orders").text
        assert order_id in c.get(f"/admin/orders/{order_id}").text      # 어드민에도 보인다


def test_order_detail_banner_only_shows_right_after_ordering(client, modumall_dir_module):
    """주문 직후(체크아웃 리다이렉트)에는 접수 안내가 보이지만, 나중에(예: 내 주문 목록에서 링크를 눌러)
    같은 주문 상세를 다시 봤을 때는 보이면 안 된다 — 과거 주문에도 항상 뜨던 버그 회귀 테스트."""
    import sqlite3
    con = sqlite3.connect(str(load_domain(modumall_dir_module).db_path))
    with TestClient(client.app) as c:
        _login(c, con)
        c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 1, "option": "M"}, follow_redirects=False)
        done = c.post("/shop/checkout", follow_redirects=False)
        location = done.headers["location"]

        just_ordered = c.get(location)
        assert "주문이 접수되었습니다" in just_ordered.text

        order_id = location.split("?", 1)[0].rsplit("/", 1)[1]
        later = c.get(f"/shop/orders/{order_id}")            # 주문 목록에서 다시 들어온 것처럼 쿼리 없이 재방문
        assert later.status_code == 200
        assert "주문이 접수되었습니다" not in later.text


def test_order_detail_banner_does_not_show_for_new_equals_zero(client, modumall_dir_module):
    """?new=0 처럼 참으로 흔히 쓰는 "거짓" 문자열도 `bool(new)` 로는 True 였다(리뷰어 실측).
    new 값이 정확히 "1" 일 때만 접수 안내가 떠야 한다."""
    import sqlite3
    con = sqlite3.connect(str(load_domain(modumall_dir_module).db_path))
    with TestClient(client.app) as c:
        _login(c, con)
        c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 1, "option": "M"}, follow_redirects=False)
        done = c.post("/shop/checkout", follow_redirects=False)
        order_id = done.headers["location"].split("?", 1)[0].rsplit("/", 1)[1]

        r = c.get(f"/shop/orders/{order_id}?new=0")
        assert r.status_code == 200
        assert "주문이 접수되었습니다" not in r.text


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
    soldout_id, stock_before = con.execute(
        "select product_id, stock from products where soldout=1 limit 1").fetchone()
    orders_before = con.execute("select count(*) from orders").fetchone()[0]
    with TestClient(client.app) as c:
        _login(c, con)
        c.post("/shop/cart/add", data={"product_id": soldout_id, "qty": 1, "option": ""}, follow_redirects=False)
        r = c.post("/shop/checkout", follow_redirects=True)
        assert "품절" in r.text

    # 스펙 4-4: 주문이 거부되면 흔적(주문 행·재고 변화)을 남기지 않아야 한다 — id·건수 하드코딩 없이 확인
    orders_after = con.execute("select count(*) from orders").fetchone()[0]
    stock_after = con.execute("select stock from products where product_id=?", (soldout_id,)).fetchone()[0]
    assert orders_after == orders_before
    assert stock_after == stock_before


def test_cart_with_missing_product_can_be_emptied(client):
    """존재하지 않는 상품 id 로 담아도 삭제할 방법이 있어야 한다 — 예전에는 quote() 가 이런 줄을
    조용히 건너뛰어 "장바구니가 비어 있습니다"로 보이면서 삭제 폼도 없는 막다른 상태가 됐었다."""
    with TestClient(client.app) as c:
        c.post("/shop/cart/add", data={"product_id": "P-NOPE", "qty": 1, "option": ""}, follow_redirects=False)
        cart = c.get("/shop/cart")
        assert "장바구니가 비어" not in cart.text
        assert 'value="P-NOPE"' in cart.text          # 삭제 폼이 실제로 렌더된다

        c.post("/shop/cart/update", data={"product_id": "P-NOPE", "option": "", "qty": 0}, follow_redirects=False)
        assert "장바구니가 비어" in c.get("/shop/cart").text   # 삭제하면 정말로 빠져나올 수 있다


def test_cart_shows_soldout_badge_before_checkout(client, modumall_dir_module):
    """품절 상품을 담으면 장바구니 화면에서부터 품절 배지가 보여야 한다 — 결제 버튼을 눌러
    거부당해야만 알 수 있으면 안 된다(리뷰어 실측)."""
    import sqlite3
    con = sqlite3.connect(str(load_domain(modumall_dir_module).db_path))
    soldout_id = con.execute("select product_id from products where soldout=1 limit 1").fetchone()[0]
    with TestClient(client.app) as c:
        c.post("/shop/cart/add", data={"product_id": soldout_id, "qty": 1, "option": ""}, follow_redirects=False)
        cart = c.get("/shop/cart")
        assert '<span class="badge">품절</span>' in cart.text


def test_checkout_blocked_message_is_deduplicated(client, modumall_dir_module):
    """같은 상품이 옵션만 다르게 두 줄로 담겨 함께 품절이면, 화면에는 같은 사유 문구가 한 번만 보여야 한다."""
    import sqlite3
    con = sqlite3.connect(str(load_domain(modumall_dir_module).db_path))
    soldout = con.execute("select product_id, name from products where soldout=1 limit 1").fetchone()
    soldout_id, soldout_name = soldout
    with TestClient(client.app) as c:
        _login(c, con)
        c.post("/shop/cart/add", data={"product_id": soldout_id, "qty": 1, "option": "A"}, follow_redirects=False)
        c.post("/shop/cart/add", data={"product_id": soldout_id, "qty": 1, "option": "B"}, follow_redirects=False)
        r = c.post("/shop/checkout", follow_redirects=True)
        reason = f"{soldout_name} 은(는) 품절입니다."
        assert r.text.count(reason) == 1


def test_shop_order_is_visible_to_agent_tool(client, modumall_dir_module):
    """쇼핑몰에서 만든 주문을 상담 에이전트의 조회 도구(get_order_status)가 찾을 수 있어야 한다.

    server/tools.py 의 진입점은 build_tools 가 아니라 make_tools(domain) 이며, 반환값은
    {함수명: 함수} 딕셔너리다. LLM 없이도 이 딕셔너리에서 get_order_status 를 직접 꺼내 부를 수
    있으므로(내부에서 repo.order() 를 호출할 뿐 모델 호출은 없다) 대체 검증 없이 실제 도구
    진입점으로 검증한다."""
    import sqlite3

    from server.domain import load_domain as _load
    from server.tools import make_tools

    con = sqlite3.connect(str(_load(modumall_dir_module).db_path))
    with TestClient(client.app) as c:
        _login(c, con)
        c.post("/shop/cart/add", data={"product_id": "P1001", "qty": 1, "option": "M"}, follow_redirects=False)
        location = c.post("/shop/checkout", follow_redirects=False).headers["location"]
        order_id = location.split("?", 1)[0].rsplit("/", 1)[1]  # "?new=1" 쿼리를 떼어내야 순수 주문번호

    # get_order_status 도구로 같은 주문을 조회한다
    domain = _load(modumall_dir_module)
    tools = make_tools(domain)
    result = tools["get_order_status"](order_id)
    assert result["order_id"] == order_id and result["status"] == "결제완료"
