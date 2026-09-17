# -*- coding: utf-8 -*-
"""매뉴얼을 장 단위로 쪼개고, 라우트에 필요한 장만 골라 컨텍스트를 만든다.

전문을 넣지 않는 이유: 입력이 길어 비용·지연이 늘고, 관련 없는 정책이 오답을 유도한다
(배송 문의에 반품 배송비가 섞여 들어가는 식).
"""
import re
from functools import lru_cache

from server.domain import Domain, DomainError


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


def validate_sections(domain: Domain) -> None:
    """always_sections 와 각 라우트 sections 가 매뉴얼에 실제로 존재하는지 확인한다.

    조용히 빠지는 장(章)은 답변 근거가 몰래 사라지는 것과 같으므로, 로드 시점에
    바로 DomainError 로 터뜨린다.
    """
    secs = split_sections(domain.policy_text)
    for key in domain.always_sections:
        if key not in secs:
            raise DomainError(f"always_sections 의 '{key}' 가 매뉴얼에 없습니다")
    for route_name, route_def in domain.routes.items():
        for key in route_def.sections:
            if key not in secs:
                raise DomainError(f"routes.{route_name}.sections 의 '{key}' 가 매뉴얼에 없습니다")


def build_context(domain: Domain, route: str) -> str:
    secs = _sections_for(domain.policy_text)
    keys = domain.always_sections + domain.routes[route].sections
    chosen = [secs[k] for k in keys if k in secs]
    return "\n\n".join([secs["_header"]] + chosen)


def _title_of(section_text: str) -> str:
    """섹션 본문(예: "## 2. 주문·구매 문의\n...")에서 사람이 읽을 장 제목만 뽑는다."""
    first_line = section_text.split("\n", 1)[0]
    return first_line[len("## "):].strip()


def section_titles(domain: Domain, route: str) -> dict[str, list[str]]:
    """그 라우트가 실제로 프롬프트에 넣는 장의 사람이 읽을 제목을, 공통 장(always_sections)과
    라우트 전용 장(routes[route].sections)으로 나눠 돌려준다.

    build_context 와 같은 소스(_sections_for)를 쓰고 같은 필터("k in secs")를 적용해서,
    여기서 "쓰인다"고 말하는 장과 build_context 가 실제로 프롬프트에 넣는 장이 항상 일치하게
    한다 — 섹션을 자르는 로직 자체는 여기서 새로 정의하지 않는다.
    """
    secs = _sections_for(domain.policy_text)

    def titles(keys: list[str]) -> list[str]:
        return [_title_of(secs[k]) for k in keys if k in secs]

    return {
        "always": titles(domain.always_sections),
        "route": titles(domain.routes[route].sections),
    }
