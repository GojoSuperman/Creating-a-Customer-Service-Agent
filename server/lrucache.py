# -*- coding: utf-8 -*-
"""스레드-세이프 고정 크기 LRU 캐시.

공개 배포에서는 방문자가 헤더(X-OpenAI-Key)만 바꿔 서로 다른 키로 반복 호출할 수 있다.
키마다 LLM/TTS 클라이언트를 만들어 캐싱하는 곳(server/pipeline.py, server/llmkey.py,
server/tts.py)이 무제한 dict 를 쓰면 키 개수만큼 메모리가 계속 자라 컨테이너가 OOM 으로
죽는다. 이 클래스는 상한을 넘으면 가장 오래 쓰이지 않은(LRU) 항목부터 버려 캐시 크기를
고정 상한 이하로 유지한다. 표준 라이브러리(collections.OrderedDict)만 쓴다.
"""
import collections
import threading
from typing import Callable, Generic, Hashable, Optional, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class LRUCache(Generic[K, V]):
    def __init__(self, maxsize: int):
        if maxsize < 1:
            raise ValueError("maxsize 는 1 이상이어야 합니다")
        self._maxsize = maxsize
        self._data: "collections.OrderedDict[K, V]" = collections.OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def get(self, key: K) -> Optional[V]:
        with self._lock:
            v = self._data.get(key)
            if v is not None:
                self._data.move_to_end(key)
            return v

    def get_or_create(self, key: K, factory: Callable[[], V]) -> V:
        """key 가 있으면 그것을, 없으면 factory() 로 만들어 캐싱한 뒤 돌려준다.

        factory 는 락 밖에서 호출한다(모델 생성 등 비용이 락을 오래 잡지 않도록). 동시에
        같은 키로 여러 스레드가 들어오면 먼저 저장에 성공한 값을 모두가 쓴다(중복 생성된
        값은 버려지되, 그 값 자체의 부작용은 없다 — 여기 쓰이는 팩토리들은 순수 생성자다).
        """
        cached = self.get(key)
        if cached is not None:
            return cached
        new_v = factory()
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            self._data[key] = new_v
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)
        return new_v
