# -*- coding: utf-8 -*-
from eval.scoring import score_turn, infer_action, load_first_turns, self_check


def test_score_turn_passes_reference():
    expect = {"action": "ANSWER", "tools": ["get_shipping_policy"], "must": ["50000", "2500"], "forbid": ["40000"]}
    ok, fails = score_turn(expect, "가방·잡화는 기준 50,000원이라 배송비 2,500원이 발생합니다.", ["get_shipping_policy"], "ANSWER")
    assert ok, fails


def test_score_turn_detects_each_failure():
    expect = {"action": "ANSWER", "tools": ["get_shipping_policy"], "must": ["50000"], "forbid": ["40000"]}
    ok, fails = score_turn(expect, "기준은 40,000원입니다.", [], "ASK")
    assert not ok
    assert any(f.startswith("action") for f in fails)
    assert any("tools 미호출" in f for f in fails)
    assert any("must 누락" in f for f in fails)
    assert any("forbid 위반" in f for f in fails)


def test_ask_requires_question_form():
    ok, fails = score_turn({"action": "ASK", "must": [], "forbid": []}, "확인했습니다.", [], "ASK")
    assert not ok and any("되묻는" in f for f in fails)


def test_infer_action():
    assert infer_action("어떤 상품인가요?", {}) == "ASK"
    assert infer_action("확인했습니다.", {"get_order_status": {"is_external_channel": True}}) == "OUT_OF_SCOPE"
    assert infer_action("기준은 100,000원입니다.", {"get_shipping_policy": {}}) == "ANSWER"


def test_goldenset_self_check_all_pass(modumall_dir):
    cases = load_first_turns(modumall_dir / "eval" / "answer_goldenset.json")
    assert len(cases) == 34
    assert self_check(cases) == []
