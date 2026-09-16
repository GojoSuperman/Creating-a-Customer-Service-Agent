# -*- coding: utf-8 -*-
"""매뉴얼을 장 단위로 쪼개고, 라우트에 필요한 장만 골라 컨텍스트를 만든다.

전문을 넣지 않는 이유: 입력이 길어 비용·지연이 늘고, 관련 없는 정책이 오답을 유도한다
(배송 문의에 반품 배송비가 섞여 들어가는 식).
"""
import re
from functools import lru_cache

from server.domain import Domain


def split_sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## ", text, flags=re.M)
    out = {"_header": parts[0].strip()}
    for p in parts[1:]:
        title = p.split("\n", 1)[0].strip()
        if title.startswith("부록"):
            continue
        m = re.match(r"(\d+)\.", title)
        key = m.group(1) if m else title
        out[key] = "## " + p.rstrip()
    return out


@lru_cache(maxsize=8)
def _sections_for(policy_text: str) -> dict[str, str]:
    return split_sections(policy_text)


def build_context(domain: Domain, route: str) -> str:
    secs = _sections_for(domain.policy_text)
    keys = domain.always_sections + domain.routes[route].sections
    chosen = [secs[k] for k in keys if k in secs]
    return "\n\n".join([secs["_header"]] + chosen)
