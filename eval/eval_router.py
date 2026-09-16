# -*- coding: utf-8 -*-
"""① 의도 분류 평가.  .venv/bin/python -m eval.eval_router [--limit N] [--rule] [--domain modumall]"""
import argparse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

from eval.calibration import calibration_table
from server.config import load_settings
from server.domain import ROUTES, load_domain
from server.router import build_router, make_llm_classifier, make_rule_classifier

LABELS4 = [r for r in ROUTES if r != "OTHER"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rule", action="store_true", help="키워드 규칙 분류기로 기준선을 잰다")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    s = load_settings()
    domain = load_domain(s.domains_root / (args.domain or s.domain))
    ev_dir = domain.path / "eval"
    inq = pd.read_csv(ev_dir / "customer_inquiries.csv", encoding="utf-8-sig")
    ans = pd.read_csv(ev_dir / "routing_answers.csv", encoding="utf-8-sig")
    ev = inq.merge(ans, on="qa_id")
    ev = ev[ev["split"] == "eval"].reset_index(drop=True)
    if args.limit:
        ev = ev.head(args.limit)

    classify = make_rule_classifier() if args.rule else make_llm_classifier(domain, s.router_model)
    graph = build_router(domain, s.conf_threshold, classify=classify, conf_margin=s.conf_margin)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        states = list(ex.map(lambda q: graph.invoke({"question": q}), ev["question"].tolist()))
    pred = [st["route"] for st in states]
    y = ev["route"].tolist()

    name = "규칙 라우터" if args.rule else f"LLM 라우터 ({s.router_model})"
    print(f"[{name}] n={len(ev)}  정확도 {accuracy_score(y, pred):.3f}  "
          f"macro F1 {f1_score(y, pred, labels=LABELS4, average='macro', zero_division=0):.3f}\n")
    print(classification_report(y, pred, labels=LABELS4, digits=3, zero_division=0))
    print("[혼동 행렬] 행=정답, 열=예측")
    print(pd.DataFrame(confusion_matrix(y, pred, labels=LABELS4), index=LABELS4, columns=LABELS4).to_string())
    miss = [(r["question"], r["route"], p, st["confidence"]) for (_, r), p, st in zip(ev.iterrows(), pred, states) if r["route"] != p]
    print(f"\n[오분류 {len(miss)}건] — 여기를 읽는 것이 개선의 출발점이다")
    for q, g, p, c in miss:
        print(f"  [{g} → {p}] conf={c:.2f}  {q[:60]}")
    conf = [st["confidence"] for st in states]
    hit = [p == g for p, g in zip(pred, y)]
    table, ece = calibration_table(conf, hit)
    print("\n[확신도 보정표] 차이 = 평균확신도 - 정확도. 양수면 과신")
    print(table.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print(f"ECE {ece:.3f}  (0 에 가까울수록 확신도를 믿을 수 있다)")
    esc = sum(1 for st in states if st["action"] == "ESCALATE")
    print(f"\n이관 {esc}건 / 자동화율 {(len(states) - esc) / len(states):.3f}")


if __name__ == "__main__":
    main()
