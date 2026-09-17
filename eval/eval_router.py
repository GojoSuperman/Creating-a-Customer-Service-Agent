# -*- coding: utf-8 -*-
"""① 의도 분류 평가.  .venv/bin/python -m eval.eval_router [--limit N] [--rule] [--domain modumall]
멀티턴: .venv/bin/python -m eval.eval_router --multiturn [--no-history]"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

from eval.calibration import calibration_table
from server.config import load_settings
from server.domain import ROUTES, load_domain
from server.pipeline import compose_router_input, should_inherit
from server.router import build_router, make_llm_classifier, make_rule_classifier

LABELS4 = [r for r in ROUTES if r != "OTHER"]


def load_multiturn(path: Path) -> list:
    return list(json.loads(Path(path).read_text(encoding="utf-8"))["conversations"])


def route_conversation(graph, turns: list, use_history: bool = True, inherit: bool = False) -> list:
    """대화 하나를 턴 순서대로 라우팅한다. 런타임 _node_route 와 같은 규칙:
    입력은 compose_router_input 으로 만들고, 라우트 이어받기는 inherit=True(런타임의 FOLLOWUP_INHERIT)
    일 때만 한다. 켠 경우에도 followup 이고 직전 턴이 게이트를 통과했고 직전 라우트가 OTHER 가 아닐 때만 이어받는다.
    is_followup 은 라우터가 낸 값 그대로 기록한다(이어받기 여부와 별개로 인식률을 재기 위해)."""
    history, prev, prev_gated, out = [], None, False, []
    for t in turns:
        q = compose_router_input(t["text"], history, prev) if use_history else t["text"]
        st = graph.invoke({"question": q})
        gated = st["action"] == "ESCALATE"   # 게이트(확신도·마진)가 이 턴의 판단을 거부했다
        followup = should_inherit(st.get("is_followup"), inherit, prev, prev_gated)
        route = prev if followup else st["route"]
        out.append({"route": route, "is_followup": bool(st.get("is_followup")), "confidence": st["confidence"]})
        history.append(t["text"])
        prev, prev_gated = route, gated
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rule", action="store_true", help="키워드 규칙 분류기로 기준선을 잰다")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--multiturn", action="store_true", help="멀티턴 라우트셋으로 드리프트 보정을 잰다")
    ap.add_argument("--no-history", action="store_true", help="--multiturn 기준선: 직전 문의·라우트 없이 단일 발화로")
    ap.add_argument("--inherit", action="store_true", help="--multiturn 에서 후속 발화가 직전 라우트를 이어받게 한다 (런타임 FOLLOWUP_INHERIT=1)")
    args = ap.parse_args()

    s = load_settings()
    domain = load_domain(s.domains_root / (args.domain or s.domain))
    ev_dir = domain.path / "eval"

    classify = make_rule_classifier() if args.rule else make_llm_classifier(domain, s.router_model)
    graph = build_router(domain, s.conf_threshold, classify=classify, conf_margin=s.conf_margin)

    if args.multiturn:
        convs = load_multiturn(ev_dir / "multiturn_routes.json")
        if args.limit:
            convs = convs[:args.limit]
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            preds = list(ex.map(lambda c: route_conversation(graph, c["turns"], use_history=not args.no_history,
                                                             inherit=args.inherit), convs))
        sc = score_multiturn(convs, preds)
        inh = "이어받기 켬" if args.inherit else "이어받기 끔"
        mode = f"히스토리 없음(기준선), {inh}" if args.no_history else f"히스토리+직전 라우트({inh})"
        print(f"[멀티턴 {len(convs)}대화, {mode}] 턴1 정확도 {sc['turn1_acc']:.3f}  턴2+ 정확도 {sc['later_acc']:.3f}")
        print("\n[followup 혼동] 행=정답, 열=예측")
        print(sc["followup_confusion"].to_string())
        print(f"\n[턴2+ 오분류 {len(sc['later_miss'])}건]")
        for cid, text, g, p in sc["later_miss"]:
            print(f"  {cid} [{g} → {p}]  {text[:50]}")
        return

    inq = pd.read_csv(ev_dir / "customer_inquiries.csv", encoding="utf-8-sig")
    ans = pd.read_csv(ev_dir / "routing_answers.csv", encoding="utf-8-sig")
    ev = inq.merge(ans, on="qa_id")
    ev = ev[ev["split"] == "eval"].reset_index(drop=True)
    if args.limit:
        ev = ev.head(args.limit)

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
