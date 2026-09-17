# -*- coding: utf-8 -*-
import pytest

from server.turnscore import TurnScore, build_score_messages, score_turn


def test_prompt_includes_answer_and_tool_results():
    msgs = build_score_messages(
        question="배송비 얼마예요?",
        answer="기본 배송비는 2,500원입니다.",
        tool_results={"get_shipping_policy": {"base_shipping_fee": 2500}},
        route="SHIPPING", guardrail={"ok": True, "violations": []},
        manual_rules="기본 배송비 2,500원")
    joined = " ".join(m["content"] if isinstance(m, dict) else str(m) for m in msgs)
    assert "2,500원" in joined and "get_shipping_policy" in joined
    assert "SHIPPING" in joined
    # 채점 기준 4가지가 프롬프트에 있어야 한다
    for k in ("매뉴얼", "조회", "말투", "응대"):
        assert k in joined


def test_score_turn_returns_structured_verdict():
    fake = lambda msgs: TurnScore(manual=5, evidence=4, tone=5, service=3, reason="근거는 충분하나 응대가 짧다")
    v = score_turn(fake, {"q": "배송비?", "a": "2,500원입니다.", "route": "SHIPPING",
                          "tools": ["get_shipping_policy"], "guardrail_ok": True})
    assert v.total == 17 and "응대" in v.reason


def test_total_is_derived_not_trusted_from_model():
    """모델이 total 을 엉뚱하게 줘도 4항목 합으로 다시 계산한다."""
    v = TurnScore(manual=1, evidence=1, tone=1, service=1, reason="x", total=999)
    assert v.total == 4


@pytest.mark.parametrize("bad", [0, 6, -1])
def test_scores_out_of_range_are_rejected(bad):
    with pytest.raises(Exception):
        TurnScore(manual=bad, evidence=3, tone=3, service=3, reason="x")
