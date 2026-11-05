# Release-first FRED and dataset-first NY Fed discovery

The Live Data Studio now has separate FRED and NY Fed tabs. FDIC, Databricks
Gold, FFIEC and CSV import remain in adjacent tabs.

## FRED metadata discovery

On app startup the index service requests /fred/releases in pages, then uses
/fred/release/series for every listed release, paging until all series
metadata for that release have been collected. Release ID, name and
series membership are stored in SQLite alongside unique series metadata.
A series may belong to more than one release. Categories are not queried.

The SQLite index is at .cache/publisher_catalog.sqlite3. It is searchable
locally while a background job crawls. Cold start is deliberately
nonblocking: an entire FRED crawl can involve many requests and does not
finish instantly. The UI reports release coverage and incomplete status,
and stores page offsets for a resumable crawl.

The server must have FRED_API_KEY configured. Copy .env.example to an
untracked local .env, or use a production secret manager. No key is
collected from the browser or committed to GitHub.

Official sources:
https://fred.stlouisfed.org/docs/api/fred/releases.html
https://fred.stlouisfed.org/docs/api/fred/release_series.html

## NY Fed analyst data products

The local search index now contains seven named data products, plus each
Primary Dealer Statistical series discovered from the official
/api/pd/list/timeseries.json metadata. The previous NY_ENDPOINT API template
entries are purged during startup migration and no longer exposed.

Dataset -> fixed JSON endpoint -> daily analytical result:

- Agency Mortgage-Backed Securities operations:
  ambs/all/results/summary/search.json. Fetch ALL operation types,
  SUM accepted par amount per day/type; also provide an all-type total.
- Central-bank liquidity swaps:
  fxs/all/search.json. Use all operation types and normal date filters,
  no dateType or counterparties filter. SUM by trade date and currency.
  Currency charts are separate; never sum values across currencies.
- Reference rates:
  rates/all/search.json. Plot percentRate as lines and volumeInBillions as
  bars on the opposite Y axis. Rates/volumes can be toggled via Plotly legend.
- Repo and reverse repo operations:
  rp/results/search.json. Expose three searchable products: both, Repo,
  and Reverse Repo. The latter two send operationTypes=Repo or
  operationTypes=Reverse%20Repo; always filter the actual returned
  operationType client-side because API filtering can be unreliable.
  For each day, sum totalAmtAccepted separately per operation type.
  For a single-type view, also compute security class amounts from each
  operation's details[].amtAccepted when they reconcile with the operation
  totalAmtAccepted (do NOT attribute a whole multi-class operation to each
  security class).
- Treasury securities operations:
  tsy/all/results/summary/search.json. Fetch all, summary, JSON. Sum
  totalParAmtAccepted per operation date, split P (Purchases) and S (Sales).
- Primary Dealer Statistics: retrieve the latest published keyid list and
  human-readable descriptions, then query /api/pd/get/{keyid}.json for
  historical observations. Seriesbreak metadata does not constrain the
  historical start date.

Guide Sheets, Primary Dealer Market Share, Securities Lending and SOMA
Holdings were excluded as requested.

Operational charts keep underlying wide publisher records below daily
aggregates, with CSV export. Legend entries toggle individual curves
(including AMBS all-type total and Repo security-type components).

Publisher series discovery is only metadata: newly found series observations
are loaded on demand and are not inserted automatically into the original
18-series scheduled Databricks Bronze/Silver/Gold pipeline.

## Files

- live/catalog_index.py: SQLite FRED releases/series and NY Fed catalog
- live/nyfed_datasets.py: data-product definitions and API transformations
- live/studio.py: source tabs, interactive charts and underlying rows
- live/providers.py: FRED observations and primary-dealer stat series
- tests/test_catalog_index.py: release paging, resumption and search
- tests/test_nyfed_datasets.py: aggregation, currency, rates, security breakdown
- scripts/probe_research_datasets.py: advisory external API integration checks

Live schema probing has verified AMBS under ambs.auctions, swaps under
fxSwaps.operations, Treasury under treasury.auctions, repo under
repo.operations with nested details[].securityType/amtAccepted, and
reference rates under refRates.

Production caveats: this code is not a commercial SaaS deployment. FRED
metadata may be incomplete until the crawler finishes. Operations with
missing accepted amounts are preserved in raw rows but excluded from
daily sums, with a warning. Dates are observation/trade dates, not
audited release timestamps.
