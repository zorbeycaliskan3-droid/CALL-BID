from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fxvol.config import TENORS, Paths, pip_size_for, price_decimals
from fxvol.io import load_store, parse_snapshot, rebuild_store
from fxvol.signals import (
    directional_lean,
    follow_through,
    percentile_set,
    position_size_lots,
    regime,
    stop_verdict,
    term_structure_note,
)
from fxvol.vol import expected_abs_move_pips, sigma_daily_pips, sigma_price_levels, verify

st.set_page_config(page_title="FX Vol Regime", layout="wide")

TONE_COLOR = {"green": "#1b7f3b", "orange": "#b26a00", "red": "#b3261e", "gray": "#5f6368"}

st.sidebar.header("Settings")
root = Path(st.sidebar.text_input("Data folder", value=str(Path(__file__).parent)))
paths = Paths(root)

st.sidebar.subheader("Upload snapshot")
uploads = st.sidebar.file_uploader(
    "Drop today's CSV export", type="csv", accept_multiple_files=True, key="uploads"
)
if uploads:
    paths.incoming.mkdir(parents=True, exist_ok=True)
    for upload in uploads:
        target = paths.incoming / upload.name
        target.write_bytes(upload.getvalue())
        try:
            parsed = parse_snapshot(target)
        except ValueError as error:
            target.unlink()
            st.sidebar.error(f"{upload.name}: {error}")
            continue
        if parsed.empty:
            target.unlink()
            st.sidebar.error(f"{upload.name}: no dated rows found.")
            continue
        dates = ", ".join(f"{d:%Y-%m-%d}" for d in parsed["date"])
        st.sidebar.success(f"{upload.name}: {len(parsed)} row(s) — {dates}")

store = rebuild_store(paths)
if store.empty:
    st.error(f"No snapshots found in {paths.incoming}")
    st.stop()

pairs = sorted(store["pair"].unique())
pair = st.sidebar.selectbox("Pair", pairs)
pip_size = st.sidebar.number_input(
    "Pip size", value=pip_size_for(pair), min_value=0.00001, max_value=1.0, step=0.0001, format="%.5f"
)
risk_per_trade = st.sidebar.number_input("Risk per trade", value=500.0, min_value=0.0, step=50.0)
pip_value_per_lot = st.sidebar.number_input("Pip value per lot", value=10.0, min_value=0.01, step=1.0)
window = st.sidebar.number_input("Percentile lookback (rows)", value=252, min_value=20, step=20)

history = store[store["pair"] == pair].sort_values("date").reset_index(drop=True)
row = history.iloc[-1]
decimals = price_decimals(pair)
snapshot_date = pd.Timestamp(row["date"])

st.title(f"{pair} — {snapshot_date:%Y-%m-%d}")
business_days_old = len(pd.bdate_range(snapshot_date, pd.Timestamp.today().normalize())) - 1
if business_days_old > 1:
    st.warning(f"Snapshot is {business_days_old} business days old.")

problems = [c for c in verify(row, pip_size) if not c.ok]
for check in problems:
    st.error(
        f"`{check.field}` mismatch: Excel {check.excel:,.6f} vs Python {check.python:,.6f} "
        f"({check.relative_error:.1%}). Check the Excel reference."
    )

pset = percentile_set(history, row, int(window))
st.caption(f"Percentile source: {pset.source}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Spot", f"{row['spot']:.{decimals}f}")
c2.metric("1M ATM vol", f"{row['iv_1m']:.2f}", f"{pset.iv_1m_pctile:.0%} pctile", delta_color="off")
c3.metric("IV − RV", f"{row['iv_minus_rv']:+.2f}", follow_through(row["iv_minus_rv"]), delta_color="off")
c4.metric("25d RR 1M", f"{row['rr25_1m']:+.4f}", f"z {pset.rr_zscore:+.2f}", delta_color="off")

reg = regime(pset.iv_1m_pctile)
st.markdown(
    f"<div style='padding:14px 18px;border-radius:6px;background:{TONE_COLOR[reg.tone]};color:#fff'>"
    f"<b>{reg.label}</b> &nbsp;·&nbsp; {pset.iv_1m_pctile:.0%} percentile &nbsp;·&nbsp; {reg.note}</div>",
    unsafe_allow_html=True,
)

st.subheader("Today's range")
sigma_pips = sigma_daily_pips(float(row["spot"]), float(row["iv_on"]), pip_size)
low, high = sigma_price_levels(float(row["spot"]), sigma_pips, pip_size)
r1, r2, r3 = st.columns(3)
r1.metric("Expected absolute move", f"{expected_abs_move_pips(sigma_pips):.1f} pips")
r2.metric("1σ low", f"{low:.{decimals}f}")
r3.metric("1σ high", f"{high:.{decimals}f}")
st.table(
    pd.DataFrame(
        {"Stop (pips)": [f"{m * sigma_pips:.1f}" for m in (0.7, 1.0, 1.5)]},
        index=["0.7 sigma", "1.0 sigma", "1.5 sigma"],
    )
)

st.subheader("Position sizing")
intended_stop = st.number_input("Intended stop (pips)", value=round(sigma_pips, 1), min_value=0.1, step=1.0)
lots = position_size_lots(risk_per_trade, intended_stop, pip_value_per_lot)
s1, s2 = st.columns([1, 2])
s1.metric("Position size", f"{lots:.2f} lots")
s2.info(stop_verdict(intended_stop, sigma_pips))

st.subheader("Term structure")
inverted, note = term_structure_note(row)
(st.warning if inverted else st.success)(note)
fig = go.Figure(go.Scatter(x=TENORS, y=[float(row[t]) for t in TENORS], mode="lines+markers"))
fig.update_layout(height=300, margin=dict(t=10, b=10), yaxis_title="ATM vol (percent points)")
st.plotly_chart(fig, use_container_width=True)

st.subheader("Positioning skew")
lean = directional_lean(pset.rr_zscore, pset.rr_pctile, float(row["rr_chg_5d"]))
st.metric(lean.direction, f"score {lean.score:+.2f}")
st.caption(lean.detail)
st.caption(
    "Risk-reversal positioning skew only. ATM implied vol carries no directional "
    "information and feeds nothing here. One input among several — not an entry signal."
)

st.subheader("History")
if len(history) < 2:
    st.info(f"Only {len(history)} snapshot stored for {pair}. History charts appear from the second snapshot.")
else:
    iv_rv = go.Figure()
    iv_rv.add_scatter(x=history["date"], y=history["iv_1m"], name="IV 1M", mode="lines")
    iv_rv.add_scatter(x=history["date"], y=history["rv_30"], name="RV 30d", mode="lines")
    iv_rv.update_layout(height=320, margin=dict(t=10, b=10), yaxis_title="percent points")
    st.plotly_chart(iv_rv, use_container_width=True)

    rr = history["rr25_1m"]
    mean, sd = float(rr.mean()), float(rr.std(ddof=1) or 0.0)
    rr_fig = go.Figure()
    rr_fig.add_scatter(x=history["date"], y=rr, name="25d RR 1M", mode="lines")
    for mult, dash in ((1, "dot"), (2, "dash")):
        for sign in (1, -1):
            rr_fig.add_hline(y=mean + sign * mult * sd, line_dash=dash, line_color="#888")
    rr_fig.update_layout(height=320, margin=dict(t=10, b=10), yaxis_title="vol points")
    st.plotly_chart(rr_fig, use_container_width=True)

st.subheader("Stored snapshots")
st.dataframe(store, use_container_width=True)
st.download_button("Download CSV", store.to_csv(index=False).encode("utf-8"), "snapshots.csv", "text/csv")
