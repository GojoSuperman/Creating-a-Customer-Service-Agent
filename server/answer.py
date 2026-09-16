# -*- coding: utf-8 -*-
"""답변 생성. 모델이 스스로 도구를 골라 부르는 그래프다.

agent 노드가 도구를 요청하면 tools 노드로 갔다가 다시 agent 로 돌아온다.
돌아오는 화살표가 곧 루프이고, 상한은 recursion_limit 으로 건다.

같은 도구가 한 턴 안에서 여러 번 불리면 결과 dict 에서 첫 번째는 도구명 그대로,
두 번째부터는 `f"{name}#2"`, `f"{name}#3"`, … 키로 보존한다 (덮어쓰지 않는다).
"""
import json
import uuid
from typing import Annotated, Optional, TypedDict

from langchain.tools import tool
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from server.context import build_context
from server.domain import Domain
from server.prompts import build_answer_rules
from server.tools import make_tools


def build_answer_prompt(domain: Domain, route: str, tool_results: Optional[dict] = None) -> str:
    ctx = build_context(domain, route)
    tr = json.dumps(tool_results or {}, ensure_ascii=False, indent=1)
    return (f"{build_answer_rules(domain)}\n"
            f"===== 업무 매뉴얼 (라우트: {route}) =====\n{ctx}\n\n"
            f"===== 조회 결과 =====\n{tr}\n")


class ToolState(TypedDict, total=False):
    messages: Annotated[list, add_messages]


class Answerer:
    def __init__(self, domain: Domain, model: Optional[str] = None, llm=None, max_tool_turns: int = 3):
        self.domain = domain
        self.max_tool_turns = max_tool_turns
        self.tools = make_tools(domain)
        self.lc_tools = [tool(fn) for fn in self.tools.values()]
        if llm is None:
            if model is None:
                raise ValueError("model 또는 llm 중 하나는 있어야 합니다")
            from langchain.chat_models import init_chat_model
            llm = init_chat_model(model, temperature=0, timeout=60, max_retries=2).bind_tools(self.lc_tools)
        self.llm = llm
        self.graph = self._build()

    def _build(self):
        def agent(state: ToolState) -> ToolState:
            # 가짜 LLM(테스트용 RunnableLambda)이 같은 AIMessage 객체를 매번 돌려주면
            # langgraph 의 add_messages 가 id 로 병합해 새 메시지로 인식하지 못하고
            # 대화가 자라지 않는다 (도구 반복 호출이 루프로 안 잡힘). 매 호출마다
            # id 를 새로 발급해 실제 LLM 이 매번 새 메시지를 내놓는 것과 동일하게 만든다.
            msg = self.llm.invoke(state["messages"])
            msg = msg.model_copy(update={"id": str(uuid.uuid4())})
            return {"messages": [msg]}

        g = StateGraph(ToolState)
        g.add_node("agent", agent)
        g.add_node("tools", ToolNode(self.lc_tools))
        g.add_edge(START, "agent")
        g.add_conditional_edges("agent", tools_condition)
        g.add_edge("tools", "agent")
        return g.compile()

    def answer(self, question: str, route: str, history: Optional[list[str]] = None):
        """(답변 텍스트, {도구명: 결과}, [{"name", "args"}] 호출 순서) 를 돌려준다.

        같은 도구가 한 턴 안에서 여러 번 불리면 결과가 서로 덮어쓰지 않도록 첫 번째
        결과는 도구명 그대로(`results[name]`), 두 번째부터는 `f"{name}#2"`, `f"{name}#3"`, …
        키로 보존한다. 가드레일이 `results.values()` 에서 허용 숫자 집합을 뽑기 때문에,
        여기서 덮어써 버리면 앞선 호출의 숫자가 근거 없는 값으로 오판된다.
        """
        full_q = " ".join((history or []) + [question])
        init = {"messages": [("system", build_answer_prompt(self.domain, route)), ("human", full_q)]}
        calls: list[dict] = []
        try:
            out = self.graph.invoke(init, {"recursion_limit": 2 * self.max_tool_turns + 1})
        except GraphRecursionError:
            return self.domain.escalate_message, {}, calls
        results: dict = {}
        seen_counts: dict[str, int] = {}
        for m in out["messages"]:
            for tc in getattr(m, "tool_calls", None) or []:
                calls.append({"name": tc["name"], "args": tc["args"]})
            if getattr(m, "name", None) in self.tools:
                content = m.content
                if isinstance(content, (dict, list)):
                    value = content
                else:
                    try:
                        value = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        value = content
                seen_counts[m.name] = seen_counts.get(m.name, 0) + 1
                n = seen_counts[m.name]
                key = m.name if n == 1 else f"{m.name}#{n}"
                results[key] = value
        return out["messages"][-1].content, results, calls
