# -*- coding: utf-8 -*-
"""server/tts.py: 방문자 키별 OpenAI 클라이언트 캐시 상한(C1)을 고정한다."""
import types
import sys

import pytest

from server import tts as tts_module
from server.lrucache import LRUCache


class _FakeResp:
    content = b"mp3-bytes"


class _FakeOpenAI:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.audio = types.SimpleNamespace(
            speech=types.SimpleNamespace(create=lambda **kw: _FakeResp()))


@pytest.fixture(autouse=True)
def _fake_openai_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))


@pytest.fixture
def _cache_spy(monkeypatch):
    """make_openai_tts 내부에서 만드는 clients LRUCache 인스턴스를 밖에서 들여다보기
    위해 생성 시점을 가로챈다(_client_for 자체는 클로저라 직접 접근할 수 없다)."""
    captured = {}
    real_init = LRUCache.__init__

    def spy_init(self, maxsize):
        real_init(self, maxsize)
        captured["cache"] = self

    monkeypatch.setattr(LRUCache, "__init__", spy_init)
    return captured


def test_client_cache_maxsize_matches_module_constant(_cache_spy):
    tts_module.make_openai_tts("tts-1", "nova")
    cache = _cache_spy["cache"]
    assert cache._maxsize == tts_module._CLIENT_CACHE_MAXSIZE


def test_synthesize_with_many_keys_keeps_client_cache_capped(_cache_spy):
    """서로 다른 방문자 키 N+10 개로 synthesize() 를 불러도 클라이언트 캐시 크기가
    상한을 넘지 않는다."""
    synth = tts_module.make_openai_tts("tts-1", "nova")
    cache = _cache_spy["cache"]
    for i in range(tts_module._CLIENT_CACHE_MAXSIZE + 10):
        synth("안녕하세요", api_key=f"sk-cap-test-{i}")
    assert len(cache) == tts_module._CLIENT_CACHE_MAXSIZE


def test_synthesize_reuses_client_for_same_key(_cache_spy):
    synth = tts_module.make_openai_tts("tts-1", "nova")
    cache = _cache_spy["cache"]
    synth("안녕하세요", api_key="sk-same-key-1234567890")
    synth("반갑습니다", api_key="sk-same-key-1234567890")
    assert len(cache) == 1


def test_client_cache_does_not_store_raw_api_key(_cache_spy):
    synth = tts_module.make_openai_tts("tts-1", "nova")
    cache = _cache_spy["cache"]
    secret = "sk-super-secret-value-12345"
    synth("안녕하세요", api_key=secret)
    assert secret not in cache._data
