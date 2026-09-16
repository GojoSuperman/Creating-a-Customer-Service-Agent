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
            cid, _, _ = pipeline.start_call()
            r = pipeline.turn(cid, c["question"])
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
