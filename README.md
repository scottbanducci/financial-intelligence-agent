# Financial Intelligence Agent — experimental analyst workbench

Multi-source financial charting and deterministic research tools on
Databricks and Streamlit.

## Run

From financial_intelligence_agent:

    python -m pip install -r requirements.txt
    python -m streamlit run app.py

For FRED, copy .env.example to a local untracked .env and supply
FRED_API_KEY or set that key in the server environment.

## Data acquisition in Live Data Studio

**FRED tab:** Searches a locally indexed release/series catalog. At
startup, the application enumerates FRED releases and pages through
each release's member series (not through categories). Indexing is
resumable and progressive; the UI reports whether it is complete.

**NY Fed tab:** Searches analyst datasets rather than arbitrary endpoints:
Agency MBS operations, central-bank liquidity swaps, reference rates
and volumes, repo/reverse repo (combined and separately), Treasury
purchases/sales and all indexed Primary Dealer Statistical series.
Operations chart daily additive amounts split by operation type or
currency, with original records and CSV downloads shown underneath.
Reference rates show rate line(s) and volume bars with a second axis.
Plotly legends toggle individual traces.

**Other tabs:** FDIC bank-level financials, bank contraction breadth,
read-only Databricks Gold, and manually uploaded FFIEC/CSV files.

The analytical library includes 48 deterministic operators, 24 ML
algorithm registrations, LLM-bounded analytical planning, chart PNG/SVG,
CSV and research-bundle exports.

[Detailed source mapping and indexing](financial_intelligence_agent/docs/source_index_and_operations.md)
[Deployment instructions](financial_intelligence_agent/docs/deploy_live.md)

This is an experimental branch, not yet a deployed and validated paid
multi-user SaaS. The original 18-series FRED ingestion job is unchanged.
