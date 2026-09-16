# -*- coding: utf-8 -*-
"""의도 분류 라우터. 노드 둘로 된 그래프다.

classify 는 모델이 하는 일(분류), gate 는 정책이 정하는 일(처리/이관/범위밖).
둘을 나눠 둔 덕에 임계값만 바꿀 때 모델을 다시 부르지 않아도 되고, classify 를
가짜 함수로 바꿔 그래프 흐름을 LLM 없이 시험할 수 있다.
"""
import re
from typing import Callable, Literal, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from server.domain import Domain
from server.prompts import build_route_guide

Route = Literal["ORDER_PLACE", "PRODUCT_INFO", "SHIPPING", "RETURN_REFUND", "OTHER"]


class RouteDecision(BaseModel):
    """고객 문의 한 건에 대한 라우팅 판단 결과."""
    route: Route = Field(description="문의를 배정할 라우트. 5개 값 중 하나만 사용한다.")
    confidence: float = Field(ge=0.0, le=1.0,
                              description="판단의 확신도. 두 라우트 사이에서 애매하면 0.5 미만으로 낮춘다.")
    reason: str = Field(description="그 라우트로 판단한 근거를 한 문장으로. 고객이 원하는 결과를 기준으로 쓴다.")


class RouterState(TypedDict, total=False):
    question: str
    route: str
    confidence: float
    reason: str
    action: str            # HANDLE / ESCALATE / OUT_OF_SCOPE
    message: Optional[str]


RULES = [
    (r"환불|반품|교환|취소|반송|수거|검품", "RETURN_REFUND"),
    (r"배송비|택배비|무료\s?배송|배송|택배|출고|도착|언제\s?(오|와)|어디쯤", "SHIPPING"),
    (r"주문할|구매할게|살게요|사고\s?싶|결제|주문\s?가능|구매\s?가능|낱개|따로|단품", "ORDER_PLACE"),
    (r"구성|포함|소재|재질|사이즈|치수|재고|정품|색상|품질|몇\s?(장|개)", "PRODUCT_INFO"),
    (r"매장|오프라인|지점|영업\s?시간|주차", "OTHER"),
]


def make_rule_classifier() -> Callable[[str], RouteDecision]:
    """키워드 규칙 분류기. 기준선(baseline) 측정용."""
    def classify(question: str) -> RouteDecision:
        for pattern, route in RULES:
            if re.search(pattern, question):
                return RouteDecision(route=route, confidence=0.8, reason="키워드 규칙 매치")
        return RouteDecision(route="OTHER", confidence=0.3, reason="매치되는 규칙 없음")
    return classify


def make_llm_classifier(domain: Domain, model: str) -> Callable[[str], RouteDecision]:
    from langchain.chat_models import init_chat_model
    guide = build_route_guide(domain)
    chain = init_chat_model(model, temperature=0, timeout=60, max_retries=8) \
        .with_structured_output(RouteDecision)

    def classify(question: str) -> RouteDecision:
        return chain.invoke([("system", guide), ("human", f"고객 문의: {question}")])
    return classify


def build_router(domain: Domain, conf_threshold: float,
                 classify: Optional[Callable[[str], RouteDecision]] = None,
                 model: Optional[str] = None):
    if classify is None:
        if model is None:
            raise ValueError("classify 또는 model 중 하나는 있어야 합니다")
        classify = make_llm_classifier(domain, model)

    def node_classify(state: RouterState) -> RouterState:
        d = classify(state["question"])
        return {"route": d.route, "confidence": d.confidence, "reason": d.reason}

    def node_gate(state: RouterState) -> RouterState:
        if state["confidence"] < conf_threshold:
            return {"action": "ESCALATE", "message": domain.escalate_message}
        if state["route"] == "OTHER":
            return {"action": "OUT_OF_SCOPE", "message": domain.out_of_scope_message}
        return {"action": "HANDLE", "message": None}

    g = StateGraph(RouterState)
    g.add_node("classify", node_classify)
    g.add_node("gate", node_gate)
    g.add_edge(START, "classify")
    g.add_edge("classify", "gate")
    g.add_edge("gate", END)
    return g.compile()
