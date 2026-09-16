import pytest
from server.config import Settings
from server.domain import load_domain
from server.router import RouteDecision, build_router
from server.pipeline import Pipeline, infer_action


@pytest.fixture
def domain(modumall_dir):
    return load_domain(modumall_dir)


@pytest.fixture
def settings(tmp_path, modumall_dir):
    return Settings(router_model="x", answer_model="x", conf_threshold=0.5, max_tool_turns=3,
                    guardrail_retry=1, domain="modumall", domains_root=modumall_dir.parent,
                    logs_dir=tmp_path / "logs", clarify_max=1)


class FakeAnswerer:
    def __init__(self, script):
        self.script = list(script)   # [(text, results, calls), ...]
        self.questions = []
        self.calls_kwargs = []

    def answer(self, question, route, history=None, feedback=None):
        self.questions.append((question, history or []))
        self.calls_kwargs.append({"question": question, "route": route,
                                  "history": history or [], "feedback": feedback})
        return self.script.pop(0)


def router_with(domain, route, conf):
    return build_router(domain, 0.5, classify=lambda q: RouteDecision(route=route, confidence=conf, reason="t"))


def test_answer_path(domain, settings):
    ans = FakeAnswerer([("기준은 100,000원이라 41,000원이 부족합니다.",
                         {"get_shipping_policy": {"free_shipping_threshold": 100000, "shortfall": 41000}},
                         [{"name": "get_shipping_policy", "args": {"product_id": "P4001"}}])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid = p.start_call()
    r = p.turn(cid, "P4001 무료배송 되나요?")
    assert r.action == "ANSWER" and r.route == "SHIPPING" and r.guardrail["ok"] and not r.end_call
    assert r.tools[0]["name"] == "get_shipping_policy"
    assert r.attempts == 1


def test_escalate_path_ends_call(domain, settings):
    # clarify_max=1 이므로 확신도 미달은 먼저 한 번 되묻고, 두 번째부터 이관한다.
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.2), answerer=FakeAnswerer([]))
    cid = p.start_call()
    p.turn(cid, "그거요")
    r = p.turn(cid, "그거요")
    assert r.action == "ESCALATE" and r.end_call and r.answer == domain.escalate_message


def test_out_of_scope_path_ends_call(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "OTHER", 0.9), answerer=FakeAnswerer([]))
    r = p.turn(p.start_call(), "홍대점 몇 시까지 해요?")
    assert r.action == "OUT_OF_SCOPE" and r.end_call and r.answer == domain.out_of_scope_message


def test_ask_path_when_no_tools(domain, settings):
    ans = FakeAnswerer([("어떤 상품인지 말씀해 주시겠어요?", {}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    r = p.turn(p.start_call(), "배송비 얼마예요?")
    assert r.action == "ASK" and not r.end_call and r.guardrail is None


def test_external_channel_order_is_out_of_scope(domain, settings):
    ans = FakeAnswerer([("확인했습니다.", {"get_order_status": {"is_external_channel": True}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "RETURN_REFUND", 0.9), answerer=ans)
    r = p.turn(p.start_call(), "O-1009 반품이요")
    assert r.action == "OUT_OF_SCOPE" and r.end_call


def test_guardrail_retry_then_escalate(domain, settings):
    bad = ("무료배송 기준은 40,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    ans = FakeAnswerer([bad, bad])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    r = p.turn(p.start_call(), "P4001 무료배송?")
    assert len(ans.questions) == 2
    assert r.action == "ESCALATE" and r.end_call
    assert (settings.logs_dir / "guardrail.jsonl").exists()


def test_guardrail_retry_succeeds(domain, settings):
    bad = ("무료배송 기준은 40,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    good = ("무료배송 기준은 100,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    ans = FakeAnswerer([bad, good])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    r = p.turn(p.start_call(), "P4001 무료배송?")
    assert r.action == "ANSWER" and r.guardrail["ok"]
    assert r.attempts == 2


def test_history_carries_across_turns(domain, settings):
    ans = FakeAnswerer([("어떤 상품인가요?", {}, []), ("네 확인했습니다.", {"get_product_detail": {}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "PRODUCT_INFO", 0.9), answerer=ans)
    cid = p.start_call()
    p.turn(cid, "소재가 뭐예요?")
    p.turn(cid, "캔버스화요")
    assert ans.questions[1] == ("캔버스화요", ["소재가 뭐예요?"])


def test_unknown_call_id(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=FakeAnswerer([]))
    with pytest.raises(KeyError):
        p.turn("nope", "x")


def test_infer_action_tolerates_error_string():
    # ToolNode 가 잘못된 kwargs 호출 시 오류 텍스트를 결과에 담는 경우, dict 가 아니므로
    # is_external_channel 을 찾으려다 예외가 나면 안 된다.
    action = infer_action("확인했습니다.", {"get_order_status": "Error invoking tool with kwargs"})
    assert action == "ANSWER"


def test_infer_action_sees_repeated_keys():
    # 같은 도구가 한 턴에 여러 번 불리면 results 에 get_order_status#2, #3 처럼 쌓인다.
    results = {"get_order_status": {"error": "x"}, "get_order_status#2": {"is_external_channel": True}}
    assert infer_action("확인했습니다.", results) == "OUT_OF_SCOPE"


def test_retry_passes_guardrail_feedback(domain, settings):
    bad = ("무료배송 기준은 40,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    good = ("무료배송 기준은 100,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    ans = FakeAnswerer([bad, good])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    p.turn(p.start_call(), "P4001 무료배송?")
    assert len(ans.calls_kwargs) == 2
    assert ans.calls_kwargs[0]["feedback"] is None
    feedback = ans.calls_kwargs[1]["feedback"]
    assert feedback and "출처 불명" in feedback


def test_ask_after_retry_clears_stale_guardrail(domain, settings):
    bad = ("무료배송 기준은 40,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    ask = ("어떤 상품인지 말씀해 주시겠어요?", {}, [])
    ans = FakeAnswerer([bad, ask])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    r = p.turn(p.start_call(), "P4001 무료배송?")
    assert r.action == "ASK" and r.guardrail is None


def test_low_confidence_asks_once_then_escalates(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.2), answerer=FakeAnswerer([]))
    cid = p.start_call()
    r1 = p.turn(cid, "그거요")
    assert r1.action == "ASK" and not r1.end_call
    assert r1.answer == domain.clarify_message
    r2 = p.turn(cid, "음")
    assert r2.action == "ESCALATE" and r2.end_call


def test_clarify_disabled_escalates_immediately(domain, modumall_dir, tmp_path):
    s = Settings(router_model="x", answer_model="x", conf_threshold=0.5, max_tool_turns=3,
                 guardrail_retry=1, domain="modumall", domains_root=modumall_dir.parent,
                 logs_dir=tmp_path / "logs", clarify_max=0)
    p = Pipeline(domain, s, router=router_with(domain, "SHIPPING", 0.2), answerer=FakeAnswerer([]))
    r = p.turn(p.start_call(), "그거요")
    assert r.action == "ESCALATE" and r.end_call


def test_clarify_count_is_per_call(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.2), answerer=FakeAnswerer([]))
    a, b = p.start_call(), p.start_call()
    assert p.turn(a, "x").action == "ASK"
    assert p.turn(b, "x").action == "ASK"      # 다른 통화는 카운트가 따로다
    assert p.turn(a, "y").action == "ESCALATE"


def test_clarify_message_default_lists_route_labels(domain):
    for name, r in domain.routes.items():
        if name != "OTHER":
            assert r.label in domain.clarify_message


def test_answerer_exhaustion_becomes_escalate(domain, settings):
    ans = FakeAnswerer([(domain.escalate_message, {}, [{"name": "search_product", "args": {"query": "x"}}])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    r = p.turn(p.start_call(), "세트 하나요")
    assert r.action == "ESCALATE" and r.end_call
    assert r.tools and r.tools[0]["name"] == "search_product"


def test_router_sees_current_question_first(domain, settings):
    seen = []
    def classify(q):
        seen.append(q)
        return RouteDecision(route="SHIPPING", confidence=0.9, reason="t")
    ans = FakeAnswerer([("어떤 상품인가요?", {}, []), ("네.", {"get_product_detail": {}}, [])])
    p = Pipeline(domain, settings, router=build_router(domain, 0.5, classify=classify), answerer=ans)
    cid = p.start_call()
    p.turn(cid, "캔버스화 배송비요")
    p.turn(cid, "소재는요?")
    assert seen[1].startswith("소재는요?") and "캔버스화 배송비요" in seen[1]


def test_stt_order_id_is_normalized(domain, settings):
    ans = FakeAnswerer([("네.", {"get_order_status": {}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    p.turn(p.start_call(), "0-1001 주문 언제 와요")
    assert ans.questions[0][0].startswith("O-1001")


def test_normalize_stt_spoken_order_ids():
    from server.pipeline import normalize_stt
    assert normalize_stt("제로 다시 1006 주문 언제 와요") == "O-1006 주문 언제 와요"
    assert normalize_stt("영 다시 일공공육 반품 배송비 누가 내요") == "O-1006 반품 배송비 누가 내요"
    assert normalize_stt("0-1001 주문이요") == "O-1001 주문이요"
    assert normalize_stt("알 다시 2001 어떻게 됐어요") == "R-2001 어떻게 됐어요"
    assert normalize_stt("1001번 주문 언제 출고돼요") == "O-1001번 주문 언제 출고돼요"
    assert normalize_stt("0-100 반품 배송비") == "O-100 반품 배송비"     # 잘린 숫자는 복구 못 함


def test_normalize_stt_leaves_ordinary_text_alone():
    from server.pipeline import normalize_stt
    for t in ["이 상품 소재가 뭐예요", "오 늘 배송 돼요", "사이즈 삼 개요", "캔버스화 배송비 얼마예요", "2026년 9월 1일"]:
        assert normalize_stt(t) == t
