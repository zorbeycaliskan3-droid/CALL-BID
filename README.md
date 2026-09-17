# FX vol regime and bias dashboard

Offline Streamlit app over Bloomberg FX volatility snapshots exported from Excel.
Read-only: no broker connection, no order placement, no network calls.

## Run

python -m streamlit run C:\Users\zorbey.caliskan\fxvol_dashboard\app.py

```
pip install -r requirements.txt
streamlit run app.py
```

Drop each morning's CSV export into `data/incoming/`. The Parquet store at
`data/store/snapshots.parquet` is rebuilt from those raw files on every run, so
the CSVs stay the source of truth. Rows are keyed on `(date, pair)` and deduped
keeping the last.

## Layout

```
fxvol/
  config.py    columns, pip-size rules, tolerances
  io.py        dialect sniffing, parsing, store read/write
  vol.py       sigma, expected move, percentile, z-score, term structure
  signals.py   regime, follow-through, positioning skew, sizing
app.py         Streamlit UI only - no calculation logic
tests/
data/          sample/ incoming/ store/
```

`fxvol/` never imports streamlit, so the core is reusable from an options tool.

## Units

Vols and realised vol are **percent points**: `6.23` means 6.23%, not 0.0623.
Risk reversal and butterfly are vol points, signed for the RR. Percentiles are
fractions 0-1. Stops and expected moves are pips, using pip size 0.0001 (0.01
for any pair containing `JPY`, overridable in the sidebar).

## Parsing

Both export dialects are handled without being told which is which:

- Excel "Save As CSV": UTF-8 BOM, CRLF, `;` delimiter, `,` decimal.
- VBA macro: UTF-8, `,` delimiter, `.` decimal.

The header is located by finding the line whose first field is `date`; the
delimiter and decimal convention are inferred from that line. Title rows above
and note rows below are discarded silently. Only rows whose first field parses
as a date are kept.

## Recompute check

`sigma_daily_pct`, `sigma_daily_pips`, `exp_abs_move_pips`, the three stops and
`iv_minus_rv` are recomputed in Python from `spot`, `iv_on`, `iv_1m`, `rv_30`
and the pip size, then compared with the Excel values. Any field off by more
than 1% raises a visible warning in the UI naming both values — this catches a
broken Excel reference.

## Percentile source

Under 60 stored rows for the pair the app uses the Excel/Bloomberg percentiles
and z-score; at 60 or more it computes them locally over the sidebar lookback
window. The source in use is always displayed.

## Directional signal

Only the risk reversal feeds direction, weighting the 5-day change above the
level. ATM implied vol sizes the expected move and never its sign, so no ATM
field or percentile of one enters the lean. The output is labelled positioning
skew, not a forecast.

## Tests

```
pytest
```

Covers both CSV dialects parsing identically, junk-row discard, dedupe, the
formulas against hand-checked numbers, the recompute warning, regime
boundaries, and the percentile-source switch at 60 rows.
