from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import LOCAL_PERCENTILE_MIN_ROWS
from .vol import is_inverted, percentile, zscore


@dataclass(frozen=True)
class Regime:
    label: str
    tone: str
    note: str


def regime(iv_1m_pctile: float) -> Regime:
    """Vol regime from the 1M ATM percentile (fraction 0-1)."""
    if pd.isna(iv_1m_pctile):
        return Regime("Unknown", "gray", "No percentile available.")
    if iv_1m_pctile < 0.25:
        return Regime("Low vol", "green", "Ranges hold. Fading extremes works better; premium is cheap.")
    if iv_1m_pctile <= 0.60:
        return Regime("Mid vol", "orange", "No volatility edge. Trade direction only.")
    return Regime("High vol", "red", "Breakouts follow through. Size down; premium is rich.")


def follow_through(iv_minus_rv: float) -> str:
    """Read of implied-less-realised, in vol points."""
    if pd.isna(iv_minus_rv):
        return "Unknown."
    if iv_minus_rv < 0:
        return "Implied below realised - expect follow-through."
    return "Implied above realised - expect chop."


def term_structure_note(row: pd.Series) -> tuple[bool, str]:
    """(inverted, plain-language note) across the four ATM tenors."""
    if is_inverted(row):
        return True, "Inverted curve - near-term event risk is priced. Size accordingly."
    return False, "Upward sloping - no near-term event premium."


@dataclass(frozen=True)
class Lean:
    direction: str
    score: float
    detail: str


def directional_lean(rr_zscore: float, rr_pctile: float, rr_chg_5d: float) -> Lean:
    """Positioning skew from the risk reversal only; ATM vol is never an input.

    Positive score means base-currency-positive skew. Vol points in, unitless
    score out. This is positioning, not a forecast.
    """
    level = 0.0
    if not pd.isna(rr_zscore):
        level += max(-1.0, min(1.0, rr_zscore / 2.0))
    if not pd.isna(rr_pctile):
        level += (rr_pctile - 0.5) * 2.0
    level /= 2.0

    change = 0.0 if pd.isna(rr_chg_5d) else max(-1.0, min(1.0, rr_chg_5d / 0.25))
    score = 0.35 * level + 0.65 * change

    if score > 0.20:
        direction = "Base-currency positive skew"
    elif score < -0.20:
        direction = "Base-currency negative skew"
    else:
        direction = "Neutral"

    detail = f"level {level:+.2f} weighted 0.35, 5d change {rr_chg_5d:+.3f} vol pts -> {change:+.2f} weighted 0.65"
    return Lean(direction, score, detail)


@dataclass(frozen=True)
class Headline:
    """Top-of-page skew read. `quote_side` is the vol-market phrasing,
    `bias` the plain-language lean, `tone` a colour key."""

    quote_side: str
    bias: str
    tone: str


def skew_headline(pair: str, rr25_1m: float, lean: Lean) -> Headline:
    """Which side of the option market is bid, and which way that leans.

    `pair` is a Bloomberg root such as EURUSD; the first three letters are the
    base currency. `rr25_1m` is in vol points, signed. Positioning, not a forecast.
    """
    base, quote = pair[:3].upper(), pair[3:6].upper()

    if pd.isna(rr25_1m) or rr25_1m == 0:
        quote_side = "No skew quoted"
    elif rr25_1m > 0:
        quote_side = f"{base} calls over puts"
    else:
        quote_side = f"{base} puts over calls"

    score = lean.score
    if pd.isna(score):
        return Headline(quote_side, "No directional read available.", "gray")
    if score > 0.45:
        return Headline(quote_side, f"Skew leans strongly bullish {base} against {quote} today.", "green")
    if score > 0.20:
        return Headline(quote_side, f"Skew leans mildly bullish {base} against {quote} today.", "green")
    if score < -0.45:
        return Headline(quote_side, f"Skew leans strongly bearish {base} against {quote} today.", "red")
    if score < -0.20:
        return Headline(quote_side, f"Skew leans mildly bearish {base} against {quote} today.", "red")
    return Headline(quote_side, f"No directional lean in {base} today — skew is balanced.", "gray")


def position_size_lots(risk_per_trade: float, stop_pips: float, pip_value_per_lot: float) -> float:
    """Lots, from account-currency risk, stop in pips, pip value per lot."""
    if stop_pips <= 0 or pip_value_per_lot <= 0:
        return float("nan")
    return risk_per_trade / (stop_pips * pip_value_per_lot)


def stop_verdict(stop_pips: float, sigma_pips: float) -> str:
    """Where the intended stop sits relative to today's 1-sigma, both in pips."""
    if sigma_pips <= 0 or pd.isna(sigma_pips):
        return "No sigma available."
    ratio = stop_pips / sigma_pips
    if ratio < 0.7:
        return f"{ratio:.2f} sigma - inside the noise band. Expect to be stopped by drift alone."
    if ratio > 1.5:
        return f"{ratio:.2f} sigma - unusually wide. Size down or tighten."
    return f"{ratio:.2f} sigma - reasonable relative to today's range."


@dataclass(frozen=True)
class PercentileSet:
    source: str
    iv_1m_pctile: float
    rv_pctile: float
    rr_pctile: float
    rr_zscore: float


def percentile_set(history: pd.DataFrame, row: pd.Series, window: int) -> PercentileSet:
    """Excel/Bloomberg percentiles until the local store holds 60+ rows for the pair."""
    clean = history.dropna(subset=["iv_1m"])
    if len(clean) < LOCAL_PERCENTILE_MIN_ROWS:
        return PercentileSet(
            f"Excel / Bloomberg (local store has {len(clean)} rows, needs {LOCAL_PERCENTILE_MIN_ROWS})",
            float(row["iv_1m_pctile"]),
            float(row["rv_pctile"]),
            float(row["rr_pctile"]),
            float(row["rr_zscore"]),
        )
    recent = clean.sort_values("date").tail(window)
    return PercentileSet(
        f"Local store, {len(recent)}-row window",
        percentile(recent["iv_1m"], float(row["iv_1m"])),
        percentile(recent["rv_30"], float(row["rv_30"])),
        percentile(recent["rr25_1m"], float(row["rr25_1m"])),
        zscore(recent["rr25_1m"], float(row["rr25_1m"])),
    )
