import pytest
from eval.calibration import calibration_table


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
