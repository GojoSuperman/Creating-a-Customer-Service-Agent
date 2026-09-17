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
    route: Route = Field(description="가장 가능성 높은 라우트. 5개 값 중 하나만 사용한다.")
    confidence: float = Field(ge=0.0, le=1.0,
                              description="route 의 확신도. 두 라우트 사이에서 애매하면 0.5 미만으로 낮춘다.")
    reason: str = Field(description="그 라우트로 판단한 근거를 한 문장으로. 고객이 원하는 결과를 기준으로 쓴다.")
    route_alt: Optional[Route] = Field(default=None,
                                       description="두 번째로 가능성 높은 라우트. 다른 후보가 전혀 없으면 null.")
    alt_confidence: float = Field(default=0.0, ge=0.0, le=1.0,
                                  description="route_alt 의 확신도. route_alt 가 null 이면 0.")
    is_followup: bool = Field(default=False,
                              description="[직전 라우트] 가 주어졌고 현재 발화가 직전 문의와 같은 라우트로 처리되어야 하는 연속 발화(같은 상품·주문·주제에 대한 추가 질문, 되묻기에 대한 대답)면 true. 배송 문의 뒤에 반품을 묻는 것처럼 주제(라우트)가 바뀌면 false. 직전 라우트가 없으면 false.")


class RouterState(TypedDict, total=False):
    question: str
    route: str
    confidence: float
    reason: str
    route_alt: Optional[str]
    alt_confidence: float
    is_followup: bool
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


def make_llm_classifier(domain: Domain, model: str, api_key: Optional[str] = None) -> Callable[[str], RouteDecision]:
    """api_key 가 주어지면 그 키로, 없으면(로컬 개발) 서버 환경변수로 모델을 만든다.

    모델 생성 자체를 지연시킨다(클로저 안에서 매 호출 때 get_chat_model 을 부른다) —
    서버 환경변수가 없는 상태로 떠 있다가(순수 BYOK 배포) 사용자가 자기 키를 보낼 때
    비로소 그 키로 모델을 만들 수 있어야 하기 때문이다. get_chat_model 이 (model, api_key)
    별로 캐싱하므로 실제로는 사용자당 한 번만 생성된다.
    """
    guide = build_route_guide(domain)

    def classify(question: str) -> RouteDecision:
        from server.llmkey import get_chat_model
        chain = get_chat_model(model, api_key).with_structured_output(RouteDecision)
        return chain.invoke([("system", guide), ("human", f"고객 문의: {question}")])
    return classify


def build_router(domain: Domain, conf_threshold: float,
                 classify: Optional[Callable[[str], RouteDecision]] = None,
                 model: Optional[str] = None, conf_margin: float = 0.0,
                 api_key: Optional[str] = None):
    if classify is None:
        if model is None:
            raise ValueError("classify 또는 model 중 하나는 있어야 합니다")
        classify = make_llm_classifier(domain, model, api_key=api_key)

    def node_classify(state: RouterState) -> RouterState:
        d = classify(state["question"])
        # 2순위가 1순위와 같은 라우트면 후보가 하나뿐이라는 뜻이니 없는 것으로 본다.
        alt = None if d.route_alt == d.route else d.route_alt
        return {"route": d.route, "confidence": d.confidence, "reason": d.reason,
                "route_alt": alt, "alt_confidence": d.alt_confidence if alt else 0.0,
                "is_followup": d.is_followup}

    def node_gate(state: RouterState) -> RouterState:
        # 마진 = 1순위 확신도 - 2순위 확신도. 2순위가 없으면 1.0(애매하지 않음)으로 본다.
        # 모델이 2순위를 더 높게 낸 경우(음수)는 0 으로 깎아, conf_margin=0.0 이면 게이트가 확실히 비활성이다.
        margin = 1.0 if state.get("route_alt") is None else max(0.0, state["confidence"] - state.get("alt_confidence", 0.0))
        if state["confidence"] < conf_threshold or margin < conf_margin:
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
