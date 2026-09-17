# -*- coding: utf-8 -*-
"""server/llmkey.py: 키별 LLM 캐시 상한(C1)과 키 리댁션(I1)을 고정한다."""
import hashlib

import pytest

from server import llmkey
from server.lrucache import LRUCache


class _FakeLLM:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch):
    """전역 _CACHE 를 테스트마다 새로 갈아 끼워 테스트 간 오염을 막는다."""
    monkeypatch.setattr(llmkey, "_CACHE", LRUCache(maxsize=llmkey._CACHE_MAXSIZE))
    yield


@pytest.fixture(autouse=True)
def _fake_init_chat_model(monkeypatch):
    monkeypatch.setattr("langchain.chat_models.init_chat_model", lambda model, **kw: _FakeLLM(**kw))


def test_get_chat_model_cache_is_capped():
    """방문자 키 N+10 개로 호출해도 캐시 크기가 _CACHE_MAXSIZE 를 넘지 않는다."""
    for i in range(llmkey._CACHE_MAXSIZE + 10):
        llmkey.get_chat_model("gpt-4.1-mini", f"sk-cap-test-{i}")
    assert len(llmkey._CACHE) == llmkey._CACHE_MAXSIZE


def test_cache_reuses_instance_for_same_key():
    a = llmkey.get_chat_model("gpt-4.1-mini", "sk-same-key-1234567890")
    b = llmkey.get_chat_model("gpt-4.1-mini", "sk-same-key-1234567890")
    assert a is b


def test_cache_key_uses_hash_not_raw_api_key():
    """캐시 딕셔너리 안에 방문자 키 원문이 그대로 남으면 안 된다(해시만 남아야 함)."""
    secret = "sk-super-secret-abcdefg"
    llmkey.get_chat_model("gpt-4.1-mini", secret)
    assert secret not in llmkey._CACHE._data
    hashed_entry = ("gpt-4.1-mini", hashlib.sha256(secret.encode("utf-8")).hexdigest())
    assert hashed_entry in llmkey._CACHE._data


def test_uncached_call_still_works_and_does_not_grow_cache():
    before = len(llmkey._CACHE)
    llmkey.get_chat_model("gpt-4.1-mini", "sk-throwaway-key", cache=False)
    assert len(llmkey._CACHE) == before


def test_redact_removes_sk_prefixed_key():
    assert llmkey.redact("에러: sk-abcdefghijklmno 가 유효하지 않습니다") == "에러: [REDACTED] 가 유효하지 않습니다"


def test_redact_removes_masked_key_fragment():
    assert llmkey.redact("Incorrect API key: sk-fake-*****************zzzz.") == \
        "Incorrect API key: [REDACTED]."


def test_redact_removes_non_sk_prefixed_key_via_literal_replace():
    """정규식은 'sk-' 접두가 없는 키를 못 잡는다 — current_request_key 이중 방어로 잡아야 한다."""
    odd_key = "NOSKPREFIX1234567890abcdef"
    token = llmkey.current_request_key.set(odd_key)
    try:
        assert llmkey.redact(f"upstream rejected key: {odd_key}") == "upstream rejected key: [REDACTED]"
    finally:
        llmkey.current_request_key.reset(token)


def test_redact_removes_spaced_key_via_literal_replace():
    """정규식은 키 중간에 공백이 섞인 형태를 못 잡는다 — literal 치환으로 잡아야 한다."""
    spaced_key = "sk-abc def-1234567890"
    token = llmkey.current_request_key.set(spaced_key)
    try:
        assert llmkey.redact(f"key={spaced_key}") == "key=[REDACTED]"
    finally:
        llmkey.current_request_key.reset(token)


def test_redact_without_current_request_key_leaves_unmatched_text():
    """literal 치환은 어디까지나 '이중 방어' 다 — contextvar 가 비어 있으면 정규식만 동작한다
    (이 테스트는 literal 치환을 실수로 지워도 sk- 케이스만으로는 안 잡힌다는 걸 보여준다,
    회귀 확인용이 아니라 동작 이해용)."""
    assert llmkey.current_request_key.get() is None
    assert "NOSKPREFIX" in llmkey.redact("key: NOSKPREFIX1234567890")
