# -*- coding: utf-8 -*-
from eval.judge import JudgeVerdict, judge_turn, judge_self_check, build_judge_messages


class FakeJudge:
    """messages 를 받아 미리 정한 판정을 돌려준다. 받은 메시지를 기록해 프롬프트 내용을 검사한다."""
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.seen = []

    def __call__(self, messages):
        self.seen.append(messages)
        return self.verdicts.pop(0)


EXPECT = {"action": "ANSWER", "must": ["품절", "재입고"], "forbid": [],
          "rubric": "품절 상태와 재입고 미확정을 말해야 한다.",
          "reference": "현재 품절이며 재입고일은 아직 확정되지 않았습니다."}


def test_judge_turn_returns_verdict_and_sends_context():
    j = FakeJudge([JudgeVerdict(passed=True, reason="같은 뜻")])
    v = judge_turn(j, "티셔츠 재고 있어요?", EXPECT, "지금은 재고가 없고 언제 들어올지 정해지지 않았습니다.", ["품절", "재입고"])
    assert v.passed is True
    system, human = j.seen[0][0][1], j.seen[0][1][1]
    assert "reference" in system or "모범 답안" in system
    assert "티셔츠 재고 있어요?" in human
    assert "품절" in human and "재입고" in human
    assert EXPECT["reference"] in human and EXPECT["rubric"] in human


def test_judge_messages_do_not_include_manual():
    msgs = build_judge_messages("q", EXPECT, "a", ["품절"])
    joined = " ".join(m[1] for m in msgs)
    assert "[업무 매뉴얼]" not in joined


def test_judge_self_check_reports_failing_conv_ids():
    cases = [{"conv_id": "C-1", "question": "q1", "expect": EXPECT},
             {"conv_id": "C-2", "question": "q2", "expect": EXPECT}]
    j = FakeJudge([JudgeVerdict(passed=True, reason=""), JudgeVerdict(passed=False, reason="틀림")])
    assert judge_self_check(j, cases) == ["C-2"]
    # 자기 검증은 reference 를 답변으로, must 전부를 누락 목록으로 넘긴다
    assert EXPECT["reference"] in j.seen[0][1][1]
