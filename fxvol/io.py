from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import COLUMNS, NUMERIC_COLUMNS, Paths


def _read_text(path: Path) -> list[str]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1254", "latin-1"):
        try:
            return raw.decode(encoding).replace("\r\n", "\n").split("\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1").replace("\r\n", "\n").split("\n")


def _sniff(lines: list[str]) -> tuple[int, str, str]:
    """Return (header index, field delimiter, decimal separator)."""
    for i, line in enumerate(lines):
        for delimiter in (";", ",", "\t", "|"):
            fields = line.split(delimiter)
            if fields[0].strip().strip('"').lower() == "date" and len(fields) > 5:
                decimal = "," if delimiter != "," else "."
                return i, delimiter, decimal
    raise ValueError(f"no header row starting with 'date' found")


def _to_float(value: str, decimal: str) -> float:
    text = value.strip().strip('"')
    if not text:
        return float("nan")
    if decimal == ",":
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return float("nan")


def parse_snapshot(path: Path | str) -> pd.DataFrame:
    """Parse one Bloomberg snapshot CSV in either Excel (`;` / `,`-decimal)
    or VBA (`,` / `.`-decimal) dialect. Vols stay in percent points."""
    path = Path(path)
    lines = _read_text(path)
    header_idx, delimiter, decimal = _sniff(lines)
    header = [f.strip().strip('"') for f in lines[header_idx].split(delimiter)]

    rows: list[dict[str, object]] = []
    for line in lines[header_idx + 1:]:
        if not line.strip(delimiter).strip():
            continue
        fields = [f.strip().strip('"') for f in line.split(delimiter)]
        stamp = pd.to_datetime(fields[0], errors="coerce", format="%Y-%m-%d")
        if pd.isna(stamp):
            continue
        record: dict[str, object] = {}
        for name, value in zip(header, fields):
            if name == "date":
                record[name] = stamp.normalize()
            elif name == "pair":
                record[name] = value.upper()
            elif name in NUMERIC_COLUMNS:
                record[name] = _to_float(value, decimal)
        rows.append(record)

    frame = pd.DataFrame(rows)
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.Series(dtype="float64")
    return frame[COLUMNS]


CHAIN_NUMERIC: list[str] = ["strike", "open_interest", "iv", "days_to_expiry"]


def has_chain_block(path: Path | str) -> bool:
    """True when the file carries an option-chain header alongside anything else.

    The combined export writes the daily snapshot and the chain into one CSV;
    each parser finds its own header and ignores the other block.
    """
    for line in _read_text(Path(path)):
        for delimiter in (";", ",", "\t", "|"):
            fields = [f.strip().strip('"').lower() for f in line.split(delimiter)]
            if "strike" in fields and "option_type" in fields:
                return True
    return False


def parse_chain(path: Path | str) -> pd.DataFrame:
    """Parse an option-chain export for the gamma exposure view.

    Same dialect sniffing as `parse_snapshot`. Required columns: strike,
    option_type ('C'/'P' or 'Call'/'Put'), open_interest, iv (percent points)
    and either days_to_expiry or expiry (a date, converted to calendar days).
    Rows missing a strike or open interest are dropped.
    """
    path = Path(path)
    lines = _read_text(path)

    header_idx = delimiter = None
    for i, line in enumerate(lines):
        for candidate in (";", ",", "\t", "|"):
            fields = [f.strip().strip('"').lower() for f in line.split(candidate)]
            if "strike" in fields and len(fields) > 2:
                header_idx, delimiter = i, candidate
                break
        if header_idx is not None:
            break
    if header_idx is None:
        raise ValueError("no header row containing 'strike' found")

    decimal = "," if delimiter != "," else "."
    header = [f.strip().strip('"').lower() for f in lines[header_idx].split(delimiter)]

    rows: list[dict[str, object]] = []
    for line in lines[header_idx + 1:]:
        if not line.strip(delimiter).strip():
            continue
        fields = [f.strip().strip('"') for f in line.split(delimiter)]
        record: dict[str, object] = {}
        for name, value in zip(header, fields):
            if name in CHAIN_NUMERIC:
                record[name] = _to_float(value, decimal)
            elif name in ("option_type", "type", "expiry"):
                record["option_type" if name in ("option_type", "type") else name] = value
        if record.get("strike") is None or pd.isna(record.get("strike", float("nan"))):
            continue
        rows.append(record)

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("chain contains no rows with a numeric strike")

    if "days_to_expiry" not in frame.columns:
        if "expiry" not in frame.columns:
            raise ValueError("chain needs either days_to_expiry or expiry")
        expiry = pd.to_datetime(frame["expiry"], errors="coerce", dayfirst=False)
        frame["days_to_expiry"] = (expiry - pd.Timestamp.today().normalize()).dt.days.astype("float64")

    for column in CHAIN_NUMERIC:
        if column not in frame.columns:
            raise ValueError(f"chain is missing required column '{column}'")

    frame["option_type"] = frame["option_type"].astype(str).str.upper().str[0]
    frame = frame[frame["option_type"].isin(["C", "P"])]
    frame = frame.dropna(subset=["strike", "open_interest", "iv", "days_to_expiry"])
    return frame.reset_index(drop=True)


def dedupe(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per (date, pair), last occurrence wins, sorted by date."""
    if frame.empty:
        return frame
    return (
        frame.sort_values(["pair", "date"], kind="stable")
        .drop_duplicates(subset=["date", "pair"], keep="last")
        .reset_index(drop=True)
    )


def rebuild_store(paths: Paths) -> pd.DataFrame:
    """Reparse every CSV in data/incoming/ into the master table and persist it."""
    frames = [parse_snapshot(p) for p in sorted(paths.incoming.glob("*.csv"))]
    frame = dedupe(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame(columns=COLUMNS)
    paths.store.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(paths.store, index=False)
    except (ImportError, ValueError):
        frame.to_csv(paths.store.with_suffix(".csv"), index=False)
    return frame


def load_store(paths: Paths) -> pd.DataFrame:
    if paths.store.exists():
        return pd.read_parquet(paths.store)
    fallback = paths.store.with_suffix(".csv")
    if fallback.exists():
        return pd.read_csv(fallback, parse_dates=["date"])
    return pd.DataFrame(columns=COLUMNS)
