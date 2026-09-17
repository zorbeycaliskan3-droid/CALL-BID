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
