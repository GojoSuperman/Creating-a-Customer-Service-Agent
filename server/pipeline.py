# -*- coding: utf-8 -*-
"""라우터 · 조회 · 생성 · 가드레일을 하나의 그래프로 잇는다.

route → answer → guard → (END | answer 재시도 | escalate)
통화(call_id)마다 체크포인터가 State 를 보존하므로 앞 턴의 발화가 뒤 턴에 이어진다.
"""
import operator
import re
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Annotated, Optional, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from server import guardrail
from server.config import Settings
from server.domain import Domain

ASK_PATTERN = r"\?|주시겠|알려주|말씀해"


def infer_action(text: str, results: dict) -> str:
    """평가 채점기와 런타임이 같은 판정을 쓴다.

    반환값: "OUT_OF_SCOPE" (외부 채널) / "ASK" (질문 패턴) / "ANSWER" (기본값)

    tools 결과는 오류 문자열일 수도 있고(모델이 잘못된 kwargs 를 넘기면 ToolNode 가
    오류 텍스트를 저장한다), 같은 도구가 한 턴에 여러 번 불리면 `get_order_status#2`,
    `#3` 같은 키로도 들어온다. 기본 이름이 `get_order_status` 인 모든 키를 훑되,
    dict 값만 검사한다.
    """
    for key, value in (results or {}).items():
        if key.split("#")[0] != "get_order_status":
            continue
        if isinstance(value, dict) and value.get("is_external_channel"):
            return "OUT_OF_SCOPE"
    if not results and re.search(ASK_PATTERN, text):
        return "ASK"
    return "ANSWER"


def normalize_stt(text: str) -> str:
    """음성 인식이 흔히 틀리는 식별자 표기를 바로잡는다. "0-1001" → "O-1001", "o-1001" → "O-1001"."""
    return re.sub(r"\b[0oO]-(\d{4})\b", r"O-\1", text)


class AgentState(TypedDict, total=False):
    question: str
    history: Annotated[list, operator.add]   # 리듀서: 턴마다 쌓인다
    route: str
    confidence: float
    action: str        # HANDLE / ASK / ANSWER / RETRY / ESCALATE / OUT_OF_SCOPE
    tools: list
    results: dict
    answer: str
    guardrail: Optional[dict]
    attempts: int
    clarify_count: int


@dataclass
class TurnResult:
    answer: str
    route: Optional[str]
    confidence: Optional[float]
    action: str
    tools: list
    guardrail: Optional[dict]
    elapsed_ms: int
    end_call: bool
    attempts: int

    def to_dict(self) -> dict:
        return asdict(self)


class Pipeline:
    def __init__(self, domain: Domain, settings: Settings, router=None, answerer=None):
        self.domain = domain
        self.settings = settings
        if router is None:
            from server.router import build_router
            router = build_router(domain, settings.conf_threshold, model=settings.router_model)
        if answerer is None:
            from server.answer import Answerer
            answerer = Answerer(domain, model=settings.answer_model, max_tool_turns=settings.max_tool_turns)
        self.router = router
        self.answerer = answerer
        self.calls: set[str] = set()
        self._current_call: Optional[str] = None   # 단일 스레드 가정. 동시 통화는 범위 밖
        self.graph = self._build()

    # ── 노드 ──────────────────────────────────────────────
    def _node_route(self, state: AgentState) -> AgentState:
        # state 에는 전체 history 를 그대로 쌓아 두고, 라우터는 최근 두 발화만 본다
        # (긴 통화일수록 앞 turn 이 라우팅을 오염시키는 걸 줄이기 위한 최소 완화책).
        hist = (state.get("history") or [])[-2:]
        q = state["question"]
        if hist:
            q = f"{q} (직전 발화: {' / '.join(hist)})"   # 현재 문의를 앞에 둬 라우팅이 이력에 끌리지 않게
        r = self.router.invoke({"question": q})
        base = {"route": r["route"], "confidence": r["confidence"], "action": r["action"],
                "attempts": 0, "tools": [], "results": {}, "guardrail": None}
        count = state.get("clarify_count", 0)
        if r["action"] == "ESCALATE" and count < self.settings.clarify_max:
            # 확신도 미달을 곧바로 이관하지 않고 한 번 되묻는다 (cs-chatbot-design 의 2단계 fallback)
            base.update({"action": "ASK", "answer": self.domain.clarify_message,
                         "clarify_count": count + 1, "history": [state["question"]]})
        return base

    def _node_answer(self, state: AgentState) -> AgentState:
        guardrail_state = state.get("guardrail")
        feedback = None
        if guardrail_state and not guardrail_state.get("ok"):
            feedback = "; ".join(f"{v['type']}: {v['detail']}" for v in guardrail_state["violations"])
        text, results, calls = self.answerer.answer(state["question"], state["route"],
                                                    history=state.get("history") or [],
                                                    feedback=feedback)
        attempts = state.get("attempts", 0) + 1
        if text == self.domain.escalate_message:
            # 답변기가 도구 호출 상한에 걸려 스스로 이관 문구를 돌려준 경우 — 일반 답변으로 흘리지 않는다
            return {"action": "ESCALATE", "tools": calls, "results": results, "attempts": attempts}
        action_inferred = infer_action(text, results)
        if action_inferred == "OUT_OF_SCOPE":
            return {"action": "OUT_OF_SCOPE", "tools": calls, "results": results, "attempts": attempts}
        if action_inferred == "ASK":
            return {"action": "ASK", "tools": calls, "results": {}, "answer": text, "attempts": attempts,
                    "history": [state["question"]], "guardrail": None}
        # action_inferred == "ANSWER" → internal "HANDLE" action
        return {"action": "HANDLE", "tools": calls, "results": results, "answer": text, "attempts": attempts}

    def _node_guard(self, state: AgentState) -> AgentState:
        g = guardrail.check(state["answer"], state["results"], self.domain)
        if not g.ok:
            for v in g.violations:
                guardrail.log_violation(self.settings.logs_dir, {
                    "call_id": self._current_call, "route": state["route"], "type": v["type"],
                    "detail": v["detail"], "answer": state["answer"]})
        result = {"guardrail": g.to_dict(), "action": "ANSWER" if g.ok else "RETRY"}
        if g.ok:
            result["history"] = [state["question"]]
        return result

    def _node_escalate(self, state: AgentState) -> AgentState:
        if state["action"] == "OUT_OF_SCOPE":
            return {"answer": self.domain.out_of_scope_message, "history": [state["question"]]}
        return {"answer": self.domain.escalate_message, "action": "ESCALATE",
                "history": [state["question"]]}

    # ── 분기 ──────────────────────────────────────────────
    def _after_route(self, state: AgentState) -> str:
        if state["action"] == "HANDLE":
            return "answer"
        return END if state["action"] == "ASK" else "escalate"

    def _after_answer(self, state: AgentState) -> str:
        if state["action"] == "ASK":
            return END
        if state["action"] in ("OUT_OF_SCOPE", "ESCALATE"):
            return "escalate"
        return "guard"

    def _after_guard(self, state: AgentState) -> str:
        if state["action"] == "ANSWER":
            return END
        return "answer" if state["attempts"] <= self.settings.guardrail_retry else "escalate"

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("route", self._node_route)
        g.add_node("answer", self._node_answer)
        g.add_node("guard", self._node_guard)
        g.add_node("escalate", self._node_escalate)
        g.add_edge(START, "route")
        g.add_conditional_edges("route", self._after_route, {"answer": "answer", "escalate": "escalate", END: END})
        g.add_conditional_edges("answer", self._after_answer, {"guard": "guard", "escalate": "escalate", END: END})
        g.add_conditional_edges("guard", self._after_guard, {"answer": "answer", "escalate": "escalate", END: END})
        g.add_edge("escalate", END)
        return g.compile(checkpointer=InMemorySaver())

    # ── 공개 API ──────────────────────────────────────────
    def start_call(self) -> str:
        cid = uuid.uuid4().hex[:12]
        self.calls.add(cid)
        return cid

    def turn(self, call_id: str, text: str) -> TurnResult:
        if call_id not in self.calls:
            raise KeyError(call_id)
        t0 = time.perf_counter()
        text = normalize_stt(text)
        cfg = {"configurable": {"thread_id": call_id}}
        self._current_call = call_id
        out = self.graph.invoke({"question": text}, cfg)
        action = out["action"]
        end = action in ("ESCALATE", "OUT_OF_SCOPE")
        return TurnResult(answer=out["answer"], route=out.get("route"), confidence=out.get("confidence"),
                          action=action, tools=out.get("tools") or [], guardrail=out.get("guardrail"),
                          elapsed_ms=int((time.perf_counter() - t0) * 1000), end_call=end,
                          attempts=out.get("attempts", 0))
