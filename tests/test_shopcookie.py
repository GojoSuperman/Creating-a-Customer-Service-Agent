import pytest

from server import shopcookie


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch):
    monkeypatch.setenv("SHOP_SECRET", "테스트용-고정-키")


def test_sign_unsign_roundtrip():
    token = shopcookie.sign("C-0001", "uid")
    assert token != "C-0001" and "." in token
    assert shopcookie.unsign(token, "uid") == "C-0001"


def test_unsign_rejects_tampered_or_garbage():
    token = shopcookie.sign("C-0001", "uid")
    payload, mac = token.rsplit(".", 1)
    forged = shopcookie.sign("C-9999", "uid").rsplit(".", 1)[0] + "." + mac
    assert shopcookie.unsign(forged, "uid") is None          # 값만 바꾼 위조
    assert shopcookie.unsign(payload + ".AAAA", "uid") is None
    assert shopcookie.unsign("점없는문자열", "uid") is None
    assert shopcookie.unsign(None, "uid") is None
    assert shopcookie.unsign("", "uid") is None


def test_unsign_fails_with_other_key(monkeypatch):
    token = shopcookie.sign("C-0001", "uid")
    monkeypatch.setenv("SHOP_SECRET", "다른-키")
    assert shopcookie.unsign(token, "uid") is None


def test_unsign_rejects_token_signed_for_other_purpose():
    """세션(uid)용으로 만든 토큰을 장바구니(cart)용으로, 혹은 그 반대로 재사용할 수 없어야 한다."""
    uid_token = shopcookie.sign("C-0002", "uid")
    assert shopcookie.unsign(uid_token, "cart") is None

    cart_token = shopcookie.dump_cart([{"product_id": "P1001", "option": None, "qty": 1}])
    assert shopcookie.unsign(cart_token, "uid") is None
    assert shopcookie.load_cart(uid_token) == []


def test_cart_roundtrip_keeps_korean_option():
    items = [{"product_id": "P1001", "option": "사이즈 M", "qty": 2}]
    assert shopcookie.load_cart(shopcookie.dump_cart(items)) == items


def test_load_cart_is_forgiving():
    assert shopcookie.load_cart(None) == []
    assert shopcookie.load_cart("깨진.토큰") == []
    assert shopcookie.load_cart(shopcookie.sign("{잘못된 json", "cart")) == []
    assert shopcookie.load_cart(shopcookie.sign('{"not": "a list"}', "cart")) == []


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


def test_load_cart_drops_non_string_product_id():
    """product_id 가 문자열이 아니면 str() 로 강제 변환하지 말고 그 줄을 버린다."""
    items = [{"product_id": "P1001", "option": None, "qty": 1},
             {"product_id": 12345, "option": None, "qty": 1},
             {"product_id": ["x"], "option": None, "qty": 1}]
    loaded = shopcookie.load_cart(shopcookie.dump_cart(items))
    assert [l["product_id"] for l in loaded] == ["P1001"]


def test_load_cart_truncates_product_id_and_option_length():
    long_id = "P" * 100
    long_option = "긴옵션" * 100
    items = [{"product_id": long_id, "option": long_option, "qty": 1}]
    loaded = shopcookie.load_cart(shopcookie.dump_cart(items))
    assert len(loaded[0]["product_id"]) == shopcookie.MAX_PRODUCT_ID_LEN
    assert loaded[0]["product_id"] == long_id[:shopcookie.MAX_PRODUCT_ID_LEN]
    assert len(loaded[0]["option"]) == shopcookie.MAX_OPTION_LEN
    assert loaded[0]["option"] == long_option[:shopcookie.MAX_OPTION_LEN]


def test_load_cart_normalizes_non_string_option_to_none():
    """option 이 dict/list 등 문자열이 아니면 None 으로 정규화한다 (쿠키 크기 폭발 방지)."""
    items = [{"product_id": "P1001", "option": {"x": [1, 2]}, "qty": 1},
             {"product_id": "P1002", "option": ["a", "b"], "qty": 1},
             {"product_id": "P1003", "option": 123, "qty": 1}]
    loaded = shopcookie.load_cart(shopcookie.dump_cart(items))
    assert [l["option"] for l in loaded] == [None, None, None]


def test_load_cart_drops_bool_qty():
    """bool 은 int 의 하위 타입이므로 isinstance(qty, int) 만으로는 True/False 를 걸러내지 못한다 — 명시적으로 막는다."""
    items = [{"product_id": "P1001", "option": None, "qty": True},
             {"product_id": "P1002", "option": None, "qty": False},
             {"product_id": "P1003", "option": None, "qty": 3}]
    loaded = shopcookie.load_cart(shopcookie.dump_cart(items))
    assert [l["product_id"] for l in loaded] == ["P1003"]


def test_ephemeral_key_is_used_when_env_missing(monkeypatch):
    monkeypatch.delenv("SHOP_SECRET", raising=False)
    monkeypatch.setattr(shopcookie, "_EPHEMERAL", None)
    token = shopcookie.sign("C-0001", "uid")
    assert shopcookie.unsign(token, "uid") == "C-0001"      # 같은 프로세스 안에서는 통한다
    assert shopcookie._EPHEMERAL is not None
