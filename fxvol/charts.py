"""Plotly figures for the FX pair view. No streamlit imports: the core stays reusable."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

PERCENTILE_BANDS: list[tuple[float, float, str, str]] = [
    (0.0, 0.25, "#1b7f3b", "Low"),
    (0.25, 0.60, "#b26a00", "Mid"),
    (0.60, 1.0, "#b3261e", "High"),
]


def percentile_strip(values: dict[str, float], title: str = "") -> go.Figure:
    """Where today sits on 0-100% for each named measure.

    `values` maps a label to a percentile as a fraction 0-1; NaN entries are
    dropped. Bands mark the low / mid / high vol regimes.
    """
    clean = {k: float(v) for k, v in values.items() if not pd.isna(v)}
    labels = list(clean)
    fig = go.Figure()

    for low, high, colour, name in PERCENTILE_BANDS:
        fig.add_shape(
            type="rect", x0=low * 100, x1=high * 100, y0=-0.5, y1=len(labels) - 0.5,
            fillcolor=colour, opacity=0.13, line_width=0, layer="below",
        )
        fig.add_annotation(
            x=(low + high) / 2 * 100, y=len(labels) - 0.4, text=name, showarrow=False,
            font=dict(size=10, color=colour), yanchor="bottom",
        )

    fig.add_scatter(
        x=[v * 100 for v in clean.values()],
        y=labels,
        mode="markers+text",
        marker=dict(size=16, symbol="diamond", color="#1f2933",
                    line=dict(width=2, color="#ffffff")),
        text=[f"{v:.0%}" for v in clean.values()],
        textposition="top center",
        showlegend=False,
    )
    fig.update_layout(
        title=title or None,
        height=90 + 52 * max(len(labels), 1),
        margin=dict(t=46 if title else 26, b=26, l=10, r=10),
        xaxis=dict(range=[0, 100], title="percentile", ticksuffix="%"),
        yaxis=dict(autorange="reversed"),
    )
    return fig


def iv_rv_chart(history: pd.DataFrame, days: int = 10, title: str = "") -> go.Figure:
    """Implied against realised vol over the last `days` stored snapshots.

    Both in percent points. The shaded band is the gap: red where implied sits
    above realised (the market charges more than the pair delivers), green
    where it sits below.
    """
    recent = history.sort_values("date").tail(days)
    fig = go.Figure()
    if recent.empty:
        return fig

    gap_positive = (recent["iv_1m"] - recent["rv_30"]).ge(0).all()
    fig.add_scatter(
        x=recent["date"], y=recent["rv_30"], name="RV 30d", mode="lines+markers",
        line=dict(color="#5f6368", width=2),
    )
    fig.add_scatter(
        x=recent["date"], y=recent["iv_1m"], name="IV 1M", mode="lines+markers",
        line=dict(color="#2f6fdb", width=2),
        fill="tonexty",
        fillcolor="rgba(179,38,30,0.16)" if gap_positive else "rgba(27,127,59,0.16)",
    )
    fig.update_layout(
        title=title or None,
        height=330,
        margin=dict(t=46 if title else 16, b=16),
        yaxis_title="percent points",
        legend=dict(orientation="h", y=1.14),
    )
    return fig
