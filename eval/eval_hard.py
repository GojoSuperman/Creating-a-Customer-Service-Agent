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
