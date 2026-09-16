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
        self.customers = []

    def answer(self, question, route, history=None, feedback=None, customer=None):
        self.questions.append((question, history or []))
        self.calls_kwargs.append({"question": question, "route": route,
                                  "history": history or [], "feedback": feedback})
        self.customers.append(customer)
        return self.script.pop(0)


def router_with(domain, route, conf):
    return build_router(domain, 0.5, classify=lambda q: RouteDecision(route=route, confidence=conf, reason="t"))


def test_answer_path(domain, settings):
    ans = FakeAnswerer([("기준은 100,000원이라 41,000원이 부족합니다.",
                         {"get_shipping_policy": {"free_shipping_threshold": 100000, "shortfall": 41000}},
                         [{"name": "get_shipping_policy", "args": {"product_id": "P4001"}}])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    r = p.turn(cid, "P4001 무료배송 되나요?")
    assert r.action == "ANSWER" and r.route == "SHIPPING" and r.guardrail["ok"] and not r.end_call
    assert r.tools[0]["name"] == "get_shipping_policy"
    assert r.attempts == 1


def test_escalate_path_ends_call(domain, settings):
    # clarify_max=1 이므로 확신도 미달은 먼저 한 번 되묻고, 두 번째부터 이관한다.
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.2), answerer=FakeAnswerer([]))
    cid, _, _ = p.start_call()
    p.turn(cid, "그거요")
    r = p.turn(cid, "그거요")
    assert r.action == "ESCALATE" and r.end_call and r.answer == domain.escalate_message


def test_out_of_scope_path_ends_call(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "OTHER", 0.9), answerer=FakeAnswerer([]))
    cid, _, _ = p.start_call()
    r = p.turn(cid, "홍대점 몇 시까지 해요?")
    assert r.action == "OUT_OF_SCOPE" and r.end_call and r.answer == domain.out_of_scope_message


def test_ask_path_when_no_tools(domain, settings):
    ans = FakeAnswerer([("어떤 상품인지 말씀해 주시겠어요?", {}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    r = p.turn(cid, "배송비 얼마예요?")
    assert r.action == "ASK" and not r.end_call and r.guardrail is None


def test_external_channel_order_is_out_of_scope(domain, settings):
    ans = FakeAnswerer([("확인했습니다.", {"get_order_status": {"is_external_channel": True}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "RETURN_REFUND", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    r = p.turn(cid, "O-1009 반품이요")
    assert r.action == "OUT_OF_SCOPE" and r.end_call


def test_guardrail_retry_then_escalate(domain, settings):
    bad = ("무료배송 기준은 40,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    ans = FakeAnswerer([bad, bad])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    r = p.turn(cid, "P4001 무료배송?")
    assert len(ans.questions) == 2
    assert r.action == "ESCALATE" and r.end_call
    assert (settings.logs_dir / "guardrail.jsonl").exists()


def test_guardrail_retry_succeeds(domain, settings):
    bad = ("무료배송 기준은 40,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    good = ("무료배송 기준은 100,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    ans = FakeAnswerer([bad, good])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    r = p.turn(cid, "P4001 무료배송?")
    assert r.action == "ANSWER" and r.guardrail["ok"]
    assert r.attempts == 2


def test_history_carries_across_turns(domain, settings):
    ans = FakeAnswerer([("어떤 상품인가요?", {}, []), ("네 확인했습니다.", {"get_product_detail": {}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "PRODUCT_INFO", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
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


def test_infer_action_ignores_closing_courtesy():
    # 답변 끝의 "추가로 궁금한 점 있으시면 말씀해 주세요" 는 질문형 어미를 갖고 있어도
    # 상담 종결 인사일 뿐, 고객에게 되묻는 ASK 로 오판하면 안 된다.
    text = "슬립 원피스 소재는 폴리 80%입니다. 추가로 궁금한 점 있으시면 말씀해 주세요."
    assert infer_action(text, {}) == "ANSWER"


def test_infer_action_still_detects_real_ask():
    assert infer_action("어떤 상품인지 말씀해 주시겠어요?", {}) == "ASK"


def test_infer_action_closing_courtesy_does_not_swallow_real_question():
    # 종결 인사와 같은 낱말("추가로", "필요")이 진짜 질문 문장에 섞여 있어도, 그 문장이
    # "?" 로 끝나면 문장 단위 제거 대상에서 제외해 ASK 로 남아야 한다.
    assert infer_action("추가로 필요한 사이즈를 말씀해 주시겠어요?", {}) == "ASK"
    assert infer_action("교환하시려면 추가로 필요한 서류가 있으신가요?", {}) == "ASK"
    assert infer_action("주문번호를 말씀해 주시겠어요?", {}) == "ASK"


def test_infer_action_drops_only_closing_sentence():
    text = "네, 더 궁금하신 점 있으시면 언제든 말씀해 주세요."
    assert infer_action(text, {}) == "ANSWER"


def test_infer_action_sees_repeated_keys():
    # 같은 도구가 한 턴에 여러 번 불리면 results 에 get_order_status#2, #3 처럼 쌓인다.
    results = {"get_order_status": {"error": "x"}, "get_order_status#2": {"is_external_channel": True}}
    assert infer_action("확인했습니다.", results) == "OUT_OF_SCOPE"


def test_retry_passes_guardrail_feedback(domain, settings):
    bad = ("무료배송 기준은 40,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    good = ("무료배송 기준은 100,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    ans = FakeAnswerer([bad, good])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    p.turn(cid, "P4001 무료배송?")
    assert len(ans.calls_kwargs) == 2
    assert ans.calls_kwargs[0]["feedback"] is None
    feedback = ans.calls_kwargs[1]["feedback"]
    assert feedback and "출처 불명" in feedback


def test_ask_after_retry_clears_stale_guardrail(domain, settings):
    bad = ("무료배송 기준은 40,000원 이상입니다.", {"get_shipping_policy": {"free_shipping_threshold": 100000}}, [])
    ask = ("어떤 상품인지 말씀해 주시겠어요?", {}, [])
    ans = FakeAnswerer([bad, ask])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    r = p.turn(cid, "P4001 무료배송?")
    assert r.action == "ASK" and r.guardrail is None


def test_low_confidence_asks_once_then_escalates(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.2), answerer=FakeAnswerer([]))
    cid, _, _ = p.start_call()
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
    cid, _, _ = p.start_call()
    r = p.turn(cid, "그거요")
    assert r.action == "ESCALATE" and r.end_call


def test_clarify_count_is_per_call(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.2), answerer=FakeAnswerer([]))
    a, _, _ = p.start_call()
    b, _, _ = p.start_call()
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
    cid, _, _ = p.start_call()
    r = p.turn(cid, "세트 하나요")
    assert r.action == "ESCALATE" and r.end_call
    assert r.tools and r.tools[0]["name"] == "search_product"


def test_router_sees_current_question_first(domain, settings):
    seen = []
    def classify(q):
        seen.append(q)
        return RouteDecision(route="SHIPPING", confidence=0.9, reason="t")
    ans = FakeAnswerer([("어떤 상품인가요?", {}, []), ("네.", {"get_product_detail": {}}, [])])
    p = Pipeline(domain, settings, router=build_router(domain, 0.5, classify=classify), answerer=ans)
    cid, _, _ = p.start_call()
    p.turn(cid, "캔버스화 배송비요")
    p.turn(cid, "소재는요?")
    assert seen[1].startswith("소재는요?") and "캔버스화 배송비요" in seen[1]


def test_stt_order_id_is_normalized(domain, settings):
    ans = FakeAnswerer([("네.", {"get_order_status": {}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    p.turn(cid, "0-1001 주문 언제 와요")
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


def test_start_call_with_known_phone_passes_customer(domain, settings, modumall_dir):
    from server.repo import Repo
    repo = Repo(domain.db_path)
    phone = repo.customer(repo.order("O-1001")["customer_id"])["phone"]
    ans = FakeAnswerer([("네.", {"get_order_status": {}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, greeting, customer = p.start_call(phone=phone)
    assert customer["name"] in greeting and customer["recent_orders"]
    p.turn(cid, "그 주문 언제 와요?")
    assert ans.customers[0]["customer_id"] == customer["customer_id"]


def test_start_call_unknown_phone_is_guest(domain, settings):
    ans = FakeAnswerer([("어떤 상품인가요?", {}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, greeting, customer = p.start_call(phone="010-0000-0000")
    assert customer is None and greeting == domain.greeting
    p.turn(cid, "배송비요")
    assert ans.customers[0] is None


def test_sample_customers_have_recent_non_delivered_order(domain, settings):
    import datetime as dt
    from server.repo import Repo
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=FakeAnswerer([]))
    repo = Repo(domain.db_path)
    samples = p.sample_customers(5)
    assert samples
    cutoff = (dt.date(2026, 9, 16) - dt.timedelta(days=30)).isoformat()
    for s in samples:
        c = repo.customer_by_phone(s["phone"])
        orders = repo._all("select ordered_at, status from orders where customer_id=?", c["customer_id"])
        assert any(o["ordered_at"] >= cutoff and o["status"] != "배송완료" for o in orders), s


def test_customer_numbers_are_allowed_by_guardrail(domain, settings):
    # 통화 고객 블록에 있는 주문 금액을 답변에 써도 출처 불명이 아니다
    ans = FakeAnswerer([("주문 금액은 25,800원입니다.", {"get_order_status": {"order_id": "O-1001"}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    p.customers[cid] = {"customer_id": "C-0001", "name": "홍길동", "recent_orders": [{"order_id": "O-1001", "order_amount": 25800}]}
    r = p.turn(cid, "그 주문 얼마였죠")
    assert r.action == "ANSWER" and r.guardrail["ok"]


def test_state_customer_context_isolated_per_call(domain, settings):
    # F1: 통화별 고객 컨텍스트가 공유 속성(_current_call)이 아니라 그래프 state 를 타고
    # 흐르는지 확인한다. 두 통화를 인터리빙(A, B, A)해도 각 턴이 자기 통화의 고객만 받아야 한다.
    ans = FakeAnswerer([("A1", {}, []), ("B1", {}, []), ("A2", {}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    a, _, cust_a = p.start_call()
    b, _, cust_b = p.start_call()
    p.customers[a] = {"customer_id": "C-0001", "name": "고객A", "recent_orders": []}
    p.customers[b] = {"customer_id": "C-0002", "name": "고객B", "recent_orders": []}
    p.turn(a, "질문1")
    p.turn(b, "질문1")
    p.turn(a, "질문2")
    assert ans.customers[0]["customer_id"] == "C-0001"
    assert ans.customers[1]["customer_id"] == "C-0002"
    assert ans.customers[2]["customer_id"] == "C-0001"


def test_current_caller_contextvar_reset_after_turn(domain, settings):
    # F2: turn() 이 current_caller 를 설정하고, 정상 종료 후에는 반드시 되돌려야 한다.
    from server.callcontext import current_caller
    ans = FakeAnswerer([("네.", {}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    p.customers[cid] = {"customer_id": "C-0001", "name": "고객A", "recent_orders": []}
    assert current_caller.get() is None
    p.turn(cid, "질문")
    assert current_caller.get() is None


def test_end_call_writes_log(domain, settings):
    from server.repo import Repo
    ans = FakeAnswerer([("네.", {"get_product_detail": {}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "PRODUCT_INFO", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    p.turn(cid, "소재요")
    p.end_call(cid)
    row = Repo(domain.db_path).call(cid)
    assert row and len(row["turns"]) == 1 and row["turns"][0]["action"] == "ANSWER"


def test_sample_customers_carry_hint_and_profile(domain, settings):
    from server.repo import Repo
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=FakeAnswerer([]))
    samples = p.sample_customers()
    assert len(samples) == 8
    assert all(set(s) == {"name", "phone", "hint"} for s in samples)
    assert len({s["hint"] for s in samples if s["hint"]}) >= 3   # 상태별로 한 명씩 뽑아 힌트가 다양하다
    cid = Repo(domain.db_path).customer_by_phone(samples[0]["phone"])["customer_id"]
    prof = p.customer_profile(cid)
    assert prof["customer_id"] == cid and "address" in prof
    _, _, cust = p.start_call(samples[0]["phone"])
    assert "address" not in cust                      # 프롬프트용 고객 dict 는 그대로 주소 없음
