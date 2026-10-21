from __future__ import annotations

import pandas as pd
import pytest

from fxvol.charts import iv_rv_chart, percentile_strip


def history(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2026-09-01", periods=n),
            "iv_1m": [5.0 + i * 0.05 for i in range(n)],
            "rv_30": [4.8 + i * 0.04 for i in range(n)],
        }
    )


def test_percentile_strip_plots_every_measure():
    fig = percentile_strip({"IV 1M": 0.08, "RV 30d": 0.33, "25d RR": 0.24})
    assert list(fig.data[0].y) == ["IV 1M", "RV 30d", "25d RR"]
    assert list(fig.data[0].x) == pytest.approx([8.0, 33.0, 24.0])
    assert len(fig.layout.shapes) == 3                 # low / mid / high bands
    assert fig.layout.xaxis.range == (0, 100)


def test_percentile_strip_drops_missing_values():
    fig = percentile_strip({"IV 1M": 0.5, "RV 30d": float("nan")})
    assert list(fig.data[0].y) == ["IV 1M"]


def test_percentile_strip_survives_an_empty_set():
    fig = percentile_strip({})
    assert list(fig.data[0].x) == []


def test_iv_rv_chart_keeps_only_the_last_ten_days():
    fig = iv_rv_chart(history(30), days=10)
    assert len(fig.data[0].x) == 10
    assert pd.Timestamp(fig.data[0].x[-1]) == history(30)["date"].iloc[-1]


def test_iv_rv_chart_handles_short_history():
    fig = iv_rv_chart(history(3), days=10)
    assert len(fig.data[0].x) == 3


def test_iv_rv_chart_is_empty_without_data():
    fig = iv_rv_chart(pd.DataFrame(columns=["date", "iv_1m", "rv_30"]))
    assert fig.data == ()


def test_iv_rv_shading_flips_with_the_sign_of_the_gap():
    rich = history(5)
    rich["iv_1m"] = rich["rv_30"] + 0.5
    cheap = history(5)
    cheap["iv_1m"] = cheap["rv_30"] - 0.5
    assert "179,38,30" in iv_rv_chart(rich).data[1].fillcolor      # red: implied over realised
    assert "27,127,59" in iv_rv_chart(cheap).data[1].fillcolor     # green: implied under realised
