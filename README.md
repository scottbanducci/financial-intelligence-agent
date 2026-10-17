# Financial Intelligence Agent — Market Liquidity Monitor

Experimental financial data research platform built around a provider-neutral,
deterministic operator engine, ML evaluation and bounded LLM tool orchestration.

**48 operators | 24 registered ML algorithms | 4 provider adapter families |
6 UI workspaces | reproducible tests and evidence**

[Experimental branch](https://github.com/scottbanducci/financial-intelligence-agent/tree/experiment/market-liquidity-launch) |
[Draft pull request #2](https://github.com/scottbanducci/financial-intelligence-agent/pull/2) |
[Technical architecture](financial_intelligence_agent/docs/architecture.md)

## Run

    cd financial_intelligence_agent
    python -m pip install -r requirements.txt
    streamlit run app.py

Default mode is credential-free and does not make LLM requests. Model-based
tool selection is explicitly opt-in and requires an API key.

**Not live:** the UI currently operates on committed historical 2026 fixtures,
not on verified current Databricks Gold data. Real-time provider ingestion,
historically correct vintages, production service auth and Databricks Apps
deployment remain outstanding. The existing refresh job is not modified.
