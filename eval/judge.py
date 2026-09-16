# -*- coding: utf-8 -*-
"""LLM-as-judge. 규칙 채점이 'must 누락' 만으로 떨어뜨린 답변을 의미 기준으로 다시 본다.

매뉴얼 본문은 주지 않는다. 판정 기준은 골든셋의 rubric 과 reference 가 이미 담고 있고,
매뉴얼을 주면 judge 가 스스로 답을 지어내 채점하는 문제가 생긴다.
judge 는 호출 가능 객체(messages -> JudgeVerdict)로 주입해 테스트에서 가짜로 바꾼다.
"""
from typing import Callable

from pydantic import BaseModel, Field

SYSTEM = """\
너는 쇼핑몰 전화 상담 답변의 채점관이다. 규칙 채점기가 답변에서 특정 문자열(must 항목)을 찾지 못해
실패시킨 건을 다시 본다. 네가 판단할 것은 하나뿐이다.

누락된 must 항목 각각에 대해, 답변이 같은 사실을 다른 표현으로 전달했는가.

기준:
- 모범 답안(reference)과 채점 기준(rubric)에 적힌 사실이 답변에 같은 뜻으로 들어 있으면 통과다.
  예: must "품절" 인데 답변이 "재고가 없습니다" 라면 같은 사실이다.
- must 항목 하나라도 사실이 다르거나 빠졌으면 실패다. 숫자는 값이 같아야 한다.
- 답변이 reference 에 없는 사실을 추가로 말했다고 감점하지 않는다. 그것은 다른 검사(forbid)가 본다.
- 말투·길이·순서는 보지 않는다.
"""


class JudgeVerdict(BaseModel):
    passed: bool = Field(description="누락된 must 항목이 전부 다른 표현으로 전달되었으면 true")
    reason: str = Field(description="판정 근거 한 문장. 실패면 어느 must 항목이 왜 빠졌는지 쓴다")


def build_judge_messages(question: str, expect: dict, answer: str, missing: list) -> list:
    must_lines = "\n".join(f"- {m}" for m in missing)
    human = f"""[고객 질문]
{question}

[채점 기준 rubric]
{expect.get("rubric", "")}

[모범 답안 reference]
{expect.get("reference", "")}

[규칙 채점기가 찾지 못한 must 항목]
{must_lines}

[실제 답변]
{answer}
"""
    return [("system", SYSTEM), ("human", human)]


def make_judge(model: str) -> Callable[[list], JudgeVerdict]:
    from langchain.chat_models import init_chat_model
    chain = init_chat_model(model, temperature=0, timeout=60, max_retries=8).with_structured_output(JudgeVerdict)
    return chain.invoke


def judge_turn(judge: Callable[[list], JudgeVerdict], question: str, expect: dict,
               answer: str, missing: list) -> JudgeVerdict:
    return judge(build_judge_messages(question, expect, answer, missing))


def judge_self_check(judge: Callable[[list], JudgeVerdict], cases: list) -> list:
    """모범 답안(reference)을 답변으로 넣고 must 전부를 누락 목록으로 넘겨 전부 통과하는지 본다.
    하나라도 실패하면 judge 프롬프트가 틀린 것이다. 실패한 conv_id 목록을 돌려준다."""
    bad = []
    for c in cases:
        e = c["expect"]
        v = judge_turn(judge, c["question"], e, e["reference"], list(e.get("must", [])))
        if not v.passed:
            bad.append(c["conv_id"])
    return bad
