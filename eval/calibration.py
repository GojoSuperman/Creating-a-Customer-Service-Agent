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
