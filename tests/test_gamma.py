from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from fxvol.gamma import (
    black_scholes_gamma,
    gamma_exposure,
    gamma_flip,
    key_levels,
    top_levels,
)
from fxvol.io import parse_chain

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "sample_option_chain.csv"


def chain(rows: list[tuple[float, str, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["strike", "option_type", "open_interest", "iv", "days_to_expiry"])


def test_gamma_peaks_at_the_money():
    atm = black_scholes_gamma(100.0, 100.0, 30 / 365, 20.0)
    otm = black_scholes_gamma(100.0, 130.0, 30 / 365, 20.0)
    assert atm > otm > 0


def test_gamma_is_zero_at_expiry_and_zero_vol():
    assert black_scholes_gamma(100.0, 100.0, 0.0, 20.0) == 0.0
    assert black_scholes_gamma(100.0, 100.0, 30 / 365, 0.0) == 0.0


def test_gamma_against_a_hand_checked_value():
    # S=K=100, T=1y, sigma=20%, r=0: d1 = 0.1, gamma = phi(0.1)/(100*0.2)
    expected = math.exp(-0.005) / math.sqrt(2 * math.pi) / 20.0
    assert black_scholes_gamma(100.0, 100.0, 1.0, 20.0) == pytest.approx(expected, rel=1e-12)


def test_calls_are_positive_and_puts_negative():
    profile = gamma_exposure(chain([(100.0, "C", 1000, 20.0, 30), (90.0, "P", 1000, 20.0, 30)]), 100.0)
    assert profile.loc[profile["strike"] == 100.0, "total_gex"].iloc[0] > 0
    assert profile.loc[profile["strike"] == 90.0, "total_gex"].iloc[0] < 0


def test_open_interest_scales_exposure_linearly():
    one = gamma_exposure(chain([(100.0, "C", 1000, 20.0, 30)]), 100.0)["total_gex"].iloc[0]
    ten = gamma_exposure(chain([(100.0, "C", 10000, 20.0, 30)]), 100.0)["total_gex"].iloc[0]
    assert ten == pytest.approx(10 * one)


def test_calls_and_puts_at_one_strike_net_off():
    profile = gamma_exposure(chain([(100.0, "C", 1000, 20.0, 30), (100.0, "P", 1000, 20.0, 30)]), 100.0)
    assert len(profile) == 1
    assert profile["total_gex"].iloc[0] == pytest.approx(0.0, abs=1e-6)


def test_empty_chain_gives_empty_profile():
    profile = gamma_exposure(chain([]), 100.0)
    assert profile.empty
    assert math.isnan(gamma_flip(profile))
    assert key_levels(profile, 100.0).regime == "Unknown"


def test_flip_point_is_interpolated_between_strikes():
    profile = pd.DataFrame(
        {"strike": [90.0, 100.0, 110.0], "total_gex": [-100.0, 50.0, 50.0], "cumulative_gex": [-100.0, -50.0, 0.0]}
    )
    flip = gamma_flip(profile)
    assert 100.0 <= flip <= 110.0


def test_flip_is_nan_without_a_crossing():
    profile = pd.DataFrame(
        {"strike": [90.0, 100.0], "total_gex": [10.0, 10.0], "cumulative_gex": [10.0, 20.0]}
    )
    assert math.isnan(gamma_flip(profile))


def test_key_levels_find_the_walls():
    profile = gamma_exposure(
        chain(
            [
                (420.0, "C", 50000, 15.0, 30),
                (430.0, "C", 1000, 16.0, 30),
                (400.0, "P", 40000, 17.0, 30),
                (390.0, "P", 500, 18.0, 30),
            ]
        ),
        415.0,
    )
    levels = key_levels(profile, 415.0)
    assert levels.call_wall == 420.0
    assert levels.put_wall == 400.0


def test_long_and_short_gamma_regimes():
    long_gamma = gamma_exposure(chain([(100.0, "C", 10000, 20.0, 30)]), 100.0)
    short_gamma = gamma_exposure(chain([(100.0, "P", 10000, 20.0, 30)]), 100.0)
    assert key_levels(long_gamma, 100.0).regime == "Long gamma"
    assert key_levels(short_gamma, 100.0).regime == "Short gamma"


def test_top_levels_labels_support_and_resistance():
    profile = gamma_exposure(
        chain([(420.0, "C", 50000, 15.0, 30), (400.0, "P", 40000, 17.0, 30)]), 410.0
    )
    table = top_levels(profile, 5)
    assert set(table["role"]) == {"Resistance", "Support"}
    assert table.loc[table["role"] == "Resistance", "strike"].iloc[0] == 420.0
    assert table.loc[table["role"] == "Support", "strike"].iloc[0] == 400.0


def test_sample_chain_parses_with_turkish_decimals():
    parsed = parse_chain(SAMPLE)
    assert len(parsed) == 250
    assert set(parsed["option_type"]) == {"C", "P"}
    assert parsed["strike"].min() == pytest.approx(340.0)
    assert parsed["iv"].between(10, 60).all()


def test_chain_accepts_an_expiry_date_instead_of_days(tmp_path):
    future = (pd.Timestamp.today().normalize() + pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    path = tmp_path / "chain.csv"
    path.write_text(f"strike,option_type,open_interest,iv,expiry\n100,C,10,20,{future}\n", encoding="utf-8")
    assert parse_chain(path)["days_to_expiry"].iloc[0] == pytest.approx(45.0)


def test_chain_without_a_strike_column_raises(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_chain(path)


def test_flat_gamma_when_calls_and_puts_cancel():
    profile = gamma_exposure(
        chain([(100.0, "C", 1000, 20.0, 30), (100.0, "P", 1000, 20.0, 30)]), 100.0
    )
    assert key_levels(profile, 100.0).regime == "Flat gamma"


def test_excel_dialect_chain_with_extra_columns_and_title_rows(tmp_path):
    lines = [
        "OPTION CHAIN FOR GAMMA EXPOSURE;;;;;",
        "Save this sheet as CSV and upload it.;;;;;",
        ";;;;;",
        "Underlying ticker (input);SPY US Equity;;;;",
        "option_ticker;strike;option_type;open_interest;iv;days_to_expiry",
        "SPY US 12/19/26 C420 Equity;420,0;Call;12000;15,50;30",
        "SPY US 12/19/26 P400 Equity;400,0;Put;9000;17,25;30",
        ";;;;;",
    ]
    path = tmp_path / "chain.csv"
    path.write_text("\n".join(lines), encoding="utf-8-sig")

    parsed = parse_chain(path)
    assert len(parsed) == 2
    assert list(parsed["option_type"]) == ["C", "P"]
    assert parsed["strike"].tolist() == [420.0, 400.0]
    assert parsed["iv"].tolist() == [15.5, 17.25]
    assert "option_ticker" not in parsed.columns or parsed["strike"].notna().all()


COMBINED = Path(__file__).resolve().parents[1] / "data" / "sample" / "combined_export_sample.csv"


def test_combined_export_chain_block_parses():
    parsed = parse_chain(COMBINED)
    assert len(parsed) == 46
    assert set(parsed["option_type"]) == {"C", "P"}
    assert parsed["strike"].min() == pytest.approx(1.10)
    assert parsed["strike"].max() == pytest.approx(1.21)


def test_gamma_on_a_futures_chain_with_a_large_multiplier():
    parsed = parse_chain(COMBINED)
    profile = gamma_exposure(parsed, 1.1540, contract_size=125000)
    levels = key_levels(profile, 1.1540)
    assert levels.call_wall == pytest.approx(1.165)
    assert levels.put_wall == pytest.approx(1.145)
    assert levels.call_wall > levels.put_wall


MULTI = Path(__file__).resolve().parents[1] / "data" / "sample" / "combined_multi_sample.csv"


def test_multi_chain_parses_all_five_underlyings():
    parsed = parse_chain(MULTI)
    assert list(parsed["underlying"].unique()) == ["EURUSD", "SPY", "NVDA", "META", "GOOG"]
    assert parsed.groupby("underlying").size().eq(82).all()


def test_multi_chain_carries_price_and_multiplier_per_underlying():
    parsed = parse_chain(MULTI)
    firsts = parsed.groupby("underlying", sort=False).first()
    assert firsts.loc["EURUSD", "multiplier"] == 125000
    assert firsts.loc["SPY", "multiplier"] == 100
    assert firsts.loc["EURUSD", "underlying_price"] == pytest.approx(1.1540)
    assert firsts.loc["META", "underlying_price"] == pytest.approx(755.0)


@pytest.mark.parametrize(
    "name, call_wall, put_wall",
    [
        ("EURUSD", 1.165, 1.145),
        ("SPY", 670.0, 640.0),
        ("NVDA", 185.0, 170.0),
        ("META", 800.0, 720.0),
        ("GOOG", 260.0, 240.0),
    ],
)
def test_each_underlying_gets_its_own_walls(name, call_wall, put_wall):
    parsed = parse_chain(MULTI)
    group = parsed[parsed["underlying"] == name]
    spot = float(group["underlying_price"].iloc[0])
    mult = int(group["multiplier"].iloc[0])
    levels = key_levels(gamma_exposure(group, spot, mult), spot)
    assert levels.call_wall == pytest.approx(call_wall)
    assert levels.put_wall == pytest.approx(put_wall)


def test_chain_without_underlying_column_is_labelled_chain():
    parsed = parse_chain(SAMPLE)
    assert set(parsed["underlying"]) == {"CHAIN"}
    assert parsed["underlying_price"].isna().all()


def test_multi_export_still_yields_one_snapshot_row():
    from fxvol.io import parse_snapshot

    frame = parse_snapshot(MULTI)
    assert len(frame) == 1
    assert frame.iloc[0]["pair"] == "EURUSD"
