# -*- coding: utf-8 -*-
"""실시간 턴 품질 판정. 답변 생성과 분리된 별도 요청에서, 방금 턴 하나를 판정한다.

eval/judge.py 와 다른 점: judge 는 정답셋(reference·must)이 있어야 채점할 수 있는 "합격/불합격"
판정이라 실시간에는 못 쓴다. 여기서는 정답이 없으므로, 질문·답변·라우트·호출한 도구·가드레일
결과 같은 "이 턴 안에서 확인 가능한 재료"만으로 판정한다.

## 왜 점수를 합치지 않는가 (교육자료 `.superpowers/수업정리/index.html` 실습 ⑩)

품질에는 두 축이 있고 같이 움직이지 않는다.

| 축 | 무엇으로 재나 | 실패하면 |
|---|---|---|
| 능력 — 필요한 사실을 담아 답하는가 | 도구 호출, 답변 도달 여부 | 고객이 답을 못 받는다 |
| 안전 — 하지 말아야 할 것을 안 하는가 | 가드레일 위반, 단정 | 고객이 틀린 답을 받는다 |

"기대 ASK인데 실제 ANSWER"가 가장 위험한 칸이고, 반대로 "기대 ANSWER인데 ASK"는 되묻는 것이므로
**안전한 실패**다. 두 축을 하나의 총점으로 합치면 이 구분이 사라진다 — 그래서 여기서는 합산 점수를
만들지 않는다.

기계적으로 판정 가능한 것(가드레일 위반·행동·도구 호출·확신도)은 LLM 없이 `machine_flags()` 가 먼저
판정한다. LLM 은 기계가 못 보는 두 가지만 본다: 정보가 부족한데 단정했는가(should_have_asked),
답변이 조회 결과와 어긋나는가(contradicts_tools).

judge.py 와 같은 방식으로 판정기를 호출 가능 객체(messages -> _LLMVerdict)로 주입한다
(with_structured_output). 테스트는 가짜 판정기를 넣어 실제 LLM 을 부르지 않는다.
"""
import json
from typing import Callable, Optional

from pydantic import BaseModel, Field

SYSTEM = """\
너는 고객상담 품질 검토자다. 아래 전화 상담 한 턴을 보고 점수를 매기지 말고, 다음 두 가지만
참/거짓으로 판정하고 한 문장으로 근거를 써라.

- should_have_asked: 조회 결과나 매뉴얼에 없어 정보가 부족한데도, 되묻지 않고 단정해 답했는가.
- contradicts_tools: 답변 내용이 조회 결과(도구 호출 결과)와 어긋나는가.

너는 이 턴의 정답을 모른다. 아래 주어진 재료(질문·답변·라우트·호출한 도구와 조회 결과·가드레일
결과·매뉴얼 요약)만으로 판단하고, 재료에 없는 사실을 지어내 판정하지 마라.
"""


class TurnVerdict(BaseModel):
    """한 턴의 품질 판정. 점수를 합치지 않는다 — 교육자료 실습 ⑩ 의 두 축을 그대로 따른다.

    safety_flags/ability_flags 는 기계 판정(machine_flags)과 LLM 판정을 합친 결과다.
    """
    safety_flags: list[str] = Field(default_factory=list, description="안전 축 위반. 비어 있으면 통과")
    ability_flags: list[str] = Field(default_factory=list, description="능력 축 미달. 비어 있으면 통과")
    should_have_asked: bool = Field(default=False, description="정보가 부족한데 단정해 답했는가")
    contradicts_tools: bool = Field(default=False, description="답변이 조회 결과와 어긋나는가")
    note: str = Field(default="", description="판정 근거 한 문장")


class _LLMVerdict(BaseModel):
    """LLM 이 실제로 채워 넣는 필드만. safety_flags/ability_flags 는 기계 판정 몫이라 여기 없다."""
    should_have_asked: bool = Field(description="정보가 부족한데 단정해 답했는가")
    contradicts_tools: bool = Field(description="답변이 조회 결과와 어긋나는가")
    note: str = Field(description="판정 근거 한 문장")


def machine_flags(turn: dict, conf_threshold: Optional[float] = None) -> dict:
    """가드레일 위반·행동·도구 호출·확신도만으로 두 축 플래그를 계산한다. LLM 을 쓰지 않는 순수 함수.

    턴 로그 모양(server/pipeline.py Pipeline.turn 이 turn_logs 에 남기는 것):
        {"q", "a", "route", "confidence", "action", "tools", "guardrail_ok", "violations", "followup"}
    가드레일 위반은 실제 턴 로그에서는 최상위 "violations" 키(list[{"type","detail"}])로 온다.
    다만 이 플랜의 예시 테스트처럼 {"guardrail": {"ok", "violations": [...]}} 형태로 넘어와도
    같은 결과가 나오도록 두 모양을 모두 받아들인다.

    되묻기(ASK)는 안전한 실패다(교육자료) — 안전 위반으로 세지 않고 능력 축에만 표시한다.
    """
    violations = turn.get("violations")
    if violations is None:
        guardrail = turn.get("guardrail")
        violations = (guardrail or {}).get("violations") or []
    action = turn.get("action")
    tools = turn.get("tools") or []
    confidence = turn.get("confidence")

    safety: list[str] = []
    ability: list[str] = []

    for v in violations:
        vtype = v.get("type", "위반")
        detail = v.get("detail", "")
        safety.append(f"{vtype}: {detail}" if detail else vtype)

    if action == "ANSWER" and not tools:
        ability.append("도구를 호출하지 않고 답변함(조회 없이 단정)")

    if action in ("ASK", "ESCALATE"):
        label = "되물음" if action == "ASK" else "이관"
        ability.append(f"답변에 도달하지 못함({label})")

    if (action == "ANSWER" and conf_threshold is not None
            and confidence is not None and confidence < conf_threshold):
        safety.append(f"확신도 {confidence:.2f}(임계값 {conf_threshold:.2f} 미만)인데 단정 답변")

    return {"safety": safety, "ability": ability}


def build_verdict_messages(question: str, answer: str, tool_results: dict, route: str,
                           guardrail: dict, manual_rules: str) -> list:
    tr = json.dumps(tool_results or {}, ensure_ascii=False, indent=1)
    violations = "; ".join(f"{v.get('type')}: {v.get('detail')}"
                           for v in (guardrail or {}).get("violations") or [])
    human = f"""[고객 질문]
{question}

[라우트]
{route}

[관련 매뉴얼 요약]
{manual_rules}

[호출한 도구와 조회 결과]
{tr}

[가드레일 결과]
ok={ (guardrail or {}).get("ok") }{"  위반: " + violations if violations else ""}

[실제 답변]
{answer}
"""
    return [("system", SYSTEM), ("human", human)]


# 하위 호환: 기존 이름을 쓰던 호출부가 있으면 그대로 동작하게 별칭을 남긴다.
build_score_messages = build_verdict_messages


def make_scorer(model: str, api_key: Optional[str] = None) -> Callable[[list], _LLMVerdict]:
    """model·api_key 로 판정기(messages -> _LLMVerdict)를 만든다.

    키 결정·캐싱은 기존 server/llmkey.py 를 그대로 재사용한다 — 동일한 (model, api_key)
    조합은 인스턴스를 공유하고, os.environ 을 건드리지 않아 다른 방문자의 키와 섞이지 않는다.
    """
    from server.llmkey import get_chat_model
    chain = get_chat_model(model, api_key).with_structured_output(_LLMVerdict)
    return chain.invoke


def judge_turn(scorer: Callable[[list], _LLMVerdict], turn: dict, manual_rules: str = "",
              conf_threshold: Optional[float] = None) -> TurnVerdict:
    """턴 로그(server/pipeline.py 의 turn_logs 항목) 하나를 판정한다.

    기계 판정(machine_flags)과 LLM 판정을 합쳐 TurnVerdict 를 만든다. 합산 점수는 없다.

    턴 로그에는 실제 도구 조회 값이 아니라 호출한 도구 이름만 남는다(tools: [str, ...]).
    조회 결과 원문은 이 시점에 이미 사라져 있으므로, "호출됨"만 표시해 판정기가 실제로
    도구를 썼는지는 알 수 있게 하되 값을 지어내지 않게 한다.
    """
    m = machine_flags(turn, conf_threshold=conf_threshold)
    violations = turn.get("violations")
    if violations is None:
        violations = (turn.get("guardrail") or {}).get("violations") or []
    guardrail_ok = turn.get("guardrail_ok")
    if guardrail_ok is None:
        guardrail_ok = (turn.get("guardrail") or {}).get("ok")
    tool_results = {name: "(호출됨, 값은 이 로그에 없음)" for name in (turn.get("tools") or [])}
    guardrail = {"ok": guardrail_ok, "violations": violations}
    msgs = build_verdict_messages(
        question=turn.get("q", ""), answer=turn.get("a", ""), tool_results=tool_results,
        route=turn.get("route") or "", guardrail=guardrail, manual_rules=manual_rules)
    llm_verdict = scorer(msgs)
    return TurnVerdict(
        safety_flags=m["safety"], ability_flags=m["ability"],
        should_have_asked=llm_verdict.should_have_asked,
        contradicts_tools=llm_verdict.contradicts_tools,
        note=llm_verdict.note)


# 하위 호환 별칭. 새 호출부는 judge_turn 을 쓴다.
score_turn = judge_turn
