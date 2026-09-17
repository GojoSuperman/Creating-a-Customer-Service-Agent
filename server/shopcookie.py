# -*- coding: utf-8 -*-
"""쇼핑몰 쿠키 서명. 값은 비밀이 아니고 변조만 막으면 되므로 HMAC 서명만 한다."""
import base64
import hashlib
import hmac
import json
import os
import secrets

MAX_CART_LINES = 20
MAX_QTY = 99

_EPHEMERAL = None


def _secret() -> bytes:
    """SHOP_SECRET 이 없으면 프로세스 임시 키를 쓴다 (재시작하면 로그인·장바구니가 풀린다)."""
    global _EPHEMERAL
    env = os.environ.get("SHOP_SECRET")
    if env:
        return env.encode("utf-8")
    if _EPHEMERAL is None:
        _EPHEMERAL = secrets.token_hex(32)
        print("[shop] SHOP_SECRET 이 없어 임시 서명 키를 만들었습니다 — 재시작하면 로그인이 풀립니다.")
    return _EPHEMERAL.encode("utf-8")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(value: str) -> str:
    payload = _b64e(value.encode("utf-8"))
    mac = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_b64e(mac)}"


def unsign(token):
    """서명이 맞으면 원래 값, 아니면 None. 예외를 던지지 않는다."""
    if not token or "." not in token:
        return None
    payload, mac = token.rsplit(".", 1)
    try:
        expected = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(mac), expected):
            return None
        return _b64d(payload).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def dump_cart(items) -> str:
    return sign(json.dumps(items, ensure_ascii=False, separators=(",", ":")))


def load_cart(token):
    """서명·형식이 조금이라도 이상하면 빈 장바구니로 돌려준다 (오류 화면 대신)."""
    raw = unsign(token)
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    out = []
    for item in items[:MAX_CART_LINES]:
        if not isinstance(item, dict) or not item.get("product_id"):
            continue
        qty = item.get("qty")
        if not isinstance(qty, int) or isinstance(qty, bool):
            continue
        out.append({"product_id": str(item["product_id"]),
                    "option": item.get("option") or None,
                    "qty": max(1, min(MAX_QTY, qty))})
    return out
