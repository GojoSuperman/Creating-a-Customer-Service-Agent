# -*- coding: utf-8 -*-
"""② 1턴 답변 평가.  .venv/bin/python -m eval.eval_answer [--limit N] [--domain modumall] [--runs N] [--judge]

라우트는 정답셋 값을 그대로 쓴다(라우터 실패와 섞지 않기 위해). 채점하는 것은 조회와 답변 생성이다.
--judge 를 켜면 규칙 채점이 must 누락만으로 떨어뜨린 건을 LLM judge 가 의미 기준으로 다시 본다.
"""
import argparse
import threading
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
    judge_lock = threading.Lock()   # judge 호출 수는 워커 스레드가 공유한다

    def run(case):
        outs = []
        for _ in range(args.runs):
            text, results, calls = answerer.answer(case["question"], case["route"])
            action = infer_action(text, results)
            ok, fails = score_turn(case["expect"], text, list(results), action)
            how = "규칙" if ok else None
            if not ok and judge is not None and needs_judge(fails):
                with judge_lock:
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
