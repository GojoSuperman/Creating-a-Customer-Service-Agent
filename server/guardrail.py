# -*- coding: utf-8 -*-
"""답변 속 숫자의 출처를 역추적한다.

허용 집합 = 조회 결과 + 도메인 고정값 + 한 단계 산술 유도값.
출처를 못 찾는 숫자가 있으면 위반. 여기에 미확정값(null / is_confirmed=false)을
확답하는 패턴 검사를 더한다. 숫자·패턴이 아닌 오류는 못 잡는다 — 그건 정답셋 채점기 몫이다.
"""
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from server.domain import Domain

VIOLATION_UNSOURCED = "출처 불명 수치"
VIOLATION_NO_TOOL = "툴 미호출 단정"
VIOLATION_ASSERT_UNCONFIRMED = "미확정값 확답"

# 미확정 필드별로, 답변에서 "확답"으로 간주할 패턴
ASSERTION_PATTERNS = {
    "expected_date": [r"예정일은\s*[^.。]*?(입니다|이에요|예요)", r"\d+월\s*\d+일에?\s*(입고|재입고)"],
    "fault_party": [r"(고객|구매자)\s*(님)?\s*(이|께서)?\s*부담하(셔야|시게|십니다)",
                    r"(단순\s*변심|하자)(이므로|으로)\s*.*부담"],
    "shipping_fee_bearer": [r"(고객|구매자)\s*(님)?\s*(이|께서)?\s*부담하(셔야|시게|십니다)",
                            r"배송비는?\s*(고객|저희|판매자)\s*(님)?\s*(이|가)?\s*부담"],
    "inspection_result": [r"검품\s*결과\s*(하자|정상)(로|으로)\s*(확인|판정)"],
}
UNCONFIRMED_HEDGE = r"(확정되지\s*않|미확정|아직\s*확인|검품\s*(후|이\s*완료되)|정해지지\s*않)"


@dataclass
class GuardResult:
    ok: bool
    violations: list = field(default_factory=list)
    numbers_in_answer: list = field(default_factory=list)
    from_tools: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def numbers_in(obj) -> set[int]:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    return {int(m) for m in re.findall(r"\d+", text)}


def allowed_numbers(tool_results: dict, domain: Domain) -> tuple[set[int], set[int]]:
    allowed: set[int] = set(domain.small_numbers_allowed)
    for v in domain.fixed_values.values():
        if isinstance(v, int):
            allowed.add(v)
        elif isinstance(v, list):
            allowed.update(x for x in v if isinstance(x, int))
    tool_nums: set[int] = set()
    for r in (tool_results or {}).values():
        tool_nums |= numbers_in(r)
    allowed |= tool_nums
    base = sorted(allowed)
    for a in base:
        for b in base:
            if a > b:
                allowed.add(a - b)
            allowed.add(a + b)
    return allowed, tool_nums


def _unconfirmed_fields(tool_results: dict) -> set[str]:
    """조회 결과에서 '아직 정해지지 않음'을 뜻하는 필드 이름을 모은다."""
    out = set()
    for r in (tool_results or {}).values():
        if not isinstance(r, dict):
            continue
        if r.get("is_confirmed") is False:
            out.add("expected_date")
        for k in ("fault_party", "shipping_fee_bearer", "inspection_result"):
            if k in r and r[k] is None:
                out.add(k)
    return out


def check(answer: str, tool_results: dict, domain: Domain, min_check: int = 1000) -> GuardResult:
    allowed, tool_nums = allowed_numbers(tool_results, domain)
    found = numbers_in(answer)
    suspicious = sorted(n for n in found if n >= min_check and n not in allowed)
    violations = []
    if suspicious:
        violations.append({"type": VIOLATION_UNSOURCED,
                           "detail": f"조회 결과·매뉴얼 고정값에 없는 숫자: {suspicious}"})
    if re.search(r"무료\s?배송", answer) and re.search(r"\d[\d,]*\s*원\s*(이상|부터)", answer):
        if "get_shipping_policy" not in (tool_results or {}):
            violations.append({"type": VIOLATION_NO_TOOL,
                               "detail": "무료배송 기준액을 get_shipping_policy 조회 없이 단정"})
    hedged = re.search(UNCONFIRMED_HEDGE, answer) is not None
    for fld in _unconfirmed_fields(tool_results):
        for pat in ASSERTION_PATTERNS.get(fld, []):
            if re.search(pat, answer) and not hedged:
                violations.append({"type": VIOLATION_ASSERT_UNCONFIRMED,
                                   "detail": f"{fld} 가 미확정인데 확답 패턴 발견: /{pat}/"})
                break
    return GuardResult(ok=not violations, violations=violations,
                       numbers_in_answer=sorted(found), from_tools=sorted(tool_nums))


def log_violation(logs_dir: Path, record: dict) -> None:
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with (logs_dir / "guardrail.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
