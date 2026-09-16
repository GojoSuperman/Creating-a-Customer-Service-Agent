# -*- coding: utf-8 -*-
from eval.scoring import score_turn, infer_action, load_first_turns, self_check, load_regression_cases, score_regression
from server.domain import ROUTES


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


def test_first_turn_uses_turn_level_route(modumall_dir):
    """멀티턴 대화에서 첫 턴의 route는 턴 레벨 값 (마지막 호프가 아님)"""
    cases = load_first_turns(modumall_dir / "eval" / "answer_goldenset.json")

    # C-014는 SHIPPING→RETURN_REFUND 대화이지만
    # 첫 턴(turn 2)은 SHIPPING 단계에 속해야 함
    c014 = next((c for c in cases if c["conv_id"] == "C-014"), None)
    assert c014 is not None, "C-014 not found in goldenset"
    assert c014["route"] == "SHIPPING", f"C-014 route should be SHIPPING, got {c014['route']}"

    # 모든 경우의 route는 ROUTES에 속해야 함 (OTHER 제외 가능)
    for case in cases:
        assert case["route"] in ROUTES, f"{case['conv_id']}: {case['route']} not in {ROUTES}"


def test_aggregate_runs_thresholds():
    from eval.scoring import aggregate_runs

    assert aggregate_runs([True]) == "PASS"
    assert aggregate_runs([False]) == "FAIL"
    assert aggregate_runs([True, True, False]) == "PASS"     # 2/3 >= ceil(3*0.67)=3? -> 아래 참조
    assert aggregate_runs([True, False, False]) == "FLAP"
    assert aggregate_runs([False, False, False]) == "FAIL"
    assert aggregate_runs([True, True, True, False, False]) == "FLAP"   # 3/5 < ceil(5*0.67)=4
    assert aggregate_runs([True, True, True, True, False]) == "PASS"


def test_score_regression():
    exp = {"action": ["ASK", "ESCALATE"], "forbid": ["40000", "40,000"]}
    assert score_regression(exp, "어떤 상품인지 말씀해 주시겠어요?", "ASK") == (True, [])
    ok, fails = score_regression(exp, "무료배송 기준은 40,000원입니다.", "ANSWER")
    assert not ok and any(f.startswith("action") for f in fails) and any("forbid" in f for f in fails)


def test_score_regression_forbid_normalizes_man_cheon():
    # forbid 값이 아라비아 숫자("40000")라도 답변 속 "4만원" 은 같은 값으로 정규화되어야 걸린다(F2).
    ok, fails = score_regression({"action": ["ANSWER"], "forbid": ["40000"]},
                                 "무료배송 기준은 4만원입니다.", "ANSWER")
    assert not ok
    assert any("forbid" in f for f in fails)


def test_regression_cases_load(modumall_dir):
    cases = load_regression_cases(modumall_dir / "eval" / "regression_cases.json")
    assert len(cases) >= 6
    assert {c["category"] for c in cases} >= {"injection", "unknown_id"}
    for c in cases:
        assert isinstance(c["expect"]["action"], list) and c["expect"]["action"]
        assert "forbid" in c["expect"]


def test_score_turn_must_normalizes_korean_myriad():
    ok, fails = score_turn({"action": "ANSWER", "tools": [], "must": ["40000"], "forbid": []},
                           "4만 원 이상 구매하시면 무료배송입니다.", [], "ANSWER")
    assert ok, fails


from eval.scoring import fail_kinds, missing_must, needs_judge


def test_fail_kinds_maps_each_prefix():
    fails = ['action: 기대 ANSWER != 실제 ASK', "tools 미호출: ['get_shipping_policy']",
             'must 누락: "2500"', 'forbid 위반: "40000"', "ASK 인데 되묻는 문장이 아님"]
    assert fail_kinds(fails) == ["action", "tools 미호출", "must 누락", "forbid 위반", "ASK 형식"]


def test_missing_must_extracts_strings():
    assert missing_must(['must 누락: "2500"', 'must 누락: "품절"', 'action: x']) == ["2500", "품절"]


def test_needs_judge_only_when_all_fails_are_must():
    assert needs_judge(['must 누락: "2500"', 'must 누락: "품절"']) is True
    assert needs_judge(['must 누락: "2500"', "tools 미호출: ['x']"]) is False
    assert needs_judge([]) is False


def test_verdict_label_all_rule_pass():
    from eval.eval_answer import verdict_label
    outs = [{"ok": True, "how": "규칙"}] * 3
    assert verdict_label(outs) == ("PASS(규칙)", True)


def test_verdict_label_majority_rule_pass():
    from eval.eval_answer import verdict_label
    outs = [{"ok": True, "how": "규칙"}, {"ok": True, "how": "규칙"}, {"ok": False, "how": None}]
    assert verdict_label(outs) == ("PASS(규칙)", True)


def test_verdict_label_judge_majority():
    from eval.eval_answer import verdict_label
    outs = [{"ok": True, "how": "규칙"}, {"ok": True, "how": "judge"}, {"ok": True, "how": "judge"}]
    assert verdict_label(outs) == ("PASS(judge)", False)


def test_verdict_label_flap():
    from eval.eval_answer import verdict_label
    outs = [{"ok": True, "how": "judge"}, {"ok": False, "how": None}, {"ok": False, "how": None}]
    assert verdict_label(outs) == ("FLAP", False)


def test_verdict_label_all_fail():
    from eval.eval_answer import verdict_label
    outs = [{"ok": False, "how": None}] * 3
    assert verdict_label(outs) == ("FAIL", False)
