# -*- coding: utf-8 -*-
"""요청별 OpenAI 키로 LLM 인스턴스를 만들고 캐싱한다.

사용자가 화면 설정 모달에 넣은 자기 키를 요청마다 헤더(X-OpenAI-Key)로 받아 여기서
모델을 만든다. 절대로 os.environ 을 덮어쓰지 않는다 — 전역 환경변수를 바꾸면 동시에
들어온 다른 사용자의 요청이 이 요청의 키를 쓰게 되는 사고(키 섞임)가 나기 때문이다.
대신 (model, api_key) 조합마다 별도의 ChatOpenAI 인스턴스를 만들어 캐싱한다. 이 인스턴스는
invoke() 호출마다 상태를 바꾸지 않는(무상태) 러너블이라, 여러 스레드가 같은 인스턴스를
동시에 invoke() 해도 서로의 키가 섞이지 않는다 — 인스턴스 자체가 이미 하나의 키에 고정돼
있기 때문이다.
"""
import os
import re
import threading
from typing import Optional

_LOCK = threading.Lock()
_CACHE: dict[tuple, object] = {}

# OpenAI 키는 "sk-" 로 시작한다(프로젝트 키는 "sk-proj-..."). 예외 메시지 등에 원문이
# 섞여 나가는 사고를 막기 위한 최후 방어선으로 정규식 치환을 쓴다. OpenAI 쪽 인증 오류
# 메시지는 키를 완전히 원문 그대로 주지 않고 "sk-fake-*****zzzz" 처럼 가운데를 별표로
# 가려서 돌려주는 경우가 있는데, 그래도 앞뒤 일부가 실제 키 조각이라 같이 지운다
# (별표 "*" 도 매치 문자 집합에 포함).
_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9*_-]{6,}")


def resolve_api_key(header_key: Optional[str]) -> Optional[str]:
    """헤더로 받은 키가 있으면 그것을, 없으면 서버 환경변수(OPENAI_API_KEY)를 폴백으로 쓴다.

    로컬 개발처럼 서버에 .env 로 키가 이미 있는 경우를 위한 폴백이다. 실제로 어느 쪽 키를
    쓸지는(캐시 키 선택은) 헤더 원문을 그대로 넘겨야 하므로, 이 함수는 "키가 있는가"를
    판정할 때만 쓰고 실제 모델 생성에는 헤더 값(x_openai_key, None 이면 폴백)을 그대로 쓴다.
    """
    if header_key:
        return header_key
    return os.environ.get("OPENAI_API_KEY") or None


def get_chat_model(model: str, api_key: Optional[str], *, cache: bool = True):
    """model·api_key 조합의 ChatOpenAI 인스턴스를 돌려준다. api_key 가 None 이면 langchain 이
    프로세스 환경변수 OPENAI_API_KEY 를 읽는다(로컬 개발 폴백).

    캐싱 여부: 기본은 캐싱한다 — 같은 사용자가 여러 턴 동안 반복 호출하는 게 보통이라
    매번 클라이언트를 새로 만드는 비용(연결 풀 등)을 줄인다. 다만 "연결 확인" 버튼처럼
    아직 유효성이 확인되지 않은 일회성 키를 시험할 때는 cache=False 로 불러 캐시가 잘못된
    키로 무한정 자라는 것을 막는다.
    """
    key = (model, api_key)
    if cache:
        with _LOCK:
            llm = _CACHE.get(key)
        if llm is not None:
            return llm
    from langchain.chat_models import init_chat_model
    kwargs = {"temperature": 0, "timeout": 60, "max_retries": 8}
    if api_key:
        kwargs["api_key"] = api_key
    llm = init_chat_model(model, **kwargs)
    if cache:
        with _LOCK:
            _CACHE.setdefault(key, llm)
            llm = _CACHE[key]
    return llm


def redact(text: str) -> str:
    """문자열에서 OpenAI 키로 보이는 부분을 지운다. 전역 예외 핸들러가 예외 메시지를
    그대로 응답 본문에 실어 보내므로, 여기서 한 번 더 걸러 키가 섞여 나가는 걸 막는다."""
    if not text:
        return text
    return _KEY_PATTERN.sub("[REDACTED]", text)
