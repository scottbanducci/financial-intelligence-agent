-- Silver revision-selection integration validation.
-- Run ONLY AFTER deploying and refreshing the dev pipeline.
-- Read-only: no writes or modifications. Uses your existing dev catalog.
--
-- Intended policy:
-- 1. The latest fetched record wins, including records whose value is '.'.
-- 2. Timestamps tie-break by source path and observation array position.
-- 3. The value_is_numeric expectation removes selected missing/non-numeric rows.
-- 4. Silver contains no resurrected older values and no duplicate (series,date).
--
-- WARNING: Timestamp ties across independent files are deterministically ordered,
-- but lexical file-path ordering cannot prove which FRED request was made later.

WITH expanded AS (
    SELECT
        r.series_id,
        TO_DATE(obs.date) AS observation_date,
        TO_TIMESTAMP(r.fetched_at) AS fetched_at,
        r._source_file,
        obs_index,
        TRY_CAST(obs.value AS DOUBLE) AS raw_value
    FROM fed_monitor_dev.bronze.fred_observations_raw AS r
    LATERAL VIEW POSEXPLODE(r.response.observations) p AS obs_index, obs
),
ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY series_id, observation_date
            ORDER BY fetched_at DESC NULLS LAST,
                     _source_file DESC NULLS LAST,
                     obs_index DESC
        ) AS rn
    FROM expanded
),
latest AS (
    SELECT *
    FROM ranked
    WHERE rn = 1
      AND series_id IS NOT NULL
      AND observation_date IS NOT NULL
      AND observation_date <= CURRENT_DATE()
)
SELECT
    COUNT(*) AS latest_records_checked,
    SUM(CASE WHEN l.raw_value IS NULL AND s.series_id IS NOT NULL
             THEN 1 ELSE 0 END) AS resurrected_missing_values,
    SUM(CASE WHEN l.raw_value IS NOT NULL AND s.series_id IS NULL
             THEN 1 ELSE 0 END) AS missing_valid_latest_values,
    SUM(CASE WHEN l.raw_value IS NOT NULL AND s.series_id IS NOT NULL
        AND ABS(s.value - (CASE WHEN l.series_id = 'B2310NCBA'
                                THEN l.raw_value / 1000
                                ELSE l.raw_value END)) > 1e-6
             THEN 1 ELSE 0 END) AS mismatched_values
FROM latest AS l
LEFT JOIN fed_monitor_dev.silver.fred_observations AS s
    ON s.series_id = l.series_id
   AND s.observation_date = l.observation_date;

-- Expect all mismatch counts to be ZERO after successful pipeline refresh.
-- This verifies behavior against the actual Bronze and Silver records.

SELECT
    series_id,
    observation_date,
    COUNT(*) AS duplicates
FROM fed_monitor_dev.silver.fred_observations
GROUP BY series_id, observation_date
HAVING COUNT(*) > 1
ORDER BY duplicates DESC, series_id, observation_date
LIMIT 20;

-- Expect ZERO rows.

-- Synthetic example showing why the order of operations matters:
-- The later missing value must win ranking, then be dropped.
WITH test_rows AS (
    SELECT *
    FROM VALUES
       ('SOFR', DATE '2026-09-10', TIMESTAMP '2026-09-11 09:00:00', 'file_a', 0, '3.88'),
       ('SOFR', DATE '2026-09-10', TIMESTAMP '2026-09-12 09:00:00', 'file_b', 0, '.'),
       ('EFFR', DATE '2026-09-10', TIMESTAMP '2026-09-11 09:00:00', 'file_c', 0, '3.80'),
       ('EFFR', DATE '2026-09-10', TIMESTAMP '2026-09-12 09:00:00', 'file_d', 0, '3.81')
    AS t(series_id, observation_date, fetched_at, source_file, obs_index, raw_value)
),
ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY series_id, observation_date
               ORDER BY fetched_at DESC, source_file DESC, obs_index DESC
           ) AS rn
    FROM test_rows
)
SELECT series_id, observation_date, raw_value
FROM ranked
WHERE rn = 1
  AND TRY_CAST(raw_value AS DOUBLE) IS NOT NULL
ORDER BY series_id;
-- Expected: one EFFR row with value 3.81; no SOFR row.
