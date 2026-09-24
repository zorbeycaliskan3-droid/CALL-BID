from dataclasses import dataclass
from pathlib import Path

NUMERIC_COLUMNS: list[str] = [
    "spot", "iv_on", "iv_1w", "iv_1m", "iv_3m", "rv_30", "rr25_1m", "bf25_1m",
    "iv_minus_rv", "iv_1m_pctile", "rv_pctile", "rr_pctile", "rr_zscore",
    "rr_chg_5d", "sigma_daily_pct", "sigma_daily_pips", "exp_abs_move_pips",
    "stop_0p7sig", "stop_1p0sig", "stop_1p5sig",
]

# Added after the first release; absent from older exports and parsed as NaN.
SURFACE_TENORS: list[str] = ["on", "1w", "2w", "1m", "2m", "3m", "6m", "1y"]
SURFACE_MEASURES: list[str] = ["atm", "rr25", "bf25", "rr10", "bf10"]
# rr25_1m and bf25_1m are already carried by the snapshot block, so the
# surface block leaves them out rather than repeating the column name.
SURFACE_COLUMNS: list[str] = [
    f"{measure}_{tenor}"
    for measure in SURFACE_MEASURES
    for tenor in SURFACE_TENORS
    if f"{measure}_{tenor}" not in NUMERIC_COLUMNS
]

OPTIONAL_COLUMNS: list[str] = [
    "bid", "ask", "range_basis_price", "chain_spot", "chain_multiplier",
] + SURFACE_COLUMNS
NUMERIC_COLUMNS += OPTIONAL_COLUMNS

COLUMNS: list[str] = ["date", "pair"] + NUMERIC_COLUMNS
TENORS: list[str] = ["iv_on", "iv_1w", "iv_1m", "iv_3m"]

TRADING_DAYS: int = 252
LOCAL_PERCENTILE_MIN_ROWS: int = 60
RECOMPUTE_TOLERANCE: float = 0.01


@dataclass(frozen=True)
class Paths:
    """Filesystem layout. `root` is the folder holding data/."""
    root: Path

    @property
    def incoming(self) -> Path:
        return self.root / "data" / "incoming"

    @property
    def sample(self) -> Path:
        return self.root / "data" / "sample"

    @property
    def store(self) -> Path:
        return self.root / "data" / "store" / "snapshots.parquet"


def pip_size_for(pair: str) -> float:
    """Pip size in price units: 0.01 for JPY crosses, else 0.0001."""
    return 0.01 if "JPY" in pair.upper() else 0.0001


def price_decimals(pair: str) -> int:
    """Display precision for spot and derived price levels."""
    return 3 if "JPY" in pair.upper() else 5
