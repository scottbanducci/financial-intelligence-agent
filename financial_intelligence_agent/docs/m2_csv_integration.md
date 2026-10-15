# M2 integration: fixed Databricks rates export

## Scope

This adds a **local, credential-free CSV reader** for an existing funding analytical tool. It does **not** make the tool query Databricks live, publish a production snapshot, run an LLM, or complete the historical revision audit. None of the existing notebooks, job YAML or M2 analytical kernel files is modified.

The included CSV is an unmodified user-exported sample of `fed_monitor_dev.gold.rates_daily` for 2026-06-01 through 2026-10-10 inclusive. Its observations might have been revised since export. The `snapshot_id` is an SHA-256 fingerprint of the raw CSV bytes; it does not attest to a Databricks ingestion run, an export timestamp, or true historical vintage.

## Install

Unzip the **new-files-only** archive into your existing Databricks bundle root (the folder containing `databricks.yml` and `pyproject.toml`), preserving paths. Do not extract into `tests/` or the outer Git repository root.

Additions:

- `src/financial_intelligence_agent/data/__init__.py`
- `src/financial_intelligence_agent/data/csv_rates.py`
- `tests/test_csv_rates_reader.py`
- `tests/test_funding_sql_parity.py`
- `fixtures/rates_daily_2026_06_01_to_2026_10_10.csv`
- `scripts/run_funding_csv.py`
- `docs/m2_csv_integration.md`

## Test

From the bundle root in PowerShell:

```powershell
python -m unittest discover -s tests -p "test_csv_rates_reader.py" -v
python -m unittest discover -s tests -p "test_funding_sql_parity.py" -v
python -m unittest discover -s tests -p "test_funding_conditions.py" -v
python -m unittest discover -s tests -p "test_verified_briefing.py" -v
```

The two new files define nine unit test methods, including SQL-reference parity across three cutoffs and four spreads (12 spread comparisons in the SQL test). Unlike `pytest`, `unittest` does not automatically load the original Databricks-connecting `tests/conftest.py`.

## Run the fixed historical analysis

```powershell
python scripts/run_funding_csv.py --as-of 2026-10-10 --lookback-days 30 --summary
```

Omit `--summary` for full, evidence-bearing JSON, with stable evidence IDs keyed to the SHA-256 of the CSV:

```powershell
python scripts/run_funding_csv.py --as-of 2026-10-10 --lookback-days 30 > funding_result.json
```

### Verified sample results

- CSV: 132 unique daily date rows, 2026-06-01 through 2026-10-10.
- 2026-10-10 requested cutoff resolves to 2026-10-08 latest complete SOFR/EFFR/IORB observation.
- 30-calendar-day window target/baseline: 2026-09-08; 23 complete core observations in the inclusive window.
- SOFR−IORB: −1 bp → −3 bp (change −2 bp).
- SOFR−EFFR: +1 bp → −1 bp (change −2 bp).
- EFFR−IORB: −2 bp → −2 bp (change 0 bp).
- DTB3−EFFR: +17 bp → +17 bp (change 0 bp); maximum +36 bp on 2026-09-16.
- 2026-10-09 and 2026-10-10 have IORB but null SOFR/EFFR, which is why the resolved end date is earlier.

The 30 calendar days are measured **back from the resolved latest complete observation**, not automatically from the requested cutoff. When a 30-day target falls on a nonpublication day, the baseline is the most recent complete observation **on or before** the target and may fall outside the summary window; this is reported explicitly.

## Independent SQL reference

`tests/test_funding_sql_parity.py` loads the original CSV directly into SQLite (not via the new reader or analytical primitives), calculates date bounds and spreads with SQL, and checks parity at three cutoffs. This is **offline SQL parity on the exported observations**, not a query against the live Gold table.

To cross-check core bounds, values and observation count **against your live Databricks Gold table**, run the following SQL in Databricks SQL Editor. It should match the CSV results *if the live table has not changed since export*:

```sql
WITH complete AS (
  SELECT observation_date, sofr, effr, iorb, dtb3
  FROM fed_monitor_dev.gold.rates_daily
  WHERE observation_date <= DATE '2026-10-10'
    AND sofr IS NOT NULL AND effr IS NOT NULL AND iorb IS NOT NULL
),
resolved AS (
  SELECT MAX(observation_date) AS end_date FROM complete
),
bounds AS (
  SELECT end_date, date_sub(end_date, 30) AS target_start
  FROM resolved
),
start_point AS (
  SELECT MAX(c.observation_date) AS baseline_date
  FROM complete c CROSS JOIN bounds b
  WHERE c.observation_date <= b.target_start
),
ending AS (
  SELECT c.* FROM complete c CROSS JOIN bounds b
  WHERE c.observation_date = b.end_date
),
beginning AS (
  SELECT c.* FROM complete c CROSS JOIN start_point p
  WHERE c.observation_date = p.baseline_date
),
counts AS (
  SELECT COUNT(*) AS core_observation_count
  FROM complete c CROSS JOIN bounds b
  WHERE c.observation_date BETWEEN b.target_start AND b.end_date
)
SELECT
  b.target_start, p.baseline_date, b.end_date,
  n.core_observation_count,
  ROUND((s.sofr - s.iorb) * 100, 2) AS baseline_sofr_iorb_bp,
  ROUND((e.sofr - e.iorb) * 100, 2) AS end_sofr_iorb_bp,
  ROUND((s.sofr - s.effr) * 100, 2) AS baseline_sofr_effr_bp,
  ROUND((e.sofr - e.effr) * 100, 2) AS end_sofr_effr_bp,
  ROUND((s.effr - s.iorb) * 100, 2) AS baseline_effr_iorb_bp,
  ROUND((e.effr - e.iorb) * 100, 2) AS end_effr_iorb_bp,
  ROUND((s.dtb3 - s.effr) * 100, 2) AS baseline_dtb3_effr_bp,
  ROUND((e.dtb3 - e.effr) * 100, 2) AS end_dtb3_effr_bp
FROM bounds b
CROSS JOIN start_point p
CROSS JOIN beginning s
CROSS JOIN ending e
CROSS JOIN counts n;
```

Expected in unchanged snapshot: baseline 2026-09-08, end 2026-10-08, count 23, four baseline spreads (−1, +1, −2, +17) bp and end spreads (−3, −1, −2, +17) bp.

## Remaining work and release boundary

1. Obtain live SQL parity from the Databricks query above (optional if you want to postpone live query testing).
2. Complete M1's silver revision-selection, time-convention, and source-lineage audit before making general historical guarantees.
3. Build a versioned Parquet/DuckDB serving snapshot with manifest and atomic publication, rather than having the public API read a manually exported CSV.
4. Pin snapshot per request and attach a proper source-run ID, schema version, file checksums and fetch/build times.
5. Only then expose the bounded tool schema to the LLM, once offline and serving parity are established.
