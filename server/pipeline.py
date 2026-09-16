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
        q = " ".join((state.get("history") or []) + [state["question"]])
        r = self.router.invoke({"question": q})
        return {"route": r["route"], "confidence": r["confidence"], "action": r["action"],
                "attempts": 0, "tools": [], "results": {}, "guardrail": None}

    def _node_answer(self, state: AgentState) -> AgentState:
        text, results, calls = self.answerer.answer(state["question"], state["route"],
                                                    history=state.get("history") or [])
        attempts = state.get("attempts", 0) + 1
        if results.get("get_order_status", {}).get("is_external_channel"):
            return {"action": "OUT_OF_SCOPE", "tools": calls, "results": results, "attempts": attempts}
        if not results and re.search(ASK_PATTERN, text):
            return {"action": "ASK", "tools": calls, "results": {}, "answer": text, "attempts": attempts,
                    "history": [state["question"]]}
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
        return "answer" if state["action"] == "HANDLE" else "escalate"

    def _after_answer(self, state: AgentState) -> str:
        if state["action"] == "ASK":
            return END
        return "escalate" if state["action"] == "OUT_OF_SCOPE" else "guard"

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
        g.add_conditional_edges("route", self._after_route, {"answer": "answer", "escalate": "escalate"})
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
        cfg = {"configurable": {"thread_id": call_id}}
        self._current_call = call_id
        out = self.graph.invoke({"question": text}, cfg)
        action = out["action"]
        end = action in ("ESCALATE", "OUT_OF_SCOPE")
        return TurnResult(answer=out["answer"], route=out.get("route"), confidence=out.get("confidence"),
                          action=action, tools=out.get("tools") or [], guardrail=out.get("guardrail"),
                          elapsed_ms=int((time.perf_counter() - t0) * 1000), end_call=end,
                          attempts=out.get("attempts", 0))
