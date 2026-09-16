import pytest
from server.domain import load_domain
from server.router import RouteDecision, build_router, make_rule_classifier
from server.prompts import build_route_guide, build_answer_rules


@pytest.fixture
def domain(modumall_dir):
    return load_domain(modumall_dir)


def fixed(route, conf):
    return lambda q: RouteDecision(route=route, confidence=conf, reason="테스트")


def test_handle_when_confident(domain):
    g = build_router(domain, 0.5, classify=fixed("SHIPPING", 0.9))
    out = g.invoke({"question": "배송비 얼마예요?"})
    assert out["route"] == "SHIPPING"
    assert out["action"] == "HANDLE"
    assert out["message"] is None


def test_escalate_below_threshold(domain):
    g = build_router(domain, 0.5, classify=fixed("SHIPPING", 0.49))
    out = g.invoke({"question": "그거요"})
    assert out["action"] == "ESCALATE"
    assert out["message"] == domain.escalate_message


def test_threshold_boundary_is_inclusive(domain):
    g = build_router(domain, 0.5, classify=fixed("SHIPPING", 0.5))
    assert g.invoke({"question": "x"})["action"] == "HANDLE"


def test_other_is_out_of_scope(domain):
    g = build_router(domain, 0.5, classify=fixed("OTHER", 0.95))
    out = g.invoke({"question": "홍대점 몇 시까지 해요?"})
    assert out["action"] == "OUT_OF_SCOPE"
    assert out["message"] == domain.out_of_scope_message


def test_route_decision_rejects_unknown_route():
    with pytest.raises(Exception):
        RouteDecision(route="배송문의", confidence=0.9, reason="x")


def test_rule_classifier_baseline():
    c = make_rule_classifier()
    assert c("환불 언제 되나요?").route == "RETURN_REFUND"
    assert c("배송비 얼마예요?").route == "SHIPPING"
    assert c("홍대 매장 어디예요?").route == "OTHER"
    assert c("ㅇㅇ").confidence < 0.5


def test_route_guide_contains_domain_definitions(domain):
    guide = build_route_guide(domain)
    for r, d in domain.routes.items():
        assert r in guide and d.definition in guide
    assert "0.5 미만" in guide


def test_answer_rules_mention_procedure(domain):
    rules = build_answer_rules(domain)
    assert "search_product" in rules
    assert "null" in rules
    assert domain.name in rules
    assert "not_in_catalog 가 false" in rules


def test_answer_rules_have_required_tool_table(domain):
    rules = build_answer_rules(domain)
    assert "[라우트별 필수 도구]" in rules
    for tool in ("get_shipping_policy", "get_return_policy", "get_return_status",
                 "get_product_detail", "get_product_options", "get_restock_info"):
        assert tool in rules, tool


def test_answer_rules_keep_step4_sentence(domain):
    rules = build_answer_rules(domain)
    assert "도구를 부를 수 있는데도 부르지 않고 되묻는 것은 실패다" in rules
