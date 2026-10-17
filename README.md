# Market Liquidity Monitor — experimental portfolio build

This repository contains a Databricks financial-data pipeline and an experimental
Streamlit research UI. **The UI is a frozen historical fixture demo, not a
deployed live product.** The tool calculations, offline tests and evidence
metadata are available on the experimental branch.

## Try the historical research UI

    cd financial_intelligence_agent
    python -m pip install -r requirements.txt
    streamlit run app.py

No cloud credentials needed for the default deterministic mode.

Features: 4 bounded analytical tools; rate and balance-sheet charts; SHA-256
snapshot fingerprints; evidence-bearing calculations; fixed-source warnings;
optional LLM tool selection (requires OPENAI_API_KEY, incurs API charges).

See financial_intelligence_agent/docs/launch.md for offline testing, known
limitations, model safety and Databricks deployment steps.

## Technical boundaries

Existing Databricks job and pipeline definitions are preserved.
Historical JSON and CSV fixtures do not imply current releases or verified
publication-time vintages. The application has not yet been deployed and
validated in Databricks Apps. Live serving, snapshot publication, complete
historical provenance, authentication and uptime monitoring remain planned.

The original description of completed production evaluation and deployment
was aspirational and is not claimed as an accomplished result here.
