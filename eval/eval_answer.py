# -*- coding: utf-8 -*-
"""② 1턴 답변 평가.  .venv/bin/python -m eval.eval_answer [--limit N] [--domain modumall] [--runs N] [--judge]

라우트는 정답셋 값을 그대로 쓴다(라우터 실패와 섞지 않기 위해). 채점하는 것은 조회와 답변 생성이다.
--judge 를 켜면 규칙 채점이 must 누락만으로 떨어뜨린 건을 LLM judge 가 의미 기준으로 다시 본다.
"""
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from eval.judge import judge_self_check, judge_turn, make_judge
from eval.scoring import (aggregate_runs, fail_kinds, infer_action, load_first_turns, missing_must,
                          needs_judge, score_answer, score_tools, score_tools_legacy, self_check,
                          self_check_split)
from server.answer import Answerer
from server.config import load_settings
from server.domain import load_domain

AUTO_ACTIONS = {"ANSWER", "ASK", "OUT_OF_SCOPE"}


def verdict_label(outs: list) -> tuple[str, bool]:
    """runs 별 결과 → (판정 라벨, rule_ok).

    규칙 통과율(rule_ok)과 판정 라벨의 PASS(규칙) 건수가 같은 정의를 쓰도록 여기 한 곳에서 정한다:
    둘 다 aggregate_runs 의 다수결로 "규칙만으로 통과"인지를 판단한다.
    """
    verdict = aggregate_runs([o["ok"] for o in outs])
    rule_ok = aggregate_runs([o["how"] == "규칙" for o in outs]) == "PASS"
    if verdict != "PASS":
        return verdict, rule_ok
    return ("PASS(규칙)" if rule_ok else "PASS(judge)"), rule_ok


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
    tool_bad, ans_bad = self_check_split(cases)
    print(f"[채점기 자기 검증] 모범 답안 {len(cases)}건 중 실패 {len(bad)}건 {bad if bad else '✅'}"
          f"  (도구 호출 적절성 실패 {len(tool_bad)}건 {tool_bad or '✅'} /"
          f" 답변 적절성 실패 {len(ans_bad)}건 {ans_bad or '✅'})")
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
    judge_lock = threading.Lock()   # judge 호출 수는 워커 스레드가 공유한다

    def run(case):
        outs = []
        for _ in range(args.runs):
            text, results, calls = answerer.answer(case["question"], case["route"])
            action = infer_action(text, results)
            # 두 지표를 따로 채점한다: 도구 호출 적절성(tool_ok)과 답변 적절성(ans_ok).
            tool_ok, tool_fails = score_tools(case["expect"], list(results), action)
            # 옛 정의(과거 62.5%·56.2% 와 같은 잣대) — missing 만 보고 extra 는 감점하지 않는다.
            # 종합(기존 정의)을 함께 내려면 필요하다. 판정 자체에는 쓰지 않는다(요구사항은 엄격 정의).
            tool_ok_legacy, tool_fails_legacy = score_tools_legacy(case["expect"], list(results), action)
            ans_ok, ans_fails = score_answer(case["expect"], text)
            how = "규칙" if ans_ok else None
            # judge 는 답변 적절성의 must 누락만 완화한다 — 도구 호출 적절성은 judge 대상이 아니다
            # (요구사항: "실제로 호출한 도구 집합이 기대 도구와 정확히 일치"는 표현 문제가 아니다).
            if not ans_ok and judge is not None and needs_judge(ans_fails):
                with judge_lock:
                    judge_calls[0] += 1
                v = judge_turn(judge, case["question"], case["expect"], text, missing_must(ans_fails))
                if v.passed:
                    ans_ok, how = True, "judge"
                    ans_fails = [f"(judge 통과) {v.reason}"]
                else:
                    ans_fails = ans_fails + [f"judge: {v.reason}"]
            ok = tool_ok and ans_ok
            ok_legacy = tool_ok_legacy and ans_ok   # 종합(기존 정의) — 과거 62.5%·56.2% 와 비교용
            fails = tool_fails + ans_fails
            outs.append({"ok": ok, "ok_legacy": ok_legacy, "tool_ok": tool_ok, "ans_ok": ans_ok, "how": how,
                         "fails": fails, "fails_legacy": tool_fails_legacy + ans_fails,
                         "action": action, "text": text})
        return outs

    # case 별로 끝나는 대로 진행 상황을 stdout 에 흘린다 — 429 로 중간에 죽어도 어디까지 됐는지
    # 로그 파일에 남아 이어갈 수 있게 한다(429 로 두 번 죽은 전례가 있다).
    results_by_conv = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(run, c): c for c in scored}
        done = 0
        for fut in as_completed(futures):
            c = futures[fut]
            outs = fut.result()
            results_by_conv[c["conv_id"]] = outs
            done += 1
            ok = aggregate_runs([o["ok"] for o in outs]) == "PASS"
            print(f"[진행 {done}/{len(scored)}] {c['conv_id']} {'PASS' if ok else 'FAIL'}", flush=True)
    results_per_case = [results_by_conv[c["conv_id"]] for c in scored]

    rows = []
    for c, outs in zip(scored, results_per_case):
        verdict = aggregate_runs([o["ok"] for o in outs])
        verdict_legacy = aggregate_runs([o["ok_legacy"] for o in outs])
        tool_verdict = aggregate_runs([o["tool_ok"] for o in outs])
        ans_verdict = aggregate_runs([o["ans_ok"] for o in outs])
        first = outs[0]
        label, rule_ok = verdict_label(outs)
        rows.append({"conv": c["conv_id"], "기대": c["expect"]["action"], "실제": first["action"],
                     "ok": verdict == "PASS", "ok_legacy": verdict_legacy == "PASS",
                     "tool_ok": tool_verdict == "PASS", "ans_ok": ans_verdict == "PASS",
                     "판정": label, "rule_ok": rule_ok,
                     "fails": "; ".join(first["fails"]), "answer": first["text"], "runs": outs})
    res = pd.DataFrame(rows)
    if res.empty:
        print("채점할 항목이 없습니다")
        return
    n = len(res)
    tool_n, ans_n = int(res["tool_ok"].sum()), int(res["ans_ok"].sum())
    print(f'\n[두 지표]  도구 호출 적절성(엄격, 집합 정확 일치) {tool_n}/{n} ({100 * tool_n / n:.1f}%)'
          f'   답변 적절성 {ans_n}/{n} ({100 * ans_n / n:.1f}%)')
    print(f'[종합(엄격) — 위 두 지표의 AND, 과거 기록과는 잣대가 달라 직접 비교 불가]  통과'
          f' {int(res["ok"].sum())}건/{n}건 ({100 * res["ok"].mean():.1f}%)'
          f'   (자동 판정 불가 {len(cases) - len(scored)}건 제외)')
    print(f'[종합(기존 정의, 과거 62.5%·56.2% 와 비교용) — 도구는 missing 만 보고 extra 는 무시]  통과'
          f' {int(res["ok_legacy"].sum())}건/{n}건 ({100 * res["ok_legacy"].mean():.1f}%)')
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
