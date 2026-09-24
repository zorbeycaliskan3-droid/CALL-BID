from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fxvol.config import pip_size_for, price_decimals
from fxvol.io import parse_snapshot
from fxvol.vol import (
    expected_abs_move_pips,
    is_inverted,
    percentile,
    recompute,
    sigma_daily_pct,
    sigma_daily_pips,
    sigma_price_levels,
    skewed_daily_range,
    skewed_price_levels,
    smile_vols,
    stop_ladder,
    term_structure,
    verify,
    zscore,
)

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "FX_Vol_Dashboard_Bloomberg_py_deneme.csv"


@pytest.fixture
def row() -> pd.Series:
    return parse_snapshot(SAMPLE).iloc[0]


def test_sigma_daily_pct_hand_checked():
    assert sigma_daily_pct(6.23) == pytest.approx(0.392453, abs=1e-6)


def test_sigma_daily_pips_hand_checked():
    assert sigma_daily_pips(1.1536, 6.23, 0.0001) == pytest.approx(45.3, abs=0.1)


def test_expected_abs_move_hand_checked():
    assert expected_abs_move_pips(45.2733909) == pytest.approx(36.1, abs=0.1)


def test_jpy_pip_size_changes_the_pip_count():
    assert sigma_daily_pips(150.0, 6.23, 0.01) == pytest.approx(58.87, abs=0.01)


def test_pip_size_rules():
    assert pip_size_for("EURUSD") == 0.0001
    assert pip_size_for("USDJPY") == 0.01
    assert pip_size_for("eurjpy") == 0.01
    assert price_decimals("EURUSD") == 5
    assert price_decimals("USDJPY") == 3


def test_stop_ladder_multiples():
    ladder = stop_ladder(100.0)
    assert list(ladder.values()) == pytest.approx([70.0, 100.0, 150.0])


def test_sigma_price_levels_bracket_spot():
    low, high = sigma_price_levels(1.1536, 45.2733909, 0.0001)
    assert low == pytest.approx(1.149073, abs=1e-6)
    assert high == pytest.approx(1.158127, abs=1e-6)


def test_recompute_matches_excel_on_the_sample(row):
    excel = recompute(row, 0.0001)
    for field, value in excel.items():
        assert value == pytest.approx(float(row[field]), rel=1e-6)


def test_verify_flags_nothing_on_the_sample(row):
    assert [check.field for check in verify(row, 0.0001) if not check.ok] == []


def test_verify_flags_a_broken_excel_reference(row):
    broken = row.copy()
    broken["sigma_daily_pips"] = float(row["sigma_daily_pips"]) * 1.5
    failures = [check for check in verify(broken, 0.0001) if not check.ok]
    assert [check.field for check in failures] == ["sigma_daily_pips"]
    assert failures[0].relative_error == pytest.approx(1 / 3, rel=1e-6)


def test_percentile_and_zscore():
    series = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert percentile(series, 3.0) == pytest.approx(0.75)
    assert percentile(series, 0.0) == pytest.approx(0.0)
    assert zscore(series, 2.5) == pytest.approx(0.0)


def test_percentile_and_zscore_degrade_without_history():
    assert pd.isna(percentile(pd.Series(dtype="float64"), 1.0))
    assert pd.isna(zscore(pd.Series([1.0]), 1.0))
    assert pd.isna(zscore(pd.Series([2.0, 2.0, 2.0]), 2.0))


def test_term_structure_and_inversion(row):
    curve = term_structure(row)
    assert list(curve) == ["iv_on", "iv_1w", "iv_1m", "iv_3m"]
    assert curve["iv_on"] == pytest.approx(6.23)
    assert is_inverted(row) is True


def test_upward_curve_is_not_inverted():
    flat = pd.Series({"iv_on": 4.0, "iv_1w": 4.5, "iv_1m": 5.0, "iv_3m": 5.5})
    assert is_inverted(flat) is False


def test_smile_vols_reconstruct_the_wings():
    # ATM 6.23, RR -0.2125 (puts bid), BF 0.1325
    put_vol, call_vol = smile_vols(6.23, -0.2125, 0.1325)
    assert put_vol == pytest.approx(6.23 + 0.1325 + 0.10625)
    assert call_vol == pytest.approx(6.23 + 0.1325 - 0.10625)
    assert call_vol - put_vol == pytest.approx(-0.2125)          # RR is the tilt
    assert (put_vol + call_vol) / 2 == pytest.approx(6.23 + 0.1325)  # BF is the lift


def test_negative_risk_reversal_widens_the_downside():
    down, up = skewed_daily_range(1.1536, 6.23, -0.2125, 0.1325, 0.0001)
    assert down > up
    low, high = skewed_price_levels(1.1536, down, up, 0.0001)
    assert low < 1.1536 < high
    assert 1.1536 - low > high - 1.1536


def test_positive_risk_reversal_widens_the_upside():
    down, up = skewed_daily_range(1.1536, 6.23, +0.2125, 0.1325, 0.0001)
    assert up > down


def test_zero_skew_matches_the_symmetric_range():
    plain = sigma_daily_pips(1.1536, 6.23, 0.0001)
    down, up = skewed_daily_range(1.1536, 6.23, 0.0, 0.0, 0.0001)
    assert down == pytest.approx(plain)
    assert up == pytest.approx(plain)


def test_butterfly_widens_both_sides():
    flat_down, flat_up = skewed_daily_range(1.1536, 6.23, 0.0, 0.0, 0.0001)
    bf_down, bf_up = skewed_daily_range(1.1536, 6.23, 0.0, 0.5, 0.0001)
    assert bf_down > flat_down and bf_up > flat_up


def test_skewed_range_never_goes_negative_on_an_extreme_quote():
    down, up = skewed_daily_range(1.1536, 1.0, -8.0, 0.0, 0.0001)
    assert down > 0 and up >= 0
