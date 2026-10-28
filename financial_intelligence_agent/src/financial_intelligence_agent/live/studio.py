"""Stateful analyst Chart Studio: live series, bank filings, operators and exports.

Network calls happen only on explicit actions. Streamlit session_state isolates
browser sessions (but is not a multi-tenant account or durable workspace).
"""
from __future__ import annotations
import json
import os
import re
from contextlib import closing
from dataclasses import replace
from datetime import date,timedelta,datetime,timezone
from pathlib import Path
import pandas as pd
import streamlit as st

from financial_intelligence_agent.live.nyfed_explorer import discover as discover_nyfed,records_from_path,map_series
from financial_intelligence_agent.live.catalog_index import get_connection,search as catalog_search,status as catalog_status
from financial_intelligence_agent.live.operations import aggregate_operations,metric_options,operation_types
from financial_intelligence_agent.live.panels import fetch_bank_panel,contraction_breadth
from financial_intelligence_agent.live.databricks_gold import read_gold,descriptors as gold_descriptors
from financial_intelligence_agent.live.fdic import bank_financial,FIELDS
from financial_intelligence_agent.live.providers import (
    catalog,fetch_series,dealer_catalog,Descriptor,FetchResult,FRED_PRESETS)
from financial_intelligence_agent.live.workspaces import export_workspace,import_workspace
from financial_intelligence_agent.live.charts import (
    ChartSpec,prepare,interactive_chart,image_bytes,csv_bytes,metadata_bytes,MODES,STYLES,research_bundle_bytes,chart_recipe_bytes,load_chart_recipe)
from financial_intelligence_agent.engine.operators import Series,execute,OPERATOR_SPECS
from financial_intelligence_agent.engine.alerts import Rule,evaluate,COMPARES
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
    if series.key != key:
        series=replace(series,key=key)
    _state()[key]={"series":series,"descriptor":descriptor,
                   "retrieved":retrieved,"warning":warnings}

def _load_public_series():
    st.markdown("#### Unified publisher data search")
    with closing(get_connection()) as db:
        state=catalog_status(db)
        query=st.text_input("Search locally indexed financial data series",
                            placeholder="Treasury positions, SOFR, reserves, dealer financing, repo ...",
                            key="global_series_search")
        results=catalog_search(db,query,limit=150)
    count=sum(state["counts"].values())
    progress=state["fred_categories"]
    complete=state["state"].get("fred_complete")=="true"
    st.caption(f"{count:,} indexed datasets/endpoint templates. NY Fed metadata and FRED category pages are fetched at app startup, then searched locally.")
    if not complete:
        st.info("FRED catalog crawl is still pending or incomplete. Discovered records remain searchable; this is not yet an exhaustive FRED index.")
    if state["state"].get("fred_error"):
        st.caption("FRED discovery: "+state["state"]["fred_error"])
    if state["state"].get("ny_spec_error"):
        st.caption("NY Fed OpenAPI discovery: "+state["state"]["ny_spec_error"])
    if not results:
        st.info("No indexed match. Try another term; the full index may still be warming.")
        return
    choice=st.selectbox(
        "Select a series or published endpoint",
        options=results,
        format_func=lambda item:f'{item["provider"]} · {item["label"]} [{item["id"]}]'[:210],
        key="global_index_result")
    details=pd.DataFrame([{k:item[k] for k in ("id","label","provider","frequency","unit","kind")}
                         for item in results[:80]])
    with st.expander("Search result metadata",expanded=False):
        st.dataframe(details,hide_index=True,use_container_width=True)
    x,y=st.columns(2)
    start=x.date_input("From",date.today()-timedelta(days=365*3),key="global_search_start")
    end=y.date_input("Through",date.today(),key="global_search_end")
    kind=choice["kind"];series_id=choice["id"]
    if kind=="endpoint_template":
        st.caption("This is a publisher API endpoint definition. It may need path parameters before retrieving its records.")
        endpoint=choice["endpoint"]
        placeholders=list(dict.fromkeys(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}",endpoint)))
        substitutions={}
        for param in placeholders:
            substitutions[param]=st.text_input(
                "Required endpoint parameter: "+param,
                key="endpoint_parameter_"+param)
        if st.button("Use this NY Fed endpoint",key="indexed_endpoint_choice"):
            try:
                for name,value in substitutions.items():
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}",value):
                        raise ValueError("Supply a valid alphanumeric value for "+name)
                    endpoint=endpoint.replace("{"+name+"}",value)
                from financial_intelligence_agent.live.nyfed_explorer import validate_endpoint
                endpoint=validate_endpoint(endpoint)
                st.session_state["nyfed_endpoint_suggestion"]=endpoint
                st.success("Endpoint selected. Open 'Other NY Fed APIs' below to load and examine publisher data.")
            except Exception as exc:
                st.error("Endpoint parameters unavailable: "+str(exc))
        return
    if st.button("Retrieve series observations",type="primary",key="indexed_fetch"):
        try:
            with st.spinner("Loading the selected publisher data"):
                if series_id.startswith("NYFED_PD:"):
                    from financial_intelligence_agent.live.providers import dealer
                    result=dealer(series_id[len("NYFED_PD:"):],
                                  start.isoformat(),end.isoformat())
                else:
                    result=_cached_fetch(series_id,start.isoformat(),end.isoformat())
            desc=Descriptor(series_id,choice["label"],choice["provider"],
                            choice["unit"],choice["frequency"],choice["category"],
                            choice["source_url"],choice["convention"])
            converted=replace(result.series,key=series_id,
                              unit=desc.unit,frequency=desc.frequency)
            _add(series_id,converted,desc,result.retrieved_at,result.warning)
            st.success(f"Loaded {len(converted.points):,} observations for {desc.label}")
            frame=pd.DataFrame([{"Observation date":p.date,"Value":float(p.value) if p.value is not None else None}
                                for p in converted.select().points]).set_index("Observation date")
            st.line_chart(frame,y_label=desc.unit)
            st.dataframe(frame.tail(100),use_container_width=True)
        except Exception as exc:
            st.error("Source retrieval failed; no demo data substituted: "+str(exc))
    st.caption("FRED keys are loaded exclusively from server-side configuration. No API credentials are accepted in the browser.")
    if query and len(results)>=150:
        st.caption("Showing the first 150 local matches; refine your keywords to narrow the list.")


def _load_dealers():
    st.markdown("#### Primary-dealer statistical series")
    with closing(get_connection()) as db:
        items=catalog_search(db,"NYFED_PD:",limit=250)
        count=db.execute("SELECT COUNT(*) FROM entries WHERE provider='NYFED_PD'").fetchone()[0]
    st.caption(f"{count:,} individual publisher series indexed at startup. Search by topic or series ID in Unified Publisher Search above.")
    if not count:
        st.info("Primary-dealer metadata is being loaded. Return to this tab when the startup index finishes.")
    else:
        st.caption("Unlike operation logs, a primary-dealer keyid already represents one defined historical series. No automatic sum of multiple observations is performed.")


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

def _load_gold():
    st.markdown("#### Governed Databricks Gold tables")
    st.caption("Read-only queries against existing H.4.1, H.8 and funding-rate Gold tables. No generated SQL is accepted.")
    catalog=os.getenv("DATABRICKS_CATALOG","fed_monitor")
    http_path=os.getenv("DATABRICKS_HTTP_PATH","")
    if not http_path:
        st.warning("Set DATABRICKS_HTTP_PATH and DATABRICKS_CATALOG in App environment; SQL warehouse compute is billable.")
        return
    try: descriptions=gold_descriptors(catalog)
    except ValueError as exc:st.error(str(exc));return
    chosen=st.selectbox("Gold metric",descriptions,format_func=lambda d:d.category+" · "+d.label)
    c1,c2=st.columns(2)
    a=c1.date_input("Gold from",date.today()-timedelta(days=365*3),key="gold_begin")
    b=c2.date_input("Gold through",date.today(),key="gold_end")
    if st.button("Query governed Gold (read only)"):
        try:
            _,table,field=chosen.id.split(":")
            with st.spinner("Reading Databricks warehouse"):
                result=read_gold(table,field,a.isoformat(),b.isoformat(),catalog=catalog)
            _add(chosen.id,result.series,result.descriptor,result.retrieved_at,result.warning)
            st.success("Loaded "+str(len(result.series.points))+" Gold observations")
        except Exception as exc:st.error("Gold query unavailable: "+str(exc))

def _load_breadth():
    st.markdown("#### Institutional loan contraction breadth")
    st.caption("Download matched institution quarterly financials and calculate the fraction of banks with YoY declines. This is a true cross-bank measure, not category breadth.")
    field=st.selectbox("Bank metric",["LNLSGR","DEP","ASSET","LNLSNET","NETINC"],
                      format_func=lambda code:FIELDS.get(code,code),key="panel_field")
    years=st.slider("Quarterly lookback (years)",2,5,2,key="panel_years")
    if st.button("Fetch population and calculate breadth",key="fetch_breadth"):
        try:
            with st.spinner("Paging FDIC bank-level reports; this may require many requests"):
                panel=fetch_bank_panel(field,(date.today()-timedelta(days=365*years)).isoformat(),
                                         date.today().isoformat())
                report=contraction_breadth(panel)
            series=report["series"]
            key="FDIC_BREADTH_"+field
            series=replace(series,key=key)
            _add(key,series,_entry_desc(key,series),
                 datetime.now(timezone.utc).isoformat(),
                 "Panel derived from "+str(len(panel.records))+" bank-quarter rows; "
                 "not point-in-time vintage verified.")
            st.success("Computed bank contraction breadth by quarter")
            st.dataframe(pd.DataFrame(report["table"]),hide_index=True,use_container_width=True)
            for limitation in report["limitations"]:st.caption(limitation)
        except Exception as exc:st.error("Panel analysis unavailable: "+str(exc))

def _load_nyfed_other():
    st.markdown("#### New York Fed operations and other API families")
    st.caption("Operations are transaction-level data. We aggregate compatible monetary amounts by day and operation type for charts, retaining the original row-level detail underneath.")
    catalog_path=st.session_state.get("nyfed_endpoint_suggestion","rp/results/search.json")
    examples={
        "Repo / reverse repo operations":"rp/results/search.json",
        "Selected indexed endpoint":catalog_path,
        "Other documented endpoint":""
    }
    selected=st.selectbox("Published Markets API endpoint",list(examples),
                          key="nyfed_api_template")
    custom=st.text_input("Relative .json path on markets.newyorkfed.org/api/",
                         value=examples[selected],key="nyfed_api_location")
    one,two=st.columns(2)
    start=one.date_input("From",date.today()-timedelta(days=30),key="nyfed_api_start")
    end=two.date_input("Through",date.today(),key="nyfed_api_end")
    use_dates=st.checkbox("Pass start/end date filters",value=True,key="nyfed_use_dates")
    if st.button("Fetch published operation records",type="primary",key="nyfed_fetch_operations"):
        try:
            data=discover_nyfed(custom,start=start.isoformat() if use_dates else None,
                                end=end.isoformat() if use_dates else None)
            st.session_state["nyfed_explorer_payload"]=data
            # A new API request invalidates previously aggregated chart data.
            # Never show an old operation chart under newly fetched rows.
            st.session_state.pop("nyfed_operation_view",None)
            st.success("Publisher JSON loaded. "+str(len(data.candidate_paths))+" record arrays found.")
        except Exception as exc:st.error("NY Fed request failed: "+str(exc))
    data=st.session_state.get("nyfed_explorer_payload")
    if data is None:return
    st.caption("Retrieved "+data.fetched_at+" · Endpoint: "+data.endpoint)
    if not data.candidate_paths:
        st.warning("This endpoint has no recognized record array; inspect the publisher schema.")
        return
    records_path=st.selectbox("Publisher record array",data.candidate_paths,
                              key="nyfed_record_array")
    try:
        rows=records_from_path(data,records_path)
    except Exception as exc:
        st.error("Cannot extract published records: "+str(exc));return
    columns=sorted(set().union(*(set(row) for row in rows[:100]))) if rows else []
    if not columns:
        st.warning("No operation records in this date window.");return
    st.caption(str(len(rows))+" source records; the table below is the original publisher detail.")
    operation_ready=("operationDate" in columns and
                     ("operationType" in columns or "type" in columns) and
                     bool(metric_options(data,records_path)))
    if operation_ready:
        metrics=metric_options(data,records_path)
        metric=st.selectbox("Additive amount to chart",metrics,
                            index=metrics.index("totalAmtAccepted") if "totalAmtAccepted" in metrics else 0,
                            key="nyfed_operations_metric")
        choices=operation_types(data,records_path)
        selected_type=st.selectbox("Operation type",["All types",*choices],
                                  key="nyfed_operations_type")
        if st.button("Aggregate per day and chart",type="primary",key="nyfed_daily_aggregate"):
            try:
                result=aggregate_operations(data,record_path=records_path,metric=metric,
                    operation_type=None if selected_type=="All types" else selected_type)
                st.session_state["nyfed_operation_view"]=result
                for typ,series in result.series.items():
                    k=series.key
                    desc=Descriptor(k,f"{typ} · {metric} (daily aggregate)",
                                    "NYFED",result.unit,"daily","NY Fed operations",
                                    "https://markets.newyorkfed.org/api/"+data.endpoint,
                                    "operation_date")
                    _add(k,series,desc,data.fetched_at,
                         "Daily monetary sum per operation type; original operation rows retained below.")
            except Exception as exc:st.error("Aggregation rejected: "+str(exc))
        report=st.session_state.get("nyfed_operation_view")
        if report is not None and report.endpoint==data.endpoint:
            st.subheader("Daily operation totals")
            matrix=pd.DataFrame(report.daily)
            if not matrix.empty:
                display=matrix.pivot(index="observation_date",
                                       columns="operation_type",values="total")
                display=display.apply(pd.to_numeric,errors="coerce")
                st.line_chart(display,y_label=report.unit)
                st.dataframe(matrix,hide_index=True,use_container_width=True)
                st.download_button("Download daily aggregates CSV",
                                   matrix.to_csv(index=False).encode("utf-8"),
                                   file_name="nyfed-daily-operation-totals.csv",mime="text/csv")
            for note in report.field_notes:st.caption(note)
    else:
        st.info("This endpoint does not match the operation-level schema. Use the explicit field mapper below for other publisher datasets.")
        date_col=st.selectbox("Observation date field",columns,
                              index=columns.index("date") if "date" in columns else 0,
                              key="nyfed_generic_date")
        amount_col=st.selectbox("Numeric field",columns,
                              index=columns.index("value") if "value" in columns else 0,
                              key="nyfed_generic_value")
        unit=st.text_input("Publisher unit (verify from documentation)",
                           "source-native",key="nyfed_generic_unit")
        frequency=st.selectbox("Frequency",["daily","weekly","monthly","quarterly","annual","irregular"],
                               key="nyfed_generic_frequency")
        aggregation=st.selectbox("Duplicate-day policy",["reject","sum","mean","last"],
                                 key="nyfed_generic_aggregation")
        if st.button("Map records to a series",key="nyfed_generic_map"):
            try:
                s=map_series(data,path=records_path,key="NYFED_MAPPED",
                             date_field=date_col,value_field=amount_col,
                             unit=unit,frequency=frequency,aggregation=aggregation)
                _add("NYFED_MAPPED",s,_entry_desc("NYFED_MAPPED",s),
                     data.fetched_at,"Verify schema, field definition and unit before use.")
                st.success("Mapped "+str(len(s.points))+" date observations.")
            except Exception as exc:st.error("Mapping rejected: "+str(exc))
    active_view=st.session_state.get("nyfed_operation_view")
    if (operation_ready and active_view is not None
            and active_view.endpoint==data.endpoint):
        # The detail table must correspond to the data actually aggregated
        # into the displayed chart, including the selected operation type.
        underlying=list(active_view.detail)
        st.subheader("Operations underlying the current daily chart")
        st.caption(f"{len(underlying)} matching source records, before daily aggregation")
    else:
        underlying=rows
        st.subheader("Underlying publisher records")
    details=pd.DataFrame(underlying)
    st.dataframe(details,hide_index=True,use_container_width=True,height=350)
    # Formula-escape all cells to avoid spreadsheet injection when exported.
    import csv,io
    out=io.StringIO()
    writer=csv.writer(out)
    writer.writerow([str(c) for c in details.columns])
    for values in details.itertuples(index=False,name=None):
        writer.writerow([("'"+str(v) if isinstance(v,str) and v.lstrip().startswith(("=","+","-","@"))
                          else v) for v in values])
    st.download_button("Download full publisher detail CSV",
                       out.getvalue().encode("utf-8-sig"),
                       file_name="nyfed-operation-detail.csv",mime="text/csv",
                       key="nyfed_detail_export")


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
    st.markdown("##### Research signal check")
    st.caption("One-shot threshold evaluation with provenance; continuous monitoring requires a deployed scheduled service.")
    rule_cols=st.columns(3)
    signal_name=rule_cols[0].selectbox("Measure to check",names,key="signal_series")
    comparator=rule_cols[1].selectbox("Condition",sorted(COMPARES),key="signal_condition")
    threshold=rule_cols[2].number_input("Threshold (source unit)",value=0.0,key="signal_level")
    signal_horizon=st.number_input("Comparison lookback (non-null observations)",
                                    min_value=1,max_value=365,value=1,key="signal_horizon")
    if st.button("Evaluate signal",key="evaluate_signal"):
        try:
            rule=Rule("User-defined signal",ws[signal_name]["series"].key,
                      comparator,threshold,int(signal_horizon))
            output=evaluate(rule,ws[signal_name]["series"])
            st.json(output)
            st.download_button("Export evaluated signal JSON",json.dumps(output,indent=2),
                  file_name="liquidity-signal-evaluation.json",mime="application/json")
        except Exception as exc:st.error("Signal rejected: "+str(exc))
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
    with st.expander("Validate an existing chart recipe"):
        uploaded_recipe=st.file_uploader("Select a saved chart recipe (.json)",
                                        type=["json"],key="saved_chart_recipe")
        if uploaded_recipe is not None and st.button("Check recipe against current workspace"):
            try:
                restored,layout=load_chart_recipe(uploaded_recipe.getvalue(),
                    {k:v["series"] for k,v in ws.items()})
                st.success("Verified source fingerprints and chart schema")
                st.json({"title":layout.title,"mode":layout.mode,"style":layout.style,
                         "series":list(restored)})
                st.caption("Apply these documented selections below. Chart recipes are intentionally inert JSON.")
            except Exception as exc:
                st.error("Chart recipe unavailable: "+str(exc))
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
        p,q=st.columns(2)
        p.download_button("Download full research bundle (.zip)",
                          research_bundle_bytes(frame,spec,unit,chosen,warnings),
                          file_name="liquidity-research-bundle.zip",
                          mime="application/zip",key="export_chart_bundle")
        q.download_button("Save reusable chart recipe (.json)",
                          chart_recipe_bytes(chosen,spec),
                          file_name="liquidity-chart-recipe.json",
                          mime="application/json",key="export_chart_recipe")
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
        tab1,tab2,tab3,tab4,tab5,tab6,tab7=st.tabs(["FRED + NY Fed rates","Primary dealers","Other NY Fed APIs","FDIC bank financials","Bank contraction breadth","Databricks Gold","FFIEC / CSV upload"])
        with tab1:_load_public_series()
        with tab2:_load_dealers()
        with tab3:_load_nyfed_other()
        with tab4:_load_fdic()
        with tab5:_load_breadth()
        with tab6:_load_gold()
        with tab7:_load_upload()
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
