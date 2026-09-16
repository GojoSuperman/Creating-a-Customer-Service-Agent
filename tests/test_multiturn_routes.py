# -*- coding: utf-8 -*-
from eval.eval_router import load_multiturn, route_conversation, score_multiturn
from server.domain import ROUTES, load_domain
from server.router import RouteDecision, build_router


def test_multiturn_dataset_shape(modumall_dir):
    convs = load_multiturn(modumall_dir / "eval" / "multiturn_routes.json")
    assert len(convs) == 20
    for c in convs:
        assert 2 <= len(c["turns"]) <= 3, c["conv_id"]
        for t in c["turns"]:
            assert t["route"] in ROUTES and isinstance(t["followup"], bool), c["conv_id"]
        assert c["turns"][0]["followup"] is False
    kinds = {"switch": sum(1 for c in convs if c["turns"][1]["followup"] is False and not c["turns"][0].get("clarify")),
             "inherit": sum(1 for c in convs if c["turns"][1]["followup"] is True and not c["turns"][0].get("clarify")),
             "clarify": sum(1 for c in convs if c["turns"][0].get("clarify"))}
    assert kinds["clarify"] == 5 and kinds["inherit"] == 6 and kinds["switch"] == 9


def test_route_conversation_passes_prev_route_and_history(modumall_dir):
    domain = load_domain(modumall_dir)
    seen = []

    def classify(q):
        seen.append(q)
        return RouteDecision(route="SHIPPING", confidence=0.9, reason="t", is_followup=len(seen) > 1)
    g = build_router(domain, 0.5, classify=classify)
    turns = [{"text": "a", "route": "SHIPPING", "followup": False}, {"text": "b", "route": "SHIPPING", "followup": True}]
    preds = route_conversation(g, turns)
    assert "[직전 라우트] SHIPPING" in seen[1] and "[직전 문의] a" in seen[1]
    assert preds[1]["is_followup"] is True and preds[1]["route"] == "SHIPPING"
    seen.clear()
    route_conversation(g, turns, use_history=False)
    assert "[직전" not in seen[1]


def test_route_conversation_inherits_route_on_followup(modumall_dir):
    domain = load_domain(modumall_dir)
    # 턴1 확신도 0.9 → 게이트 통과(HANDLE) → 턴2 는 후속 발화이므로 직전 라우트를 이어받는다
    decisions = iter([RouteDecision(route="SHIPPING", confidence=0.9, reason="t"),
                      RouteDecision(route="PRODUCT_INFO", confidence=0.8, reason="t", is_followup=True)])
    g = build_router(domain, 0.5, classify=lambda q: next(decisions))
    turns = [{"text": "a", "route": "SHIPPING", "followup": False}, {"text": "b", "route": "SHIPPING", "followup": True}]
    preds = route_conversation(g, turns)
    assert preds[1]["route"] == "SHIPPING"


def test_route_conversation_does_not_inherit_from_gated_turn(modumall_dir):
    domain = load_domain(modumall_dir)
    # 턴1 확신도 0.2 → 게이트 미달(ESCALATE) → 그 라우트는 추측이므로 이어받기 앵커로 쓰지 않는다
    decisions = iter([RouteDecision(route="SHIPPING", confidence=0.2, reason="t"),
                      RouteDecision(route="PRODUCT_INFO", confidence=0.8, reason="t", is_followup=True)])
    g = build_router(domain, 0.5, classify=lambda q: next(decisions))
    turns = [{"text": "a", "route": "SHIPPING", "followup": False}, {"text": "b", "route": "PRODUCT_INFO", "followup": True}]
    preds = route_conversation(g, turns)
    assert preds[1]["route"] == "PRODUCT_INFO" and preds[1]["is_followup"] is True


def test_score_multiturn():
    convs = [{"conv_id": "x", "turns": [{"text": "a", "route": "SHIPPING", "followup": False},
                                        {"text": "b", "route": "SHIPPING", "followup": True}]}]
    preds = [[{"route": "SHIPPING", "is_followup": False, "confidence": 0.9},
              {"route": "PRODUCT_INFO", "is_followup": False, "confidence": 0.9}]]
    s = score_multiturn(convs, preds)
    assert s["turn1_acc"] == 1.0 and s["later_acc"] == 0.0
    assert s["followup_confusion"].loc[True, False] == 1
