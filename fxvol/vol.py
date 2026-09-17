from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .config import RECOMPUTE_TOLERANCE, TENORS, TRADING_DAYS


def sigma_daily_pct(iv_on: float) -> float:
    """Daily sigma in percent points, from overnight ATM vol in percent points."""
    return iv_on / math.sqrt(TRADING_DAYS)


def sigma_daily_pips(spot: float, iv_on: float, pip_size: float) -> float:
    """Daily sigma in pips. `spot` in price units, `iv_on` in percent points."""
    return spot * (sigma_daily_pct(iv_on) / 100.0) / pip_size


def expected_abs_move_pips(sigma_pips: float) -> float:
    """Mean absolute move of a zero-mean normal, in pips."""
    return sigma_pips * math.sqrt(2.0 / math.pi)


def stop_ladder(sigma_pips: float) -> dict[str, float]:
    """Stop distances in pips at 0.7 / 1.0 / 1.5 sigma."""
    return {"0.7 sigma": 0.7 * sigma_pips, "1.0 sigma": sigma_pips, "1.5 sigma": 1.5 * sigma_pips}


def sigma_price_levels(spot: float, sigma_pips: float, pip_size: float) -> tuple[float, float]:
    """1-sigma (low, high) as price levels."""
    move = sigma_pips * pip_size
    return spot - move, spot + move


@dataclass(frozen=True)
class Recheck:
    """One Excel-vs-Python comparison. `field` names the CSV column."""

    field: str
    excel: float
    python: float

    @property
    def relative_error(self) -> float:
        if self.excel == 0 or pd.isna(self.excel):
            return float("nan")
        return abs(self.python - self.excel) / abs(self.excel)

    @property
    def ok(self) -> bool:
        error = self.relative_error
        return bool(pd.isna(error) or error <= RECOMPUTE_TOLERANCE)


def recompute(row: pd.Series, pip_size: float) -> dict[str, float]:
    """Python-side derived fields, units as per the CSV contract."""
    pct = sigma_daily_pct(float(row["iv_on"]))
    pips = sigma_daily_pips(float(row["spot"]), float(row["iv_on"]), pip_size)
    return {
        "sigma_daily_pct": pct,
        "sigma_daily_pips": pips,
        "exp_abs_move_pips": expected_abs_move_pips(pips),
        "stop_0p7sig": 0.7 * pips,
        "stop_1p0sig": pips,
        "stop_1p5sig": 1.5 * pips,
        "iv_minus_rv": float(row["iv_1m"]) - float(row["rv_30"]),
    }


def verify(row: pd.Series, pip_size: float) -> list[Recheck]:
    """Every recomputed field compared against the Excel value."""
    return [
        Recheck(field, float(row[field]), value)
        for field, value in recompute(row, pip_size).items()
        if field in row.index
    ]


def percentile(series: pd.Series, value: float) -> float:
    """Fraction of observations at or below `value`, 0-1."""
    clean = series.dropna()
    if clean.empty:
        return float("nan")
    return float((clean <= value).mean())


def zscore(series: pd.Series, value: float) -> float:
    """Z-score of `value` against `series`, same units as the series."""
    clean = series.dropna()
    if len(clean) < 2:
        return float("nan")
    sd = float(clean.std(ddof=1))
    if sd == 0 or pd.isna(sd):
        return float("nan")
    return (value - float(clean.mean())) / sd


def term_structure(row: pd.Series) -> dict[str, float]:
    """ATM vol by tenor, percent points."""
    return {tenor: float(row[tenor]) for tenor in TENORS}


def is_inverted(row: pd.Series) -> bool:
    """True when any shorter tenor sits above a longer one."""
    values = [float(row[t]) for t in TENORS]
    return any(a > b for a, b in zip(values, values[1:]))
