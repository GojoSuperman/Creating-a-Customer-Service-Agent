# 2차: 평가 강화와 응대 개선 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 1차 에이전트에 확신도 보정표, 플랩 감지, 되묻기 후 이관, 범주어·동의어 검색, 회귀 스위트를 더한다.

**Architecture:** 기존 모듈 경계를 유지한다. 순수 채점·집계 함수는 `eval/scoring.py`·`eval/calibration.py`에 두고 단위 테스트한다. 파이프라인 변경은 `_node_route` 한 곳의 분기 추가다. 도메인 데이터 확장은 `domain.json` 선택 키로 하여 다른 도메인이 없어도 동작한다.

**Tech Stack:** 1차와 동일 (Python 3.12 `.venv`, LangGraph, pandas, pytest).

**Spec:** `docs/superpowers/specs/2026-09-16-eval-hardening-design.md`

## Global Constraints

- `.venv/bin/pytest`, `.venv/bin/python` 만 사용. 모든 테스트는 API 키 없이 통과.
- 라우트 5개, 도구 9개 이름 고정. 새 도구를 추가하지 않는다.
- `domain.json`의 새 키는 전부 선택(optional)이며 없으면 기본값으로 동작한다.
- 코드 식별자 영어, 주석·로그·UI 문구 한국어.
- 커밋 메시지 끝에 `Co-Authored-By: Claude <모델명> <noreply@anthropic.com>` 트레일러.
- 현재 테스트 69개는 그대로 통과해야 한다.

## 현재 코드 요약 (구현자가 알아야 할 것)

- `server/pipeline.py`: `AgentState`(question, history[reducer add], route, confidence, action, tools, results, answer, guardrail, attempts), `TurnResult`(answer, route, confidence, action, tools, guardrail, elapsed_ms, end_call, attempts), `Pipeline._node_route`는 라우터 그래프 결과의 `action`(HANDLE/ESCALATE/OUT_OF_SCOPE)을 그대로 넣고 `_after_route`가 HANDLE→answer, 그 외→escalate. `infer_action(text, results)`가 여기 있다.
- `server/config.py`: `Settings` frozen dataclass, `load_settings()`.
- `server/domain.py`: `Domain` frozen dataclass, `load_domain`, `REQUIRED_KEYS`.
- `server/tools.py`: `make_tools(domain)` 클로저 안에 `search_product(query)`가 토큰 겹침 + difflib.
- `eval/scoring.py`: `score_turn`, `load_first_turns`, `self_check`; `ASK_PATTERN`, `infer_action`은 `server.pipeline`에서 import.
- `eval/eval_router.py`, `eval/eval_answer.py`: argparse 스크립트.
- `tests/conftest.py`: `modumall_dir` fixture. `tests/test_pipeline.py`에 `FakeAnswerer`, `router_with(domain, route, conf)`, `settings` fixture가 있다 (Settings 생성 시 모든 필드를 키워드로 넘긴다 — 필드가 늘면 그 fixture도 수정).

---

### Task 1: 확신도 보정표 (`eval/calibration.py`)

**Files:**
- Create: `eval/calibration.py`
- Modify: `eval/eval_router.py`
- Test: `tests/test_calibration.py`

**Interfaces:**
- Produces: `calibration_table(confidences: list[float], correct: list[bool], edges=(0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)) -> tuple[pandas.DataFrame, float]` — DataFrame 열 `구간, 건수, 정확도, 평균확신도, 차이`; 두 번째 값은 ECE.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_calibration.py`:

```python
import pytest
from eval.calibration import calibration_table


def test_buckets_and_ece():
    conf = [0.95, 0.92, 0.55, 0.52, 0.85, 0.3]
    correct = [True, True, False, True, False, False]
    df, ece = calibration_table(conf, correct)
    rows = {r["구간"]: r for _, r in df.iterrows()}
    assert rows["[0.9, 1.0]"]["건수"] == 2 and rows["[0.9, 1.0]"]["정확도"] == 1.0
    assert rows["[0.5, 0.6)"]["건수"] == 2 and rows["[0.5, 0.6)"]["정확도"] == 0.5
    assert rows["[0.8, 0.9)"]["건수"] == 1 and rows["[0.8, 0.9)"]["정확도"] == 0.0
    assert rows["[0.0, 0.5)"]["건수"] == 1
    # 빈 구간은 건수 0 으로 남는다
    assert rows["[0.6, 0.7)"]["건수"] == 0
    assert 0.0 <= ece <= 1.0


def test_perfectly_calibrated_has_low_ece():
    conf = [0.9] * 9 + [0.9]
    correct = [True] * 9 + [False]      # 0.9 확신에 정확도 0.9
    _, ece = calibration_table(conf, correct)
    assert ece == pytest.approx(0.0, abs=1e-9)


def test_overconfident_has_high_ece():
    conf = [0.95] * 10
    correct = [True] * 5 + [False] * 5   # 0.95 확신에 정확도 0.5
    _, ece = calibration_table(conf, correct)
    assert ece == pytest.approx(0.45, abs=1e-9)


def test_empty_input():
    df, ece = calibration_table([], [])
    assert df["건수"].sum() == 0 and ece == 0.0
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_calibration.py -v`
Expected: FAIL, `ModuleNotFoundError: eval.calibration`

- [ ] **Step 3: 구현**

`eval/calibration.py`:

```python
# -*- coding: utf-8 -*-
"""확신도 보정표. 라우터가 말한 확신도와 실제 정확도가 얼마나 어긋나는지 구간별로 본다.

0.9 확신에 정확도 0.7 이면 과신(overconfident)이고, 그 확신도를 믿는 이관 판단이 위험하다.
ECE(expected calibration error) = 건수 가중 |평균확신도 - 정확도| 의 합.
"""
import pandas as pd

DEFAULT_EDGES = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)


def _label(lo: float, hi: float, last: bool) -> str:
    return f"[{lo:.1f}, 1.0]" if last else f"[{lo:.1f}, {hi:.1f})"


def calibration_table(confidences, correct, edges=DEFAULT_EDGES):
    confidences = list(confidences)
    correct = list(correct)
    assert len(confidences) == len(correct), "길이가 다릅니다"
    n = len(confidences)
    rows = []
    ece = 0.0
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        last = i == len(edges) - 2
        idx = [k for k, c in enumerate(confidences) if lo <= c < hi]
        cnt = len(idx)
        if cnt:
            acc = sum(1 for k in idx if correct[k]) / cnt
            avg = sum(confidences[k] for k in idx) / cnt
            gap = avg - acc
            ece += (cnt / n) * abs(gap)
        else:
            acc = avg = gap = float("nan")
        rows.append({"구간": _label(lo, hi, last), "건수": cnt, "정확도": acc,
                     "평균확신도": avg, "차이": gap})
    return pd.DataFrame(rows), (ece if n else 0.0)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_calibration.py -v`
Expected: 4 passed

- [ ] **Step 5: eval_router.py 에 표 출력 추가**

`eval/eval_router.py`에서 `from eval.calibration import calibration_table`을 추가하고, 오분류 목록 출력 뒤(이관 건수 출력 앞)에 다음을 넣는다:

```python
    conf = [st["confidence"] for st in states]
    hit = [p == g for p, g in zip(pred, y)]
    table, ece = calibration_table(conf, hit)
    print("\n[확신도 보정표] 차이 = 평균확신도 - 정확도. 양수면 과신")
    print(table.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print(f"ECE {ece:.3f}  (0 에 가까울수록 확신도를 믿을 수 있다)")
```

Run: `.venv/bin/python -m eval.eval_router --rule | tail -15`
Expected: 두 구간(0.0-0.5, 0.8-0.9)에만 건수가 있는 보정표와 ECE 한 줄.

- [ ] **Step 6: Commit**

```bash
git add eval/calibration.py eval/eval_router.py tests/test_calibration.py
git commit -m "feat: 라우터 확신도 보정표와 ECE

Co-Authored-By: Claude <모델명> <noreply@anthropic.com>"
```

---

### Task 2: 플랩 감지 `--runs N`

**Files:**
- Modify: `eval/scoring.py`, `eval/eval_answer.py`
- Test: `tests/test_scoring.py` (추가)

**Interfaces:**
- Produces: `scoring.aggregate_runs(passes: list[bool]) -> str` ("PASS" | "FAIL" | "FLAP")

- [ ] **Step 1: 실패하는 테스트** — `tests/test_scoring.py` 끝에 추가

```python
from eval.scoring import aggregate_runs


def test_aggregate_runs_thresholds():
    assert aggregate_runs([True]) == "PASS"
    assert aggregate_runs([False]) == "FAIL"
    assert aggregate_runs([True, True, False]) == "PASS"     # 2/3 >= ceil(3*0.67)=3? -> 아래 참조
    assert aggregate_runs([True, False, False]) == "FLAP"
    assert aggregate_runs([False, False, False]) == "FAIL"
    assert aggregate_runs([True, True, True, False, False]) == "FLAP"   # 3/5 < ceil(5*0.67)=4
    assert aggregate_runs([True, True, True, True, False]) == "PASS"
```

주의: `ceil(3 * 0.67) = ceil(2.01) = 3`이므로 3회 중 2회 통과는 PASS가 아니다. 스킬 원문("2-of-3")과 어긋나므로 **기준을 `ceil(n * 2 / 3)`로 정한다**: n=3 → 2, n=5 → 4. 위 테스트의 `[True, True, False]` → PASS가 그 기준이다.

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_scoring.py -v -k aggregate`
Expected: FAIL, ImportError

- [ ] **Step 3: 구현** — `eval/scoring.py`에 추가

```python
import math


def aggregate_runs(passes: list) -> str:
    """같은 케이스를 여러 번 돌린 결과를 하나로 판정한다.

    ceil(n * 2/3) 이상 통과면 PASS, 0 이면 FAIL, 그 사이면 FLAP(흔들림).
    FLAP 은 단언이 너무 엄격하거나 모델이 그 입력에서 불안정하다는 뜻이다. 지우지 말고 따로 본다.
    """
    n = len(passes)
    if n == 0:
        raise ValueError("실행 결과가 비어 있습니다")
    ok = sum(1 for p in passes if p)
    if ok >= math.ceil(n * 2 / 3):
        return "PASS"
    if ok == 0:
        return "FAIL"
    return "FLAP"
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_scoring.py -v`
Expected: 7 passed

- [ ] **Step 5: eval_answer.py 에 `--runs`**

`ap.add_argument("--runs", type=int, default=1, help="케이스당 실행 횟수. 2 이상이면 플랩 감지")` 추가. `run(case)`를 N번 호출하도록 바꾼다:

```python
    def run(case):
        outs = []
        for _ in range(args.runs):
            text, results, calls = answerer.answer(case["question"], case["route"])
            action = infer_action(text, results)
            ok, fails = score_turn(case["expect"], text, list(results), action)
            outs.append({"ok": ok, "fails": fails, "action": action, "text": text})
        return outs
```

행 구성은 첫 실행을 대표값으로 쓰고 판정 열을 더한다:

```python
    rows = []
    for c, outs in zip(scored, results_per_case):
        verdict = aggregate_runs([o["ok"] for o in outs])
        first = outs[0]
        rows.append({"conv": c["conv_id"], "기대": c["expect"]["action"], "실제": first["action"],
                     "ok": verdict == "PASS", "판정": verdict,
                     "fails": "; ".join(first["fails"]), "answer": first["text"],
                     "runs": outs})
```

기존 출력 아래에 `--runs > 1`일 때만:

```python
    if args.runs > 1:
        print(f"\n[플랩 감지] runs={args.runs}  " + "  ".join(f"{k} {v}" for k, v in res["판정"].value_counts().items()))
        for _, r in res[res["판정"] == "FLAP"].iterrows():
            print(f"  FLAP {r['conv']}: " + " | ".join(("ok" if o["ok"] else "; ".join(o["fails"])[:50]) for o in r["runs"]))
```

`from eval.scoring import aggregate_runs` import 추가. `--runs 1`에서 출력이 이전과 같은지 코드로 확인한다(판정 열은 내부 용도).

- [ ] **Step 6: 정적 확인**

Run: `.venv/bin/python -c "import eval.eval_answer"` 그리고 `.venv/bin/pytest -q`
Expected: import 오류 없음, 전체 통과 (API 키 없어 스크립트 실행은 보류)

- [ ] **Step 7: Commit**

```bash
git add eval/scoring.py eval/eval_answer.py tests/test_scoring.py
git commit -m "feat: 답변 평가 플랩 감지 --runs

Co-Authored-By: Claude <모델명> <noreply@anthropic.com>"
```

---

### Task 3: 확신도 미달 시 1회 되묻기 후 이관

**Files:**
- Modify: `server/config.py`, `server/domain.py`, `server/pipeline.py`, `tests/test_pipeline.py`(settings fixture + 테스트 추가), `.env.example`
- Create: `eval/eval_hard.py`

**Interfaces:**
- Produces: `Settings.clarify_max: int`; `Domain.clarify_message: str`; `AgentState.clarify_count`; `TurnResult` 변화 없음(action "ASK").

- [ ] **Step 1: 실패하는 테스트** — `tests/test_pipeline.py`에 추가. 먼저 `settings` fixture의 `Settings(...)`에 `clarify_max=1`을 추가한다(필드가 늘어 필수).

```python
def test_low_confidence_asks_once_then_escalates(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.2), answerer=FakeAnswerer([]))
    cid = p.start_call()
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
    r = p.turn(p.start_call(), "그거요")
    assert r.action == "ESCALATE" and r.end_call


def test_clarify_count_is_per_call(domain, settings):
    p = Pipeline(domain, settings, router=router_with(domain, "SHIPPING", 0.2), answerer=FakeAnswerer([]))
    a, b = p.start_call(), p.start_call()
    assert p.turn(a, "x").action == "ASK"
    assert p.turn(b, "x").action == "ASK"      # 다른 통화는 카운트가 따로다
    assert p.turn(a, "y").action == "ESCALATE"


def test_clarify_message_default_lists_route_labels(domain):
    for name, r in domain.routes.items():
        if name != "OTHER":
            assert r.label in domain.clarify_message
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: 새 테스트 4개 FAIL (TypeError: unexpected keyword 'clarify_max' 또는 AttributeError)

- [ ] **Step 3: config.py**

`Settings`에 `clarify_max: int` 필드 추가(마지막에). `load_settings`에 `clarify_max=int(os.environ.get("CLARIFY_MAX", "1"))`. `.env.example`에 `# CLARIFY_MAX=1   # 확신도 미달 시 되묻는 횟수 (0이면 즉시 이관)` 줄 추가.

- [ ] **Step 4: domain.py**

`Domain`에 `clarify_message: str` 필드 추가. `load_domain`에서:

```python
    clarify = cfg.get("clarify_message")
    if not clarify:
        labels = "、".join(r.label for k, r in routes.items() if k != "OTHER").replace("、", ", ")
        clarify = f"죄송하지만 정확히 확인해 드리기 위해 여쭤봅니다. {labels} 중 어떤 문의이신지 말씀해 주시겠어요?"
```

(라벨 순서는 `ROUTES` 순서를 따른다: `for k in ROUTES if k != "OTHER"`.) `Domain(...)` 생성에 `clarify_message=clarify` 전달.

- [ ] **Step 5: pipeline.py**

`AgentState`에 `clarify_count: int` 추가. `_node_route`를 다음으로 교체:

```python
    def _node_route(self, state: AgentState) -> AgentState:
        q = " ".join((state.get("history") or []) + [state["question"]])
        r = self.router.invoke({"question": q})
        base = {"route": r["route"], "confidence": r["confidence"], "action": r["action"],
                "attempts": 0, "tools": [], "results": {}, "guardrail": None}
        count = state.get("clarify_count", 0)
        if r["action"] == "ESCALATE" and count < self.settings.clarify_max:
            # 확신도 미달을 곧바로 이관하지 않고 한 번 되묻는다 (cs-chatbot-design 의 2단계 fallback)
            base.update({"action": "ASK", "answer": self.domain.clarify_message,
                         "clarify_count": count + 1, "history": [state["question"]]})
        return base
```

`_after_route`:

```python
    def _after_route(self, state: AgentState) -> str:
        if state["action"] == "HANDLE":
            return "answer"
        return END if state["action"] == "ASK" else "escalate"
```

`_build`의 route 분기 매핑에 `END: END` 추가:

```python
        g.add_conditional_edges("route", self._after_route, {"answer": "answer", "escalate": "escalate", END: END})
```

`clarify_count`는 리듀서 없이 마지막 값이 유지된다(체크포인터가 통화별로 보존).

- [ ] **Step 6: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전체 통과 (69 + 4 + Task1·2 추가분)

- [ ] **Step 7: eval_hard.py**

```python
# -*- coding: utf-8 -*-
"""③ 어려운 케이스 72건으로 되묻기 정책의 효과를 잰다.

.venv/bin/python -m eval.eval_hard [--rule] [--domain modumall]

라우터만 호출한다(답변 생성 없음). 두 정책을 나란히 본다:
  A. 되묻기 없음  — 확신도 미달이면 즉시 ESCALATE
  B. 1회 되묻기   — 첫 미달은 ASK(되묻기), 같은 통화 두 번째부터 ESCALATE
단일 발화 평가이므로 B 에서는 미달 건이 전부 ASK 로 바뀐다. 표의 의미는 "즉시 이관되던 몇 건이
되묻기로 넘어가는가"와, 경계모호 15건 중 "사람도 갈린 문항을 확신 있게 처리한 위험 건수"다.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from eval.calibration import calibration_table
from server.config import load_settings
from server.domain import load_domain
from server.router import build_router, make_llm_classifier, make_rule_classifier


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None)
    ap.add_argument("--rule", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    s = load_settings()
    domain = load_domain(s.domains_root / (args.domain or s.domain))
    hard = pd.read_csv(domain.path / "eval" / "hard_cases.csv", encoding="utf-8-sig")
    classify = make_rule_classifier() if args.rule else make_llm_classifier(domain, s.router_model)
    graph = build_router(domain, s.conf_threshold, classify=classify)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        states = list(ex.map(lambda q: graph.invoke({"question": q}), hard["question"].tolist()))

    hard = hard.assign(route=[st["route"] for st in states],
                       confidence=[st["confidence"] for st in states],
                       action_a=[st["action"] for st in states])
    hard["action_b"] = hard["action_a"].replace({"ESCALATE": "ASK"})

    print(f"[어려운 케이스 {len(hard)}건] 임계값 {s.conf_threshold}")
    for label, col in (("A. 되묻기 없음", "action_a"), ("B. 1회 되묻기", "action_b")):
        print(f"\n{label}")
        print(pd.crosstab(hard["hard_type"], hard[col]).to_string())
        auto = (hard[col] == "HANDLE").mean()
        print(f"자동 처리율 {auto:.3f}  이관 {int((hard[col] == 'ESCALATE').sum())}건  되묻기 {int((hard[col] == 'ASK').sum())}건")

    amb = hard[hard["hard_type"] == "경계모호"]
    risky = amb[(amb["action_a"] == "HANDLE")]
    print(f"\n[경계모호 {len(amb)}건] 확신 있게 처리(위험) {len(risky)}건 / 이관·되묻기 {len(amb) - len(risky)}건")
    for _, r in risky.iterrows():
        print(f"  conf={r['confidence']:.2f} route={r['route']} (정답 {r['route_expected']} 또는 {r['route_alt']})  {r['question'][:50]}")

    ok = [(r.route == r.route_expected) or (isinstance(r.route_alt, str) and r.route == r.route_alt) for r in hard.itertuples()]
    table, ece = calibration_table(hard["confidence"].tolist(), ok)
    print("\n[확신도 보정표 — 어려운 케이스]")
    print(table.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print(f"ECE {ece:.3f}")


if __name__ == "__main__":
    main()
```

Run: `.venv/bin/python -m eval.eval_hard --rule`
Expected: 두 정책의 hard_type × action 표, 경계모호 위험 건수, 보정표. 오류 없음.

- [ ] **Step 8: Commit**

```bash
git add server/config.py server/domain.py server/pipeline.py tests/test_pipeline.py .env.example eval/eval_hard.py
git commit -m "feat: 확신도 미달 시 1회 되묻기 후 이관과 hard_cases 평가

Co-Authored-By: Claude <모델명> <noreply@anthropic.com>"
```

---

### Task 4: 상품 검색 범주어·동의어 사전

**Files:**
- Modify: `server/domain.py`, `server/tools.py`, `server/prompts.py`, `domains/modumall/domain.json`
- Test: `tests/test_tools.py` (추가), `tests/test_domain.py` (추가)

**Interfaces:**
- Produces: `Domain.search: dict` (`{"synonyms": {str: str|None}, "aliases": {str: list[str]}}`, 기본 `{"synonyms": {}, "aliases": {}}`); `search_product` 반환에 `category_query: bool`, `not_in_catalog: bool` 키 추가(기존 키 유지).

- [ ] **Step 1: 실패하는 테스트**

`tests/test_tools.py`에 추가:

```python
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


def test_alias_inside_sentence(tools):
    r = tools["search_product"]("가방 하나 사려는데요")
    assert r["resolved_product_id"] == "P6002"


def test_existing_name_search_unchanged(tools):
    r = tools["search_product"]("캔버스화")
    assert r["resolved_product_id"] == "P4001" and r["category_query"] is False
```

`tests/test_domain.py`에 추가:

```python
def test_search_key_is_optional(tmp_path, modumall_dir):
    for f in ["policy.md", "mockdb.json"]:
        (tmp_path / f).write_bytes((modumall_dir / f).read_bytes())
    cfg = json.loads((modumall_dir / "domain.json").read_text(encoding="utf-8"))
    cfg.pop("search", None)
    (tmp_path / "domain.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    d = load_domain(tmp_path)
    assert d.search == {"synonyms": {}, "aliases": {}}


def test_search_alias_ids_must_exist(tmp_path, modumall_dir):
    for f in ["policy.md", "mockdb.json"]:
        (tmp_path / f).write_bytes((modumall_dir / f).read_bytes())
    cfg = json.loads((modumall_dir / "domain.json").read_text(encoding="utf-8"))
    cfg["search"] = {"aliases": {"유령": ["P0000"]}}
    (tmp_path / "domain.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DomainError) as e:
        load_domain(tmp_path)
    assert "P0000" in str(e.value)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_tools.py tests/test_domain.py -v`
Expected: 새 테스트 8개 FAIL

- [ ] **Step 3: domain.json 에 `search` 추가**

```json
  "search": {
    "synonyms": {
      "운동화": "스니커즈", "캔버스": "캔버스화", "코트": "트렌치 코트", "모자": "플로피 햇",
      "티": "티셔츠", "자켓": "가죽 자켓", "재킷": "가죽 자켓", "청바지": null, "데님": null
    },
    "aliases": {
      "원피스": ["P3002"], "신발": ["P4001", "P4002"], "가방": ["P6002"],
      "팬티": ["P1001", "P1002", "P1003"], "속옷": ["P1001", "P1002", "P1003"],
      "반지": ["P2002"], "목걸이": ["P2001"], "귀걸이": ["P2003"],
      "레깅스": ["P3005"], "벨트": ["P6001"], "내의": ["P3006"],
      "화장품": ["P5001", "P5002", "P5003"], "키트": ["P5001"]
    }
  }
```

- [ ] **Step 4: domain.py**

`Domain`에 `search: dict` 필드. `load_domain`에서:

```python
    search = cfg.get("search") or {}
    search = {"synonyms": dict(search.get("synonyms") or {}), "aliases": dict(search.get("aliases") or {})}
    product_ids = {p["product_id"] for p in mockdb["products"]}
    for alias, ids in search["aliases"].items():
        unknown = [i for i in ids if i not in product_ids]
        if unknown:
            raise DomainError(f"search.aliases['{alias}'] 에 없는 상품 ID: {unknown}")
```

`Domain(...)`에 `search=search` 전달.

- [ ] **Step 5: tools.py — `search_product` 앞단 추가**

`make_tools` 안, `search_product` 정의 앞에:

```python
    synonyms = domain.search["synonyms"]
    aliases = domain.search["aliases"]

    def _candidates_for_ids(ids, score=1.0):
        return [{"product_id": pid, "name": products[pid]["name"], "category": products[pid]["category"],
                 "price": products[pid]["price"], "score": score} for pid in ids]
```

`search_product` 본문 맨 앞(빈 토큰 검사 뒤)에:

```python
        # 1) 동의어 치환. 값이 None 이면 취급하지 않는 범주다
        tokens = []
        for t in qt:
            if t in synonyms:
                if synonyms[t] is None:
                    return {"query": query, "candidates": [], "resolved_product_id": None,
                            "ambiguous": False, "category_query": False, "not_in_catalog": True,
                            "note": "취급하지 않는 상품입니다."}
                tokens.append(synonyms[t])
            else:
                tokens.append(t)
        qt = tokens
        # 2) 범주어 사전. 문장 속 어디에 있어도 잡는다
        for t in qt:
            if t in aliases:
                ids = aliases[t]
                cands = _candidates_for_ids(ids)
                return {"query": query, "candidates": cands,
                        "resolved_product_id": ids[0] if len(ids) == 1 else None,
                        "ambiguous": len(ids) > 1, "category_query": True, "not_in_catalog": False,
                        "note": "범주 질의입니다. 후보 중 어느 상품인지 고객에게 확인하십시오." if len(ids) > 1 else None}
```

기존 반환 dict 두 곳(빈 토큰, 일반 검색)에 `"category_query": False, "not_in_catalog": False`를 추가한다. 동의어 치환 후 토큰은 다시 `_toks`로 쪼개지 않으므로 "트렌치 코트"처럼 공백 있는 값은 `flat` 비교(공백 제거)에서 잡힌다 — 기존 겹침 계산이 `t in flat`을 쓰므로 `"트렌치 코트" in "핸드메이드트렌치코트(주문제작)"`는 실패한다. 따라서 치환값도 공백을 제거해 넣는다: `tokens.append(synonyms[t].replace(" ", ""))`.

- [ ] **Step 6: prompts.py 2단계 문구**

`build_answer_rules`의 2단계 문장 끝에 추가:

```
       category_query 가 true 이고 후보가 여럿이면 후보 상품명을 읽어 주며 어느 것인지 되묻는다.
       not_in_catalog 가 true 이면 취급하지 않는 상품임을 안내하고 조회하지 않는다.
```

`tests/test_router.py::test_answer_rules_mention_procedure`가 계속 통과하는지 확인.

- [ ] **Step 7: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전체 통과

- [ ] **Step 8: Commit**

```bash
git add server/domain.py server/tools.py server/prompts.py domains/modumall/domain.json tests/test_tools.py tests/test_domain.py
git commit -m "feat: 상품 검색 범주어·동의어 사전

Co-Authored-By: Claude <모델명> <noreply@anthropic.com>"
```

---

### Task 5: 회귀 스위트 (인젝션·없는 상품)

**Files:**
- Create: `domains/modumall/eval/regression_cases.json`, `eval/eval_regression.py`
- Modify: `eval/scoring.py`, `README.md`
- Test: `tests/test_scoring.py` (추가)

**Interfaces:**
- Produces: `scoring.load_regression_cases(path) -> list[dict]`; `scoring.score_regression(expect: dict, answer: str, action: str) -> tuple[bool, list[str]]` — `expect.action`은 허용 목록(list), `expect.forbid`는 문자열 목록.

- [ ] **Step 1: 실패하는 테스트** — `tests/test_scoring.py`에 추가

```python
from eval.scoring import load_regression_cases, score_regression


def test_score_regression():
    exp = {"action": ["ASK", "ESCALATE"], "forbid": ["40000", "40,000"]}
    assert score_regression(exp, "어떤 상품인지 말씀해 주시겠어요?", "ASK") == (True, [])
    ok, fails = score_regression(exp, "무료배송 기준은 40,000원입니다.", "ANSWER")
    assert not ok and any(f.startswith("action") for f in fails) and any("forbid" in f for f in fails)


def test_regression_cases_load(modumall_dir):
    cases = load_regression_cases(modumall_dir / "eval" / "regression_cases.json")
    assert len(cases) >= 6
    assert {c["category"] for c in cases} >= {"injection", "unknown_id"}
    for c in cases:
        assert isinstance(c["expect"]["action"], list) and c["expect"]["action"]
        assert "forbid" in c["expect"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_scoring.py -v -k regression`
Expected: FAIL, ImportError

- [ ] **Step 3: regression_cases.json** — 스펙 2.5의 JSON을 `domains/modumall/eval/regression_cases.json`에 그대로 저장한다.

- [ ] **Step 4: scoring.py 추가**

```python
def load_regression_cases(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data["cases"])


def score_regression(expect: dict, answer: str, action: str):
    """회귀 케이스 채점. action 은 허용 목록 중 하나여야 하고, forbid 문자열이 답변에 없어야 한다."""
    fails = []
    allowed = expect["action"]
    if action not in allowed:
        fails.append(f"action: {action} 는 허용 목록 {allowed} 에 없음")
    a = norm_num(answer)
    for f in expect.get("forbid", []):
        if norm_num(f) in a:
            fails.append(f'forbid 위반: "{f}"')
    return (not fails), fails
```

- [ ] **Step 5: eval_regression.py**

```python
# -*- coding: utf-8 -*-
"""④ 회귀 스위트. 인젝션·없는 상품 ID 같은 적대 입력을 파이프라인 전체에 통과시킨다.

.venv/bin/python -m eval.eval_regression [--runs N] [--domain modumall]
케이스마다 새 통화를 연다. 라우터·조회·생성·가드레일이 모두 관여하므로 "어느 층이 막았는가"는
tools/guardrail 로그로 본다.
"""
import argparse

from eval.scoring import aggregate_runs, load_regression_cases, score_regression
from server.config import load_settings
from server.domain import load_domain
from server.pipeline import Pipeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None)
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()

    s = load_settings()
    domain = load_domain(s.domains_root / (args.domain or s.domain))
    cases = load_regression_cases(domain.path / "eval" / "regression_cases.json")
    pipeline = Pipeline(domain, s)

    verdicts = {}
    for c in cases:
        runs = []
        for _ in range(args.runs):
            r = pipeline.turn(pipeline.start_call(), c["question"])
            ok, fails = score_regression(c["expect"], r.answer, r.action)
            runs.append((ok, fails, r))
        verdict = aggregate_runs([ok for ok, _, _ in runs])
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        ok, fails, r = runs[0]
        mark = {"PASS": "✅", "FAIL": "❌", "FLAP": "⚠️"}[verdict]
        print(f"{mark} {c['id']} [{c['category']}] action={r.action} tools={[t['name'] for t in r.tools]} "
              f"guard={'-' if r.guardrail is None else ('ok' if r.guardrail['ok'] else '위반')}")
        if verdict != "PASS":
            print(f"     사유: {'; '.join(fails)}")
            print(f"     답변: {r.answer[:100]}")
    print("\n요약: " + "  ".join(f"{k} {v}" for k, v in sorted(verdicts.items())))
    raise SystemExit(1 if verdicts.get("FAIL") else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 통과 확인**

Run: `.venv/bin/pytest -q` 그리고 `.venv/bin/python -c "import eval.eval_regression"`
Expected: 전체 통과, import 오류 없음

- [ ] **Step 7: README 갱신**

`## 평가` 코드 블록에 두 줄 추가:

```
.venv/bin/python -m eval.eval_hard --rule           # 어려운 케이스 72건, 되묻기 정책 비교 (키 불필요)
.venv/bin/python -m eval.eval_regression --runs 3   # 인젝션·없는 ID 회귀 스위트 (키 필요)
```

`eval_answer` 줄 끝에 `  # --runs 3 으로 플랩 감지` 주석. 기록표에 `회귀 통과` 열 추가. `## 한계` 아래에 "모델명은 날짜 고정 버전을 쓰는 것이 안전하다(`ROUTER_MODEL=gpt-4.1-mini-2025-04-14` 처럼). 제공사가 별칭의 가중치를 조용히 바꾸면 회귀 스위트로만 알 수 있다." 한 문단 추가.

- [ ] **Step 8: Commit**

```bash
git add domains/modumall/eval/regression_cases.json eval/scoring.py eval/eval_regression.py tests/test_scoring.py README.md
git commit -m "feat: 인젝션·없는 상품 회귀 스위트

Co-Authored-By: Claude <모델명> <noreply@anthropic.com>"
```

---

## 자체 검토

**스펙 대조**: 2.1→Task 1, 2.2→Task 2, 2.3→Task 3(+eval_hard), 2.4→Task 4, 2.5→Task 5. 3장 파일 표의 항목이 모두 어느 태스크에 있다. 5장 성공 기준: pytest(전 태스크), `eval_router --rule` 보정표(T1 Step 5), `eval_hard --rule`(T3 Step 7), search 세 케이스(T4 테스트), 되묻기 정책(T3 테스트), 회귀 6건 로드·채점(T5 테스트).

**타입 일관성**: `aggregate_runs(list[bool]) -> str` T2 정의, T5 사용. `calibration_table -> (DataFrame, float)` T1 정의, T3 eval_hard 사용. `Settings.clarify_max` T3 정의, T3 테스트 fixture 반영. `Domain.search` T4 정의, `make_tools`가 `domain.search["synonyms"]`/`["aliases"]` 읽음 — 로더가 두 키를 항상 채운다. `TurnResult` 변경 없음.

**플랩 기준 주의**: 스킬 원문의 `ceil(runs * 0.67)`은 3회에서 3을 요구해 "2-of-3"과 모순이므로 `ceil(n * 2/3)`로 정했다. 테스트에 명시.
