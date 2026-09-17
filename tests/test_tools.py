import pytest
from server.callcontext import current_caller
from server.domain import load_domain
from server.tools import make_tools, find_identifiers

TOOL_NAMES = ["search_product", "get_order_status", "get_product_detail", "get_product_options",
              "get_shipping_policy", "get_return_policy", "get_return_status",
              "get_restock_info", "escalate_to_agent"]


@pytest.fixture
def tools(modumall_dir):
    return make_tools(load_domain(modumall_dir))


def test_first_nine_tools_unchanged(tools):
    assert list(tools)[:9] == TOOL_NAMES
    for fn in tools.values():
        assert fn.__doc__ and fn.__doc__.strip(), fn.__name__


def test_tools_now_ten(tools):
    assert list(tools)[-1] == "find_customer" and len(tools) == 10


def test_find_customer_by_phone_and_order(tools, modumall_dir):
    from server.repo import Repo
    from server.domain import load_domain
    repo = Repo(load_domain(modumall_dir).db_path)
    cust_1001 = repo.customer(repo.order("O-1001")["customer_id"])
    phone = cust_1001["phone"]
    token = current_caller.set({"customer_id": cust_1001["customer_id"], "phone": phone})
    try:
        c = tools["find_customer"](phone=phone.replace("-", ""))
        assert c["name"]
        # 합성 주문이 늘면서 O-1001 이 "최근 3건" 창에서 밀려날 수 있어, 주문 존재가 아니라
        # 조회된 고객이 O-1001 소유자와 같은지로 확인한다.
        assert c["customer_id"] == cust_1001["customer_id"]
        assert c["recent_orders"]
        assert "error" in tools["find_customer"](phone="010-0000-0000")
        assert "error" in tools["find_customer"]()
    finally:
        current_caller.reset(token)


def test_find_customer_requires_caller_context(tools, modumall_dir):
    from server.repo import Repo
    from server.domain import load_domain
    repo = Repo(load_domain(modumall_dir).db_path)
    phone = repo.customer(repo.order("O-1001")["customer_id"])["phone"]
    # 발신자 컨텍스트가 없으면 실제로 존재하는 전화번호라도 조회를 거절한다.
    assert current_caller.get() is None
    assert "error" in tools["find_customer"](phone=phone)


def test_find_customer_blocks_other_customers_order(tools, modumall_dir):
    from server.repo import Repo
    from server.domain import load_domain
    repo = Repo(load_domain(modumall_dir).db_path)
    cust_1001 = repo.customer(repo.order("O-1001")["customer_id"])
    other_order = repo.order("O-1006")
    assert other_order["customer_id"] != cust_1001["customer_id"]
    token = current_caller.set({"customer_id": cust_1001["customer_id"], "phone": cust_1001["phone"]})
    try:
        # 본인 전화번호 조회는 성공
        assert tools["find_customer"](phone=cust_1001["phone"])["customer_id"] == cust_1001["customer_id"]
        # 다른 고객의 주문번호로 조회하면 본인 확인 오류
        result = tools["find_customer"](order_id="O-1006")
        assert "error" in result
    finally:
        current_caller.reset(token)


def test_find_customer_by_order_id_own_record(tools, modumall_dir):
    # F2 긍정 케이스: caller 가 O-1001 소유자 본인이면 order_id 로도 자기 기록을 조회할 수 있다.
    from server.repo import Repo
    from server.domain import load_domain
    repo = Repo(load_domain(modumall_dir).db_path)
    owner = repo.customer(repo.order("O-1001")["customer_id"])
    token = current_caller.set({"customer_id": owner["customer_id"], "phone": owner["phone"]})
    try:
        result = tools["find_customer"](order_id="O-1001")
        assert result["customer_id"] == owner["customer_id"]
    finally:
        current_caller.reset(token)


def test_get_order_status_includes_tracking_events(tools):
    o = tools["get_order_status"]("O-1002")
    assert "events" in o and isinstance(o["events"], list)


def test_unknown_id_returns_error(tools):
    assert "error" in tools["get_product_detail"]("P9999")
    assert "error" in tools["get_order_status"]("O-9999")
    assert "error" in tools["get_return_status"](order_id="O-9999")


def test_teaching_notes_are_stripped(tools):
    o = tools["get_order_status"]("O-1001")
    assert "$teaching_note" not in o
    r = tools["get_return_status"](order_id="O-1006")
    assert not any(k.startswith("$") for k in r)


def test_shipping_shortfall_is_computed_by_tool(tools):
    # 캔버스화(P4001)는 신발, 기준 100,000원
    out = tools["get_shipping_policy"](product_id="P4001", order_amount=59000)
    assert out["free_shipping_threshold"] == 100000
    assert out["shipping_fee"] == 2500
    assert out["shortfall"] == 41000


def test_shipping_no_threshold_category(tools):
    # 어성초 기초 케어 세트(P5002)는 화장품, 무료배송 대상 아님
    out = tools["get_shipping_policy"](product_id="P5002", order_amount=999999)
    assert out["free_shipping_threshold"] is None
    assert out["free_shipping_available"] is False
    assert out["shipping_fee"] == 2500


def test_shipping_without_amount_has_no_shortfall(tools):
    out = tools["get_shipping_policy"](product_id="P4001")
    assert "shortfall" not in out
    assert out["free_shipping_threshold"] == 100000


def test_return_status_keeps_null(tools):
    r = tools["get_return_status"](order_id="O-1006")
    assert r["inspection_result"] is None
    assert r["fault_party"] is None
    assert r["shipping_fee_bearer"] is None


def test_restock_unconfirmed(tools):
    r = tools["get_restock_info"]("P4002")
    assert r["is_confirmed"] is False
    assert r["expected_date"] is None


def test_search_product_single_hit(tools):
    r = tools["search_product"]("캔버스화")
    assert r["resolved_product_id"] == "P4001"
    assert r["ambiguous"] is False


def test_search_product_typo_tolerant(tools):
    r = tools["search_product"]("켄버스화")
    assert r["candidates"] and r["candidates"][0]["product_id"] == "P4001"


def test_search_product_ambiguous(tools):
    r = tools["search_product"]("세트")
    assert r["ambiguous"] is True
    assert r["resolved_product_id"] is None
    assert len(r["candidates"]) >= 2


def test_search_product_no_hit(tools):
    r = tools["search_product"]("청바지")
    assert r["candidates"] == []
    assert r["resolved_product_id"] is None


def test_alias_single_resolves(tools):
    r = tools["search_product"]("원피스")
    assert r["resolved_product_id"] == "P3002" and r["category_query"] is True


def test_alias_multi_is_ambiguous(tools):
    r = tools["search_product"]("신발")
    assert r["ambiguous"] is True and r["category_query"] is True
    assert {c["product_id"] for c in r["candidates"]} == {"P4001", "P4002"}


def test_synonym_maps_to_product(tools):
    r = tools["search_product"]("운동화")
    assert r["resolved_product_id"] == "P4002"


def test_not_in_catalog(tools):
    r = tools["search_product"]("청바지")
    assert r["candidates"] == [] and r["not_in_catalog"] is True


def test_generic_no_hit_is_not_flagged_not_in_catalog(tools):
    r = tools["search_product"]("가방끈")
    assert r["candidates"] == [] and r["not_in_catalog"] is False
    assert r["resolved_product_id"] is None


def test_alias_inside_sentence(tools):
    r = tools["search_product"]("가방 하나 사려는데요")
    assert r["resolved_product_id"] == "P6002"


def test_existing_name_search_unchanged(tools):
    r = tools["search_product"]("캔버스화")
    assert r["resolved_product_id"] == "P4001" and r["category_query"] is False


def test_exact_product_name_beats_alias(tools):
    # "팬티" 는 범주어 사전에 있지만, 문장 전체로 보면 상품명 "브라·팬티 세트" 와 유일하게
    # 가장 높은 점수로 겹친다. 범주어(단일 토큰)의 상품 집합 안에 있는 경우에만 확정한다(F1).
    r = tools["search_product"]("브라·팬티 세트 80A로 살 건데 팬티는 몇 사이즈로 와요?")
    assert r["resolved_product_id"] == "P1003"
    assert r["ambiguous"] is False
    assert r["category_query"] is True


def test_alias_used_when_name_search_ambiguous(tools):
    # "세트" 는 여러 상품명과 동점으로 겹쳐 상품명 검색이 유일한 후보를 내지 못한다.
    # 이때는 "화장품" 이 범주어 사전에 있으므로 범주어 후보로 되돌아간다(F1).
    r = tools["search_product"]("화장품 세트")
    assert r["ambiguous"] is True and r["category_query"] is True
    assert {c["product_id"] for c in r["candidates"]} == {"P5001", "P5002", "P5003"}


def test_mixed_alias_intent_asks_instead_of_guessing(tools):
    # "반지"(단품)와 "신발"(범주, 조사가 붙은 "신발도" 형태)이 한 문장에 섞여 있으면
    # 상품명 검색이 우연히 하나를 골라도(예: 반지 P2002) 그대로 확정해서는 안 된다.
    # 서로 다른 상품군이 섞였으니 후보를 모아 되묻는다(controller fix).
    r = tools["search_product"]("반지 신발도 있어요?")
    assert r["ambiguous"] is True
    assert r["category_query"] is True
    assert {"P2002", "P4001", "P4002"} <= {c["product_id"] for c in r["candidates"]}


def test_mixed_alias_intent_two_exact_alias_tokens(tools):
    r = tools["search_product"]("레깅스 신발")
    assert r["ambiguous"] is True
    assert r["category_query"] is True
    assert {"P3005", "P4001", "P4002"} <= {c["product_id"] for c in r["candidates"]}


def test_find_identifiers():
    assert find_identifiers("O-1006 반품 배송비 누가 내요?") == {"product_id": None, "order_id": "O-1006"}
    assert find_identifiers("P4001 배송비") == {"product_id": "P4001", "order_id": None}


def test_name_token_inside_query_token_is_not_a_match(tools):
    # "팬티" 가 "요일팬티" 안에 있다고 오가닉 팬티가 동점이 되면 안 된다
    r = tools["search_product"]("요일팬티 세트")
    assert r["resolved_product_id"] == "P1001" and r["ambiguous"] is False


def test_leather_jacket_beats_leather_belt(tools):
    r = tools["search_product"]("가죽 자켓 블랙 XL")
    assert r["resolved_product_id"] == "P3001"


def test_speech_misrecognition_is_matched_by_jamo(tools):
    # 음성 인식이 "캔버스화"를 "캠퍼스와"/"캠퍼스"로 듣는 경우
    assert tools["search_product"]("캠퍼스와")["resolved_product_id"] == "P4001"
    assert tools["search_product"]("캠퍼스")["resolved_product_id"] == "P4001"


def test_jamo_does_not_overmatch(tools):
    assert tools["search_product"]("가방끈")["candidates"] == []


def test_synonym_with_particle_resolves(tools):
    # 실측(2026-09-17): 카탈로그의 티셔츠 상품은 '기본 티셔츠'(P3004) 하나. 현재 "티는"은 빈 후보다.
    for q in ("티는", "티만", "티도"):
        r = tools["search_product"](q)
        assert r["resolved_product_id"] == "P3004", (q, r)


def test_compound_tshirt_words_resolve(tools):
    for q in ("반팔티", "무지티", "긴팔티"):
        r = tools["search_product"](q)
        assert r["resolved_product_id"] == "P3004", (q, r)


def test_short_query_skips_fuzzy_fallback(modumall_dir, monkeypatch):
    # 유사도를 항상 0.99 로 만들어도 두 글자 이하 질의는 폴백을 타지 않고, 세 글자부터는 탄다
    import server.tools as tm
    monkeypatch.setattr(tm, "jamo_ratio", lambda a, b: 0.99)
    tools = tm.make_tools(load_domain(modumall_dir))
    r2 = tools["search_product"]("ㅋㅋ")
    assert r2["candidates"] == [] and r2["not_in_catalog"] is False
    r3 = tools["search_product"]("ㅋㅋㅋ")
    assert r3["candidates"] and r3["candidates"][0]["score"] < 1.0


def test_three_char_typo_still_uses_fallback(tools):
    assert tools["search_product"]("켄버스화")["resolved_product_id"] == "P4001"


def test_synonym_longest_key_wins_over_prefix(modumall_dir):
    # 짧은 키가 긴 키를 가리지 않는다. "반팔티는" 은 "반팔티", "반팔은" 은 "반팔" 로 풀려야 한다.
    domain = load_domain(modumall_dir)
    domain.search["synonyms"] = {"반팔": "티셔츠", "반팔티": "티셔츠"}
    tools = make_tools(domain)
    assert tools["search_product"]("반팔티는")["resolved_product_id"] == "P3004"
    assert tools["search_product"]("반팔은")["resolved_product_id"] == "P3004"
