# Historical Market Liquidity Monitor

This is a historical offline demonstration. It is not a live Databricks deployment.

## Run locally

From financial_intelligence_agent directory:

    python -m pip install -r requirements.txt
    streamlit run app.py

No credentials are needed in default deterministic mode. The optional model
selector requires OPENAI_API_KEY and incurs charges. Do not enable on a public
unrestricted endpoint without authentication and spend limits.

## Offline tests

Set PYTHONPATH=src, then run:

    python -m unittest discover -s tests -p "test_agent_dispatcher.py" -v
    python -m unittest discover -s tests -p "test_funding_conditions.py" -v
    python -m unittest discover -s tests -p "test_csv_rates_reader.py" -v
    python -m unittest discover -s tests -p "test_funding_sql_parity.py" -v
    python -m unittest discover -s tests -p "test_verified_briefing.py" -v
    python -m unittest discover -s tests -p "test_fredpipe_revision_contract.py" -v

The pre-existing pytest conftest initializes Databricks Connect; unittest
is the intended offline validation path.

## Next release gates

- Validate against live Databricks Gold and audit revision/publication timing.
- Publish versioned atomic snapshots with manifests and checksums.
- Replace frozen fixtures with read-only snapshot serving adapters.
- Configure a new isolated Databricks App using app.py, app.yaml,
  requirements.txt and the included local source package.
- Test real deployment, authorization, freshness, alerts, budget and uptime.
- Do not imply production or live-feed readiness before these gates pass.

The existing Databricks jobs and pipelines are intentionally untouched.
