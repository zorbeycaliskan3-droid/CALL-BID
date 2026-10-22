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


def _net_gamma_curve(
    chain: pd.DataFrame, prices: np.ndarray, contract_size: int, rate: float = 0.0
) -> np.ndarray:
    """Net dealer gamma at each hypothetical price, all at once.

    Same arithmetic as `gamma_exposure` summed over strikes, as a prices x
    contracts matrix so a full sweep costs one numpy pass instead of hundreds
    of groupbys.
    """
    strike = chain["strike"].to_numpy(dtype=float)
    years = chain["days_to_expiry"].to_numpy(dtype=float) / DAYS_PER_YEAR
    sigma = chain["iv"].to_numpy(dtype=float) / 100.0
    oi = chain["open_interest"].to_numpy(dtype=float)
    sign = np.where(chain["option_type"].astype(str).str.upper().str[0].to_numpy() == "C", 1.0, -1.0)
    valid = (years > 0) & (sigma > 0) & (strike > 0)

    s = np.asarray(prices, dtype=float)[:, None]
    vol_time = sigma * np.sqrt(years)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(s / strike) + (rate + 0.5 * sigma**2) * years) / vol_time
        gamma = np.exp(-0.5 * d1**2) / (np.sqrt(2.0 * np.pi) * s * vol_time)
    gamma = np.where(valid & np.isfinite(gamma), gamma, 0.0)
    exposure = gamma * oi * contract_size * s * s * 0.01 * sign
    return exposure.sum(axis=1)


def price_check(chain: pd.DataFrame, price: float, tolerance: float = 0.05) -> str | None:
    """Warn when `price` cannot be where this chain was built around.

    Bloomberg centres a chain pull on the live price, so a wrong price cell
    shows up as a price sitting outside the strikes actually listed. The
    middle strike alone is a poor test: a name that has fallen a long way
    keeps its old high strikes, which drags the median up without anything
    being wrong. So this warns only when the price falls outside the middle
    half of the strikes *and* is more than `tolerance` from the median.
    """
    if chain.empty or price <= 0:
        return None

    strikes = chain["strike"].dropna()
    if strikes.empty:
        return None

    centre = float(strikes.median())
    low, high = float(strikes.quantile(0.25)), float(strikes.quantile(0.75))
    gap = price / centre - 1.0
    if low <= price <= high or abs(gap) <= tolerance:
        return None

    where = "below" if price < low else "above"
    return (
        f"The price {price:,.2f} sits {where} the middle half of this chain's strikes "
        f"({low:,.2f} to {high:,.2f}, centred {centre:,.2f}) and is {gap:+.1%} from the "
        f"centre. Bloomberg builds a chain around the live price, so the price cell may "
        f"be stale — check it on the Chain tab, or correct it under 'Adjust underlying "
        f"price or multiplier' below."
    )


def chain_quality(chain: pd.DataFrame, minimum_strikes: int = 5) -> str | None:
    """Warn when a chain holds too few strikes to say anything at all.

    A Bloomberg pull populates asynchronously, so a workbook saved before it
    finishes carries only the first ticker of each spill. One or two strikes
    produce a chart, walls and a regime that are all meaningless, so the
    caller should show this instead of any of them.
    """
    if chain.empty:
        return "This chain is empty."
    strikes = chain["strike"].dropna().nunique()
    if strikes >= minimum_strikes:
        return None
    return (
        f"Only {strikes} strike{'s' if strikes != 1 else ''} came through for this underlying "
        f"({len(chain)} row{'s' if len(chain) != 1 else ''}), far too few for a gamma profile. "
        f"Bloomberg fills a chain asynchronously, so this usually means the workbook was saved "
        f"before the pull finished. Open the Chain tab, wait until the status cell reads READY, "
        f"then save the Export sheet again."
    )


def window_reach(chain: pd.DataFrame, price: float) -> tuple[float, float]:
    """How far the chain's strikes reach below and above `price`, as fractions.

    Returns (down, up), both positive, e.g. (0.031, 0.026) for a window
    covering 3.1% below and 2.6% above.
    """
    if chain.empty or price <= 0:
        return float("nan"), float("nan")
    strikes = chain["strike"].dropna()
    if strikes.empty:
        return float("nan"), float("nan")
    return max(0.0, 1.0 - float(strikes.min()) / price), max(0.0, float(strikes.max()) / price - 1.0)


def coverage_check(chain: pd.DataFrame, price: float, minimum: float = 0.05) -> str | None:
    """Warn when the strike window is too narrow to compare across days.

    A pull centred on spot slides as price moves, so a narrow window measures a
    different set of strikes each day and the regime can appear to flip when
    nothing changed. `minimum` is the reach each side, as a fraction, below
    which the window is called too narrow.
    """
    down, up = window_reach(chain, price)
    if pd.isna(down) or pd.isna(up):
        return None
    if down >= minimum and up >= minimum:
        return None

    if down < minimum and up < minimum:
        thin = "both sides fall"
    else:
        thin = "the downside falls" if down < minimum else "the upside falls"
    return (
        f"Narrow strike window: this chain only reaches {down:.1%} below and {up:.1%} above "
        f"the price, and {thin} short of {minimum:.0%}. The pull is centred on spot, so "
        f"the window slides as price moves and the gamma reading can flip between days purely "
        f"because different strikes came into view. Raise 'Strikes each side' on the Chain tab "
        f"and re-pull."
    )


def net_gamma_at(chain: pd.DataFrame, price: float, contract_size: int = CONTRACT_SIZE) -> float:
    """Total dealer gamma if the underlying traded at `price`, per 1% move."""
    if chain.empty:
        return 0.0
    return float(_net_gamma_curve(chain, np.array([price]), contract_size)[0])


def gamma_flip(
    chain: pd.DataFrame,
    spot: float,
    contract_size: int = CONTRACT_SIZE,
    width: float = 0.15,
    steps: int = 301,
) -> float:
    """The zero-gamma level: the price where total dealer gamma changes sign.

    Re-prices the whole chain at hypothetical underlying prices within
    ±`width` of spot and finds where net gamma crosses zero. When there is
    more than one crossing the one nearest spot is returned, since that is the
    level price would actually have to cross. NaN when net gamma keeps one sign.
    """
    if chain.empty or spot <= 0:
        return float("nan")

    grid = np.linspace(spot * (1 - width), spot * (1 + width), steps)
    values = _net_gamma_curve(chain, grid, contract_size)
    crossings = np.where(np.diff(np.sign(values)) != 0)[0]
    if crossings.size == 0:
        return float("nan")

    levels = []
    for i in crossings:
        lo, hi = values[i], values[i + 1]
        weight = 0.0 if hi == lo else -lo / (hi - lo)
        levels.append(float(grid[i] + weight * (grid[i + 1] - grid[i])))
    return min(levels, key=lambda level: abs(level - spot))


def key_levels(
    profile: pd.DataFrame,
    spot: float,
    chain: pd.DataFrame | None = None,
    contract_size: int = CONTRACT_SIZE,
) -> Levels:
    """The call wall (resistance), put wall (support) and gamma flip.

    The flip needs the raw `chain` to re-price at other underlying prices;
    without it the flip is reported as NaN.
    """
    if profile.empty:
        return Levels(float("nan"), float("nan"), float("nan"), 0.0, "Unknown", "No chain loaded.")

    call_wall = float(profile.loc[profile["total_gex"].idxmax(), "strike"])
    put_wall = float(profile.loc[profile["total_gex"].idxmin(), "strike"])
    flip = gamma_flip(chain, spot, contract_size) if chain is not None else float("nan")
    total = float(profile["total_gex"].sum())

    if total == 0:
        regime = "Flat gamma"
        note = "Call and put gamma cancel out. No hedging pressure either way."
    elif total > 0:
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

    if not math.isnan(flip) and total != 0:
        places = 4 if flip < 10 else 2
        move = "rises above" if flip > spot else "falls below"
        other = "short" if total > 0 else "long"
        keeps = "stay long gamma unless" if total > 0 else "stay short gamma until"
        note += (
            f" The gamma flip is at {flip:,.{places}f}: dealers {keeps} price {move} it, "
            f"where they turn {other} gamma."
        )
    elif total != 0:
        note += " No gamma flip within 15% of spot, so this regime should hold for any normal move."
    return Levels(call_wall, put_wall, flip, total, regime, note)


@dataclass(frozen=True)
class Balance:
    """Call side against put side for one chain.

    Gamma figures are in notional per 1% move, calls positive and puts as a
    positive magnitude. `tilt` runs -1 (all puts) to +1 (all calls).
    """

    call_gamma: float
    put_gamma: float
    call_oi: float
    put_oi: float
    tilt: float
    oi_ratio: float
    side: str
    note: str


def call_put_balance(
    profile: pd.DataFrame, chain: pd.DataFrame | None = None
) -> Balance:
    """Which side of the book carries more weight, by gamma and by open interest."""
    if profile.empty:
        return Balance(0.0, 0.0, 0.0, 0.0, 0.0, float("nan"), "Unknown", "No chain loaded.")

    call_gamma = float(profile["call_gex"].sum())
    put_gamma = abs(float(profile["put_gex"].sum()))
    total = call_gamma + put_gamma
    tilt = 0.0 if total == 0 else (call_gamma - put_gamma) / total

    call_oi = put_oi = float("nan")
    if chain is not None and not chain.empty:
        side = chain["option_type"].astype(str).str.upper().str[0]
        call_oi = float(chain.loc[side == "C", "open_interest"].sum())
        put_oi = float(chain.loc[side == "P", "open_interest"].sum())
    ratio = call_oi / put_oi if put_oi and put_oi > 0 else float("nan")

    if tilt > 0.20:
        label = "Call side stronger"
        note = (
            "Call gamma outweighs put gamma. Under the usual dealer assumption that is the "
            "stabilising side: hedging leans against rallies and the upside strikes pin."
        )
    elif tilt < -0.20:
        label = "Put side stronger"
        note = (
            "Put gamma outweighs call gamma. That is the destabilising side: hedging sells "
            "into weakness, so downside moves extend rather than get absorbed."
        )
    else:
        label = "Balanced"
        note = "Neither side dominates, so hedging pressure is roughly symmetric."

    return Balance(call_gamma, put_gamma, call_oi, put_oi, tilt, ratio, label, note)


def top_levels(profile: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """The n largest positive and n most negative strikes, labelled."""
    if profile.empty:
        return pd.DataFrame(columns=["strike", "total_gex", "role"])

    resistance = profile.nlargest(n, "total_gex").assign(role="Resistance")
    support = profile.nsmallest(n, "total_gex").assign(role="Support")
    out = pd.concat([resistance, support])
    out = out[out["total_gex"] != 0]
    return out[["strike", "total_gex", "role"]].reset_index(drop=True)
