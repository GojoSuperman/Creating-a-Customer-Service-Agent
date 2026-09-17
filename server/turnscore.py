# -*- coding: utf-8 -*-
"""실시간 턴 채점. 답변 생성과 분리된 별도 요청에서, 방금 턴 하나를 4가지 기준으로 점수 매긴다.

eval/judge.py 와 다른 점: judge 는 정답셋(reference·must)이 있어야 채점할 수 있는 "합격/불합격"
판정이라 실시간에는 못 쓴다. 여기서는 정답이 없으므로, 질문·답변·라우트·호출한 도구·가드레일
결과 같은 "이 턴 안에서 확인 가능한 재료"만으로 1~5점씩 매긴다.

judge.py 와 같은 방식으로 채점기를 호출 가능 객체(messages -> TurnScore)로 주입한다
(with_structured_output). 테스트는 가짜 채점기를 넣어 실제 LLM 을 부르지 않는다.
"""
import json
from typing import Callable, Optional

from pydantic import BaseModel, Field, model_validator

SYSTEM = """\
너는 고객상담 품질 평가자다. 아래 전화 상담 답변을 다음 4가지 기준으로 각각 1~5점으로 매기고,
한 문장으로 이유를 써라.

기준:
- 매뉴얼 준수: 정책에 없는 수치·조건을 지어내지 않았는가. 금지된 안내를 하지 않았는가.
- 조회 근거: 도구를 불러야 할 때 불렀는가. 조회 결과와 다른 값을 말하지 않았는가.
- 말투·간결성: 전화 상담답게 짧고 자연스러운가.
- 고객 응대: 되물어야 할 때 되물었는가. 공감·사과 표현이 적절한가.

너는 이 턴의 정답을 모른다. 아래 주어진 재료(질문·답변·라우트·호출한 도구·가드레일 결과·매뉴얼
요약)만으로 판단하고, 재료에 없는 사실을 지어내 채점하지 마라.
"""


class TurnScore(BaseModel):
    """4항목 채점 결과. total 은 모델 출력을 믿지 않고 서버에서 4항목 합으로 다시 계산한다."""
    manual: int = Field(ge=1, le=5, description="매뉴얼 준수 점수")
    evidence: int = Field(ge=1, le=5, description="조회 근거 점수")
    tone: int = Field(ge=1, le=5, description="말투·간결성 점수")
    service: int = Field(ge=1, le=5, description="고객 응대 점수")
    reason: str = Field(description="판정 근거 한 문장")
    total: int = 0

    @model_validator(mode="after")
    def _derive_total(self) -> "TurnScore":
        self.total = self.manual + self.evidence + self.tone + self.service
        return self


def build_score_messages(question: str, answer: str, tool_results: dict, route: str,
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


def make_scorer(model: str, api_key: Optional[str] = None) -> Callable[[list], TurnScore]:
    """model·api_key 로 채점기(messages -> TurnScore)를 만든다.

    키 결정·캐싱은 기존 server/llmkey.py 를 그대로 재사용한다 — 동일한 (model, api_key)
    조합은 인스턴스를 공유하고, os.environ 을 건드리지 않아 다른 방문자의 키와 섞이지 않는다.
    """
    from server.llmkey import get_chat_model
    chain = get_chat_model(model, api_key).with_structured_output(TurnScore)
    return chain.invoke


def score_turn(scorer: Callable[[list], TurnScore], turn: dict,
              manual_rules: str = "") -> TurnScore:
    """턴 로그(server/pipeline.py 의 turn_logs 항목) 하나를 채점한다.

    턴 로그에는 실제 도구 조회 값이 아니라 호출한 도구 이름만 남는다(tools: [str, ...]).
    조회 결과 원문은 이 시점에 이미 사라져 있으므로, "호출됨"만 표시해 채점기가 실제로
    도구를 썼는지는 알 수 있게 하되 값을 지어내지 않게 한다.
    """
    tool_results = {name: "(호출됨, 값은 이 로그에 없음)" for name in (turn.get("tools") or [])}
    guardrail = {"ok": turn.get("guardrail_ok"), "violations": []}
    msgs = build_score_messages(
        question=turn.get("q", ""), answer=turn.get("a", ""), tool_results=tool_results,
        route=turn.get("route") or "", guardrail=guardrail, manual_rules=manual_rules)
    return scorer(msgs)
