from dataclasses import dataclass
from pathlib import Path

NUMERIC_COLUMNS: list[str] = [
    "spot", "iv_on", "iv_1w", "iv_1m", "iv_3m", "rv_30", "rr25_1m", "bf25_1m",
    "iv_minus_rv", "iv_1m_pctile", "rv_pctile", "rr_pctile", "rr_zscore",
    "rr_chg_5d", "sigma_daily_pct", "sigma_daily_pips", "exp_abs_move_pips",
    "stop_0p7sig", "stop_1p0sig", "stop_1p5sig",
]

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
