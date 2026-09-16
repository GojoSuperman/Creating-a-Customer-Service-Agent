# -*- coding: utf-8 -*-
"""현재 턴의 발신자(통화 고객) 컨텍스트.

find_customer 는 이 값을 근거로 "발신번호로 식별된 고객 본인" 여부를 판정한다.
Pipeline.turn 이 그래프를 호출하는 동안만 값을 채우고, 끝나면 반드시 되돌린다
(스레드/코루틴 경계를 넘지 않도록 contextvars 로 관리).
"""
from contextvars import ContextVar
from typing import Optional

current_caller: ContextVar[Optional[dict]] = ContextVar("current_caller", default=None)
