# Financial intelligence analyst SaaS — implementation review

Vision: A commercial research workbench allowing analysts to retrieve US banking,
market/funding and Fed data, use a large source-neutral operator/ML library,
compose charts, archive reusable work, download CSV and high-quality labeled
images, and monitor indicators with safe LLM-driven analytical planning.

## Source and engine review

| Need | Status | Evidence or key limitation |
|---|---|---|
| FRED exploration | Built | Keyed observations, >30 curated starting IDs, full dynamic FRED catalog search; no historical as-of release guarantee |
| NY Fed funding rates | Live verified | SOFR API returned 8 observations in CI |
| NY Fed broker dealers | Live verified for sample | Publisher catalog returned 1,539 entries and actual Treasury-position series returned 51 observations |
| All New York Fed Markets APIs | Not complete | Endpoint schema mapping for other product families remains |
| FDIC bank-level data | Live verified sample | Actual bank financial API returned 3 observations; matches by FDIC certificate |
| FFIEC | Partial | Downloaded bulk tabular parser and FDIC-derived data; automated CDR extraction absent |
| Databricks Gold | Implemented adapter | Read-only H4.1, H8 and rates query contract; no actual workspace query yet |
| Analytical operators | 48 implemented | Typed, unit-aware, date-aligned, safe bounded DAG |
| ML library | 24 registered | Purged chronological evaluation and baselines; empirical predictive benefit unproven |
| Bank contraction breadth | Implemented | Matched FDIC certificates, attrition and merger bias disclosed |
| Vintage-correct multisource backtests | Incomplete | Publication-time data unavailable for some source families |

## Analyst UX review

| Need | Status | Next task |
|---|---|---|
| Data search | Available per provider | Unified index and custom data collections |
| Interactive visualizations | Plotly line/bar/scatter/area | Dashboards, annotations and templates |
| Professional graphic export | PNG and SVG including axes/units/title/source | Visual regression baselines and rich styling |
| CSV export | Available with spreadsheet formula escaping | Server-side very large data exports |
| Reproducibility | JSON source fingerprints, research ZIP, validated chart recipes | Revision-aware published datasets |
| Workspace portability | Checksum-verified JSON | Durable per-user account workspaces and cloud sharing |
| Signals | On-demand threshold checks and provenance | Recurring scheduler, notifications, notification history |
| Source-neutral LLM plans | Safe JSON DAG, 4 macro tools | Automated source discovery, approval and provider quotas |

## Paid SaaS operating review — currently BLOCKED

P0 blockers prior to charging users:
1. No actual deployed/public app URL with production TLS and uptime controls.
2. No account identity/SSO, tenant separation, permission tests or secure cloud persistence.
3. No payment plans, entitlements, billing events, quotas or user-level usage ledger.
4. No verified commercial publisher licensing / redistributable-data agreements.
5. Refresh utility exists but is NOT wired into a verified production schedule.
6. No full time-of-publication/backtest correctness guarantee or complete revision metadata.
7. Source endpoint agreements and rate limits not evaluated for many concurrent analysts.
8. No complete app/browser, performance, accessibility, threat-model or tenant isolation tests.
9. No known, measured monthly cost for an always-on hosted public product.

Recommended constrained pilot: authenticated, read-only data/chart research on
FRED/NYFed/FDIC plus governed Gold, with all exports, strict quotas, sourcing
caveats and a small beta cohort. Do not sell model forecasting accuracy.

## Final architecture and correctness review

- Source adapters do network calls only on deliberate user actions or explicit
  refresh jobs; LLM plans cannot generate arbitrary SQL/network destinations.
- Calculations require compatible units and align dates exactly. No silent fill.
- FDIC company-level breadth uses a matched bank denominator, not aggregate
  loan-category counts. Mergers and survivor bias are disclosed.
- Published snapshots are checked and pointers are atomically advanced only
  after full content retrieval. No application serving switch has been deployed.
- PNG/SVG figure and CSV include source axis labels. Research ZIP and chart
  recipes include fingerprinted provenance and no API secrets.
- CI compiles code, runs unit and integration simulations, renders seven UI
  workspaces and performs advisory live public probes (not blocking source SLA).
- Standalone Streamlit sessions are not tenant authentication. A deployable
  Dockerfile is infrastructure packaging, not proof of a hosted product.

Evidence: GitHub Actions workflow and draft pull request #2. Keep main
untouched until licensing, deployment, account controls and analyst beta pass.
