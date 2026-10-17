# Architecture: Financial Intelligence Workbench

## Definition vs experimental build

The target is a large extensible financial analytical toolset, used by LLMs
only through deterministic, auditable operators, across FRED, New York Fed,
FFIEC and other provider series. This branch is a fixed historical demo and
an increasingly reusable library. It is NOT a live production integration.

## Four passes, verifiable in Git history

1. Provider-independent observation Series and 48 deterministic operators,
   grouped into 22 unary transformations, 15 rolling statistics and 11
   cross-series calculations. Strict unit/frequency checks and no implicit fill.
2. FRED/ALFRED vintage JSON, mapped NY Fed JSON, FFIEC bulk tabular and CSV
   parsers. ML registry with 13 regression, 7 classification and 4
   unsupervised algorithms; chronological evaluation and baselines.
3. Dark-themed six-page Streamlit UI: command center, funding desk,
   operator lab, model lab, analyst agent and data catalog. Full CI suite.
4. Correct calendar YoY, propagated availability restrictions, real ML
   target-date purging, four actual unsupervised algorithms, bounded
   LLM-driven operator DAGs, HTTPS redirect rejection and checksum-verified
   snapshot manifest plus atomic pointer publication.

## Trust and provenance

Adapter -> typed observation series -> deterministic operator plan ->
verified execution trace -> UI. The model selects operations and data keys
but never supplies computed values or arbitrary SQL/Python. A general
analytical plan allows at most 12 operations, and each source series has
at most 10,000 rows. A manifest fingerprints physical bytes; fingerprints
alone do not prove correct source release times or revisions.

Inputs separate observation dates and available-at dates. Without
publication information, point-in-time as-of selection refuses to infer
historical availability. Mixed frequencies and measurement conventions
require explicit transformations. Derived time series retain source
fingerprints for auditability.

## Production gates not completed

- Live provider ingestion and schema version tracking
- True release/vintage-aware Silver and Gold validation in Databricks
- Versioned serving snapshots wired to successful scheduled ETL jobs
- An authenticated Databricks App, working public URL and access controls
- Query/model quotas, telemetry, on-call logging and monthly cost validation
- Release-date-correct historical backtesting and external predictive validation
- Load and browser tests against the actual deployed service

The initial fixture dates are 2026-09-30 to 2026-10-10. They must not
be presented as live or current data.

## Verified references

FRED observations: https://fred.stlouisfed.org/docs/api/fred/series_observations.html

New York Fed APIs: https://markets.newyorkfed.org/static/docs/markets-api.html

FFIEC bulk downloads: https://cdr.ffiec.gov/public/pws/downloadbulkdata.aspx

Databricks Apps Streamlit: https://docs.databricks.com/aws/en/dev-tools/databricks-apps/tutorial-streamlit

## Run

From financial_intelligence_agent directory:

    python -m pip install -r requirements.txt
    python -m unittest discover -s tests -p "test_*.py" -v
    streamlit run app.py

Github Actions installs Python dependencies, runs offline tests, and attempts
six-page Streamlit headless rendering. No live Databricks auth required.

Monthly target budget is USD 1 to 150, but actual hosting/model costs are
not established. LLM planning is off by default and should not be enabled
on an unrestricted public endpoint.
