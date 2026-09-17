import os

import pytest

from server import shopcookie


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch):
    monkeypatch.setenv("SHOP_SECRET", "테스트용-고정-키")


def test_sign_unsign_roundtrip():
    token = shopcookie.sign("C-0001")
    assert token != "C-0001" and "." in token
    assert shopcookie.unsign(token) == "C-0001"


def test_unsign_rejects_tampered_or_garbage():
    token = shopcookie.sign("C-0001")
    payload, mac = token.rsplit(".", 1)
    forged = shopcookie.sign("C-9999").rsplit(".", 1)[0] + "." + mac
    assert shopcookie.unsign(forged) is None          # 값만 바꾼 위조
    assert shopcookie.unsign(payload + ".AAAA") is None
    assert shopcookie.unsign("점없는문자열") is None
    assert shopcookie.unsign(None) is None
    assert shopcookie.unsign("") is None


def test_unsign_fails_with_other_key(monkeypatch):
    token = shopcookie.sign("C-0001")
    monkeypatch.setenv("SHOP_SECRET", "다른-키")
    assert shopcookie.unsign(token) is None


def test_cart_roundtrip_keeps_korean_option():
    items = [{"product_id": "P1001", "option": "사이즈 M", "qty": 2}]
    assert shopcookie.load_cart(shopcookie.dump_cart(items)) == items


def test_load_cart_is_forgiving():
    assert shopcookie.load_cart(None) == []
    assert shopcookie.load_cart("깨진.토큰") == []
    assert shopcookie.load_cart(shopcookie.sign("{잘못된 json")) == []
    assert shopcookie.load_cart(shopcookie.sign('{"not": "a list"}')) == []


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


def test_ephemeral_key_is_used_when_env_missing(monkeypatch):
    monkeypatch.delenv("SHOP_SECRET", raising=False)
    monkeypatch.setattr(shopcookie, "_EPHEMERAL", None)
    token = shopcookie.sign("C-0001")
    assert shopcookie.unsign(token) == "C-0001"      # 같은 프로세스 안에서는 통한다
    assert shopcookie._EPHEMERAL is not None
