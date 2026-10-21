from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fxvol.charts import iv_rv_chart, percentile_strip
from fxvol.config import SURFACE_TENORS, TENORS, Paths, pip_size_for, price_decimals
from fxvol.gamma import (
    call_put_balance,
    coverage_check,
    gamma_exposure,
    key_levels,
    price_check,
    top_levels,
    window_reach,
)
from fxvol.io import has_chain_block, load_store, parse_chain, parse_snapshot, rebuild_store
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
from fxvol.vol import (
    expected_abs_move_pips,
    sigma_daily_pips,
    sigma_price_levels,
    skewed_daily_range,
    skewed_price_levels,
    smile_vols,
    verify,
)

st.set_page_config(page_title="FX Vol Regime", layout="wide")

TONE_COLOR = {"green": "#1b7f3b", "orange": "#b26a00", "red": "#b3261e", "gray": "#5f6368"}

paths = Paths(Path(__file__).parent)
chain_path = paths.root / "data" / "chain.csv"

st.sidebar.header("Upload")
uploads = st.sidebar.file_uploader(
    "Daily export (snapshot + option chains)", type="csv", accept_multiple_files=True, key="uploads"
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

        if has_chain_block(target):
            try:
                embedded = parse_chain(target)
            except ValueError as error:
                st.sidebar.warning(f"{upload.name}: chain block unreadable — {error}")
            else:
                chain_path.parent.mkdir(parents=True, exist_ok=True)
                chain_path.write_bytes(target.read_bytes())
                per = embedded.groupby("underlying", sort=False).size()
                listing = ", ".join(f"{name} {count}" for name, count in per.items())
                st.sidebar.success(f"{upload.name}: chains — {listing}")

chain_file = st.sidebar.file_uploader(
    "Option chain only (optional override)", type="csv", key="chain"
)
if chain_file is not None:
    chain_path.parent.mkdir(parents=True, exist_ok=True)
    chain_path.write_bytes(chain_file.getvalue())

store = rebuild_store(paths)
fx_pairs = sorted(store["pair"].unique()) if not store.empty else []

tab_gamma, tab_fx = st.tabs(["Gamma exposure", "FX pairs"])

latest_row = (
    store.sort_values("date").iloc[-1] if not store.empty else pd.Series(dtype="object")
)

with tab_gamma:
    chain = None
    if chain_path.exists():
        try:
            chain = parse_chain(chain_path)
        except ValueError as error:
            st.error(f"Chain: {error}")

    if chain is None or chain.empty:
        st.info(
            "No option chain loaded. Save the Export sheet as CSV and upload it in the "
            "sidebar — it carries the snapshot and the chains for every underlying on the "
            "Chain tab in one file."
        )
    else:
        def chain_defaults(group: pd.DataFrame) -> tuple[float, int]:
            price = group["underlying_price"].dropna()
            mult = group["multiplier"].dropna()
            if not price.empty:
                spot_guess = float(price.iloc[0])
            elif not pd.isna(latest_row.get("chain_spot", float("nan"))):
                spot_guess = float(latest_row["chain_spot"])
            else:
                spot_guess = float(group["strike"].median())
            if not mult.empty:
                mult_guess = int(mult.iloc[0])
            elif not pd.isna(latest_row.get("chain_multiplier", float("nan"))):
                mult_guess = int(latest_row["chain_multiplier"])
            else:
                mult_guess = 100
            return spot_guess, mult_guess

        groups = {name: g.reset_index(drop=True) for name, g in chain.groupby("underlying", sort=False)}

        overview = []
        for name, group in groups.items():
            spot_guess, mult_guess = chain_defaults(group)
            lv = key_levels(gamma_exposure(group, spot_guess, mult_guess), spot_guess, group, mult_guess)
            d = 4 if spot_guess < 10 else 2
            overview.append(
                {
                    "Underlying": name,
                    "Price": f"{spot_guess:,.{d}f}",
                    "Put wall (support)": f"{lv.put_wall:,.{d}f}",
                    "Call wall (resistance)": f"{lv.call_wall:,.{d}f}",
                    "Gamma flip": "n/a" if pd.isna(lv.flip_point) else f"{lv.flip_point:,.{d}f}",
                    "Net gamma (M)": f"{lv.total_gex / 1e6:,.0f}",
                    "Regime": lv.regime,
                    "Price check": "⚠ check" if price_check(group, spot_guess) else "ok",
                    "Window": "{:.1f}% / {:.1f}%".format(
                        *[v * 100 for v in window_reach(group, spot_guess)]
                    ),
                    "Contracts": len(group),
                }
            )

        for tab, (name, group) in zip(st.tabs(list(groups)), groups.items()):
            with tab:
                spot_guess, mult_guess = chain_defaults(group)
                chart_slot = st.container()
                with st.expander("Adjust underlying price or multiplier", expanded=False):
                    g1, g2 = st.columns([1, 1])
                    gex_spot = g1.number_input(
                        "Underlying price", value=spot_guess, min_value=0.0001, format="%.4f", key=f"gex_spot_{name}_{spot_guess}"
                    )
                    contract_size = g2.number_input(
                        "Contract multiplier", value=mult_guess, min_value=1, step=1, key=f"contract_size_{name}_{mult_guess}"
                    )

                profile = gamma_exposure(group, gex_spot, int(contract_size))
                stale = price_check(group, gex_spot)
                if stale:
                    chart_slot.warning(stale)
                narrow = coverage_check(group, gex_spot)
                if narrow:
                    chart_slot.warning(narrow)
                levels = key_levels(profile, gex_spot, group, int(contract_size))
                gd = 4 if gex_spot < 10 else 2

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Call wall (resistance)", f"{levels.call_wall:,.{gd}f}")
                k2.metric("Put wall (support)", f"{levels.put_wall:,.{gd}f}")
                k3.metric("Gamma flip", "n/a" if pd.isna(levels.flip_point) else f"{levels.flip_point:,.{gd}f}")
                k4.metric("Net gamma", f"{levels.total_gex / 1e6:,.0f}M")
                (st.success if levels.total_gex > 0 else st.warning)(f"**{levels.regime}.** {levels.note}")
                st.caption(
                    f"{name} · {len(group):,} contracts · {len(profile):,} strikes from "
                    f"{profile['strike'].min():,.{gd}f} to {profile['strike'].max():,.{gd}f}"
                )

                colors = ["#2f6fdb" if v >= 0 else "#c0392b" for v in profile["total_gex"]]
                gex_fig = go.Figure(
                    go.Bar(x=profile["strike"], y=profile["total_gex"], marker_color=colors, name="Total gamma")
                )
                gex_fig.add_vline(x=gex_spot, line_color="#888", line_width=2, annotation_text="Spot")
                gex_fig.add_vline(x=levels.call_wall, line_color="#1b7f3b", line_dash="dash", annotation_text="Call wall")
                gex_fig.add_vline(x=levels.put_wall, line_color="#b3261e", line_dash="dash", annotation_text="Put wall")
                if not pd.isna(levels.flip_point):
                    gex_fig.add_vline(x=levels.flip_point, line_color="#b26a00", line_dash="dot", annotation_text="Flip")
                gex_fig.update_layout(
                    title=f"{name} gamma exposure",
                    height=460,
                    margin=dict(t=50, b=10),
                    xaxis_title="Strike price",
                    yaxis_title="Total gamma (per 1% move)",
                    bargap=0.15,
                )
                chart_slot.plotly_chart(gex_fig, use_container_width=True, key=f"gex_fig_{name}")
                st.caption(
                    "**What this shows.** Dealer gamma at each strike, calls positive and puts "
                    "negative. Tall blue bars are strikes where dealers must sell into strength "
                    "to stay hedged — they act as **resistance** and tend to pin price. Deep red "
                    "bars are strikes where hedging sells into weakness, so they act as "
                    "**support** until they break, and break hard. The dotted line is the gamma "
                    "flip: the price where dealers switch between long gamma (hedging damps "
                    "moves) and short gamma (hedging amplifies them). Levels are only as good as "
                    "the open interest behind them, so they move "
                    "as positioning rolls and decay sharply after a big expiry."
                )
                st.dataframe(
                    top_levels(profile, 5)
                    .assign(total_gex=lambda f: (f["total_gex"] / 1e6).round(1))
                    .rename(columns={"strike": "Strike", "total_gex": "Gamma (M)", "role": "Role"}),
                    use_container_width=True,
                    hide_index=True,
                    key=f"gex_table_{name}",
                )

                st.markdown("**Call side vs put side**")
                bal = call_put_balance(profile, group)
                tone = (
                    "#1b7f3b" if bal.tilt > 0.20 else "#b3261e" if bal.tilt < -0.20 else "#5f6368"
                )
                st.markdown(
                    f"<div style='padding:12px 16px;border-radius:6px;border-left:6px solid {tone};"
                    f"background:rgba(128,128,128,0.10)'>"
                    f"<b style='font-size:1.15rem'>{bal.side}</b> &nbsp;·&nbsp; tilt {bal.tilt:+.2f}"
                    f"<div style='opacity:0.8;margin-top:4px'>{bal.note}</div></div>",
                    unsafe_allow_html=True,
                )

                b1, b2, b3, b4 = st.columns(4)
                b1.metric("Call gamma", f"{bal.call_gamma / 1e6:,.0f}M")
                b2.metric("Put gamma", f"{bal.put_gamma / 1e6:,.0f}M")
                b3.metric("Call OI", f"{bal.call_oi:,.0f}")
                b4.metric(
                    "Call / put OI",
                    "n/a" if pd.isna(bal.oi_ratio) else f"{bal.oi_ratio:.2f}",
                    f"{bal.put_oi:,.0f} puts",
                    delta_color="off",
                )

                split = go.Figure()
                split.add_bar(x=profile["strike"], y=profile["call_gex"], name="Calls",
                              marker_color="#2f6fdb")
                split.add_bar(x=profile["strike"], y=profile["put_gex"], name="Puts",
                              marker_color="#c0392b")
                split.add_vline(x=gex_spot, line_color="#888", line_width=2, annotation_text="Spot")
                split.update_layout(
                    title=f"{name} call and put gamma by strike",
                    height=380,
                    margin=dict(t=50, b=10),
                    barmode="relative",
                    xaxis_title="Strike price",
                    yaxis_title="Gamma (per 1% move)",
                    legend=dict(orientation="h", y=1.12),
                    bargap=0.15,
                )
                st.plotly_chart(split, use_container_width=True, key=f"split_fig_{name}")
                st.caption(
                    f"**What this shows.** The same gamma split into its two halves: blue is call "
                    f"gamma above the line, red is put gamma below it. The net chart above is these "
                    f"two added together. Where blue dominates, hedging leans against rallies and "
                    f"price gets pinned; where red dominates, hedging sells into weakness and moves "
                    f"extend. Today the call side carries {bal.call_gamma / 1e6:,.0f}M against "
                    f"{bal.put_gamma / 1e6:,.0f}M on the put side"
                    + (
                        f", with {bal.oi_ratio:.2f} calls open for every put."
                        if not pd.isna(bal.oi_ratio)
                        else "."
                    )
                )

        st.markdown("**All underlyings**")
        st.dataframe(pd.DataFrame(overview), use_container_width=True, hide_index=True)


with tab_fx:
    if store.empty:
        st.info("No snapshot stored yet. Upload the daily export in the sidebar.")
        st.stop()

    for pair, pair_tab in zip(fx_pairs, st.tabs(fx_pairs)):
        with pair_tab:
            with st.expander("Settings", expanded=False):
                f2, f3, f4, f5 = st.columns(4)
                pip_size = f2.number_input(
                    "Pip size", value=pip_size_for(pair), min_value=0.00001, max_value=1.0,
                    step=0.0001, format="%.5f", key=f"pip_{pair}",
                )
                risk_per_trade = f3.number_input(
                    "Risk per trade", value=500.0, min_value=0.0, step=50.0, key=f"risk_{pair}"
                )
                pip_value_per_lot = f4.number_input(
                    "Pip value per lot", value=10.0, min_value=0.01, step=1.0, key=f"pipval_{pair}"
                )
                window = f5.number_input(
                    "Percentile lookback (rows)", value=252, min_value=20, step=20, key=f"win_{pair}"
                )

            history = store[store["pair"] == pair].sort_values("date").reset_index(drop=True)
            row = history.iloc[-1]
            decimals = price_decimals(pair)
            snapshot_date = pd.Timestamp(row["date"])

            st.markdown(f"### {pair} — {snapshot_date:%Y-%m-%d}")
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
            lean = directional_lean(pset.rr_zscore, pset.rr_pctile, float(row["rr_chg_5d"]))
            headline = skew_headline(pair, float(row["rr25_1m"]), lean)
            st.markdown(
                f"<div style='padding:16px 20px;border-radius:6px;border-left:6px solid "
                f"{TONE_COLOR[headline.tone]};background:rgba(128,128,128,0.10)'>"
                f"<div style='font-size:1.45rem;font-weight:700'>{headline.quote_side}</div>"
                f"<div style='font-size:1.05rem;margin-top:2px'>{headline.bias}</div>"
                f"<div style='opacity:0.7;margin-top:6px'>Skew score {lean.score:+.2f} · "
                f"25d RR {row['rr25_1m']:+.4f} vol pts · 5d change {row['rr_chg_5d']:+.3f} · "
                f"positioning, not a forecast</div></div>",
                unsafe_allow_html=True,
            )
            st.caption(f"Percentile source: {pset.source}")
            pc1, pc2 = st.columns([1, 1])
            with pc1:
                st.plotly_chart(
                    percentile_strip(
                        {
                            "IV 1M": pset.iv_1m_pctile,
                            "RV 30d": pset.rv_pctile,
                            "25d RR": pset.rr_pctile,
                        },
                        title=f"Where {pair} sits today",
                    ),
                    use_container_width=True,
                    key=f"pctile_{pair}",
                )
            with pc2:
                st.plotly_chart(
                    iv_rv_chart(history, days=10, title="Implied vs realised, last 10 snapshots"),
                    use_container_width=True,
                    key=f"ivrv10_{pair}",
                )
            st.caption(
                f"**Left.** Each diamond is today's percentile against its own history: under 25% is "
                f"a cheap regime, over 60% a rich one. IV 1M at {pset.iv_1m_pctile:.0%} is the one "
                f"that sets the regime banner below. **Right.** What the market charges for the next "
                f"month against what the pair actually delivered over the last thirty days. A red "
                f"band means implied sits above realised — the market is charging for movement that "
                f"has not been happening."
            )

            with st.expander("What the terms mean"):
                st.markdown(
                    """
            **Percent points** — the unit of every volatility number here. `6.23` means
            6.23%, an annualised standard deviation of returns. Not 0.0623. The whole app
            divides by 100 exactly once, where the formula needs a fraction.

            **Vol points** — the unit of a *difference* between two vols. `iv_minus_rv` of
            +0.16 means implied sits 0.16 percentage points above realised. Risk reversal
            and butterfly are quoted in the same unit: they are vol spreads, not vols.

            **Positioning skew** — where the options market is paying up for protection.
            The 25-delta risk reversal is (call vol − put vol) at equal distance either side
            of spot. Negative means puts cost more than calls: the market is paying for
            downside. It measures *positioning*, what people have already paid for, not
            where price will go. It is not a forecast and not an entry trigger.

            **History** — the snapshots you have accumulated locally, one row per day per
            pair. It drives the time-series charts and, once 60 rows are stored, the
            percentiles and z-score. Below 60 rows the Bloomberg figures from Excel are used
            instead, because a percentile over a handful of days means nothing.

            **Why ATM vol never sets direction** — implied volatility is symmetric by
            construction. It prices the *size* of the expected move and says nothing about
            its sign. Only the risk reversal is directional.
                    """
                )

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
            snapshot_spot = float(row["spot"])
            basis = row.get("range_basis_price", float("nan"))
            basis_price = float(basis) if not pd.isna(basis) else snapshot_spot
            sides = {"Excel range basis": basis_price, "Mid": snapshot_spot}
            for label, column in (("Bid", "bid"), ("Ask", "ask")):
                value = row.get(column, float("nan"))
                if not pd.isna(value):
                    sides[label] = float(value)

            b1, b2 = st.columns([1, 1])
            side = b1.selectbox("Centre the range on", list(sides), key=f"side_{pair}")
            current_spot = b2.number_input(
                "Current spot (override)",
                value=sides[side],
                min_value=0.0,
                step=float(pip_size) * 10,
                format=f"%.{decimals}f",
                key=f"spot_{pair}",
            )
            if "Bid" in sides and "Ask" in sides:
                spread = (sides["Ask"] - sides["Bid"]) / pip_size
                st.caption(
                    f"Snapshot bid {sides['Bid']:.{decimals}f} / ask {sides['Ask']:.{decimals}f}, "
                    f"spread {spread:.1f} pips."
                )
            sigma_pips = sigma_daily_pips(current_spot, float(row["iv_on"]), pip_size)
            low, high = sigma_price_levels(current_spot, sigma_pips, pip_size)
            drift_pips = (current_spot - snapshot_spot) / pip_size
            st.caption(
                f"Snapshot mid was {snapshot_spot:.{decimals}f}; the range is centred "
                f"{drift_pips:+.1f} pips from it. Every level below is built off that price."
            )
            rr_on = row.get("rr25_on", float("nan"))
            bf_on = row.get("bf25_on", float("nan"))
            skewed = not (pd.isna(rr_on) or pd.isna(bf_on))
            if skewed:
                down_pips, up_pips = skewed_daily_range(
                    current_spot, float(row["iv_on"]), float(rr_on), float(bf_on), pip_size
                )
                low, high = skewed_price_levels(current_spot, down_pips, up_pips, pip_size)
                put_vol, call_vol = smile_vols(float(row["iv_on"]), float(rr_on), float(bf_on))

            r1, r2, r3 = st.columns(3)
            r1.metric("Expected absolute move", f"{expected_abs_move_pips(sigma_pips):.1f} pips")
            if skewed:
                r2.metric("1σ low", f"{low:.{decimals}f}", f"-{down_pips:.1f} pips", delta_color="off")
                r3.metric("1σ high", f"{high:.{decimals}f}", f"+{up_pips:.1f} pips", delta_color="off")
                wider = "downside" if down_pips > up_pips else "upside"
                st.caption(
                    f"**Skew-adjusted range.** The overnight smile prices the 25-delta put at "
                    f"{put_vol:.2f} and the call at {call_vol:.2f} percent points (ATM "
                    f"{float(row['iv_on']):.2f}, RR {float(rr_on):+.3f}, BF {float(bf_on):.3f}), so the "
                    f"{wider} sigma is the wider of the two: {down_pips:.1f} pips down against "
                    f"{up_pips:.1f} up. A symmetric band would put both at {sigma_pips:.1f}."
                )
            else:
                r2.metric("1σ low", f"{low:.{decimals}f}")
                r3.metric("1σ high", f"{high:.{decimals}f}")
                st.caption(
                    "Symmetric range from the ATM vol alone. Export the full surface "
                    "(rr25_on and bf25_on) to get a skew-adjusted band."
                )
            st.table(
                pd.DataFrame(
                    {
                        "Stop (pips)": [f"{m * sigma_pips:.1f}" for m in (0.7, 1.0, 1.5)],
                        "Long stop": [f"{current_spot - m * sigma_pips * pip_size:.{decimals}f}" for m in (0.7, 1.0, 1.5)],
                        "Short stop": [f"{current_spot + m * sigma_pips * pip_size:.{decimals}f}" for m in (0.7, 1.0, 1.5)],
                    },
                    index=["0.7 sigma", "1.0 sigma", "1.5 sigma"],
                )
            )
            st.caption(
                "One sigma is the move the option market implies for a single session, about "
                "a 2-in-3 chance of staying inside. The expected absolute move is the average "
                "size of the day's move, which is smaller than one sigma because big days are rare."
            )

            st.subheader("Position sizing")
            intended_stop = st.number_input("Intended stop (pips)", value=round(sigma_pips, 1),
                                           min_value=0.1, step=1.0, key=f"stop_{pair}")
            lots = position_size_lots(risk_per_trade, intended_stop, pip_value_per_lot)
            s1, s2 = st.columns([1, 2])
            s1.metric("Position size", f"{lots:.2f} lots")
            s2.info(stop_verdict(intended_stop, sigma_pips))

            st.subheader("Term structure")
            inverted, note = term_structure_note(row)
            (st.warning if inverted else st.success)(note)
            surface_atm = {
                t.upper(): float(row[f"atm_{t}"])
                for t in SURFACE_TENORS
                if not pd.isna(row.get(f"atm_{t}", float("nan")))
            }
            fig = go.Figure()
            if len(surface_atm) >= 4:
                fig.add_scatter(x=list(surface_atm), y=list(surface_atm.values()),
                                mode="lines+markers", name="ATM")
                for wing, dash in (("rr25", "dash"), ("bf25", "dot")):
                    series = {
                        t.upper(): float(row[f"{wing}_{t}"])
                        for t in SURFACE_TENORS
                        if not pd.isna(row.get(f"{wing}_{t}", float("nan")))
                    }
                    if len(series) >= 4:
                        fig.add_scatter(x=list(series), y=list(series.values()), mode="lines+markers",
                                        name=wing.upper(), yaxis="y2", line=dict(dash=dash))
                fig.update_layout(
                    yaxis2=dict(title="RR / BF (vol points)", overlaying="y", side="right", zeroline=True)
                )
            else:
                fig.add_scatter(x=TENORS, y=[float(row[t]) for t in TENORS], mode="lines+markers", name="ATM")
            fig.update_layout(height=340, margin=dict(t=10, b=10), yaxis_title="ATM vol (percent points)",
                              legend=dict(orientation="h", y=1.12))
            st.plotly_chart(fig, use_container_width=True, key=f"term_{pair}")
            curve = {t: float(row[t]) for t in TENORS}
            st.caption(
                f"**What this shows.** The cost of volatility at four horizons: overnight "
                f"{curve['iv_on']:.2f}, 1 week {curve['iv_1w']:.2f}, 1 month {curve['iv_1m']:.2f}, "
                f"3 months {curve['iv_3m']:.2f} percent points. Normally the line rises to the "
                "right — more time, more that can happen. "
                + (
                    "**Here it does not.** A short tenor sits above a longer one, so the market is "
                    "paying extra for the next few sessions specifically: a data release, a central "
                    "bank, an event with a date on it. Expect a wider day than the 1M vol suggests, "
                    "and size down accordingly."
                    if inverted
                    else "**Here it does.** No dated event is being priced into the front end; today "
                    "should behave like an ordinary session."
                )
            )

            st.subheader("Positioning skew")
            st.metric(headline.quote_side, f"score {lean.score:+.2f}", headline.bias, delta_color="off")
            st.caption(lean.detail)
            st.caption(
                f"**How to read it.** The score runs −1 to +1. It is built only from the 25-delta "
                f"risk reversal: 35% from where the skew sits today (its z-score and percentile) and "
                f"65% from how far it has moved in five days. Beyond ±0.20 it is called a lean, "
                f"otherwise neutral. Today: {row['rr25_1m']:+.4f} vol points, "
                f"{'puts bid over calls' if float(row['rr25_1m']) < 0 else 'calls bid over puts'}, "
                f"having moved {float(row['rr_chg_5d']):+.3f} in the last week."
            )
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
                st.plotly_chart(iv_rv, use_container_width=True, key=f"ivrv_full_{pair}")
                st.caption(
                    "**What this shows.** What the option market charges for the next month (IV) "
                    "against what the pair actually delivered over the last thirty days (RV), both "
                    "in percent points. When IV sits above RV the market is charging for movement "
                    "that has not been happening — the setup for chop, and a poor time to pay for "
                    "a breakout. When IV drops below RV the pair is moving more than it costs to "
                    "own that movement, which is when trends tend to run. The gap between the two "
                    "lines is the `IV − RV` metric at the top of the page."
                )

                rr = history["rr25_1m"]
                mean, sd = float(rr.mean()), float(rr.std(ddof=1) or 0.0)
                rr_fig = go.Figure()
                rr_fig.add_scatter(x=history["date"], y=rr, name="25d RR 1M", mode="lines")
                for mult, dash in ((1, "dot"), (2, "dash")):
                    for sign in (1, -1):
                        rr_fig.add_hline(y=mean + sign * mult * sd, line_dash=dash, line_color="#888")
                rr_fig.update_layout(height=320, margin=dict(t=10, b=10), yaxis_title="vol points")
                st.plotly_chart(rr_fig, use_container_width=True, key=f"rr_{pair}")
                st.caption(
                    f"**What this shows.** The 25-delta risk reversal over time, in vol points, with "
                    f"its own mean ({mean:+.3f}) and the ±1σ dotted / ±2σ dashed bands around it. "
                    "Below zero means puts are bid over calls — the market is paying for downside. "
                    "The **level** tells you how crowded that positioning already is; the **slope** "
                    "tells you whether it is being put on or taken off, and the slope is what the "
                    "skew score weights most heavily. A line pushing past ±2σ is crowded and prone "
                    "to snapping back; a line travelling *away* from an extreme is positioning "
                    "unwinding, which is the more tradeable of the two."
                )

            st.subheader("Stored snapshots")
            st.dataframe(store, use_container_width=True, key=f"store_{pair}")
            st.download_button("Download CSV", store.to_csv(index=False).encode("utf-8"),
                               "snapshots.csv", "text/csv", key=f"dl_{pair}")
