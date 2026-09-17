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
MAX_PRODUCT_ID_LEN = 40
MAX_OPTION_LEN = 80
MAX_COOKIE_BYTES = 3800  # 브라우저 쿠키 4096바이트 한도에서 이름·속성 오버헤드를 뺀 여유값

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


def sign(value: str, purpose: str) -> str:
    """purpose 를 값에 묶어 서명한다 — 한 용도(예: 세션)로 만든 토큰이 다른 용도(예: 장바구니)로 재사용되지 못하게."""
    payload = _b64e(f"{purpose}:{value}".encode("utf-8"))
    mac = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_b64e(mac)}"


def unsign(token, purpose):
    """서명이 맞고 purpose 도 일치해야 원래 값, 아니면 None. 예외를 던지지 않는다."""
    if not token or "." not in token:
        return None
    payload, mac = token.rsplit(".", 1)
    try:
        expected = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(mac), expected):
            return None
        text = _b64d(payload).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    prefix = f"{purpose}:"
    if not text.startswith(prefix):
        return None
    return text[len(prefix):]


def _normalize_items(items):
    """원본 리스트에서 형식이 맞는 줄만 골라 길이를 절단한다 (줄 단위 방어 — 전체 크기 상한은 별도)."""
    if not isinstance(items, list):
        return []
    out = []
    for item in items[:MAX_CART_LINES]:
        if not isinstance(item, dict):
            continue
        product_id = item.get("product_id")
        if not isinstance(product_id, str) or not product_id:
            continue
        qty = item.get("qty")
        if not isinstance(qty, int) or isinstance(qty, bool):
            continue
        option = item.get("option")
        if not isinstance(option, str) or not option:
            option = None
        else:
            option = option[:MAX_OPTION_LEN]
        out.append({"product_id": product_id[:MAX_PRODUCT_ID_LEN],
                    "option": option,
                    "qty": max(1, min(MAX_QTY, qty))})
    return out


def _sign_cart(normalized):
    return sign(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")), "cart")


def _fit_within_cookie_cap(normalized):
    """서명된 토큰이 MAX_COOKIE_BYTES 를 넘으면 뒤쪽 줄부터 하나씩 버리고 다시 서명한다.

    줄 하나도 안 들어갈 만큼 커도(이론상) 결국 빈 리스트로 수렴해 항상 한도 안의 토큰을 돌려준다.
    """
    token = _sign_cart(normalized)
    while len(token.encode("utf-8")) > MAX_COOKIE_BYTES and normalized:
        normalized = normalized[:-1]
        token = _sign_cart(normalized)
    return normalized, token


def dump_cart(items) -> str:
    normalized = _normalize_items(items)
    _, token = _fit_within_cookie_cap(normalized)
    return token


def load_cart(token):
    """서명·형식이 조금이라도 이상하면 빈 장바구니로 돌려준다 (오류 화면 대신)."""
    raw = unsign(token, "cart")
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    normalized = _normalize_items(items)
    trimmed, _ = _fit_within_cookie_cap(normalized)
    return trimmed
