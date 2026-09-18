from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fxvol.config import NUMERIC_COLUMNS, Paths
from fxvol.io import dedupe, load_store, parse_snapshot, rebuild_store

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample" / "FX_Vol_Dashboard_Bloomberg_py_deneme.csv"

HEADER = (
    "date,pair,spot,iv_on,iv_1w,iv_1m,iv_3m,rv_30,rr25_1m,bf25_1m,iv_minus_rv,"
    "iv_1m_pctile,rv_pctile,rr_pctile,rr_zscore,rr_chg_5d,sigma_daily_pct,"
    "sigma_daily_pips,exp_abs_move_pips,stop_0p7sig,stop_1p0sig,stop_1p5sig"
)
DOT_ROW = (
    "2026-09-17,EURUSD,1.1536,6.23,4.645,4.775,5.4,4.618,-0.2125,0.1325,0.157,"
    "0.034351145,0.332061069,0.450381679,-0.198151155,-0.12,0.392453111,"
    "45.2733909,36.12293962,31.69137363,45.2733909,67.91008635"
)


def write_vba_dialect(path: Path, date: str = "2026-09-17") -> Path:
    path.write_text(f"{HEADER}\n{DOT_ROW.replace('2026-09-17', date)}\n", encoding="utf-8")
    return path


def test_semicolon_comma_decimal_sample_parses():
    frame = parse_snapshot(SAMPLE)
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["date"] == pd.Timestamp("2026-09-17")
    assert row["pair"] == "EURUSD"
    assert row["spot"] == pytest.approx(1.1536)
    assert row["iv_on"] == pytest.approx(6.23)
    assert row["rr25_1m"] == pytest.approx(-0.2125)
    assert row["iv_1m_pctile"] == pytest.approx(0.034351145)


def test_spot_is_not_mangled_by_the_decimal_comma():
    spot = parse_snapshot(SAMPLE).iloc[0]["spot"]
    assert spot == pytest.approx(1.1536)
    assert spot != pytest.approx(11536.0)
    assert spot != pytest.approx(1.0)


def test_both_dialects_parse_identically(tmp_path):
    excel = parse_snapshot(SAMPLE)
    vba = parse_snapshot(write_vba_dialect(tmp_path / "vba.csv"))
    pd.testing.assert_frame_equal(excel, vba, check_exact=False, rtol=1e-9)


def test_title_and_footer_rows_are_discarded():
    text = SAMPLE.read_text(encoding="utf-8-sig")
    assert "MACHINE-READABLE SNAPSHOT" in text
    assert "one-off backfill" in text
    frame = parse_snapshot(SAMPLE)
    assert len(frame) == 1
    assert frame["date"].notna().all()


def test_all_numeric_columns_are_floats():
    frame = parse_snapshot(SAMPLE)
    for column in NUMERIC_COLUMNS:
        assert frame[column].dtype.kind == "f"


def test_appending_the_same_date_twice_yields_one_row(tmp_path):
    a = parse_snapshot(SAMPLE)
    b = parse_snapshot(write_vba_dialect(tmp_path / "again.csv"))
    assert len(dedupe(pd.concat([a, b], ignore_index=True))) == 1


def test_dedupe_keeps_the_last_occurrence():
    first = parse_snapshot(SAMPLE)
    second = first.copy()
    second.loc[0, "spot"] = 1.2000
    merged = dedupe(pd.concat([first, second], ignore_index=True))
    assert len(merged) == 1
    assert merged.iloc[0]["spot"] == pytest.approx(1.2000)


def test_distinct_dates_both_survive(tmp_path):
    a = parse_snapshot(SAMPLE)
    b = parse_snapshot(write_vba_dialect(tmp_path / "next.csv", date="2026-09-18"))
    merged = dedupe(pd.concat([a, b], ignore_index=True))
    assert len(merged) == 2
    assert list(merged["date"]) == [pd.Timestamp("2026-09-17"), pd.Timestamp("2026-09-18")]


def test_rebuild_store_round_trips(tmp_path):
    paths = Paths(tmp_path)
    paths.incoming.mkdir(parents=True)
    paths.store.parent.mkdir(parents=True)
    (paths.incoming / "day1.csv").write_bytes(SAMPLE.read_bytes())
    write_vba_dialect(paths.incoming / "day2.csv", date="2026-09-18")

    built = rebuild_store(paths)
    assert len(built) == 2
    loaded = load_store(paths)
    assert len(loaded) == 2
    assert loaded.iloc[0]["spot"] == pytest.approx(1.1536)


def test_missing_header_raises(tmp_path):
    path = tmp_path / "junk.csv"
    path.write_text("nothing;useful;here\n1;2;3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_snapshot(path)


def test_bid_ask_columns_parse_when_present(tmp_path):
    lines = SAMPLE.read_text(encoding="utf-8-sig").split("\n")
    for i, line in enumerate(lines):
        if line.startswith("date;"):
            lines[i] = line.rstrip(";") + ";bid;ask;range_basis_price"
        elif line.startswith("2026-09-17;"):
            lines[i] = line + ";1,1535;1,1537;1,1535"
    path = tmp_path / "bidask.csv"
    path.write_text("\n".join(lines), encoding="utf-8-sig")

    row = parse_snapshot(path).iloc[0]
    assert row["spot"] == pytest.approx(1.1536)
    assert row["bid"] == pytest.approx(1.1535)
    assert row["ask"] == pytest.approx(1.1537)
    assert row["range_basis_price"] == pytest.approx(1.1535)


def test_older_exports_without_bid_ask_still_parse():
    row = parse_snapshot(SAMPLE).iloc[0]
    assert pd.isna(row["bid"])
    assert pd.isna(row["ask"])
    assert pd.isna(row["range_basis_price"])


COMBINED = ROOT / "data" / "sample" / "combined_export_sample.csv"


def test_combined_export_yields_one_snapshot_row():
    frame = parse_snapshot(COMBINED)
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["pair"] == "EURUSD"
    assert row["spot"] == pytest.approx(1.1536)
    assert row["chain_spot"] == pytest.approx(1.1540)
    assert row["chain_multiplier"] == pytest.approx(125000)


def test_combined_export_chain_rows_are_not_read_as_snapshots():
    frame = parse_snapshot(COMBINED)
    assert len(frame) == 1
    assert frame["date"].notna().all()


def test_has_chain_block_detects_the_second_header():
    from fxvol.io import has_chain_block

    assert has_chain_block(COMBINED) is True
    assert has_chain_block(SAMPLE) is False
