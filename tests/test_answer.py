import json
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from server.domain import load_domain
from server.answer import Answerer, build_answer_prompt


@pytest.fixture
def domain(modumall_dir):
    return load_domain(modumall_dir)


def scripted_llm(script):
    """호출될 때마다 script 의 다음 AIMessage 를 돌려주는 가짜 LLM."""
    it = iter(script)
    return RunnableLambda(lambda messages: next(it))


def test_prompt_contains_rules_context_and_results(domain):
    p = build_answer_prompt(domain, "SHIPPING", {"get_shipping_policy": {"shipping_fee": 2500}})
    assert "search_product" in p
    assert "## 4. 배송 문의" in p
    assert "## 6." not in p
    assert '"shipping_fee": 2500' in p


def test_tool_loop_calls_tool_then_answers(domain):
    llm = scripted_llm([
        AIMessage(content="", tool_calls=[{"name": "get_shipping_policy", "id": "c1",
                                           "args": {"product_id": "P4001", "order_amount": 59000}}]),
        AIMessage(content="무료배송 기준은 100,000원이라 41,000원이 부족합니다."),
    ])
    a = Answerer(domain, llm=llm, max_tool_turns=3)
    text, results, calls = a.answer("P4001 59000원어치 무료배송 되나요?", "SHIPPING")
    assert "41,000" in text
    assert results["get_shipping_policy"]["shortfall"] == 41000
    assert calls == [{"name": "get_shipping_policy", "args": {"product_id": "P4001", "order_amount": 59000}}]


def test_no_tool_call_returns_empty_results(domain):
    llm = scripted_llm([AIMessage(content="어떤 상품인지 말씀해 주시겠어요?")])
    text, results, calls = Answerer(domain, llm=llm).answer("배송비 얼마예요?", "SHIPPING")
    assert results == {} and calls == []
    assert text.endswith("?")


def test_recursion_limit_escalates(domain):
    forever = AIMessage(content="", tool_calls=[{"name": "search_product", "id": "x", "args": {"query": "세트"}}])
    llm = RunnableLambda(lambda m: forever)
    text, results, calls = Answerer(domain, llm=llm, max_tool_turns=2).answer("세트", "PRODUCT_INFO")
    assert text == domain.escalate_message
    assert results == {}


def test_history_is_prepended(domain):
    seen = {}
    def capture(messages):
        seen["human"] = [m for m in messages if getattr(m, "type", "") == "human" or (isinstance(m, tuple) and m[0] == "human")]
        return AIMessage(content="네.")
    a = Answerer(domain, llm=RunnableLambda(capture))
    a.answer("그거 배송비는요?", "SHIPPING", history=["캔버스화 살 건데요"])
    human = seen["human"][0]
    content = human.content if hasattr(human, "content") else human[1]
    assert "캔버스화 살 건데요" in content and "그거 배송비는요?" in content
