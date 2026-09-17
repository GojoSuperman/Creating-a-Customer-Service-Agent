import pandas as pd
import pytest
from eval.calibration import _margin, calibration_table, gate_grid, recommend_gate


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

def _hard():
    return pd.DataFrame([
        # 경계모호: 1순위 0.7, 2순위 0.6 → 마진 0.1
        {"confidence": 0.7, "alt_confidence": 0.6, "route_alt": "ORDER_PLACE", "route": "PRODUCT_INFO",
         "route_expected": "PRODUCT_INFO", "route_alt_expected": "ORDER_PLACE", "hard_type": "경계모호"},
        # 비모호, 정답, 마진 넓음
        {"confidence": 0.9, "alt_confidence": 0.1, "route_alt": "SHIPPING", "route": "RETURN_REFUND",
         "route_expected": "RETURN_REFUND", "route_alt_expected": None, "hard_type": "오타"},
        # 비모호, 정답, 2순위 없음
        {"confidence": 0.6, "alt_confidence": 0.0, "route_alt": None, "route": "SHIPPING",
         "route_expected": "SHIPPING", "route_alt_expected": None, "hard_type": "구어체"},
    ])


def test_gate_grid_counts_risky_and_wrong_escalation():
    g = gate_grid(_hard(), thresholds=(0.5,), margins=(0.0, 0.2))
    m0 = g[(g["임계값"] == 0.5) & (g["마진"] == 0.0)].iloc[0]
    assert m0["경계모호위험"] == 1 and m0["비모호오이관"] == 0 and abs(m0["게이트통과율"] - 1.0) < 1e-9
    m2 = g[(g["임계값"] == 0.5) & (g["마진"] == 0.2)].iloc[0]
    assert m2["경계모호위험"] == 0 and m2["비모호오이관"] == 0
    assert abs(m2["게이트통과율"] - 2 / 3) < 1e-9


def test_gate_grid_threshold_escalates_low_confidence():
    g = gate_grid(_hard(), thresholds=(0.7,), margins=(0.0,))
    row = g.iloc[0]
    assert row["비모호오이관"] == 1        # conf 0.6 정답 건이 이관됨


def test_recommend_gate_prefers_max_automation_under_risk_cap():
    g = gate_grid(_hard(), thresholds=(0.5, 0.7), margins=(0.0, 0.2))
    best = recommend_gate(g, max_risky=0)
    assert best["임계값"] == 0.5 and best["마진"] == 0.2
    assert recommend_gate(g.iloc[0:0]) is None


def test_margin_is_clamped_at_zero_when_alt_is_higher():
    # 2순위 확신도가 1순위보다 높게 나온 응답도 마진은 음수가 되지 않는다(런타임 게이트와 동일)
    row = pd.Series({"confidence": 0.4, "alt_confidence": 0.6, "route_alt": "SHIPPING"})
    assert _margin(row) == 0.0
