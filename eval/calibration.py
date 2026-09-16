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
