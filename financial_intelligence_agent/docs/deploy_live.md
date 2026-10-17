# Live operations and hosting runbook

Status: executable app, connector code, CI, container recipe and tests exist.
There is NO deployed public production environment certified by this repo.

## Local server
From the financial_intelligence_agent directory:
    python -m pip install -r requirements.txt
    export FRED_API_KEY=YOUR_OWN_KEY
    streamlit run app.py

Get FRED keys directly from the source provider, store in a secrets manager,
not a Git commit. Per-user keys can alternatively be entered temporarily
in Live Data Studio. The public NY Fed Markets and FDIC APIs used here
normally do not require authentication. Terms still apply.

External live smoke:
    python scripts/probe_public_apis.py

## Docker
    docker build -t financial-intelligence-agent -f Dockerfile .
    docker run --rm -p 8000:8000 -e FRED_API_KEY="$FRED_API_KEY" financial-intelligence-agent

The image is non-root with HTTP healthcheck. No secret ever in build args,
source files or Docker layers. For production use hosted secrets and TLS.

## Databricks App
The existing repo contains app.py, app.yaml, requirements.txt and asset bundle.
Provision an isolated Databricks App service principal and workspace deployment;
grant only read-only Gold access plus CAN USE on a dedicated SQL warehouse.
Server environment: DATABRICKS_CATALOG, DATABRICKS_HTTP_PATH, and secret-backed
FRED_API_KEY. Confirm the deployment syntax against the installed Databricks CLI.
https://docs.databricks.com/aws/en/dev-tools/databricks-apps/tutorial-streamlit

Databricks app and SQL warehouse can incur compute charges; measure them.
No asset bundle deploy, bind, or production schedule was executed here.

## Scheduled ingestion
A manual or externally schedulable atomic snapshot publisher exists:
    python scripts/refresh_live.py --source NYFED:SOFR --source NYFED:EFFR --source FRED:WALCL --days 365 --out /volume/snapshots

Volume must be durable and readable by the app identity. Publication of
LATEST.json occurs only after all fetches and manifest checks pass.
The app currently uses live on-demand API/SQL calls, not this new snapshot
serving layer. Schedule and storage access must be configured externally,
and hourly/day refresh cadence selected per data provider.

## Commercial launch prerequisites
SSO/account database, tenant isolation, entitlements and billing, provider
redistribution licenses, external API quotas, spend ceilings, rate limiting,
persistent workspaces, deletion/privacy policy, monitoring, support, reliability
metrics and verified point-in-time release metadata. No code-level PASS
alone certifies these external services. A monthly USD 1–150 cap is a
deployment planning constraint, not a measured operational expense.
