# 3차 모두몰 강화 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 답변 정답셋 통과율을 50%에서 70% 이상으로, 경계모호 15건의 확신 처리(위험)를 5건 이하로 내린다. 라우팅 F1 0.945와 회귀 6/6은 유지한다.

**Architecture:** 평가 쪽은 규칙 채점 실패 중 must 누락 건만 LLM judge 로 재판정하고(`eval/judge.py`), 라우터는 2순위 라우트·마진·followup 을 구조화 출력에 추가해 게이트와 파이프라인이 쓴다. 도구 매핑·동의어는 기존 함수 안의 분기 추가다. 측정 스크립트(`eval_answer`, `eval_hard`, `eval_router`)는 새 플래그로 확장하고 기본 동작은 바꾸지 않는다.

**Tech Stack:** Python 3.12, LangGraph, LangChain(`init_chat_model` + `with_structured_output`), pydantic, pandas, pytest. 실행은 항상 `.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-09-17-modumall-hardening-design.md`

## Global Constraints

- 코드 구현은 Opus 서브에이전트가 하고, 컨트롤러(Fable)는 브리프·리뷰·판정만 한다.
- `pytest` 는 API 키 없이 전부 통과해야 한다. LLM 을 쓰는 함수는 반드시 호출 가능 객체를 주입받아 가짜로 대체할 수 있어야 한다.
- 기존 평가 스크립트의 **플래그 없는 기본 동작은 그대로** 유지한다(`--judge`, `--multiturn` 이 꺼지면 이전과 같은 출력).
- 프롬프트로 가는 고객 dict 에 주소·전화를 넣지 않는다. 통화 중 말한 전화번호로 고객 조회 금지 규칙 유지.
- `.env` 의 키는 절대 출력·커밋하지 않는다. 커밋 메시지는 한국어, 끝에 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- 주석·로그·문서는 한국어. 식별자는 영어.
- 평가셋(`split == "eval"`)과 골든셋 내용을 프롬프트에 넣지 않는다.
- 서버 포트 8000 은 다른 프로젝트가 쓴다. 수동 확인은 `PORT=8010`.

---

## 파일 구조

| 파일 | 책임 | 변경 |
|---|---|---|
| `eval/judge.py` | judge 프롬프트·구조화 출력·자기 검증 (신규) | Task 1 |
| `eval/scoring.py` | 실패 사유 분류 `fail_kinds`, `missing_must`, `needs_judge` | Task 1 |
| `eval/eval_answer.py` | `--judge`, 세 갈래 판정, 사유 분포 | Task 1 |
| `server/config.py` | `judge_model`, `conf_margin` | Task 1, 3 |
| `server/prompts.py` | 필수 도구 표·자기 점검, 라우터 후보·followup 지침 | Task 2, 3, 5 |
| `server/router.py` | `route_alt`·`alt_confidence`·`is_followup`, 마진 게이트 | Task 3, 5 |
| `server/pipeline.py` | `compose_router_input`, `routes` 이력, followup 덮어쓰기, escalate 도구 매핑, TurnResult 필드 | Task 3, 5, 7 |
| `web/panel.js` | 디버그 패널에 2순위 라우트·followup 표시 | Task 3 |
| `eval/calibration.py` | `gate_grid`, `recommend_gate` | Task 4 |
| `eval/eval_hard.py` | 격자 표·마진 보정표·추천값 | Task 4 |
| `domains/modumall/eval/multiturn_routes.json` | 멀티턴 라우트 정답셋 (신규) | Task 6 |
| `eval/eval_router.py` | `--multiturn`, `--no-history` | Task 6 |
| `server/tools.py` | 동의어 조사 제거, 짧은 질의 폴백 차단 | Task 8 |
| `server/domain.py` | 동의어 값 검증 | Task 8 |
| `domains/modumall/domain.json` | 복합어 동의어 | Task 8 |
| `.env.example`, `README.md` | 새 변수·명령·실험표 4행 | Task 3, 9 |

---

### Task 1: LLM-as-judge 채점기 (규칙 실패의 must 누락 건만)

**Files:**
- Create: `eval/judge.py`
- Modify: `eval/scoring.py` (끝에 함수 3개 추가)
- Modify: `eval/eval_answer.py`
- Modify: `server/config.py:14-25, 29-41`
- Test: `tests/test_judge.py` (신규), `tests/test_scoring.py` (추가)

**Interfaces:**
- Produces: `eval.scoring.fail_kinds(fails: list[str]) -> list[str]` (값은 `"action" | "tools 미호출" | "must 누락" | "forbid 위반" | "ASK 형식"`), `eval.scoring.missing_must(fails) -> list[str]`, `eval.scoring.needs_judge(fails) -> bool`
- Produces: `eval.judge.JudgeVerdict(passed: bool, reason: str)`, `eval.judge.make_judge(model: str) -> Callable[[list], JudgeVerdict]`, `eval.judge.judge_turn(judge, question, expect, answer, missing) -> JudgeVerdict`, `eval.judge.judge_self_check(judge, cases) -> list[str]`
- Produces: `Settings.judge_model: str` (환경 변수 `JUDGE_MODEL`, 기본 `gpt-4.1-mini`)

- [ ] **Step 1: scoring 헬퍼 실패 테스트 작성**

`tests/test_scoring.py` 끝에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_scoring.py -q -k "fail_kinds or missing_must or needs_judge"`
Expected: ImportError (fail_kinds 없음)

- [ ] **Step 3: scoring 헬퍼 구현**

`eval/scoring.py` 끝에 추가:

```python
_KIND_PREFIX = (("action:", "action"), ("tools 미호출:", "tools 미호출"), ("must 누락:", "must 누락"),
                ("forbid 위반:", "forbid 위반"), ("ASK 인데", "ASK 형식"))


def fail_kinds(fails: list) -> list:
    """score_turn 실패 문자열을 사유 종류 5가지로 분류한다. 리포트의 사유 분포에 쓴다."""
    kinds = []
    for f in fails:
        for prefix, kind in _KIND_PREFIX:
            if f.startswith(prefix):
                kinds.append(kind)
                break
    return kinds


def missing_must(fails: list) -> list:
    """'must 누락: "X"' 문자열에서 X 만 뽑는다."""
    return [m.group(1) for f in fails for m in [re.match(r'must 누락: "(.*)"$', f)] if m]


def needs_judge(fails: list) -> bool:
    """규칙 실패가 must 누락뿐일 때만 judge 대상이다. action·tools·forbid·ASK 형식 실패는 judge 로 완화하지 않는다."""
    kinds = fail_kinds(fails)
    return bool(kinds) and all(k == "must 누락" for k in kinds)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_scoring.py -q`
Expected: 전부 PASS

- [ ] **Step 5: judge 실패 테스트 작성**

`tests/test_judge.py` 신규:

```python
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
```

- [ ] **Step 6: 실패 확인**

Run: `.venv/bin/pytest tests/test_judge.py -q`
Expected: ModuleNotFoundError (eval.judge 없음)

- [ ] **Step 7: judge 구현**

`eval/judge.py` 신규:

```python
# -*- coding: utf-8 -*-
"""LLM-as-judge. 규칙 채점이 'must 누락' 만으로 떨어뜨린 답변을 의미 기준으로 다시 본다.

매뉴얼 본문은 주지 않는다. 판정 기준은 골든셋의 rubric 과 reference 가 이미 담고 있고,
매뉴얼을 주면 judge 가 스스로 답을 지어내 채점하는 문제가 생긴다.
judge 는 호출 가능 객체(messages -> JudgeVerdict)로 주입해 테스트에서 가짜로 바꾼다.
"""
from typing import Callable

from pydantic import BaseModel, Field

SYSTEM = """\
너는 쇼핑몰 전화 상담 답변의 채점관이다. 규칙 채점기가 답변에서 특정 문자열(must 항목)을 찾지 못해
실패시킨 건을 다시 본다. 네가 판단할 것은 하나뿐이다.

누락된 must 항목 각각에 대해, 답변이 같은 사실을 다른 표현으로 전달했는가.

기준:
- 모범 답안(reference)과 채점 기준(rubric)에 적힌 사실이 답변에 같은 뜻으로 들어 있으면 통과다.
  예: must "품절" 인데 답변이 "재고가 없습니다" 라면 같은 사실이다.
- must 항목 하나라도 사실이 다르거나 빠졌으면 실패다. 숫자는 값이 같아야 한다.
- 답변이 reference 에 없는 사실을 추가로 말했다고 감점하지 않는다. 그것은 다른 검사(forbid)가 본다.
- 말투·길이·순서는 보지 않는다.
"""


class JudgeVerdict(BaseModel):
    passed: bool = Field(description="누락된 must 항목이 전부 다른 표현으로 전달되었으면 true")
    reason: str = Field(description="판정 근거 한 문장. 실패면 어느 must 항목이 왜 빠졌는지 쓴다")


def build_judge_messages(question: str, expect: dict, answer: str, missing: list) -> list:
    must_lines = "\n".join(f"- {m}" for m in missing)
    human = f"""[고객 질문]
{question}

[채점 기준 rubric]
{expect.get("rubric", "")}

[모범 답안 reference]
{expect.get("reference", "")}

[규칙 채점기가 찾지 못한 must 항목]
{must_lines}

[실제 답변]
{answer}
"""
    return [("system", SYSTEM), ("human", human)]


def make_judge(model: str) -> Callable[[list], JudgeVerdict]:
    from langchain.chat_models import init_chat_model
    chain = init_chat_model(model, temperature=0, timeout=60, max_retries=8).with_structured_output(JudgeVerdict)
    return chain.invoke


def judge_turn(judge: Callable[[list], JudgeVerdict], question: str, expect: dict,
               answer: str, missing: list) -> JudgeVerdict:
    return judge(build_judge_messages(question, expect, answer, missing))


def judge_self_check(judge: Callable[[list], JudgeVerdict], cases: list) -> list:
    """모범 답안(reference)을 답변으로 넣고 must 전부를 누락 목록으로 넘겨 전부 통과하는지 본다.
    하나라도 실패하면 judge 프롬프트가 틀린 것이다. 실패한 conv_id 목록을 돌려준다."""
    bad = []
    for c in cases:
        e = c["expect"]
        v = judge_turn(judge, c["question"], e, e["reference"], list(e.get("must", [])))
        if not v.passed:
            bad.append(c["conv_id"])
    return bad
```

- [ ] **Step 8: 통과 확인**

Run: `.venv/bin/pytest tests/test_judge.py tests/test_scoring.py -q`
Expected: 전부 PASS

- [ ] **Step 9: Settings 에 judge_model 추가**

`server/config.py` 의 `Settings` 끝(`tts_voice` 뒤)에 `judge_model: str = "gpt-4.1-mini"` 를 넣고, `load_settings()` 에 `judge_model=os.environ.get("JUDGE_MODEL", "gpt-4.1-mini"),` 를 추가한다. `.env.example` 에 `# JUDGE_MODEL=gpt-4.1-mini   # eval_answer --judge 가 쓰는 채점 모델` 한 줄 추가.

- [ ] **Step 10: eval_answer 에 --judge 연결**

`eval/eval_answer.py` 를 다음으로 바꾼다(전체 교체. 플래그 없으면 기존 출력과 동일해야 한다):

```python
# -*- coding: utf-8 -*-
"""② 1턴 답변 평가.  .venv/bin/python -m eval.eval_answer [--limit N] [--domain modumall] [--runs N] [--judge]

라우트는 정답셋 값을 그대로 쓴다(라우터 실패와 섞지 않기 위해). 채점하는 것은 조회와 답변 생성이다.
--judge 를 켜면 규칙 채점이 must 누락만으로 떨어뜨린 건을 LLM judge 가 의미 기준으로 다시 본다.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from eval.judge import judge_self_check, judge_turn, make_judge
from eval.scoring import (aggregate_runs, fail_kinds, infer_action, load_first_turns, missing_must,
                          needs_judge, score_turn, self_check)
from server.answer import Answerer
from server.config import load_settings
from server.domain import load_domain

AUTO_ACTIONS = {"ANSWER", "ASK", "OUT_OF_SCOPE"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--runs", type=int, default=1, help="케이스당 실행 횟수. 2 이상이면 플랩 감지")
    ap.add_argument("--judge", action="store_true", help="must 누락만으로 실패한 건을 LLM judge 로 재판정")
    args = ap.parse_args()

    s = load_settings()
    domain = load_domain(s.domains_root / (args.domain or s.domain))
    cases = load_first_turns(domain.path / "eval" / "answer_goldenset.json")
    bad = self_check(cases)
    print(f"[채점기 자기 검증] 모범 답안 {len(cases)}건 중 실패 {len(bad)}건 {bad if bad else '✅'}")
    if bad:
        raise SystemExit("채점기가 모범 답안을 통과시키지 못했습니다. 채점기부터 고치세요.")

    scored = [c for c in cases if c["expect"]["action"] in AUTO_ACTIONS]
    if args.limit:
        scored = scored[:args.limit]

    judge = None
    if args.judge:
        judge = make_judge(s.judge_model)
        jbad = judge_self_check(judge, scored)
        print(f"[judge 자기 검증 {s.judge_model}] 모범 답안 {len(scored)}건 중 실패 {len(jbad)}건 {jbad if jbad else '✅'}")
        if jbad:
            raise SystemExit("judge 가 모범 답안을 통과시키지 못했습니다. judge 프롬프트부터 고치세요.")

    answerer = Answerer(domain, model=s.answer_model, max_tool_turns=s.max_tool_turns)
    judge_calls = [0]

    def run(case):
        outs = []
        for _ in range(args.runs):
            text, results, calls = answerer.answer(case["question"], case["route"])
            action = infer_action(text, results)
            ok, fails = score_turn(case["expect"], text, list(results), action)
            how = "규칙" if ok else None
            if not ok and judge is not None and needs_judge(fails):
                judge_calls[0] += 1
                v = judge_turn(judge, case["question"], case["expect"], text, missing_must(fails))
                if v.passed:
                    ok, how = True, "judge"
                    fails = [f"(judge 통과) {v.reason}"]
                else:
                    fails = fails + [f"judge: {v.reason}"]
            outs.append({"ok": ok, "how": how, "fails": fails, "action": action, "text": text})
        return outs

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results_per_case = list(ex.map(run, scored))

    rows = []
    for c, outs in zip(scored, results_per_case):
        verdict = aggregate_runs([o["ok"] for o in outs])
        first = outs[0]
        hows = [o["how"] for o in outs if o["ok"]]
        label = verdict
        if verdict == "PASS":
            label = "PASS(judge)" if hows and all(h == "judge" for h in hows) else \
                    ("PASS(규칙+judge)" if "judge" in hows else "PASS(규칙)")
        rows.append({"conv": c["conv_id"], "기대": c["expect"]["action"], "실제": first["action"],
                     "ok": verdict == "PASS", "판정": label, "rule_ok": all(o["how"] == "규칙" for o in outs),
                     "fails": "; ".join(first["fails"]), "answer": first["text"], "runs": outs})
    res = pd.DataFrame(rows)
    if res.empty:
        print("채점할 항목이 없습니다")
        return
    print(f'\n채점 {len(res)}건 / 통과 {int(res["ok"].sum())}건 ({100 * res["ok"].mean():.1f}%)'
          f'   (자동 판정 불가 {len(cases) - len(scored)}건 제외)')
    if args.judge:
        print(f'  규칙 통과율 {int(res["rule_ok"].sum())}/{len(res)}   judge 포함 통과율 {int(res["ok"].sum())}/{len(res)}'
              f'   judge 호출 {judge_calls[0]}회')
        print("  " + "  ".join(f"{k} {v}" for k, v in res["판정"].value_counts().items()))
    print("\n[행동 판정 혼동] 행=기대, 열=실제")
    print(pd.crosstab(res["기대"], res["실제"]).to_string())
    kinds = [k for o in results_per_case for r in o if not r["ok"] for k in fail_kinds(r["fails"])]
    print(f"\n[실패 유형] runs={args.runs} 전체 실행 기준, 한 케이스에 여러 사유면 각각 셈")
    print(pd.Series(kinds).value_counts().to_string() if kinds else "  없음")
    print("\n[실패 사례] — 여기를 읽는 것이 개선의 출발점이다")
    for _, r in res[~res["ok"]].iterrows():
        print(f'  {r["conv"]} 기대={r["기대"]} 실제={r["실제"]}  {r["fails"][:120]}')
        print(f'      답변: {r["answer"][:100]}')

    if args.runs > 1:
        agg = res["판정"].str.replace(r"\(.*\)", "", regex=True)
        print(f"\n[플랩 감지] runs={args.runs}  " + "  ".join(f"{k} {v}" for k, v in agg.value_counts().items()))
        for _, r in res[agg == "FLAP"].iterrows():
            print(f"  FLAP {r['conv']}: " + " | ".join(("ok" if o["ok"] else "; ".join(o["fails"])[:50]) for o in r["runs"]))


if __name__ == "__main__":
    main()
```

- [ ] **Step 11: 전체 테스트와 import 확인**

Run: `.venv/bin/pytest -q && .venv/bin/python -c "import eval.eval_answer"`
Expected: 전부 PASS, import 오류 없음

- [ ] **Step 12: 커밋**

```bash
git add eval/judge.py eval/scoring.py eval/eval_answer.py server/config.py .env.example tests/test_judge.py tests/test_scoring.py
git commit -m "feat: LLM-as-judge 채점기 - 규칙 실패의 must 누락 건만 rubric·reference 로 재판정 (--judge)"
```

- [ ] **Step 13: 판정기 효과 측정 (키 필요, 컨트롤러가 실행 여부 결정)**

Run: `.venv/bin/python -m eval.eval_answer --workers 1 --runs 3 --judge 2>&1 | tee logs/eval-answer-judge-baseline.log`
기록: `규칙 통과율`, `judge 포함 통과율`, `judge 호출` 수, judge 자기 검증 결과. 이 값이 "판정기 효과"(에이전트 코드는 85b5543 과 동일)다. judge 자기 검증에 실패가 있으면 프롬프트를 고쳐 다시 돌린다. 값은 Task 9 에서 README 표에 옮긴다.

---

### Task 2: 도구 호출 유도 프롬프트

**Files:**
- Modify: `server/prompts.py:27-58` (`build_answer_rules`)
- Test: `tests/test_router.py` (build_answer_rules 를 이미 import 한다 — 여기에 추가)

**Interfaces:**
- Consumes: `build_answer_rules(domain) -> str`
- Produces: 같은 시그니처. 본문에 `[라우트별 필수 도구]` 표와 새 4단계 문구.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_router.py` 끝에 추가:

```python
def test_answer_rules_have_required_tool_table(domain):
    rules = build_answer_rules(domain)
    assert "[라우트별 필수 도구]" in rules
    for tool in ("get_shipping_policy", "get_return_policy", "get_return_status",
                 "get_product_detail", "get_product_options", "get_restock_info"):
        assert tool in rules, tool


def test_answer_rules_self_check_sentence(domain):
    rules = build_answer_rules(domain)
    assert "그 값을 준 도구 이름을 스스로 확인" in rules
    assert "도구를 부를 수 있는데 부르지 않고 되묻거나 답하는 것은 실패다" in rules
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_router.py -q -k answer_rules`
Expected: FAIL (문구 없음)

- [ ] **Step 3: 프롬프트 수정**

`server/prompts.py` `build_answer_rules` 에서 4단계 줄

```
4단계. 조회를 마친 뒤에 답변을 쓴다. 도구를 부를 수 있는데도 부르지 않고 되묻는 것은 실패다.
```

을 다음으로 바꾼다:

```
4단계. 답변 초안에 금액·기간·재고·진행 상태가 들어가면, 그 값을 준 도구 이름을 스스로 확인한다.
       매뉴얼 본문의 고정값(절대 규칙 1)이 아니고 도구 결과에도 없으면, 답하기 전에 그 도구를 부른다.
       도구를 부를 수 있는데 부르지 않고 되묻거나 답하는 것은 실패다.

[라우트별 필수 도구] 아래 상황이면 해당 도구를 반드시 부른 뒤에 답한다.
- SHIPPING 이고 상품이 특정됨 → get_shipping_policy(product_id, order_amount)
- RETURN_REFUND 이고 상품이 특정됨 → get_return_policy(product_id)
- RETURN_REFUND 이고 주문번호가 있음 → get_order_status 다음 get_return_status
- PRODUCT_INFO 이고 구성·소재·재고·개별 구매를 물음 → get_product_detail
- PRODUCT_INFO 이고 사이즈·색상·옵션을 물음 → get_product_options
- 품절·재입고를 물음 → get_restock_info
```

(`[라우트별 필수 도구]` 블록은 4단계 바로 아래, `절대 규칙` 위에 둔다.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전부 PASS (다른 테스트가 프롬프트 문구를 단언하면 그 테스트도 함께 확인)

- [ ] **Step 5: 커밋**

```bash
git add server/prompts.py tests/test_router.py
git commit -m "feat: 답변 프롬프트에 라우트별 필수 도구 표와 도구 자기 점검 규칙 추가"
```

- [ ] **Step 6: 측정 (키 필요, 컨트롤러 결정)**

Run: `.venv/bin/python -m eval.eval_answer --workers 1 --runs 3 --judge 2>&1 | tee logs/eval-answer-after-prompt.log`
비교: Task 1 Step 13 대비 `tools 미호출` 사유 건수와 judge 포함 통과율.

---

### Task 3: 확신도 마진 게이트 + 2순위 라우트

**Files:**
- Modify: `server/router.py` (RouteDecision, RouterState, build_router, node_gate)
- Modify: `server/prompts.py:10-25` (`build_route_guide` 확신도 지침)
- Modify: `server/config.py` (`conf_margin`)
- Modify: `server/pipeline.py` (AgentState, TurnResult, `_node_route`, `__init__` 의 build_router 호출, `turn()`)
- Modify: `web/panel.js:76-96`
- Modify: `.env.example`
- Test: `tests/test_router.py`, `tests/test_pipeline.py`

**Interfaces:**
- Produces: `RouteDecision.route_alt: Optional[Route] = None`, `RouteDecision.alt_confidence: float = 0.0`
- Produces: `build_router(domain, conf_threshold, classify=None, model=None, conf_margin: float = 0.0)` — 키워드 인자, 기본 0.0 은 마진 게이트 비활성
- Produces: `RouterState.route_alt`, `RouterState.alt_confidence`; `AgentState.route_alt`, `AgentState.alt_confidence`; `TurnResult.route_alt: Optional[str] = None`, `TurnResult.alt_confidence: Optional[float] = None`
- Produces: `Settings.conf_margin: float = 0.0` (환경 변수 `CONF_MARGIN`)

- [ ] **Step 1: 라우터 실패 테스트 작성**

`tests/test_router.py` 끝에 추가:

```python
def fixed_alt(route, conf, alt, alt_conf):
    return lambda q: RouteDecision(route=route, confidence=conf, reason="테스트",
                                   route_alt=alt, alt_confidence=alt_conf)


def test_margin_gate_escalates_when_alternatives_are_close(domain):
    g = build_router(domain, 0.5, classify=fixed_alt("PRODUCT_INFO", 0.7, "ORDER_PLACE", 0.6), conf_margin=0.2)
    out = g.invoke({"question": "낱개로도 구매 가능한가요?"})
    assert out["action"] == "ESCALATE"
    assert out["route_alt"] == "ORDER_PLACE" and out["alt_confidence"] == 0.6


def test_margin_gate_handles_when_margin_is_wide(domain):
    g = build_router(domain, 0.5, classify=fixed_alt("PRODUCT_INFO", 0.9, "ORDER_PLACE", 0.2), conf_margin=0.2)
    assert g.invoke({"question": "x"})["action"] == "HANDLE"


def test_margin_boundary_is_inclusive(domain):
    g = build_router(domain, 0.5, classify=fixed_alt("SHIPPING", 0.8, "RETURN_REFUND", 0.6), conf_margin=0.2)
    assert g.invoke({"question": "x"})["action"] == "HANDLE"


def test_no_alt_route_means_full_margin(domain):
    g = build_router(domain, 0.5, classify=fixed("SHIPPING", 0.6), conf_margin=0.9)
    out = g.invoke({"question": "x"})
    assert out["action"] == "HANDLE" and out["route_alt"] is None


def test_margin_disabled_by_default(domain):
    g = build_router(domain, 0.5, classify=fixed_alt("SHIPPING", 0.55, "RETURN_REFUND", 0.54))
    assert g.invoke({"question": "x"})["action"] == "HANDLE"


def test_route_guide_asks_for_alt_route(domain):
    guide = build_route_guide(domain)
    assert "route_alt" in guide and "0.5 미만" in guide
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_router.py -q`
Expected: 새 테스트 FAIL (route_alt 필드 없음 / conf_margin 인자 없음)

- [ ] **Step 3: router.py 수정**

`RouteDecision` 을 다음으로 교체:

```python
class RouteDecision(BaseModel):
    """고객 문의 한 건에 대한 라우팅 판단 결과."""
    route: Route = Field(description="가장 가능성 높은 라우트. 5개 값 중 하나만 사용한다.")
    confidence: float = Field(ge=0.0, le=1.0,
                              description="route 의 확신도. 두 라우트 사이에서 애매하면 0.5 미만으로 낮춘다.")
    reason: str = Field(description="그 라우트로 판단한 근거를 한 문장으로. 고객이 원하는 결과를 기준으로 쓴다.")
    route_alt: Optional[Route] = Field(default=None,
                                       description="두 번째로 가능성 높은 라우트. 다른 후보가 전혀 없으면 null.")
    alt_confidence: float = Field(default=0.0, ge=0.0, le=1.0,
                                  description="route_alt 의 확신도. route_alt 가 null 이면 0.")
```

`RouterState` 에 `route_alt: Optional[str]`, `alt_confidence: float` 추가.

`build_router` 시그니처를 `def build_router(domain: Domain, conf_threshold: float, classify=None, model=None, conf_margin: float = 0.0):` 로 바꾸고 노드를 다음으로 교체:

```python
    def node_classify(state: RouterState) -> RouterState:
        d = classify(state["question"])
        return {"route": d.route, "confidence": d.confidence, "reason": d.reason,
                "route_alt": d.route_alt, "alt_confidence": d.alt_confidence if d.route_alt else 0.0}

    def node_gate(state: RouterState) -> RouterState:
        # 마진 = 1순위 확신도 - 2순위 확신도. 2순위가 없으면 1.0(애매하지 않음)으로 본다.
        margin = 1.0 if state.get("route_alt") is None else state["confidence"] - state.get("alt_confidence", 0.0)
        if state["confidence"] < conf_threshold or margin < conf_margin:
            return {"action": "ESCALATE", "message": domain.escalate_message}
        if state["route"] == "OTHER":
            return {"action": "OUT_OF_SCOPE", "message": domain.out_of_scope_message}
        return {"action": "HANDLE", "message": None}
```

`make_rule_classifier` 는 그대로(route_alt 기본 None).

- [ ] **Step 4: 라우터 프롬프트 수정**

`server/prompts.py` `build_route_guide` 의 `[확신도 지침]` 블록을 다음으로 교체:

```
[확신도 지침]
가장 가능성 높은 라우트를 route 에, 두 번째로 가능성 높은 라우트를 route_alt 에 쓰고 각각의 확신도를
confidence, alt_confidence 로 낸다. 둘의 차이가 작을수록 애매한 문의다. 두 라우트 사이에서 결정하기
어려우면 confidence 를 0.5 미만으로 낮춰라. 애매한 것을 확신 있게 답하는 것보다, 애매하다고 밝히는
것이 이 시스템에서는 더 좋은 판단이다. 다른 후보가 전혀 없으면 route_alt 를 null 로 둔다.
```

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/pytest tests/test_router.py -q`
Expected: 전부 PASS

- [ ] **Step 6: 파이프라인 실패 테스트 작성**

`tests/test_pipeline.py` 끝에 추가:

```python
def test_turn_result_carries_alt_route(domain, settings):
    classify = lambda q: RouteDecision(route="PRODUCT_INFO", confidence=0.9, reason="t",
                                       route_alt="ORDER_PLACE", alt_confidence=0.3)
    router = build_router(domain, 0.5, classify=classify)
    ans = FakeAnswerer([("네 확인했습니다.", {"get_product_detail": {}}, [])])
    p = Pipeline(domain, settings, router=router, answerer=ans)
    cid, _, _ = p.start_call()
    r = p.turn(cid, "낱개로도 구매 가능한가요?")
    assert r.route_alt == "ORDER_PLACE" and r.alt_confidence == 0.3
    assert "route_alt" in r.to_dict()


def test_pipeline_passes_conf_margin_to_router(domain, modumall_dir, tmp_path, monkeypatch):
    import server.router as router_mod
    seen = {}
    real = router_mod.build_router

    def spy(domain_, threshold, classify=None, model=None, conf_margin=0.0):
        seen["margin"] = conf_margin
        return real(domain_, threshold, classify=lambda q: RouteDecision(route="SHIPPING", confidence=0.9, reason="t"),
                    conf_margin=conf_margin)
    monkeypatch.setattr(router_mod, "build_router", spy)
    s = Settings(router_model="x", answer_model="x", conf_threshold=0.5, max_tool_turns=3, guardrail_retry=1,
                 domain="modumall", domains_root=modumall_dir.parent, logs_dir=tmp_path / "logs",
                 clarify_max=1, conf_margin=0.25)
    Pipeline(domain, s, answerer=FakeAnswerer([]))
    assert seen["margin"] == 0.25
```

- [ ] **Step 7: 실패 확인**

Run: `.venv/bin/pytest tests/test_pipeline.py -q -k "alt_route or conf_margin"`
Expected: FAIL

- [ ] **Step 8: config·pipeline·panel 수정**

`server/config.py`: `Settings` 끝에 `conf_margin: float = 0.0`, `load_settings()` 에 `conf_margin=float(os.environ.get("CONF_MARGIN", "0.0")),`. `.env.example` 에 `# CONF_MARGIN=0.0   # 1순위-2순위 확신도 차이가 이보다 작으면 되묻기/이관. 기본값은 eval_hard 격자로 정한다` 추가.

`server/pipeline.py`:
- `AgentState` 에 `route_alt: Optional[str]`, `alt_confidence: float` 추가.
- `TurnResult` 끝에 `route_alt: Optional[str] = None`, `alt_confidence: Optional[float] = None` 추가.
- `__init__` 의 `router = build_router(domain, settings.conf_threshold, model=settings.router_model)` 을 `router = build_router(domain, settings.conf_threshold, model=settings.router_model, conf_margin=settings.conf_margin)` 으로.
- `_node_route` 의 `base` dict 에 `"route_alt": r.get("route_alt"), "alt_confidence": r.get("alt_confidence", 0.0)` 추가.
- `turn()` 의 `TurnResult(...)` 에 `route_alt=out.get("route_alt"), alt_confidence=out.get("alt_confidence")` 추가.

`web/panel.js` `addTurn` 의 라우트 행을 다음으로 교체:

```js
        <div class="row"><b>라우트</b> ${esc(r.route || "-")} <span class="${low ? "bad" : ""}">conf ${r.confidence == null ? "-" : r.confidence.toFixed(2)}</span>
          ${r.route_alt ? `<span class="muted">2순위 ${esc(r.route_alt)} ${Number(r.alt_confidence || 0).toFixed(2)}</span>` : ""}
          <span class="badge ${bad ? "badge-bad" : ""}">${esc(r.action)}</span>${retryBadge}</div>
```

- [ ] **Step 9: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전부 PASS

- [ ] **Step 10: 커밋**

```bash
git add server/router.py server/prompts.py server/config.py server/pipeline.py web/panel.js .env.example tests/test_router.py tests/test_pipeline.py
git commit -m "feat: 라우터가 2순위 라우트를 내고 확신도 마진(CONF_MARGIN)으로 게이트한다"
```

---

### Task 4: eval_hard 임계값×마진 격자와 마진 보정표

**Files:**
- Modify: `eval/calibration.py` (함수 2개 추가)
- Modify: `eval/eval_hard.py:36-62`
- Test: `tests/test_calibration.py`

**Interfaces:**
- Consumes: Task 3 의 `RouterState.route_alt`, `alt_confidence`
- Produces: `eval.calibration.gate_grid(df: pd.DataFrame, thresholds, margins) -> pd.DataFrame` — 입력 df 열: `confidence, alt_confidence, route_alt, route, route_expected, route_alt_expected, hard_type`; 출력 열: `임계값, 마진, 자동처리율, 경계모호위험, 비모호오이관`
- Produces: `eval.calibration.recommend_gate(grid: pd.DataFrame, max_risky: int = 5) -> Optional[dict]`
- Produces: `eval.calibration.MARGIN_EDGES = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 1.0001)`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_calibration.py` 끝에 추가:

```python
import pandas as pd
from eval.calibration import gate_grid, recommend_gate


def _hard():
    return pd.DataFrame([
        # 경계모호: 1순위 0.7, 2순위 0.6 → 마진 0.1
        {"confidence": 0.7, "alt_confidence": 0.6, "route_alt": "ORDER_PLACE", "route": "PRODUCT_INFO",
         "route_expected": "PRODUCT_INFO", "route_alt_expected": "ORDER_PLACE", "hard_type": "경계모호"},
        # 비모호, 정답, 마진 넓음
        {"confidence": 0.9, "alt_confidence": 0.1, "route_alt": "SHIPPING", "route": "RETURN_REFUND",
         "route_expected": "RETURN_REFUND", "route_alt_expected": None, "hard_type": "오타"},
        # 비모호, 정답, 2순위 없음
        {"confidence": 0.6, "alt_confidence": 0.0, "route_alt": None, "route": "SHIPPING",
         "route_expected": "SHIPPING", "route_alt_expected": None, "hard_type": "구어체"},
    ])


def test_gate_grid_counts_risky_and_wrong_escalation():
    g = gate_grid(_hard(), thresholds=(0.5,), margins=(0.0, 0.2))
    m0 = g[(g["임계값"] == 0.5) & (g["마진"] == 0.0)].iloc[0]
    assert m0["경계모호위험"] == 1 and m0["비모호오이관"] == 0 and abs(m0["자동처리율"] - 1.0) < 1e-9
    m2 = g[(g["임계값"] == 0.5) & (g["마진"] == 0.2)].iloc[0]
    assert m2["경계모호위험"] == 0 and m2["비모호오이관"] == 0
    assert abs(m2["자동처리율"] - 2 / 3) < 1e-9


def test_gate_grid_threshold_escalates_low_confidence():
    g = gate_grid(_hard(), thresholds=(0.7,), margins=(0.0,))
    row = g.iloc[0]
    assert row["비모호오이관"] == 1        # conf 0.6 정답 건이 이관됨


def test_recommend_gate_prefers_max_automation_under_risk_cap():
    g = gate_grid(_hard(), thresholds=(0.5, 0.7), margins=(0.0, 0.2))
    best = recommend_gate(g, max_risky=0)
    assert best["임계값"] == 0.5 and best["마진"] == 0.2
    assert recommend_gate(g.iloc[0:0]) is None
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_calibration.py -q`
Expected: ImportError

- [ ] **Step 3: 구현**

`eval/calibration.py` 끝에 추가:

```python
MARGIN_EDGES = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 1.0001)


def _margin(row) -> float:
    return 1.0 if row["route_alt"] is None or pd.isna(row["route_alt"]) else row["confidence"] - row["alt_confidence"]


def gate_grid(df: pd.DataFrame, thresholds=(0.5, 0.6, 0.7, 0.8), margins=(0.0, 0.1, 0.2, 0.3, 0.4)) -> pd.DataFrame:
    """라우터 호출 결과를 재사용해 임계값×마진 조합마다 게이트를 오프라인으로 돌린다.

    자동처리율 = HANDLE 비율(route OTHER 는 여기서 구분하지 않는다 — hard_cases 에 OTHER 정답이 없다).
    경계모호위험 = hard_type 경계모호인데 HANDLE 한 건수. 비모호오이관 = 경계모호가 아니고 route 가
    정답(route_expected 또는 route_alt_expected)인데 이관된 건수(마진을 키운 대가)."""
    margins_col = df.apply(_margin, axis=1)
    correct = [(r.route == r.route_expected) or (isinstance(r.route_alt_expected, str) and r.route == r.route_alt_expected)
               for r in df.itertuples()]
    amb = (df["hard_type"] == "경계모호").tolist()
    rows = []
    for t in thresholds:
        for m in margins:
            handle = [(c >= t) and (mg >= m) for c, mg in zip(df["confidence"], margins_col)]
            rows.append({"임계값": t, "마진": m,
                         "자동처리율": sum(handle) / len(df) if len(df) else float("nan"),
                         "경계모호위험": sum(1 for h, a in zip(handle, amb) if h and a),
                         "비모호오이관": sum(1 for h, a, ok in zip(handle, amb, correct) if (not h) and (not a) and ok)})
    return pd.DataFrame(rows)


def recommend_gate(grid: pd.DataFrame, max_risky: int = 5):
    """경계모호위험 ≤ max_risky 인 칸 중 자동처리율 최대. 동률이면 마진이 작은 쪽. 없으면 None."""
    ok = grid[grid["경계모호위험"] <= max_risky]
    if ok.empty:
        return None
    best = ok.sort_values(["자동처리율", "마진", "임계값"], ascending=[False, True, True]).iloc[0]
    return {"임계값": float(best["임계값"]), "마진": float(best["마진"]),
            "자동처리율": float(best["자동처리율"]), "경계모호위험": int(best["경계모호위험"])}
```

`eval/eval_hard.py` `main()` 에서:
- `hard = hard.assign(...)` 에 `route_alt=[st.get("route_alt") for st in states], alt_confidence=[st.get("alt_confidence", 0.0) for st in states]` 를 추가하고, `hard["route_alt_expected"] = hard["route_alt"]` **가 아니라** csv 의 `route_alt` 열과 이름이 겹치므로 **csv 를 읽은 직후** `hard = hard.rename(columns={"route_alt": "route_alt_expected"})` 를 한다.
- 이후 코드에서 `r['route_alt']` 로 정답 후보를 읽던 곳(risky 출력, `ok` 계산)은 `route_alt_expected` 로 바꾼다.
- 파일 끝 `ECE` 출력 뒤에 추가:

```python
    margins = [1.0 if pd.isna(a) else c - ac for c, ac, a in zip(hard["confidence"], hard["alt_confidence"], hard["route_alt"])]
    mtable, mece = calibration_table(margins, ok, edges=MARGIN_EDGES)
    print("\n[마진 보정표 — 1순위-2순위 확신도 차이 구간별 정확도]")
    print(mtable.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    grid = gate_grid(hard)
    print("\n[임계값×마진 격자] 자동처리율 / 경계모호 위험 / 비모호 오이관")
    print(grid.pivot(index="임계값", columns="마진", values="자동처리율").to_string(float_format=lambda v: f"{v:.3f}"))
    print(grid.pivot(index="임계값", columns="마진", values="경계모호위험").to_string())
    print(grid.pivot(index="임계값", columns="마진", values="비모호오이관").to_string())
    best = recommend_gate(grid, max_risky=5)
    print("\n[추천] " + (f"CONF_THRESHOLD={best['임계값']} CONF_MARGIN={best['마진']}  자동처리율 {best['자동처리율']:.3f}  경계모호 위험 {best['경계모호위험']}건"
                       if best else "경계모호 위험 5건 이하를 만족하는 조합이 없음"))
```

import 줄을 `from eval.calibration import MARGIN_EDGES, calibration_table, gate_grid, recommend_gate` 로 바꾼다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest -q && .venv/bin/python -m eval.eval_hard --rule | tail -25`
Expected: 테스트 PASS. 규칙 라우터는 route_alt 가 없어 마진 보정표가 마지막 구간에만 값이 있고 격자의 마진 축이 전부 같은 값이다(정상).

- [ ] **Step 5: 커밋**

```bash
git add eval/calibration.py eval/eval_hard.py tests/test_calibration.py
git commit -m "feat: eval_hard 임계값×마진 격자, 마진 보정표, 게이트 추천값 출력"
```

- [ ] **Step 6: 측정과 기본값 확정 (키 필요, 컨트롤러 결정)**

Run: `.venv/bin/python -m eval.eval_hard 2>&1 | tee logs/eval-hard-grid.log`
추천값을 읽고 `server/config.py` 의 `CONF_THRESHOLD`·`CONF_MARGIN` 기본값과 `.env.example` 주석을 그 값으로 바꾼다. 그 다음 `.venv/bin/python -m eval.eval_router 2>&1 | tee logs/eval-router-after-margin.log` 로 120건 macro F1 이 0.945±0.01 인지, 이관 건수가 얼마나 늘었는지 본다. F1 이 떨어지면 마진을 한 단계 낮춘 칸으로 바꾼다. 기본값 변경은 별도 커밋: `chore: 격자 측정으로 CONF_THRESHOLD/CONF_MARGIN 기본값 확정`.

---

### Task 5: 히스토리 라우트 드리프트 보정 (is_followup + routes 이력)

**Files:**
- Modify: `server/router.py` (RouteDecision.is_followup, RouterState, node_classify)
- Modify: `server/prompts.py` (`build_route_guide` followup 지침)
- Modify: `server/pipeline.py` (`compose_router_input`, AgentState.routes, `_node_route`, TurnResult.is_followup)
- Test: `tests/test_pipeline.py`, `tests/test_router.py`

**Interfaces:**
- Produces: `RouteDecision.is_followup: bool = False`
- Produces: `server.pipeline.compose_router_input(question: str, history: list, prev_route: Optional[str]) -> str` — Task 6 의 멀티턴 평가가 같은 함수를 쓴다.
- Produces: `AgentState.routes: Annotated[list, operator.add]` (원소 `{"route", "confidence", "is_followup"}`), `TurnResult.is_followup: bool = False`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_pipeline.py` 끝에 추가:

```python
from server.pipeline import compose_router_input


def test_compose_router_input_formats_history_and_prev_route():
    s = compose_router_input("배송비는요?", ["캔버스화 살 건데요", "사이즈 있나요"], "PRODUCT_INFO")
    assert s.startswith("배송비는요?")
    assert "[직전 문의] 캔버스화 살 건데요 / 사이즈 있나요" in s
    assert "[직전 라우트] PRODUCT_INFO" in s
    assert compose_router_input("배송비는요?", [], None) == "배송비는요?"


def test_compose_router_input_keeps_last_two_only():
    s = compose_router_input("q", ["a", "b", "c"], None)
    assert "[직전 문의] b / c" in s and "a /" not in s


class ScriptedRouter:
    """턴마다 정해진 RouteDecision 을 내고, 받은 입력 문자열을 기록한다."""
    def __init__(self, domain, decisions):
        self.decisions = list(decisions)
        self.inputs = []
        self.graph = build_router(domain, 0.5, classify=self._classify)

    def _classify(self, q):
        self.inputs.append(q)
        return self.decisions.pop(0)

    def invoke(self, state):
        return self.graph.invoke(state)


def test_followup_inherits_previous_route(domain, settings):
    router = ScriptedRouter(domain, [
        RouteDecision(route="SHIPPING", confidence=0.9, reason="t"),
        RouteDecision(route="PRODUCT_INFO", confidence=0.8, reason="t", is_followup=True),
    ])
    ans = FakeAnswerer([("2,500원입니다.", {"get_shipping_policy": {}}, []),
                        ("무료배송 기준은 100,000원입니다.", {"get_shipping_policy": {}}, [])])
    p = Pipeline(domain, settings, router=router, answerer=ans)
    cid, _, _ = p.start_call()
    p.turn(cid, "캔버스화 배송비 얼마예요?")
    r = p.turn(cid, "그럼 무료배송은요?")
    assert r.route == "SHIPPING" and r.is_followup is True
    assert "[직전 라우트] SHIPPING" in router.inputs[1]
    assert "[직전 라우트]" not in router.inputs[0]


def test_followup_does_not_inherit_other_route(domain, settings):
    # 직전 라우트가 OTHER 면 이어받지 않고 라우터가 낸 현재 라우트를 쓴다
    router = ScriptedRouter(domain, [
        RouteDecision(route="OTHER", confidence=0.9, reason="t"),
        RouteDecision(route="SHIPPING", confidence=0.8, reason="t", is_followup=True),
    ])
    ans = FakeAnswerer([("2,500원입니다.", {"get_shipping_policy": {}}, [])])
    p = Pipeline(domain, settings, router=router, answerer=ans)
    cid, _, _ = p.start_call()
    r1 = p.turn(cid, "홍대점 몇 시까지 해요?")
    assert r1.action == "OUT_OF_SCOPE"
    r2 = p.turn(cid, "아 그럼 배송비는요?")
    assert r2.route == "SHIPPING" and r2.is_followup is False
    assert "[직전 라우트] OTHER" in router.inputs[1]


def test_ask_turn_is_recorded_in_routes(domain, settings):
    router = ScriptedRouter(domain, [
        RouteDecision(route="SHIPPING", confidence=0.2, reason="t"),          # 확신도 미달 → ASK(되묻기)
        RouteDecision(route="PRODUCT_INFO", confidence=0.9, reason="t", is_followup=True),
    ])
    ans = FakeAnswerer([("네 확인했습니다.", {"get_shipping_policy": {}}, [])])
    p = Pipeline(domain, settings, router=router, answerer=ans)
    cid, _, _ = p.start_call()
    r1 = p.turn(cid, "그거요")
    assert r1.action == "ASK"
    r2 = p.turn(cid, "배송 문의요")
    assert "[직전 라우트] SHIPPING" in router.inputs[1]
    assert r2.route == "SHIPPING"
```

`tests/test_router.py` 끝에 추가:

```python
def test_route_guide_explains_followup(domain):
    assert "is_followup" in build_route_guide(domain)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_pipeline.py tests/test_router.py -q`
Expected: 새 테스트 FAIL. (`test_history_carries_across_turns` 등 기존 테스트가 `(직전 발화:` 형식을 단언하면 새 형식에 맞게 그 단언만 고친다 — 의도는 "직전 발화가 라우터 입력에 들어간다"이므로 보존된다.)

- [ ] **Step 3: router·prompts 수정**

`RouteDecision` 에 추가:

```python
    is_followup: bool = Field(default=False,
                              description="[직전 라우트] 가 주어졌고 현재 발화가 그 문의의 연속(같은 상품·주문·주제의 추가 질문)이면 true. 새 주제면 false. 직전 라우트가 없으면 false.")
```

`RouterState` 에 `is_followup: bool` 추가. `node_classify` 반환 dict 에 `"is_followup": d.is_followup` 추가.

`build_route_guide` 의 `[확신도 지침]` 블록 뒤에 추가:

```
[직전 문의와 후속 발화]
입력에 [직전 문의] 와 [직전 라우트] 가 붙어 올 수 있다. 현재 발화가 직전 문의의 연속(같은 상품·같은
주문·같은 주제에 대한 추가 질문, 되묻기에 대한 대답)이면 is_followup 을 true 로 표시한다. 새 주제를
꺼내면 false 다. route 는 현재 발화만 보고 판단하고, 이어받을지는 시스템이 is_followup 으로 정한다.
```

- [ ] **Step 4: pipeline 수정**

`server/pipeline.py` 의 `AgentState` 에 `routes: Annotated[list, operator.add]`, `is_followup: bool` 추가. `TurnResult` 끝에 `is_followup: bool = False` 추가.

모듈 레벨(클래스 `Pipeline` 위)에 함수 추가:

```python
def compose_router_input(question: str, history: list, prev_route: Optional[str]) -> str:
    """라우터 입력 문자열. 현재 문의를 앞에 두고 최근 두 발화와 직전 라우트를 뒤에 붙인다.
    (긴 통화일수록 앞 turn 이 라우팅을 오염시키는 걸 줄이기 위해 두 발화만 본다.)
    eval_router --multiturn 이 같은 함수를 써서 평가와 런타임의 입력이 같다."""
    q = question
    hist = (history or [])[-2:]
    if hist:
        q += f"\n[직전 문의] {' / '.join(hist)}"
    if prev_route:
        q += f"\n[직전 라우트] {prev_route}"
    return q
```

`_node_route` 를 다음으로 교체:

```python
    def _node_route(self, state: AgentState) -> AgentState:
        routes = state.get("routes") or []
        prev = routes[-1]["route"] if routes else None
        q = compose_router_input(state["question"], state.get("history") or [], prev)
        r = self.router.invoke({"question": q})
        route = r["route"]
        followup = bool(r.get("is_followup")) and prev is not None and prev != "OTHER"
        if followup:
            route = prev   # 후속 발화는 라우트만 이어받고, 확신도 판정(action)은 라우터 결과를 그대로 쓴다
        base = {"route": route, "confidence": r["confidence"], "action": r["action"],
                "route_alt": r.get("route_alt"), "alt_confidence": r.get("alt_confidence", 0.0),
                "is_followup": followup,
                "routes": [{"route": route, "confidence": r["confidence"], "is_followup": followup}],
                "attempts": 0, "tools": [], "results": {}, "guardrail": None}
        count = state.get("clarify_count", 0)
        if r["action"] == "ESCALATE" and count < self.settings.clarify_max:
            base.update({"action": "ASK", "answer": self.domain.clarify_message,
                         "clarify_count": count + 1, "history": [state["question"]]})
        return base
```

`turn()` 의 `TurnResult(...)` 에 `is_followup=out.get("is_followup", False)` 추가. `turn_logs` 항목에 `"followup": out.get("is_followup", False)` 추가.

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add server/router.py server/prompts.py server/pipeline.py tests/test_pipeline.py tests/test_router.py
git commit -m "feat: 라우터 is_followup 으로 후속 발화가 직전 라우트를 이어받고 routes 이력을 쌓는다"
```

---

### Task 6: 멀티턴 라우트 정답셋과 eval_router --multiturn

**Files:**
- Create: `domains/modumall/eval/multiturn_routes.json`
- Modify: `eval/eval_router.py`
- Test: `tests/test_multiturn_routes.py` (신규)

**Interfaces:**
- Consumes: `server.pipeline.compose_router_input`, `build_router(..., classify=...)`
- Produces: `eval.eval_router.load_multiturn(path) -> list[dict]`, `eval.eval_router.route_conversation(graph, turns, use_history=True) -> list[dict]` (턴별 `{"route", "is_followup", "confidence"}`), `eval.eval_router.score_multiturn(convs, preds) -> dict` (`turn1_acc, later_acc, followup_confusion(DataFrame)`)

- [ ] **Step 1: 데이터 작성**

`domains/modumall/eval/multiturn_routes.json`. 스키마와 첫 3개를 보인다. **총 20개**를 만든다: 라우트가 바뀌는 대화 4개(M-001~004, 둘째 턴 `followup:false`), 짧은 후속 발화로 라우트를 이어받는 대화 6개(M-005~010, `followup:true`), 되묻기 뒤 대답 5개(M-011~015, 첫 턴은 `clarify:true` 로 표시하고 둘째 턴 `followup:true`), 새 주제 전환 5개(M-016~020, `followup:false`). 질문 표현은 `customer_inquiries.csv` 의 실제 발화 패턴을 참고하되 그대로 복사하지 않는다. 상품명은 `domain.json` 동의어·mockdb 상품명에서 고른다.

```json
{
  "$comment": "멀티턴 라우팅 정답셋 v1.0 — provenance=합성. 후속 발화가 단독으로는 다른 라우트로 보이는 대화를 모아 라우트 드리프트 보정을 측정한다. 턴마다 route 정답과 followup(직전 문의의 연속 여부) 정답을 둔다.",
  "version": "1.0",
  "conversations": [
    {"conv_id": "M-001", "title": "배송비 → 반품비 (라우트 전환)",
     "turns": [{"text": "레깅스 배송비 얼마예요?", "route": "SHIPPING", "followup": false},
               {"text": "그럼 반품할 땐 배송비 얼마예요?", "route": "RETURN_REFUND", "followup": false}]},
    {"conv_id": "M-005", "title": "상품 문의 → 짧은 후속 (이어받기)",
     "turns": [{"text": "캔버스화 사이즈 어떤 거 있어요?", "route": "PRODUCT_INFO", "followup": false},
               {"text": "흰색도요?", "route": "PRODUCT_INFO", "followup": true}]},
    {"conv_id": "M-011", "title": "되묻기 뒤 대답",
     "turns": [{"text": "그거 어떻게 돼요?", "route": "OTHER", "followup": false, "clarify": true},
               {"text": "어제 주문한 거 배송이요", "route": "SHIPPING", "followup": true}]}
  ]
}
```

되묻기 뒤 대답(M-011~015)의 둘째 턴은 `followup:true` 지만 직전 라우트가 없거나 OTHER 이므로 라우트 이어받기 대상이 아니다. 이 대화의 목적은 "되묻기에 대한 대답을 followup 으로 인식하는가"와 "그때 route 를 현재 발화로 제대로 내는가"를 재는 것이다.

- [ ] **Step 2: 실패 테스트 작성**

`tests/test_multiturn_routes.py` 신규:

```python
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
    decisions = iter([RouteDecision(route="SHIPPING", confidence=0.9, reason="t"),
                      RouteDecision(route="PRODUCT_INFO", confidence=0.8, reason="t", is_followup=True)])
    g = build_router(domain, 0.5, classify=lambda q: next(decisions))
    turns = [{"text": "a", "route": "SHIPPING", "followup": False}, {"text": "b", "route": "SHIPPING", "followup": True}]
    preds = route_conversation(g, turns)
    assert preds[1]["route"] == "SHIPPING"


def test_score_multiturn():
    convs = [{"conv_id": "x", "turns": [{"text": "a", "route": "SHIPPING", "followup": False},
                                        {"text": "b", "route": "SHIPPING", "followup": True}]}]
    preds = [[{"route": "SHIPPING", "is_followup": False, "confidence": 0.9},
              {"route": "PRODUCT_INFO", "is_followup": False, "confidence": 0.9}]]
    s = score_multiturn(convs, preds)
    assert s["turn1_acc"] == 1.0 and s["later_acc"] == 0.0
    assert s["followup_confusion"].loc[True, False] == 1
```

- [ ] **Step 3: 실패 확인**

Run: `.venv/bin/pytest tests/test_multiturn_routes.py -q`
Expected: ImportError / 파일 없음

- [ ] **Step 4: eval_router 확장**

`eval/eval_router.py` 에 import 추가 `import json`, `from pathlib import Path`, `from server.pipeline import compose_router_input`. 모듈 레벨 함수 추가:

```python
def load_multiturn(path: Path) -> list:
    return list(json.loads(Path(path).read_text(encoding="utf-8"))["conversations"])


def route_conversation(graph, turns: list, use_history: bool = True) -> list:
    """대화 하나를 턴 순서대로 라우팅한다. 런타임 _node_route 와 같은 규칙:
    입력은 compose_router_input, followup 이고 직전 라우트가 OTHER 가 아니면 직전 라우트를 이어받는다."""
    history, prev, out = [], None, []
    for t in turns:
        q = compose_router_input(t["text"], history, prev) if use_history else t["text"]
        st = graph.invoke({"question": q})
        followup = bool(st.get("is_followup")) and prev is not None and prev != "OTHER"
        route = prev if followup else st["route"]
        out.append({"route": route, "is_followup": bool(st.get("is_followup")), "confidence": st["confidence"]})
        history.append(t["text"])
        prev = route
    return out


def score_multiturn(convs: list, preds: list) -> dict:
    first = [(c["turns"][0]["route"], p[0]["route"]) for c, p in zip(convs, preds)]
    later = [(t["route"], q["route"]) for c, p in zip(convs, preds) for t, q in zip(c["turns"][1:], p[1:])]
    fu = [(t["followup"], q["is_followup"]) for c, p in zip(convs, preds) for t, q in zip(c["turns"][1:], p[1:])]
    conf = pd.crosstab(pd.Series([a for a, _ in fu], name="정답 followup"),
                       pd.Series([b for _, b in fu], name="예측 followup")).reindex(index=[False, True], columns=[False, True], fill_value=0)
    return {"turn1_acc": sum(a == b for a, b in first) / len(first) if first else float("nan"),
            "later_acc": sum(a == b for a, b in later) / len(later) if later else float("nan"),
            "followup_confusion": conf,
            "later_miss": [(c["conv_id"], t["text"], t["route"], q["route"]) for c, p in zip(convs, preds)
                           for t, q in zip(c["turns"][1:], p[1:]) if t["route"] != q["route"]]}
```

`main()` 에 인자 `ap.add_argument("--multiturn", action="store_true", help="멀티턴 라우트셋으로 드리프트 보정을 잰다")`, `ap.add_argument("--no-history", action="store_true", help="--multiturn 기준선: 직전 문의·라우트 없이 단일 발화로")` 를 추가하고, `classify`/`graph` 를 만든 직후(단일 발화 평가 데이터 로드 전이어도 됨) 분기:

```python
    if args.multiturn:
        convs = load_multiturn(ev_dir / "multiturn_routes.json")
        if args.limit:
            convs = convs[:args.limit]
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            preds = list(ex.map(lambda c: route_conversation(graph, c["turns"], use_history=not args.no_history), convs))
        sc = score_multiturn(convs, preds)
        mode = "히스토리 없음(기준선)" if args.no_history else "히스토리+직전 라우트"
        print(f"[멀티턴 {len(convs)}대화, {mode}] 턴1 정확도 {sc['turn1_acc']:.3f}  턴2+ 정확도 {sc['later_acc']:.3f}")
        print("\n[followup 혼동] 행=정답, 열=예측")
        print(sc["followup_confusion"].to_string())
        print(f"\n[턴2+ 오분류 {len(sc['later_miss'])}건]")
        for cid, text, g, p in sc["later_miss"]:
            print(f"  {cid} [{g} → {p}]  {text[:50]}")
        return
```

(이 분기는 `ev = inq.merge(...)` 앞에 두어 단일 발화 데이터를 읽지 않게 한다. `graph` 생성은 분기 위로 올린다.)

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/pytest -q && .venv/bin/python -m eval.eval_router --rule --multiturn`
Expected: 테스트 PASS. 규칙 라우터는 is_followup 을 내지 않으므로 followup 예측이 전부 False 인 표가 나온다(정상).

- [ ] **Step 6: 커밋**

```bash
git add domains/modumall/eval/multiturn_routes.json eval/eval_router.py tests/test_multiturn_routes.py
git commit -m "feat: 멀티턴 라우트 정답셋 20대화와 eval_router --multiturn (히스토리 유무 비교)"
```

- [ ] **Step 7: 측정 (키 필요, 컨트롤러 결정)**

Run:
```bash
.venv/bin/python -m eval.eval_router --multiturn --no-history 2>&1 | tee logs/eval-multiturn-baseline.log
.venv/bin/python -m eval.eval_router --multiturn 2>&1 | tee logs/eval-multiturn-history.log
```
기록: 두 모드의 턴2+ 정확도와 followup 혼동표. 히스토리 있음이 낮으면 프롬프트의 후속 발화 지침을 고치고 재측정(골든셋·평가셋 문장은 프롬프트에 넣지 않는다).

---

### Task 7: escalate_to_agent 도구 호출을 통화 종료로 잇기

**Files:**
- Modify: `server/pipeline.py` (`_node_answer`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `results` dict 의 키 `escalate_to_agent`(또는 `escalate_to_agent#2`), 값 `{"escalated": True, ...}`
- Produces: 그 턴의 `action == "ESCALATE"`, `TurnResult.end_call is True`, `answer == domain.escalate_message`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_pipeline.py` 끝에 추가:

```python
def test_escalate_tool_call_ends_call(domain, settings):
    ans = FakeAnswerer([("상담원에게 연결해 드리겠습니다.",
                         {"escalate_to_agent": {"escalated": True, "reason": "환불 계좌 변경"}},
                         [{"name": "escalate_to_agent", "args": {"reason": "환불 계좌 변경"}}])])
    p = Pipeline(domain, settings, router=router_with(domain, "RETURN_REFUND", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    r = p.turn(cid, "환불 계좌를 바꾸고 싶어요")
    assert r.action == "ESCALATE" and r.end_call is True
    assert r.answer == domain.escalate_message
    assert [t["name"] for t in r.tools] == ["escalate_to_agent"]


def test_escalate_tool_repeated_key_also_ends_call(domain, settings):
    ans = FakeAnswerer([("연결합니다.", {"get_order_status": {"status": "배송중"},
                                    "escalate_to_agent#2": {"escalated": True, "reason": "x"}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    assert p.turn(cid, "O-1001 이관해 주세요").end_call is True


def test_escalate_tool_error_string_is_ignored(domain, settings):
    ans = FakeAnswerer([("확인했습니다.", {"escalate_to_agent": "Error: reason missing", "get_order_status": {"status": "배송중"}}, [])])
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.9), answerer=ans)
    cid, _, _ = p.start_call()
    assert p.turn(cid, "O-1001 어디쯤이에요?").end_call is False
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_pipeline.py -q -k escalate_tool`
Expected: FAIL (action 이 HANDLE/ANSWER)

- [ ] **Step 3: 구현**

`server/pipeline.py` 모듈 레벨에 함수 추가:

```python
def called_escalate(results: dict) -> bool:
    """답변기가 escalate_to_agent 도구를 실제로 불렀는가. 오류 문자열 값은 호출로 치지 않는다."""
    for key, value in (results or {}).items():
        if key.split("#")[0] == "escalate_to_agent" and isinstance(value, dict) and value.get("escalated"):
            return True
    return False
```

`_node_answer` 에서 `if text == self.domain.escalate_message:` 분기 **바로 뒤**에 추가:

```python
        if called_escalate(results):
            # 모델이 이관 도구를 불렀으면 답변 문구와 무관하게 이관한다 (매뉴얼 7.2)
            return {"action": "ESCALATE", "tools": calls, "results": results, "attempts": attempts}
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add server/pipeline.py tests/test_pipeline.py
git commit -m "fix: escalate_to_agent 도구 호출을 ESCALATE 액션으로 매핑해 통화를 종료한다"
```

---

### Task 8: "티" 동의어 과매칭 — 조사·복합어·짧은 질의 폴백·값 검증

**Files:**
- Modify: `server/tools.py:86-160` (`search_product`)
- Modify: `server/domain.py:80-86` (동의어 값 검증)
- Modify: `domains/modumall/domain.json` (`search.synonyms`)
- Test: `tests/test_tools.py`, `tests/test_domain.py`

**Interfaces:**
- Consumes: `domain.search["synonyms"]`, `_toks`, `jamo_ratio`
- Produces: `search_product` 동작 변경(아래 테스트가 계약), `DomainError` 메시지 `search.synonyms['{k}'] 값 '{v}' 이 어떤 상품명에도 없음`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_tools.py` 끝에 추가:

```python
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
```

`tests/test_domain.py` 끝에 추가:

```python
def test_synonym_value_must_exist_in_catalog(tmp_path, modumall_dir):
    for f in ("domain.json", "policy.md", "mockdb.json"):
        (tmp_path / f).write_bytes((modumall_dir / f).read_bytes())
    cfg = json.loads((tmp_path / "domain.json").read_text(encoding="utf-8"))
    cfg["search"]["synonyms"]["후드"] = "후드티"
    (tmp_path / "domain.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DomainError) as e:
        load_domain(tmp_path)
    assert "search.synonyms['후드']" in str(e.value)


def test_null_synonym_is_not_validated(tmp_path, modumall_dir):
    for f in ("domain.json", "policy.md", "mockdb.json"):
        (tmp_path / f).write_bytes((modumall_dir / f).read_bytes())
    cfg = json.loads((tmp_path / "domain.json").read_text(encoding="utf-8"))
    cfg["search"]["synonyms"]["수영복"] = None
    (tmp_path / "domain.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    load_domain(tmp_path)   # 예외 없음
```

(`tests/test_domain.py` 상단에 `import json` 이 없으면 추가.)

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_tools.py tests/test_domain.py -q`
Expected: 새 테스트 FAIL

- [ ] **Step 3: domain.json 동의어 추가**

`domains/modumall/domain.json` 의 `search.synonyms` 에 `"반팔티": "티셔츠", "긴팔티": "티셔츠", "무지티": "티셔츠"` 를 추가한다.

- [ ] **Step 4: tools.py 수정**

`search_product` 안에서 `_particles` 와 `_alias_match` 정의를 **동의어 치환 단계 위로** 옮기고, 동의어 치환 루프를 다음으로 교체:

```python
        def _synonym_key(token: str):
            if token in synonyms:
                return token
            for k in synonyms:
                if _alias_match(k, token):     # "티는"→"티", 조사 화이트리스트만 허용
                    return k
            return None

        tokens = []
        for t in qt:
            k = _synonym_key(t)
            if k is not None:
                if synonyms[k] is None:
                    return {"query": query, "candidates": [], "resolved_product_id": None,
                            "ambiguous": False, "category_query": False, "not_in_catalog": True,
                            "note": "취급하지 않는 상품입니다."}
                tokens.append(synonyms[k].replace(" ", ""))
            else:
                tokens.append(t)
        qt = tokens
```

`_name_search` 의 오타 폴백을 다음으로 교체:

```python
                if score == 0 and len(flat_q) >= 3:
                    # 오타 보정: 공백 제거 문자열 유사도. 두 글자 이하는 우연한 유사도가 0.6 을 넘기 쉬워 건너뛴다
                    ratio = max(difflib.SequenceMatcher(None, flat_q, flat).ratio(),
                                jamo_ratio(flat_q, flat))
                    if ratio >= 0.6:
                        score = round(ratio * 0.9, 2)
```

- [ ] **Step 5: domain.py 검증 추가**

`server/domain.py` 의 alias 검증 루프 뒤에 추가:

```python
    flat_names = [p["name"].replace(" ", "") for p in mockdb["products"]]
    for key, value in search["synonyms"].items():
        if value is None:
            continue
        if not any(value.replace(" ", "") in n for n in flat_names):
            raise DomainError(f"search.synonyms['{key}'] 값 '{value}' 이 어떤 상품명에도 없음")
```

- [ ] **Step 6: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전부 PASS. `test_search_product_typo_tolerant`("켄버스화", 4글자)가 여전히 통과해야 한다.

- [ ] **Step 7: 커밋**

```bash
git add server/tools.py server/domain.py domains/modumall/domain.json tests/test_tools.py tests/test_domain.py
git commit -m "fix: 동의어 조사·복합어 처리, 두 글자 이하 오타 폴백 차단, 동의어 값 카탈로그 검증"
```

---

### Task 9: 최종 측정과 README 기록

**Files:**
- Modify: `README.md:43-62` (평가 명령·실험표), `.env.example`
- 로그: `logs/` (gitignore)

**Interfaces:**
- Consumes: Task 1·2·4·6 의 측정 로그, Task 4 Step 6 에서 확정한 기본값

- [ ] **Step 1: 최종 측정 실행 (키 필요)**

```bash
.venv/bin/pytest -q
.venv/bin/python -m eval.eval_router 2>&1 | tee logs/eval-router-final.log
.venv/bin/python -m eval.eval_hard 2>&1 | tee logs/eval-hard-final.log
.venv/bin/python -m eval.eval_router --multiturn 2>&1 | tee logs/eval-multiturn-final.log
.venv/bin/python -m eval.eval_regression --runs 3 2>&1 | tee logs/eval-regression-final.log
.venv/bin/python -m eval.eval_answer --workers 1 --runs 3 --judge 2>&1 | tee logs/eval-answer-final.log
```

- [ ] **Step 2: 성공 기준 대조**

| 기준 | 목표 | 실측 |
|---|---|---|
| judge 포함 통과율 | ≥ 70% (≥ 23/32) | |
| 규칙 통과율 | ≥ 50% | |
| 경계모호 위험 처리 | ≤ 5건 | |
| 라우팅 macro F1 | 0.945 ± 0.01 | |
| 회귀 | 6/6 ×3 | |
| 멀티턴 턴2+ 정확도 | 히스토리 없음 기준선보다 높음 | |

미달 항목이 있으면 그 항목의 Task 로 돌아가 원인을 실패 사례 목록에서 찾는다. 프롬프트 수정 → 해당 평가만 재실행 → 표 갱신.

- [ ] **Step 3: README 갱신**

`README.md` 평가 명령 블록에 추가:

```bash
.venv/bin/python -m eval.eval_answer --workers 1 --runs 3 --judge   # 규칙 실패의 must 누락 건을 LLM judge 로 재판정 (JUDGE_MODEL)
.venv/bin/python -m eval.eval_router --multiturn [--no-history]     # 멀티턴 라우트 드리프트 (20대화, 키 필요)
```

`eval_hard` 줄 설명을 `어려운 케이스 72건, 되묻기 정책 비교 + 임계값×마진 격자` 로 바꾼다. 실험표에 4행 추가(값은 Step 1 실측):

```
| 4 | 3차 강화: judge 채점기 · 필수 도구 표 · 확신도 마진 게이트(CONF_MARGIN=…) · followup 라우트 이어받기 · 이관 도구 매핑 · 동의어 보강 | … | 규칙 …% / judge 포함 …% (…/32, runs=3) | 6/6 | judge 만 붙였을 때 …%(판정기 효과), 경계모호 위험 15→…건, 멀티턴 턴2+ 정확도 기준선 … → … |
```

"한계" 절에 한 줄 추가: `judge 는 must 누락 건만 보며, rubric·reference 가 틀리면 judge 도 틀린다. 자기 검증(reference 전부 통과)이 그 최소 방어다.`

- [ ] **Step 4: 커밋**

```bash
git add README.md .env.example
git commit -m "docs: 3차 강화 측정 기록 - judge 통과율, 마진 게이트 격자, 멀티턴 드리프트"
```

---

## 자기 검토 결과

- **스펙 커버리지**: 2.1→Task 2(+사유 분포는 Task 1), 2.2→Task 1, 2.3→Task 3·4, 2.4→Task 5·6, 2.5→Task 7, 2.6→Task 8, §4 측정 순서→각 Task 의 측정 Step 과 Task 9, §3 `server/app.py` 는 `to_dict()` 가 dataclass 전체를 직렬화하므로 변경 불필요(TurnResult 필드 추가만으로 응답에 실림).
- **시그니처 일치**: `build_router(..., conf_margin=)` 키워드 인자를 Task 3·5·6·테스트가 같은 이름으로 쓴다. `compose_router_input(question, history, prev_route)` 를 Task 5 에서 정의하고 Task 6 이 import 한다. `RouteDecision` 의 `route_alt`·`alt_confidence`(Task 3)·`is_followup`(Task 5) 은 전부 기본값이 있어 기존 `fixed()`·`router_with()` 헬퍼가 그대로 동작한다.
- **키 없는 테스트**: Task 1 judge 는 `FakeJudge`, Task 5·6 은 `classify` 주입, Task 7 은 `FakeAnswerer`. 키가 필요한 것은 각 Task 의 "측정" Step 뿐이며 컨트롤러가 실행 여부를 정한다.
