"""Stateful analyst Chart Studio: live series, bank filings, operators and exports.

Network calls happen only on explicit actions. Streamlit session_state isolates
browser sessions (but is not a multi-tenant account or durable workspace).
"""
from __future__ import annotations
import json
import os
import re
from dataclasses import replace
from datetime import date,timedelta,datetime,timezone
from pathlib import Path
import pandas as pd
import streamlit as st

from financial_intelligence_agent.live.fdic import bank_financial,FIELDS
from financial_intelligence_agent.live.providers import (
    catalog,fetch_series,dealer_catalog,Descriptor,FetchResult,FRED_PRESETS)
from financial_intelligence_agent.live.workspaces import export_workspace,import_workspace
from financial_intelligence_agent.live.charts import (
    ChartSpec,prepare,interactive_chart,image_bytes,csv_bytes,metadata_bytes,MODES,STYLES)
from financial_intelligence_agent.engine.operators import Series,execute,OPERATOR_SPECS
from financial_intelligence_agent.engine.normalization import convert_units,resample,align_date_convention,UNITS,ORDER,AGGREGATIONS
from financial_intelligence_agent.engine.adapters import CsvSeriesAdapter,FFIECAdapter
from financial_intelligence_agent.agent.operator_planner import AnalyticalService

@st.cache_data(ttl=900,max_entries=80,show_spinner=False)
def _cached_fetch(series_id:str,start:str,end:str):
    return fetch_series(series_id,start,end)

@st.cache_data(ttl=3600,max_entries=1,show_spinner=False)
def _cached_dealers():
    return dealer_catalog()

def _state():
    if "analyst_workspace" not in st.session_state:
        st.session_state["analyst_workspace"]={}
    return st.session_state["analyst_workspace"]

def _entry_desc(key:str,series:Series)->Descriptor:
    return Descriptor(key,key,series.source,series.unit,series.frequency,
                      "Uploaded or calculated series","User session",series.convention)

def _add(key:str,series:Series,descriptor:Descriptor,retrieved:str="",warnings:str=""):
    if len(_state())>=80 and key not in _state():
        raise ValueError("Workspace capacity 80 series; remove unused series")
    _state()[key]={"series":series,"descriptor":descriptor,
                   "retrieved":retrieved,"warning":warnings}

def _load_public_series():
    st.markdown("#### Live public datasets")
    presets=catalog()
    choices={d.label+"  ·  "+d.id:d.id for d in presets}
    choice=st.selectbox("Curated publisher series",list(choices),key="live_choice")
    query=st.text_input("Search the complete FRED series catalog",placeholder="e.g. commercial-bank loans")
    user_fred_key=st.text_input("Your FRED API key (session only; optional when configured server-side)",
                                type="password",key="studio_fred_key")
    if st.button("Search FRED",key="fred_search_button"):
        try:
            from financial_intelligence_agent.live.providers import fred_search
            st.session_state["fred_search_result"]=fred_search(query,api_key=user_fred_key or None)
        except Exception as exc:st.error("Search unavailable: "+str(exc))
    matches=st.session_state.get("fred_search_result",[])
    if matches:
        selected_search=st.selectbox("Publisher search results",matches,
            format_func=lambda d:(d.label+" · "+d.id+" · "+d.unit)[:190],
            key="fred_search_result_select")
        st.caption("Select a search result and click Fetch; its reported units/frequency are retained.")
    x,y,z=st.columns([1,1,1])
    start=x.date_input("From",date.today()-timedelta(days=365*3),key="live_start")
    end=y.date_input("Through",date.today(),key="live_end")
    custom=z.text_input("FRED ID (optional)",placeholder="e.g. WALCL",key="live_fred_custom").strip().upper()
    use_search=st.checkbox("Fetch selected FRED search result",value=False,key="choose_search_result") if matches else False
    series_id=selected_search.id if use_search else ("FRED:"+custom if custom else choices[choice])
    if st.button("Fetch current publisher data",type="primary",key="get_live"):
        try:
            if series_id.startswith("FRED:") and not (user_fred_key or os.getenv("FRED_API_KEY")):
                raise ValueError("Supply your FRED API key or configure server-side credentials")
            with st.spinner("Fetching and validating publisher observations"):
                if series_id.startswith("FRED:") and user_fred_key:
                    result=fetch_series(series_id,start.isoformat(),end.isoformat(),api_key=user_fred_key)
                else:
                    result=_cached_fetch(series_id,start.isoformat(),end.isoformat())
            if use_search:
                described=selected_search
                result=replace(result,series=replace(result.series,unit=described.unit,frequency=described.frequency),
                               descriptor=described)
            _add(series_id,result.series,result.descriptor,result.retrieved_at,result.warning)
            st.success("Loaded "+str(len(result.series.points))+" observed records from "+result.descriptor.provider)
        except Exception as exc:
            st.error("Live fetch failed (no fixture fallback): "+str(exc))
    st.caption("NY Fed reference rates are public. FRED keys are never exported; users of a commercial FRED application may need their own FRED API keys. Revisions and publication timing are not guaranteed.")

def _load_dealers():
    st.markdown("#### NY Federal Reserve primary dealers")
    st.caption("Discover publisher-defined positions, transactions, financing and settlement-fails series. Historical series-break definitions must be considered.")
    if st.button("Discover primary-dealer catalog",key="discover_pd"):
        try:
            with st.spinner("Retrieving primary dealer field dictionary"):
                st.session_state["pd_descriptors"]=_cached_dealers()
            st.success("Loaded publisher metadata for "+str(len(st.session_state["pd_descriptors"]))+" series IDs")
        except Exception as exc:st.error(str(exc))
    descriptions=st.session_state.get("pd_descriptors",[])
    if descriptions:
        search=st.text_input("Search dealer series labels",placeholder="Treasury, repo, fails ...").lower()
        filtered=[d for d in descriptions if search in d.label.lower() or search in d.id.lower()]
        if filtered:
            choice=st.selectbox("Dealer series",filtered,format_func=lambda d:(d.id+" · "+d.label)[:180])
            if st.button("Fetch dealer time series",key="fetch_pd"):
                try:
                    from financial_intelligence_agent.live.providers import dealer
                    from datetime import timezone
                    result=dealer(choice.id[9:],(date.today()-timedelta(days=365*3)).isoformat(),
                                  date.today().isoformat(),
                                  authorized_ids={d.id[9:] for d in descriptions})
                    _add(choice.id,result.series,replace(choice,unit=result.descriptor.unit),
                         result.retrieved_at,result.warning)
                    st.success("Loaded "+str(len(result.series.points))+" records")
                except Exception as exc:
                    st.error("Dealer response could not be validated: "+str(exc))
        else:st.info("No matching dealer series")
    st.caption("The New York Fed publishes primary-dealer aggregate series, not individual firms' complete balance sheets.")

def _load_fdic():
    st.markdown("#### FDIC bank-by-bank financial reports")
    st.caption("Quarterly institution financials from the FDIC BankFind API, including Call Report-derived measures. These are NOT raw FFIEC CDR archives.")
    col1,col2=st.columns(2)
    cert=col1.number_input("FDIC certificate",min_value=1,max_value=999999,value=14,step=1)
    from_ = col2.date_input("From quarter",date(2022,1,1),key="fdic_begin")
    name=st.selectbox("Financial metric",list(FIELDS),
                      format_func=lambda v:FIELDS[v]+" ("+v+")")
    custom=st.text_input("Or enter another FDIC field code",placeholder="e.g. NETINC")
    code=custom.strip().upper() or name
    if st.button("Fetch FDIC quarterly bank history",key="load_fdic"):
        try:
            result=bank_financial(int(cert),code,from_.isoformat(),date.today().isoformat())
            _add(result.descriptor.id,result.series,result.descriptor,
                 result.retrieved_at,result.warning)
            st.success("Loaded "+str(len(result.series.points))+" quarterly observations")
        except Exception as exc:
            st.error("FDIC query rejected: "+str(exc))
    st.caption("Bank metrics can use different accounting units. Verify unit definitions from FDIC before comparing or transforming.")

def _load_upload():
    st.markdown("#### Import tabular research data")
    kind=st.radio("Data type",["General CSV","FFIEC bank Call Report (tab-delimited)"],horizontal=True)
    file=st.file_uploader("Upload CSV or tab-delimited text (max 10 MB)",type=["csv","tsv","txt"],key="series_upload")
    if file is None:return
    if file.size>10_000_000:
        st.error("10 MB upload cap; narrow your bulk extract")
        return
    sep="\t" if kind.startswith("FFIEC") else st.selectbox("Delimiter",[",","\t",";"])
    try:
        raw=file.getvalue()
        preview=pd.read_csv(__import__("io").BytesIO(raw),sep=sep,nrows=10)
    except Exception as exc:
        st.error("Cannot parse header/preview: "+str(exc));return
    if len(preview.columns)<2:st.error("Two or more columns required");return
    st.dataframe(preview.head(),use_container_width=True,hide_index=True)
    cols=list(preview.columns)
    c1,c2,c3=st.columns(3)
    date_field=c1.selectbox("Date column",cols)
    value_field=c2.selectbox("Value column",cols,index=min(1,len(cols)-1))
    unit=c3.text_input("Unit / denomination","USD thousands" if kind.startswith("FFIEC") else "source-native")
    frequency=st.selectbox("Observation frequency",["daily","weekly","monthly","quarterly","annual","irregular"])
    name=st.text_input("Series key","uploaded_series")
    if kind.startswith("FFIEC"):
        inst_field=st.selectbox("Institution identifier column",cols)
        inst_id=st.text_input("Exact institution identifier",placeholder="RSSD or bank identifier from file")
    if st.button("Validate & add to workspace",key="import_series"):
        try:
            safe=re.sub(r"[^A-Za-z0-9_.:-]","_",name)[:75]
            if not safe or not safe[0].isalpha():
                raise ValueError("Series key must start with a letter")
            if kind.startswith("FFIEC"):
                if not inst_id.strip():raise ValueError("Institution ID required")
                source=FFIECAdapter(field=value_field,institution_field=inst_field,
                    institution_id=inst_id,date_field=date_field,unit=unit,
                    frequency=frequency).parse(raw)
            else:
                source=CsvSeriesAdapter(key=safe,source="UPLOAD",unit=unit,frequency=frequency,
                    date_field=date_field,value_field=value_field,delimiter=sep).parse(raw)
            source=replace(source,key=safe,source="FFIEC" if kind.startswith("FFIEC") else "UPLOAD")
            _add(safe,source,_entry_desc(safe,source),
                 datetime.now(timezone.utc).isoformat(),
                 "User-supplied data: source provenance/units not independently verified.")
            st.success("Loaded "+str(len(source.points))+" records")
        except Exception as exc:st.error("Import rejected: "+str(exc))

def _operators():
    ws=_state()
    if not ws:return
    st.markdown("#### Build derived time series")
    op=st.selectbox("Analytical operation",sorted(OPERATOR_SPECS),key="series_operation")
    spec=OPERATOR_SPECS[op]
    names=list(ws)
    x,y,z=st.columns(3)
    a=x.selectbox("First input",names,key="series_op_a")
    b=y.selectbox("Second input",names,key="series_op_b") if spec.arity==2 else None
    window=z.number_input("Observation window",min_value=2,max_value=500,value=20,key="series_op_window")
    new=st.text_input("Output name",value="derived_"+op,key="derived_key")
    if st.button("Compute and save in workspace"):
        try:
            src=[ws[a]["series"]]+([ws[b]["series"]] if b else [])
            result=execute(op,*src,window=int(window))
            safe=re.sub(r"[^A-Za-z0-9_.:-]","_",new)[:75]
            if not safe or not safe[0].isalpha():raise ValueError("Name must begin with a letter")
            result=replace(result,key=safe)
            _add(safe,result,_entry_desc(safe,result),datetime.now(timezone.utc).isoformat(),
                 "Derived from "+", ".join([a]+([b] if b else []))+
                 "; historical release timestamps may be unknown.")
            st.success("Derived series now available for charting")
        except Exception as exc:st.error("Operator rejected: "+str(exc))
    st.markdown("##### Explicitly normalize units and observation frequency")
    u1,u2,u3=st.columns(3)
    chosen=u1.selectbox("Transform series",names,key="normalize_series")
    kind=u2.selectbox("Transformation",["Unit conversion","Resample to slower frequency","Align observation-date convention"],key="normalize_method")
    incoming=ws[chosen]["series"]
    if kind=="Unit conversion":
        valid=[name for name,(dimension,_) in UNITS.items()
               if incoming.unit in UNITS and UNITS[incoming.unit][0]==dimension]
        destination=u3.selectbox("Target unit",valid,key="normalize_unit") if valid else None
    elif kind=="Resample to slower frequency":
        targets=[freq for freq,order in ORDER.items()
                 if incoming.frequency in ORDER and order>ORDER[incoming.frequency]]
        destination=u3.selectbox("Target frequency",targets,key="normalize_freq") if targets else None
        aggregator=st.selectbox("Aggregation / stock-vs-flow choice",AGGREGATIONS,key="normalize_aggregate")
    else:destination="observation_date"
    if st.button("Apply normalization",key="apply_normalization"):
        try:
            if not destination:raise ValueError("No valid conversion for this input")
            if kind=="Unit conversion":out=convert_units(incoming,destination)
            elif kind=="Resample to slower frequency":out=resample(incoming,destination,aggregator)
            else:out=align_date_convention(incoming)
            new_key=("norm_"+chosen).replace(":","_")[:85]
            out=replace(out,key=new_key)
            _add(new_key,out,_entry_desc(new_key,out),datetime.now(timezone.utc).isoformat(),
                 "Explicit conversion requested by analyst. Verify period and vintage alignment.")
            st.success("Normalized data added: "+new_key)
        except Exception as exc:st.error("Normalization rejected: "+str(exc))
    with st.expander("Direct JSON multi-step operator plan"):
        default={"steps":[{"id":"change","operator":"first_difference","inputs":[names[0]]}],
                 "result_id":"change"}
        source=st.text_area("Plan",value=json.dumps(default,indent=2),height=150,key="studio_jsonplan")
        if st.button("Compute plan"):
            try:
                svc=AnalyticalService({k:entry["series"] for k,entry in ws.items()})
                output=svc.run(json.loads(source))
                st.json(output)
            except Exception as exc:st.error(str(exc))

def _chart_builder():
    ws=_state()
    if not ws:
        st.info("Fetch or import one or more series to unlock chart building and exports.")
        return
    st.markdown("### Chart designer")
    selected=st.multiselect("Datasets",list(ws),default=list(ws)[:min(2,len(ws))],
                            max_selections=12,key="plot_selection")
    if not selected:return
    x,y,z=st.columns(3)
    mode=x.selectbox("Scale and normalization",MODES,key="plot_mode")
    style=y.selectbox("Chart type",STYLES,key="plot_style")
    start=z.date_input("Start",date.today()-timedelta(days=365*3),key="plot_start")
    end=z.date_input("End",date.today(),key="plot_end")
    heading=st.text_input("Publication chart title","Financial conditions comparison")
    subtitle=st.text_input("Subtitle","Observed time series · verify reported units and dates")
    chosen={name:ws[name]["series"] for name in selected}
    sources="; ".join(dict.fromkeys(ws[name]["descriptor"].provider for name in selected))
    spec=ChartSpec(heading,subtitle,mode,style,start,end,source_note=sources,
                   footnote="Official source observation dates; publication-time/vintage limitations apply.")
    try:
        frame,unit,warnings=prepare(chosen,spec)
        fig=interactive_chart(frame,spec,unit)
        st.plotly_chart(fig,use_container_width=True,theme=None)
        st.caption("Y axis: "+unit+" · "+str(len(frame))+" dated rows · "+
                   " · ".join(warnings[:5]))
        if mode!="Levels":
            st.warning("Each series uses its own first nonzero observation as baseline; baseline dates may differ.")
        for name in selected:
            entry=ws[name]
            st.caption(name+": "+entry["descriptor"].source_url+
                       " · latest "+entry["series"].select().points[-1].date.isoformat()+
                       " · "+entry["warning"])
        st.dataframe(frame.tail(40),use_container_width=True)
        st.markdown("#### Export publication assets")
        a,b,c,d=st.columns(4)
        a.download_button("Download CSV",csv_bytes(frame),file_name="liquidity-chart-data.csv",
                          mime="text/csv",key="export_chart_csv")
        b.download_button("Download PNG",image_bytes(frame,spec,unit,"png"),
                          file_name="liquidity-chart.png",mime="image/png",key="export_chart_png")
        c.download_button("Download SVG",image_bytes(frame,spec,unit,"svg"),
                          file_name="liquidity-chart.svg",mime="image/svg+xml",key="export_chart_svg")
        d.download_button("Chart provenance JSON",metadata_bytes(chosen,spec,unit,warnings),
                          file_name="liquidity-chart-provenance.json",mime="application/json",
                          key="export_chart_meta")
        st.caption("PNG and SVG include chart title, unit-aware y-axis, observation-date x-axis, legend, source attribution and caveats.")
    except Exception as exc:
        st.error("Chart validation: "+str(exc))

def render():
    st.caption("LIVE DATA / EXPLORATION / CHART EXPORT")
    st.title("Financial Data Studio")
    st.write("Retrieve current publisher series, apply deterministic analytics, "
             "build charts, and export research-ready data and graphics.")
    st.info("Live mode fetches upstream API data on demand. Publisher release dates and data vintages are not guaranteed. "
            "Never treat this as a real-time trading feed.")
    with st.expander("Data acquisition",expanded=not bool(_state())):
        tab1,tab2,tab3,tab4=st.tabs(["FRED + NY Fed rates","Primary dealers","FDIC bank financials","FFIEC / CSV upload"])
        with tab1:_load_public_series()
        with tab2:_load_dealers()
        with tab3:_load_fdic()
        with tab4:_load_upload()
    ws=_state()
    if ws:
        st.subheader("Working data catalog")
        summary=[{"Series ID":k,"Provider":v["descriptor"].provider,
                  "Unit":v["series"].unit,"Frequency":v["series"].frequency,
                  "Observations":len(v["series"].points),
                  "Latest observation":max(p.date for p in v["series"].points).isoformat(),
                  "Retrieved":v["retrieved"][:19]} for k,v in ws.items()]
        st.dataframe(pd.DataFrame(summary),hide_index=True,use_container_width=True)
        with st.expander("Manage datasets and transformations"):
            st.markdown("##### Portable research workspace")
            st.caption("Save an auditable portable copy of your data and source metadata. No API credentials are stored.")
            try:
                st.download_button("Download workspace JSON",export_workspace(ws),
                                   file_name="liquidity-research-workspace.json",
                                   mime="application/json",key="save_research_workspace")
            except Exception as exc:st.warning("Cannot export workspace: "+str(exc))
            uploaded=st.file_uploader("Restore a previously exported workspace",type=["json"],
                                      key="restore_research_workspace")
            if uploaded is not None and st.button("Restore verified workspace",key="restore_confirm"):
                try:
                    recovered=import_workspace(uploaded.getvalue())
                    if len(recovered)>80:raise ValueError("Workspace series limit exceeded")
                    st.session_state["analyst_workspace"]=recovered
                    st.rerun()
                except Exception as exc:st.error("Restore rejected: "+str(exc))
            remove=st.multiselect("Remove series from this session",list(ws),key="remove_series")
            if st.button("Remove selected"):
                for key in remove:ws.pop(key,None)
                st.rerun()
            _operators()
    _chart_builder()
