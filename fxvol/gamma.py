from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

CONTRACT_SIZE: int = 100
DAYS_PER_YEAR: float = 365.0


def black_scholes_gamma(
    spot: float, strike: float, years: float, iv: float, rate: float = 0.0
) -> float:
    """Gamma per unit of underlying: change in delta per 1.0 move in spot.

    `iv` is in percent points (20.0 means 20%), `years` is time to expiry in
    years, `rate` is the continuously compounded rate as a fraction.
    """
    if spot <= 0 or strike <= 0 or years <= 0 or iv <= 0:
        return 0.0
    sigma = iv / 100.0
    vol_time = sigma * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma**2) * years) / vol_time
    return math.exp(-0.5 * d1**2) / (math.sqrt(2.0 * math.pi) * spot * vol_time)


def gamma_column(chain: pd.DataFrame, spot: float, rate: float = 0.0) -> pd.Series:
    """Per-contract gamma for every row of a chain, vectorised."""
    strike = chain["strike"].to_numpy(dtype=float)
    years = chain["days_to_expiry"].to_numpy(dtype=float) / DAYS_PER_YEAR
    sigma = chain["iv"].to_numpy(dtype=float) / 100.0

    vol_time = sigma * np.sqrt(years)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(spot / strike) + 0.5 * sigma**2 * years + rate * years) / vol_time
        gamma = np.exp(-0.5 * d1**2) / (np.sqrt(2.0 * np.pi) * spot * vol_time)
    gamma[~np.isfinite(gamma)] = 0.0
    gamma[(years <= 0) | (sigma <= 0)] = 0.0
    return pd.Series(gamma, index=chain.index)


def gamma_exposure(
    chain: pd.DataFrame,
    spot: float,
    contract_size: int = CONTRACT_SIZE,
    rate: float = 0.0,
) -> pd.DataFrame:
    """Dealer gamma exposure per strike, in notional currency per 1% spot move.

    `chain` needs columns strike, option_type ('C'/'P'), open_interest, iv
    (percent points) and days_to_expiry. The dealer convention assumed here is
    the standard one: dealers are long calls and short puts against customer
    flow, so call gamma is positive and put gamma negative.

    Returns one row per strike with call_gex, put_gex, total_gex and the
    running cumulative total, sorted by strike.
    """
    if chain.empty:
        return pd.DataFrame(columns=["strike", "call_gex", "put_gex", "total_gex", "cumulative_gex"])

    work = chain.copy()
    work["gamma"] = gamma_column(work, spot, rate)
    work["gex"] = (
        work["gamma"] * work["open_interest"] * contract_size * spot * spot * 0.01
    )

    side = work["option_type"].astype(str).str.upper().str[0]
    calls = work.loc[side == "C"].groupby("strike")["gex"].sum()
    puts = work.loc[side == "P"].groupby("strike")["gex"].sum()

    frame = pd.DataFrame({"call_gex": calls, "put_gex": puts}).fillna(0.0)
    frame["put_gex"] *= -1.0
    frame["total_gex"] = frame["call_gex"] + frame["put_gex"]
    frame = frame.sort_index().reset_index()
    frame["cumulative_gex"] = frame["total_gex"].cumsum()
    return frame


@dataclass(frozen=True)
class Levels:
    """Support and resistance read off the gamma profile. Prices in strike units."""

    call_wall: float
    put_wall: float
    flip_point: float
    total_gex: float
    regime: str
    note: str


def gamma_flip(profile: pd.DataFrame) -> float:
    """Strike where cumulative dealer gamma crosses zero, interpolated.

    Below it dealers are short gamma and hedging amplifies moves; above it they
    are long gamma and hedging damps moves. NaN when there is no crossing.
    """
    if profile.empty or len(profile) < 2:
        return float("nan")

    cumulative = profile["cumulative_gex"].to_numpy(dtype=float)
    strikes = profile["strike"].to_numpy(dtype=float)
    signs = np.sign(cumulative)
    crossings = np.where(np.diff(signs) != 0)[0]
    if crossings.size == 0:
        return float("nan")

    i = int(crossings[-1])
    lo, hi = cumulative[i], cumulative[i + 1]
    if hi == lo:
        return float(strikes[i])
    weight = -lo / (hi - lo)
    return float(strikes[i] + weight * (strikes[i + 1] - strikes[i]))


def key_levels(profile: pd.DataFrame, spot: float) -> Levels:
    """The call wall (resistance), put wall (support) and gamma flip."""
    if profile.empty:
        return Levels(float("nan"), float("nan"), float("nan"), 0.0, "Unknown", "No chain loaded.")

    call_wall = float(profile.loc[profile["total_gex"].idxmax(), "strike"])
    put_wall = float(profile.loc[profile["total_gex"].idxmin(), "strike"])
    flip = gamma_flip(profile)
    total = float(profile["total_gex"].sum())

    if total > 0:
        regime = "Long gamma"
        note = (
            "Dealers are net long gamma. Their hedging sells rallies and buys dips, "
            "which pins price and favours mean reversion inside the walls."
        )
    else:
        regime = "Short gamma"
        note = (
            "Dealers are net short gamma. Their hedging buys strength and sells weakness, "
            "which amplifies moves. Breakouts run further and stops need more room."
        )

    if not math.isnan(flip):
        side = "above" if spot > flip else "below"
        note += f" Spot is {side} the flip at {flip:,.2f}."
    return Levels(call_wall, put_wall, flip, total, regime, note)


def top_levels(profile: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """The n largest positive and n most negative strikes, labelled."""
    if profile.empty:
        return pd.DataFrame(columns=["strike", "total_gex", "role"])

    resistance = profile.nlargest(n, "total_gex").assign(role="Resistance")
    support = profile.nsmallest(n, "total_gex").assign(role="Support")
    out = pd.concat([resistance, support])
    out = out[out["total_gex"] != 0]
    return out[["strike", "total_gex", "role"]].reset_index(drop=True)
