# Startup catalog, API secrets, and operation-level data

## Set up FRED credentials (server only)

Do not commit your FRED API key to a GitHub branch. The Live Data Studio
no longer offers any API key input control or curated series selector.

On Windows PowerShell from the financial_intelligence_agent directory:

    Copy-Item .env.example .env
    notepad .env
    python -m streamlit run app.py

In .env, replace the FRED_API_KEY placeholder with the actual key. The app
loads the untracked local .env on startup, and production should use a managed
server secret. .env is excluded from the Docker build and ignored by Git.
This commit deliberately contains NO user's FRED key.

## Catalog discovery and search

FRED does not expose a single endpoint listing every live series. It exposes
a category hierarchy and paginated series listings. On startup, the app:
- Seeds a local SQLite index of known reference series and NY Fed endpoints.
- Fetches New York Fed primary-dealer keyid/description metadata.
- Fetches the NY Fed OpenAPI YAML to index published GET JSON endpoint
  templates. Templates are not automatically numerical time series.
- Incrementally traverses FRED categories, fetching 1,000 series metadata
  entries per page, deduplicating by ID, and checkpointing completed nodes.

This scan runs in a background thread to prevent page-launch delays and
request spikes. Database: .cache/publisher_catalog.sqlite3. Searches query
SQLite locally, not the remote APIs. A fresh install reports INCOMPLETE
FRED index status until all categories have been processed. Partial,
failed, and missing-key cases are clearly marked.

An all-FRED crawl may take a considerable number of source requests. The
app must not claim completeness before its category queue is exhausted.
A missing FRED_API_KEY prevents only FRED crawling, not public NY Fed use.

NY Fed Markets APIs provide several fundamentally different data structures:
reference rates, individual primary-dealer statistical time series,
operation-level records, and holdings/summary datasets. Not every GET
endpoint returns a simple series; required path parameters and schemas
must be validated for individual endpoints.

References:
https://fred.stlouisfed.org/docs/api/fred/category_series.html
https://fred.stlouisfed.org/docs/api/fred/series_search.html
https://markets.newyorkfed.org/static/docs/markets-api.html

## Analyst operations and data tables

Repo results are usually a repo.operations[] collection. Individual rows
include operationDate, operationType, totalAmtAccepted and
totalAmtSubmitted. Those amounts are USD (e.g. 200000000 = $200 million),
NOT values in millions of dollars.

The operation chart sums ADDITIVE monetary amounts by operation date and
operation type, preserving separate Repo and Reverse Repo series. Missing
amounts fail validation. Rates and prices are not summed. The full original
publisher-record table appears below the daily aggregate chart and can be
exported separately as CSV.

Primary Dealer statistical keyids, by contrast, are already individual
series with their own observation frequencies. They are retrieved and
displayed without adding together unrelated series.

More complex NY Fed API families remain available via explicitly mapped
publisher endpoint templates; this does not imply schema validation for
every individual NY Fed endpoint.

## Testing

Tests cover local catalog search, FRED tree pagination/checkpoints, NY Fed
metadata, daily operation-type sums, unaggregated detail retention,
input validation and the existing chart and analytical functionality.
Headless UI tests disable startup network warming via
ANALYST_DISABLE_CATALOG_WARM=1.

Live FRED full-catalog crawling cannot be validated without an actual
server-side key and network-enabled environment.
