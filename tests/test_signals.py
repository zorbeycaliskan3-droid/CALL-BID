from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fxvol.io import parse_snapshot
from fxvol.signals import (
    directional_lean,
    follow_through,
    percentile_set,
    position_size_lots,
    regime,
    skew_headline,
    stop_verdict,
    term_structure_note,
)

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "FX_Vol_Dashboard_Bloomberg_py_deneme.csv"


@pytest.fixture
def row() -> pd.Series:
    return parse_snapshot(SAMPLE).iloc[0]


def synthetic_history(n: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=n, freq="B"),
            "pair": "EURUSD",
            "iv_1m": rng.normal(5.0, 0.5, n),
            "rv_30": rng.normal(4.6, 0.5, n),
            "rr25_1m": rng.normal(-0.2, 0.1, n),
        }
    )


def test_regime_boundaries():
    assert regime(0.10).label == "Low vol"
    assert regime(0.2499).label == "Low vol"
    assert regime(0.25).label == "Mid vol"
    assert regime(0.60).label == "Mid vol"
    assert regime(0.61).label == "High vol"
    assert regime(float("nan")).label == "Unknown"


def test_regime_on_the_sample(row):
    assert regime(float(row["iv_1m_pctile"])).label == "Low vol"


def test_follow_through():
    assert "follow-through" in follow_through(-0.3)
    assert "chop" in follow_through(0.157)
    assert follow_through(float("nan")) == "Unknown."


def test_term_structure_note_on_the_sample(row):
    inverted, note = term_structure_note(row)
    assert inverted is True
    assert "Inverted" in note


def test_lean_is_blind_to_atm_vol():
    baseline = directional_lean(-0.2, 0.45, -0.12)
    assert directional_lean(-0.2, 0.45, -0.12) == baseline


def test_lean_weights_the_change_over_the_level():
    from_extreme = directional_lean(-2.0, 0.02, 0.30)
    sitting_at_extreme = directional_lean(-2.0, 0.02, 0.0)
    assert from_extreme.score > sitting_at_extreme.score
    assert from_extreme.direction == "Base-currency positive skew"


def test_lean_is_neutral_when_flat():
    lean = directional_lean(0.0, 0.5, 0.0)
    assert lean.direction == "Neutral"
    assert lean.score == pytest.approx(0.0)


def test_lean_survives_missing_inputs():
    lean = directional_lean(float("nan"), float("nan"), float("nan"))
    assert lean.direction == "Neutral"
    assert lean.score == pytest.approx(0.0)


def test_lean_on_the_sample(row):
    lean = directional_lean(float(row["rr_zscore"]), float(row["rr_pctile"]), float(row["rr_chg_5d"]))
    assert lean.direction == "Base-currency negative skew"


def test_position_size_lots():
    assert position_size_lots(500.0, 50.0, 10.0) == pytest.approx(1.0)
    assert pd.isna(position_size_lots(500.0, 0.0, 10.0))
    assert pd.isna(position_size_lots(500.0, 50.0, 0.0))


def test_stop_verdict_bands():
    assert "noise band" in stop_verdict(30.0, 45.27)
    assert "reasonable" in stop_verdict(45.0, 45.27)
    assert "unusually wide" in stop_verdict(80.0, 45.27)
    assert stop_verdict(50.0, float("nan")) == "No sigma available."


def test_percentile_source_is_excel_with_one_row(row):
    history = parse_snapshot(SAMPLE)
    pset = percentile_set(history, row, 252)
    assert pset.source.startswith("Excel / Bloomberg")
    assert pset.iv_1m_pctile == pytest.approx(float(row["iv_1m_pctile"]))
    assert pset.rr_zscore == pytest.approx(float(row["rr_zscore"]))


def test_percentile_source_switches_to_local_at_60_rows(row):
    history = synthetic_history(60)
    pset = percentile_set(history, row, 252)
    assert pset.source.startswith("Local store")
    assert 0.0 <= pset.iv_1m_pctile <= 1.0
    assert pset.iv_1m_pctile != pytest.approx(float(row["iv_1m_pctile"]))


def test_percentile_source_still_excel_at_59_rows(row):
    pset = percentile_set(synthetic_history(59), row, 252)
    assert pset.source.startswith("Excel / Bloomberg")


def test_local_window_is_respected(row):
    pset = percentile_set(synthetic_history(200), row, 90)
    assert "90-row window" in pset.source


def test_skew_headline_names_the_side_that_is_bid():
    bearish = skew_headline("EURUSD", -0.2125, directional_lean(-0.5, 0.2, -0.20))
    assert bearish.quote_side == "EUR puts over calls"
    assert "bearish EUR against USD" in bearish.bias
    assert bearish.tone == "red"

    bullish = skew_headline("EURUSD", 0.2125, directional_lean(0.5, 0.8, 0.20))
    assert bullish.quote_side == "EUR calls over puts"
    assert "bullish EUR against USD" in bullish.bias
    assert bullish.tone == "green"


def test_skew_headline_uses_the_pair_currencies():
    headline = skew_headline("USDJPY", -0.30, directional_lean(-0.5, 0.2, -0.20))
    assert headline.quote_side == "USD puts over calls"
    assert "USD against JPY" in headline.bias


def test_skew_headline_strength_tiers():
    mild = skew_headline("EURUSD", 0.2, directional_lean(0.4, 0.7, 0.10))
    strong = skew_headline("EURUSD", 0.2, directional_lean(2.0, 0.99, 0.50))
    assert "mildly bullish" in mild.bias
    assert "strongly bullish" in strong.bias


def test_skew_headline_neutral_and_missing():
    flat = skew_headline("EURUSD", 0.0, directional_lean(0.0, 0.5, 0.0))
    assert flat.quote_side == "No skew quoted"
    assert "No directional lean" in flat.bias
    assert flat.tone == "gray"


def test_skew_headline_on_the_sample(row):
    lean = directional_lean(float(row["rr_zscore"]), float(row["rr_pctile"]), float(row["rr_chg_5d"]))
    headline = skew_headline("EURUSD", float(row["rr25_1m"]), lean)
    assert headline.quote_side == "EUR puts over calls"
    assert "bearish" in headline.bias
