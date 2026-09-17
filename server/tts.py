# -*- coding: utf-8 -*-
"""서버 측 음성 합성. 브라우저 TTS 보다 한국어 발음이 자연스럽다.

app.create_app 에 `tts(text, api_key=None) -> bytes(mp3)` 호출 가능 객체로 주입한다.
없으면 /api/tts 는 501. api_key 를 안 주면(로컬 개발) 서버 환경변수 OPENAI_API_KEY 를 쓴다.
"""
import threading
from typing import Callable, Optional


def make_openai_tts(model: str, voice: str) -> Callable[[str, Optional[str]], bytes]:
    from openai import OpenAI

    lock = threading.Lock()
    clients: dict[Optional[str], OpenAI] = {}

    def _client_for(api_key: Optional[str]) -> OpenAI:
        # 라우터/답변기의 get_chat_model 과 같은 이유로 api_key 별 클라이언트를 캐싱한다 —
        # 전역 환경변수를 건드리지 않아야 동시 요청의 키가 섞이지 않는다.
        with lock:
            client = clients.get(api_key)
        if client is None:
            client = OpenAI(api_key=api_key) if api_key else OpenAI()
            with lock:
                clients.setdefault(api_key, client)
                client = clients[api_key]
        return client

    def synthesize(text: str, api_key: Optional[str] = None) -> bytes:
        client = _client_for(api_key)
        r = client.audio.speech.create(model=model, voice=voice, input=text, response_format="mp3")
        return r.content
    return synthesize
