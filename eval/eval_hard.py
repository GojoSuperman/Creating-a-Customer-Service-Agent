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

from eval.calibration import MARGIN_EDGES, _margin, calibration_table, gate_grid, recommend_gate
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
    # csv 의 route_alt(정답 후보)와 라우터가 낸 route_alt(2순위 예측)의 이름이 겹치므로 미리 구분한다
    hard = hard.rename(columns={"route_alt": "route_alt_expected"})
    classify = make_rule_classifier() if args.rule else make_llm_classifier(domain, s.router_model)
    graph = build_router(domain, s.conf_threshold, classify=classify, conf_margin=s.conf_margin)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        states = list(ex.map(lambda q: graph.invoke({"question": q}), hard["question"].tolist()))

    hard = hard.assign(route=[st["route"] for st in states],
                       confidence=[st["confidence"] for st in states],
                       action_a=[st["action"] for st in states],
                       route_alt=[st.get("route_alt") for st in states],
                       alt_confidence=[st.get("alt_confidence", 0.0) for st in states])
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
        print(f"  conf={r['confidence']:.2f} route={r['route']} (정답 {r['route_expected']} 또는 {r['route_alt_expected']})  {r['question'][:50]}")

    ok = [(r.route == r.route_expected) or (isinstance(r.route_alt_expected, str) and r.route == r.route_alt_expected)
          for r in hard.itertuples()]
    table, ece = calibration_table(hard["confidence"].tolist(), ok)
    print("\n[확신도 보정표 — 어려운 케이스]")
    print(table.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print(f"ECE {ece:.3f}")

    margins = hard.apply(_margin, axis=1).tolist()   # 게이트 마진 계산은 calibration._margin 하나만 쓴다
    mtable, _ = calibration_table(margins, ok, edges=MARGIN_EDGES)
    print("\n[마진 보정표 — 1순위-2순위 확신도 차이 구간별 정확도]")
    print(mtable.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    grid = gate_grid(hard)
    print("\n[임계값×마진 격자] 게이트통과율 / 경계모호 위험 / 비모호 오이관")
    print(grid.pivot(index="임계값", columns="마진", values="게이트통과율").to_string(float_format=lambda v: f"{v:.3f}"))
    print(grid.pivot(index="임계값", columns="마진", values="경계모호위험").to_string())
    print(grid.pivot(index="임계값", columns="마진", values="비모호오이관").to_string())
    best = recommend_gate(grid, max_risky=5)
    print("\n[추천] " + (f"CONF_THRESHOLD={best['임계값']} CONF_MARGIN={best['마진']}  게이트통과율 {best['게이트통과율']:.3f}  경계모호 위험 {best['경계모호위험']}건"
                       if best else "경계모호 위험 5건 이하를 만족하는 조합이 없음"))


if __name__ == "__main__":
    main()
