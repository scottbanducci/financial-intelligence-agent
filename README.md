# Liquidity Intelligence · Financial Data Studio

An experimental analyst-first financial research platform with **live on-demand
US financial data, deterministic computation, professional chart exports,
an extensible data-provider interface and bounded LLM/ML workflows.**

[Browse experimental branch](https://github.com/scottbanducci/financial-intelligence-agent/tree/experiment/market-liquidity-launch)
· [Draft PR #2](https://github.com/scottbanducci/financial-intelligence-agent/pull/2)
· [Commercial readiness audit](financial_intelligence_agent/docs/commercial_readiness.md)
· [Live deployment runbook](financial_intelligence_agent/docs/deploy_live.md)

## Functional overview

**Live Data Studio** — Search FRED's catalog with a user/server key, fetch
NY Fed money-market rates, published primary-dealer aggregates and bounded additional Markets API endpoint mappings (including repo operations),
query FDIC banking statistics, derive bank-level credit contraction breadth,
load your Databricks Gold H.4.1/H.8/rates tables with read-only permissions
or import FFIEC tabular bulk data and arbitrary CSV.

**Charts** — Compare up to 12 series with unit validation; create line, bar,
area or scatter charts; index dissimilar units; edit titles, dates and
subtitles. Download **CSV, high-resolution PNG, scalable SVG, provenance JSON,
reproducible research ZIP and saved chart recipes**.

**Analysis** — 48 registered deterministic time-series operators, explicit
resampling and unit conversion, 24 registered classical ML approaches with
walk-forward tests and baseline metrics, LLM-selectable bounded operator
plans and standalone macro tools. Threshold alerts can be evaluated and
exported; scheduled delivery is not deployed.

**Reproducibility** — SHA-256 fingerprinted source data, portable audited
workspaces, immutable snapshot publishing primitives, research ZIPs and
source/measurement caveats.

## Start locally

From financial_intelligence_agent:

    python -m pip install -r requirements.txt
    streamlit run app.py

Without credentials, the live NY Fed/FDIC public sources should be available
when their endpoints are reachable. FRED requires FRED_API_KEY in server env,
or analysts can provide their own key in the studio. Databricks Gold requires
configured DATABRICKS_HTTP_PATH, DATABRICKS_CATALOG and workspace auth.

Docker:

    docker build -t liquidity-intelligence .
    docker run --rm -p 8000:8000 liquidity-intelligence

## Verification and limitations

GitHub Actions runs source/ML/UI/export unit and contract tests, seven
headless Streamlit workspace renders, Docker image build and advisory live
source probes. The NY Fed sample probes verified 1,539 dealer metadata
entries and 51 actual Treasury-dealer observations; these do not guarantee
every identifier has valid recent observations.

**This is not a deployed, sellable production SaaS yet.** Missing verified
public URL/uptime, tenant-scoped SSO and storage, subscriptions, provider
redistribution licenses, comprehensive point-in-time vintages and deployed
scheduled snapshot refresh. The historical Command Center tutorial is
still pinned to its October 2026 fixture; the Live Data Studio must be used
for on-demand current data.

Existing Databricks jobs/pipelines and the GitHub main branch are untouched.
