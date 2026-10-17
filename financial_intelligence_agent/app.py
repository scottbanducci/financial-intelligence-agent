"""Streamlit historical market liquidity demo."""
import sys
from pathlib import Path
import pandas as pd
import streamlit as st
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.agent.dispatcher import Engine, TOOLS
from financial_intelligence_agent.data.csv_rates import CsvRatesReader
from financial_intelligence_agent.tools.snapshot_insights import MacroSnapshot, FROZEN_CUTOFF
st.set_page_config(page_title="Market Liquidity Monitor",page_icon="◈",layout="wide")
@st.cache_resource
def load():
    return Engine(MacroSnapshot(ROOT/"fixtures/briefing_2026_10_10.json"),CsvRatesReader(ROOT/"fixtures/rates_daily_2026_06_01_to_2026_10_10.csv"))
engine=load()
st.sidebar.caption("FINANCIAL INTELLIGENCE")
view=st.sidebar.radio("Explore",["Overview","Funding markets","Ask the agent","Methodology"])
st.sidebar.warning("Historical fixture, not live data. Cutoff: 2026-10-10.")
use_llm=st.sidebar.checkbox("Model-selected tool routing",False,help="Paid API usage; requires OPENAI_API_KEY.")
model=st.sidebar.text_input("Model","gpt-4.1-mini") if use_llm else "gpt-4.1-mini"
st.title("Market Liquidity Monitor")
st.caption("Federal Reserve balance sheet · Funding markets · Bank credit")
st.info("Historical research demo. Source observation dates differ; not live Databricks or a trading signal.")
if view=="Overview":
    fed=engine.invoke("get_fed_liquidity")
    bank=engine.invoke("get_bank_credit")
    funding=engine.invoke("get_funding_conditions")
    a,b,c,d=st.columns(4)
    a.metric("Fed total assets (USD B)",f'{fed["levels"]["total_assets"]:,.1f}')
    b.metric("Reserves (USD B)",f'{fed["levels"]["reserve_balances"]:,.1f}',f'{fed["weekly_changes"]["reserve_balances_wow"]:+.1f}B weekly')
    c.metric("Bank credit (USD B)",f'{bank["levels"]["bank_credit"]:,.1f}',f'{bank["bank_credit_yoy_pct"]:+.2f}% YoY')
    s=funding["spreads"]["sofr_iorb_spread_bps"]
    d.metric("SOFR minus IORB",f'{s["end_bps"]:+.1f} bp',f'{s["change_bps"]:+.1f} bp / 30d')
    l,r=st.columns([3,2])
    with l:
        st.subheader("Balance-sheet levels")
        df=pd.DataFrame({"Series":["Fed assets","Reserves","Treasury General Account","ON RRP"],"USD billions":[fed["levels"][x] for x in ("total_assets","reserve_balances","tga_balance","on_rrp")]})
        st.bar_chart(df.set_index("Series"),horizontal=True)
        st.caption("TGA weekly average and point-date balances have different conventions; do not add these bars.")
    with r:
        st.subheader("Evidence-backed briefing")
        st.markdown(engine.answer("Fed reserve balances")["report"])
        st.markdown(engine.answer("Bank credit")["report"])
        st.caption("Fed: "+fed["observation_date"]+" | H.8: "+bank["observation_date"])
    with st.expander("Provenance and warnings"):
        st.json({"fed":fed,"bank":bank})
elif view=="Funding markets":
    st.subheader("Observed short-term rates")
    frame=pd.DataFrame(engine.rates.read_rates_through(FROZEN_CUTOFF))
    frame["observation_date"]=pd.to_datetime(frame["observation_date"])
    for k in ("sofr","effr","iorb","dtb3"):
        frame[k]=pd.to_numeric(frame[k],errors="coerce")
    st.line_chart(frame.set_index("observation_date")[["sofr","effr","iorb","dtb3"]],y_label="Percent")
    st.caption("Gaps are unfilled; observation dates are not publication timestamps.")
    days=st.slider("Lookback (calendar days)",7,90,30)
    result=engine.invoke("get_funding_conditions",lookback_days=days)
    st.markdown(engine.answer("SOFR IORB",lookback_days=days)["report"])
    st.dataframe(pd.DataFrame([{"Spread":k,"End (bp)":v.get("end_bps"),"Change (bp)":v.get("change_bps"),"Min (bp)":v.get("min_bps"),"Max (bp)":v.get("max_bps")} for k,v in result["spreads"].items()]),hide_index=True)
    with st.expander("Detailed evidence"):
        st.json(result)
elif view=="Ask the agent":
    st.subheader("Ask the analytical agent")
    question=st.selectbox("Try",["What is SOFR versus IORB?","How have reserves changed?","What happened to bank deposits?","How fresh is the data?"])
    custom=st.text_input("Or type your question")
    lookback=st.slider("Lookback days",7,90,30)
    if st.button("Analyze",type="primary"):
        try:
            answer=engine.answer(custom.strip() or question,lookback_days=lookback,use_llm=use_llm,model=model)
            st.markdown(answer["report"])
            st.caption("Selected tool: "+answer["selected_tool"]+" | Routing: "+answer["route_mode"])
            for warning in answer["result"].get("warnings",[]):
                st.warning(warning)
            with st.expander("Trace and source evidence",expanded=True):
                st.json(answer)
        except Exception as exc:
            st.error("Analysis unavailable: "+str(exc))
else:
    st.subheader("Methodology")
    st.markdown("The demonstration uses immutable historical fixture files with SHA-256-derived IDs. Four bounded tools calculate evidence-backed values. An optional LLM selects a tool only; it cannot insert numbers or execute SQL. Historical release dates and revision vintages are not verified. H.4.1 Treasury General Account weekly averages are not synchronized with balance sheet levels. H.8 totals do not measure individual bank contraction breadth. A live deployment requires verified versioned Databricks exports, application security and runtime checks.")
    st.table(pd.DataFrame([{"Tool":name,"Purpose":description} for name,description in TOOLS.items()]))
    st.caption("Snapshot ID: "+engine.macro.id)
