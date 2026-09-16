# -*- coding: utf-8 -*-
"""서버 측 음성 합성. 브라우저 TTS 보다 한국어 발음이 자연스럽다.

app.create_app 에 `tts(text) -> bytes(mp3)` 호출 가능 객체로 주입한다. 없으면 /api/tts 는 501.
"""
from typing import Callable


def make_openai_tts(model: str, voice: str) -> Callable[[str], bytes]:
    from openai import OpenAI
    client = OpenAI()

    def synthesize(text: str) -> bytes:
        r = client.audio.speech.create(model=model, voice=voice, input=text, response_format="mp3")
        return r.content
    return synthesize
