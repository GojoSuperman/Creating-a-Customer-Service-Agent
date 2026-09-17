# -*- coding: utf-8 -*-
from server.turnscore import (TurnVerdict, build_verdict_messages, judge_turn,
                              machine_flags)


def test_prompt_includes_answer_and_tool_results():
    msgs = build_verdict_messages(
        question="배송비 얼마예요?",
        answer="기본 배송비는 2,500원입니다.",
        tool_results={"get_shipping_policy": {"base_shipping_fee": 2500}},
        route="SHIPPING", guardrail={"ok": True, "violations": []},
        manual_rules="기본 배송비 2,500원")
    joined = " ".join(m["content"] if isinstance(m, dict) else str(m) for m in msgs)
    assert "2,500원" in joined and "get_shipping_policy" in joined
    assert "SHIPPING" in joined
    # 점수를 매기라고 하지 않는다 — should_have_asked / contradicts_tools 두 가지만 판정하라고 요청한다.
    assert "should_have_asked" in joined or "단정" in joined
    assert "조회 결과" in joined


def test_judge_turn_combines_machine_and_llm_verdict():
    """기계 플래그(machine_flags)와 LLM 판정(should_have_asked 등)이 하나의 TurnVerdict로 합쳐진다."""
    class _FakeLLM:
        should_have_asked = False
        contradicts_tools = False
        note = "조회 결과와 일치하고 근거도 충분하다"

    fake = lambda msgs: _FakeLLM()
    v = judge_turn(fake, {"q": "배송비?", "a": "2,500원입니다.", "route": "SHIPPING",
                         "action": "ANSWER", "tools": ["get_shipping_policy"],
                         "guardrail_ok": True, "violations": [], "confidence": 0.9})
    assert isinstance(v, TurnVerdict)
    assert v.safety_flags == [] and v.ability_flags == []
    assert v.should_have_asked is False and v.contradicts_tools is False
    assert "일치" in v.note
    # 합산 점수가 없다 — total 같은 필드가 없어야 한다
    assert not hasattr(v, "total")


def test_machine_flags_from_guardrail_and_tools():
    turn = {"q": "배송비?", "a": "무료배송 기준은 40,000원입니다.", "action": "ANSWER",
            "tools": [], "guardrail": {"ok": False, "violations": [{"type": "출처 불명 수치", "detail": "40000"}]},
            "confidence": 0.82}
    m = machine_flags(turn)
    assert any("출처 불명" in f for f in m["safety"])
    assert any("도구" in f for f in m["ability"])   # 조회 없이 단정


def test_machine_flags_clean_turn():
    turn = {"q": "배송비?", "a": "기본 배송비는 2,500원입니다.", "action": "ANSWER",
            "tools": ["get_shipping_policy"], "guardrail": {"ok": True, "violations": []}, "confidence": 0.93}
    m = machine_flags(turn)
    assert m["safety"] == [] and m["ability"] == []


def test_ask_turn_is_ability_gap_not_safety_failure():
    """되묻기는 '안전한 실패' 다 — 능력 축에만 표시하고 안전 위반으로 세지 않는다(교육자료)."""
    m = machine_flags({"q": "이거 언제 와요?", "a": "주문번호를 알려주시겠어요?", "action": "ASK",
                       "tools": [], "guardrail": {"ok": True, "violations": []}, "confidence": 0.7})
    assert m["safety"] == []
    assert any("답변에 도달" in f or "되물" in f for f in m["ability"])


def test_machine_flags_accepts_real_turn_log_shape():
    """실제 턴 로그(server/pipeline.py)는 violations 를 최상위 키로 남긴다."""
    turn = {"q": "환불 언제 돼요?", "a": "환불은 영업일 기준 3일 내 처리됩니다.", "action": "ANSWER",
            "tools": [], "guardrail_ok": False,
            "violations": [{"type": "출처 불명 수치", "detail": "3"}], "confidence": 0.6}
    m = machine_flags(turn)
    assert any("출처 불명" in f for f in m["safety"])
    assert any("도구" in f for f in m["ability"])


def test_escalate_turn_is_ability_gap():
    m = machine_flags({"q": "환불 왜 안 돼요", "a": "상담원에게 연결해 드릴게요.", "action": "ESCALATE",
                       "tools": [], "violations": [], "confidence": 0.4})
    assert m["safety"] == []
    assert any("이관" in f for f in m["ability"])


def test_out_of_scope_is_not_ability_gap():
    """범위 밖 안내는 올바른 처리다(매뉴얼 §7.1) — 능력 미달로 세지 않는다."""
    m = machine_flags({"q": "홍대점 몇 시까지 해요?", "a": "그 문의는 저희가 답해드리기 어렵습니다.",
                       "action": "OUT_OF_SCOPE", "tools": [], "violations": [], "confidence": 0.9})
    assert m["safety"] == [] and m["ability"] == []


def test_low_confidence_answer_is_safety_flag_with_configured_threshold():
    """확신도가 임계값보다 낮은데 단정했으면 안전 플래그. 임계값은 하드코딩하지 않고 인자로 받는다."""
    turn = {"q": "이 옷 재입고 언제 돼요?", "a": "다음 주에 재입고됩니다.", "action": "ANSWER",
            "tools": ["get_product"], "violations": [], "confidence": 0.3}
    m_default = machine_flags(turn)          # 임계값을 안 주면 확신도 판정을 하지 않는다
    assert not any("확신도" in f for f in m_default["safety"])
    m = machine_flags(turn, conf_threshold=0.5)
    assert any("확신도" in f for f in m["safety"])
    # 임계값을 넘으면 안 걸린다
    m_ok = machine_flags(turn, conf_threshold=0.2)
    assert not any("확신도" in f for f in m_ok["safety"])
