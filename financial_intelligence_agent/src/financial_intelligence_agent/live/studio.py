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

from financial_intelligence_agent.live.catalog_index import get_connection,search as catalog_search,status as catalog_status,release_list
from financial_intelligence_agent.live.nyfed_datasets import fetch_dataset,BY_ID
from financial_intelligence_agent.live.panels import fetch_bank_panel,contraction_breadth
from financial_intelligence_agent.live.databricks_gold import read_gold,descriptors as gold_descriptors
from financial_intelligence_agent.live.fdic import bank_financial,FIELDS
from financial_intelligence_agent.live.providers import fetch_series,Descriptor
from financial_intelligence_agent.live.workspaces import export_workspace,import_workspace
from financial_intelligence_agent.live.charts import (
    ChartSpec,prepare,interactive_chart,preview_figure,image_bytes,csv_bytes,metadata_bytes,MODES,STYLES,research_bundle_bytes,chart_recipe_bytes,load_chart_recipe)
from financial_intelligence_agent.engine.operators import Series,execute,OPERATOR_SPECS
from financial_intelligence_agent.engine.alerts import Rule,evaluate,COMPARES
from financial_intelligence_agent.engine.normalization import convert_units,resample,align_date_convention,UNITS,ORDER,AGGREGATIONS
from financial_intelligence_agent.engine.adapters import CsvSeriesAdapter,FFIECAdapter
from financial_intelligence_agent.agent.operator_planner import AnalyticalService

@st.cache_data(ttl=900,max_entries=80,show_spinner=False)
def _cached_fetch(series_id:str,start:str,end:str):
    return fetch_series(series_id,start,end)

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

def _publication_buttons(frame:pd.DataFrame,spec:ChartSpec,unit:str,
                         chosen:dict[str,Series],warnings:list[str],*,prefix:str)->None:
    """Reusable chart publication downloads for source previews and transformed charts."""
    st.markdown("#### Export publication assets")
    a,b,c,d=st.columns(4)
    a.download_button("Download CSV",csv_bytes(frame),
                      file_name=f"{prefix}-chart.csv",mime="text/csv",
                      key=prefix+"_csv")
    b.download_button("Download PNG",image_bytes(frame,spec,unit,"png"),
                      file_name=f"{prefix}-chart.png",mime="image/png",
                      key=prefix+"_png")
    c.download_button("Download SVG",image_bytes(frame,spec,unit,"svg"),
                      file_name=f"{prefix}-chart.svg",mime="image/svg+xml",
                      key=prefix+"_svg")
    d.download_button("Chart provenance JSON",metadata_bytes(chosen,spec,unit,warnings),
                      file_name=f"{prefix}-provenance.json",
                      mime="application/json",key=prefix+"_meta")
    p,q=st.columns(2)
    p.download_button("Download full research bundle (.zip)",
                      research_bundle_bytes(frame,spec,unit,chosen,warnings),
                      file_name=f"{prefix}-research.zip",mime="application/zip",
                      key=prefix+"_bundle")
    q.download_button("Save reusable chart recipe (.json)",
                      chart_recipe_bytes(chosen,spec),
                      file_name=f"{prefix}-recipe.json",
                      mime="application/json",key=prefix+"_recipe")
    st.caption("Exports include a labeled date axis, the chart's unit, source attribution and vintage caveats.")


def _source_publication(sources:dict[str,Series],*,title:str,subtitle:str,
                        prefix:str,style:str="Line")->None:
    """Offer a consistent publication pack for a visible source preview.

    A source figure with separate incompatible axes (NY Fed rates/volumes)
    offers source-native Plotly download for the combined view and separate
    rate/volume publication packs rather than mislabeling a single axis.
    """
    if not sources:return
    note="; ".join(dict.fromkeys(s.source for s in sources.values()))
    spec=ChartSpec(title[:140],subtitle[:250],style=style,
                   source_note=note,footnote="Publisher observation dates; historical release/vintage times not verified.")
    try:
        frame,unit,warnings=prepare(sources,spec)
        _publication_buttons(frame,spec,unit,sources,warnings,prefix=prefix)
    except Exception as exc:
        st.warning("Cannot prepare source publication assets: "+str(exc))


def _load_fred():
    st.markdown("#### FRED · release and series discovery")
    st.caption("All FRED releases and every release's paginated series are indexed in a background worker. Search runs locally; a fresh index may be incomplete until crawling finishes.")
    with closing(get_connection()) as db:
        report=catalog_status(db)
        releases=release_list(db)
        count=report["counts"].get("FRED",0)
    progressed=report["fred_releases"]
    total=sum(progressed.values())
    done=progressed.get("done",0)
    st.caption(f"{count:,} known FRED series • {done:,}/{total:,} releases indexed • "+
               ("Complete" if report["state"].get("fred_complete")=="true" else "Indexing / incomplete"))
    error=report["state"].get("fred_error")
    if error:st.warning("Catalog: "+error)
    if total and done<total:
        st.progress(done/total,text=f"Indexed {done} of {total} FRED releases")
    options=[None,*releases]
    selected_release=st.selectbox("Browse by release",options,
        format_func=lambda r:"All releases" if r is None else f'{r["name"]} ({r["series"]} indexed)',
        key="fred_release_selected")
    query=st.text_input("Search FRED series by name, identifier or release",
        placeholder="e.g. H.8, commercial bank credit, SOFR",key="fred_series_search")
    with closing(get_connection()) as db:
        matches=catalog_search(db,query,provider="FRED",
            release_id=selected_release["id"] if selected_release is not None else None,limit=150)
    if not matches:
        st.info("No indexed series match. The release may still be indexing, or the search is too specific.")
        return
    chosen=st.selectbox("Series",matches,
        format_func=lambda item:f'{item["label"]} · {item["id"][5:]} ({item["frequency"]}, {item["unit"]})',
        key="fred_series_item")
    if len(matches)==150:st.caption("Showing the first 150 matches. Refine your search to narrow the results.")
    with st.expander("Metadata for indexed series"):
        st.dataframe(pd.DataFrame(matches[:75])[["id","label","frequency","unit"]],
                     use_container_width=True,hide_index=True)
    a,b=st.columns(2)
    start=a.date_input("From",date.today()-timedelta(days=365*4),key="fred_range_start")
    end=b.date_input("Through",date.today(),key="fred_range_end")
    if st.button("Load FRED observations",type="primary",key="fred_load_observations"):
        try:
            with st.spinner("Loading FRED data; using the private server API key"):
                found=_cached_fetch(chosen["id"],start.isoformat(),end.isoformat())
            descriptor=Descriptor(chosen["id"],chosen["label"],"FRED",
                chosen["unit"],chosen["frequency"],chosen["category"],
                chosen["source_url"],chosen["convention"])
            series=replace(found.series,key=chosen["id"],unit=descriptor.unit,
                           frequency=descriptor.frequency)
            _add(chosen["id"],series,descriptor,found.retrieved_at,found.warning)
            st.session_state["fred_last_result"]=chosen["id"]
            st.success(f"Loaded {len(series.points):,} published observations")
        except Exception as exc:st.error("FRED series request failed: "+str(exc))
    recent=st.session_state.get("fred_last_result")
    if recent in _state():
        series=_state()[recent]["series"]
        frame=pd.DataFrame([{"date":p.date,"value":float(p.value) if p.value is not None else None}
                            for p in series.points]).set_index("date")
        label=_state()[recent]["descriptor"].label
        st.caption("Displaying "+label+" · "+recent)
        fig=preview_figure(series,label)
        st.plotly_chart(fig,use_container_width=True,theme=None,
            key="fred_preview",config={"toImageButtonOptions":{
                "format":"png","filename":"fred-series","height":900,"width":1500,"scale":2}})
        st.caption("Y-axis automatically follows the observed range rather than starting at zero.")
        st.dataframe(frame.tail(200),use_container_width=True)
        _source_publication({recent:series},title=label,
                            subtitle="FRED published observations · "+series.frequency,
                            prefix="fred-source")
    st.caption("FRED observation values are latest-vintage API results; release listing is metadata, not evidence of historical availability.")


@st.cache_data(ttl=900,max_entries=60,show_spinner=False)
def _fetch_ny_dataset(dataset_id:str,start:str,end:str):
    return fetch_dataset(dataset_id,start,end)

def _show_ny_chart(result):
    import plotly.graph_objects as go
    definition=result.definition
    if definition.mode=="swaps":
        # Different currencies are separate physical units: separate axes and
        # charts rather than misleading mixed-currency dollar comparisons.
        for name,series in result.series.items():
            fig=go.Figure()
            fig.add_trace(go.Scatter(x=[p.date for p in series.points],
                                     y=[float(p.value) for p in series.points],
                                     mode="lines+markers",name=name))
            fig.update_layout(title=f"{definition.label} · {name}",
                xaxis_title="Operation date",yaxis_title=f"Amount ({name})",
                template="plotly_dark",height=320)
            st.plotly_chart(fig,use_container_width=True,key="nyfx_"+series.key,
                config={"toImageButtonOptions":{"format":"png","filename":"nyfed-swaps-"+name,
                        "height":750,"width":1300,"scale":2}})
        return
    fig=go.Figure()
    if definition.mode=="rates":
        for typ,series in sorted(result.series.items()):
            fig.add_trace(go.Scatter(x=[p.date for p in series.points],
                y=[float(p.value) for p in series.points],name=typ+" rate",
                mode="lines",yaxis="y",legendgroup=typ))
            volume=result.volume.get(typ)
            if volume:
                fig.add_trace(go.Bar(x=[p.date for p in volume.points],
                    y=[float(p.value) for p in volume.points],
                    name=typ+" volume",yaxis="y2",opacity=0.22,
                    legendgroup=typ,visible=True if typ=="SOFR" else "legendonly"))
        fig.update_layout(yaxis_title="Reference rate (%)",
            yaxis2=dict(title="Volume (USD billions)",overlaying="y",side="right",
                        showgrid=False),barmode="group")
    else:
        for label,series in sorted(result.series.items()):
            default="legendonly" if label=="All operation types" else True
            fig.add_trace(go.Scatter(x=[p.date for p in series.points],
                y=[float(p.value) for p in series.points],mode="lines+markers",
                name=label,visible=default))
        fig.update_layout(yaxis_title=("Accepted par amount (USD)" if definition.mode in ("treasury","operations")
                                      else "Accepted amount (USD)"))
    fig.update_layout(title=definition.label,
        xaxis_title="Effective date" if definition.mode=="rates" else "Operation date",
        template="plotly_dark",height=460,hovermode="x unified",
        legend_title_text="Click a legend entry to show/hide")
    st.plotly_chart(fig,use_container_width=True,key="ny_chart_"+definition.id,
        config={"toImageButtonOptions":{"format":"png","filename":"nyfed-"+definition.id.split(":")[-1],
                "height":900,"width":1500,"scale":2}})


def _load_nyfed():
    st.markdown("#### New York Fed · research datasets")
    st.caption("Search actual research datasets, including operation aggregation views and separately indexed primary-dealer statistical keyids. No endpoint-configuration forms.")
    query=st.text_input("Find NY Fed data",
        placeholder="repo, reverse repo, agency MBS, liquidity swaps, primary dealer, SOFR...",
        key="nyfed_dataset_search")
    with closing(get_connection()) as db:
        report=catalog_status(db)
        # Search each publisher namespace independently. Otherwise the
        # first 250 alphabetical FRED records could hide every NY Fed hit.
        matches=(catalog_search(db,query,provider="NYFED_DATASET",limit=100)
                 +catalog_search(db,query,provider="NYFED_PD",limit=150))
    st.caption(f"{report['counts'].get('NYFED_PD',0):,} primary-dealer time series indexed • "+
               f"{report['counts'].get('NYFED_DATASET',0):,} structured data products")
    if report["state"].get("ny_dealers_error"):
        st.info(report["state"]["ny_dealers_error"])
    if not matches:
        st.info("No matching data product. Check spelling or wait for primary-dealer metadata to finish.")
        return
    choice=st.selectbox("Dataset to chart",matches,
        format_func=lambda x:f'{x["label"]} · {x["provider"]} [{x["id"]}]'[:220],
        key="nyfed_selected_dataset")
    a,b=st.columns(2)
    start=a.date_input("From",date.today()-timedelta(days=365),key="nyfed_range_start")
    end=b.date_input("Through",date.today(),key="nyfed_range_end")
    if st.button("Load NY Fed dataset",key="nyfed_fetch_dataset",type="primary"):
        try:
            if choice["provider"]=="NYFED_PD":
                from financial_intelligence_agent.live.providers import dealer
                result=dealer(choice["id"][len("NYFED_PD:"):],
                    start.isoformat(),end.isoformat())
                descriptor=Descriptor(choice["id"],choice["label"],"NYFED_PD",
                      choice["unit"],choice["frequency"],choice["category"],
                      choice["source_url"],choice["convention"])
                series=replace(result.series,key=choice["id"])
                _add(choice["id"],series,descriptor,result.retrieved_at,result.warning)
                st.session_state["nyfed_loaded"]={"kind":"dealer","id":choice["id"]}
            else:
                result=_fetch_ny_dataset(choice["id"],start.isoformat(),end.isoformat())
                for name,series in result.series.items():
                    desc=Descriptor(series.key,choice["label"]+" · "+name,"NYFED",
                        result.units[name],"daily",choice["category"],
                        result.source_url,series.convention)
                    _add(series.key,series,desc,result.retrieved_at,
                         "; ".join(result.warnings))
                for name,series in result.volume.items():
                    desc=Descriptor(series.key,choice["label"]+" · "+name+" volume",
                       "NYFED","Billions of USD","daily",choice["category"],
                       result.source_url,series.convention)
                    _add(series.key,series,desc,result.retrieved_at,
                         "NY Fed published volume, billions of USD")
                st.session_state["nyfed_loaded"]={"kind":"product","id":choice["id"],
                                                   "result":result}
            st.success("Publisher observations loaded and available in the Chart Designer below.")
        except Exception as exc:
            st.error("NY Fed request rejected: "+str(exc))
            st.session_state.pop("nyfed_loaded",None)
    loaded=st.session_state.get("nyfed_loaded")
    if loaded is None:return
    if loaded["id"]!=choice["id"]:
        st.info("Select Load NY Fed dataset to update the chart to the current dataset.")
        return
    if loaded["kind"]=="dealer":
        item=_state().get(loaded["id"])
        if item is None:return
        series=item["series"]
        frame=pd.DataFrame([{"Date":p.date,"Value":float(p.value)} for p in series.points])
        st.plotly_chart(preview_figure(series,item["descriptor"].label),
                        use_container_width=True,theme=None,key="nydealer_preview")
        st.dataframe(frame.tail(250),use_container_width=True,hide_index=True)
        _source_publication({loaded["id"]:series},title=item["descriptor"].label,
                            subtitle="NY Fed primary dealer historical series",
                            prefix="nydealer-source")
        st.download_button("Download dealer series CSV",
            frame.to_csv(index=False).encode("utf-8-sig"),"nyfed-dealer-series.csv",
            mime="text/csv",key="nydealer_csv")
        return
    result=loaded["result"]
    st.caption("Publisher source: "+result.source_url)
    _show_ny_chart(result)
    for note in result.warnings:st.caption(note)
    st.subheader("Daily chart observations")
    daily=pd.DataFrame(result.daily)
    st.dataframe(daily,use_container_width=True,hide_index=True,height=260)
    from financial_intelligence_agent.live.charts import _safe_cell
    safe_daily=daily.copy()
    for col in safe_daily.columns:
        safe_daily[col]=safe_daily[col].map(
            lambda v:_safe_cell(v) if isinstance(v,str) else v)
    st.download_button("Download daily aggregates CSV",
       safe_daily.to_csv(index=False).encode("utf-8-sig"),"nyfed-daily.csv",
       mime="text/csv",key="ny_daily_download")
    st.subheader("Underlying publisher records (wide table)")
    details=pd.DataFrame(result.detail)
    st.dataframe(details,use_container_width=True,hide_index=True,height=360)
    # Use the app's CSV safety helper: spreadsheet formula-looking input is
    # escaped without altering the raw data displayed in the table.
    from financial_intelligence_agent.live.charts import _safe_cell
    export=details.copy()
    for column in export.columns:
        export[column]=export[column].map(lambda value:
            _safe_cell(value) if isinstance(value,str) else value)
    st.download_button("Download complete underlying records CSV",
        export.to_csv(index=False).encode("utf-8-sig"),
        "nyfed-record-level-data.csv",mime="text/csv",key="ny_detail_download")
    # Keep the original operation table beneath the publisher chart, and
    # expose the same publication asset pack available for transformed data.
    with st.expander("Export publication assets",expanded=False):
        if result.definition.mode=="rates":
            family=st.radio("Publication series",["Rates (%)","Volume (USD billions)"],
                            horizontal=True,key="ny_publication_family")
            selected=result.series if family.startswith("Rates") else result.volume
            chart_style="Line" if family.startswith("Rates") else "Bar"
        elif result.definition.mode=="swaps":
            currency=st.selectbox("Export one currency",list(result.series),
                                  key="ny_export_currency")
            selected={currency:result.series[currency]}
            chart_style="Line"
        else:
            selected=result.series
            chart_style="Line"
        _source_publication(selected,title=result.definition.label,
             subtitle="NY Fed daily publisher observations · "+result.retrieved_at[:10],
             prefix="nyfed-source",style=chart_style)
        if result.definition.mode=="rates":
            st.caption("The Plotly chart toolbar exports the combined dual-axis figure. "+
                       "These high-resolution publication files export rates and volumes "+
                       "separately to preserve their different units.")


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
    """Operator-first interface, with controls determined by the selected arity."""
    ws=_state()
    st.markdown("### Apply an operator")
    st.caption("Choose an operation, then the dataset for each input. Results are saved for charting and reuse.")
    if not ws:
        st.info("No datasets loaded yet. Visit Live Data Studio and load a FRED, NY Fed, FDIC or uploaded series.")
        return
    operator=st.selectbox("1 · Operator",sorted(OPERATOR_SPECS),
                          key="series_operation",
                          format_func=lambda name:name.replace("_"," ").capitalize())
    spec=OPERATOR_SPECS[operator]
    names=list(ws)
    st.caption(f"{spec.group.replace('_',' ').title()} · {spec.arity} input"+
               ("s" if spec.arity!=1 else "")+
               (" · rolling window" if spec.windowed else ""))
    cols=st.columns(spec.arity)
    first=cols[0].selectbox("2 · First dataset",names,
                            key="series_op_a",format_func=lambda key:
                            f"{ws[key]['descriptor'].label} [{key}]")
    second=cols[1].selectbox("3 · Second dataset",names,
                             key="series_op_b",format_func=lambda key:
                             f"{ws[key]['descriptor'].label} [{key}]") if spec.arity==2 else None
    window=(st.number_input("Window (observations)",min_value=2,max_value=1000,
              value=20,key="series_op_window") if spec.windowed else 5)
    with st.expander("Output settings",expanded=False):
        output_name=st.text_input("Save result as",value="derived_"+operator,
                                  key="derived_key")
        st.caption("Results remain in the current research session and can be used in later calculations.")
    if st.button("Calculate and save result",type="primary",key="calculate_operator"):
        try:
            inputs=[ws[first]["series"]]+([ws[second]["series"]] if second else [])
            result=execute(operator,*inputs,window=int(window))
            safe=re.sub(r"[^A-Za-z0-9_.:-]","_",output_name.strip())[:75]
            if not safe or not safe[0].isalpha():
                raise ValueError("Output name must begin with a letter")
            result=replace(result,key=safe)
            _add(safe,result,_entry_desc(safe,result),datetime.now(timezone.utc).isoformat(),
                 "Derived from "+", ".join([first]+([second] if second else []))+
                 "; source publication times may not be independently verifiable.")
            st.session_state["latest_transform_result"]=safe
            st.success("Calculated and saved "+safe+" — available to all chart selections.")
        except Exception as exc:
            st.error("Operator rejected: "+str(exc))
    latest=st.session_state.get("latest_transform_result")
    if latest in ws:
        series=ws[latest]["series"]
        st.markdown("#### Most recent result")
        try:
            st.plotly_chart(preview_figure(series,latest),use_container_width=True,
                            theme=None,key="transform_result_preview")
            frame=pd.DataFrame([{"Date":p.date,"Value":float(p.value) if p.value is not None else None}
                                 for p in series.select().points]).set_index("Date")
            st.dataframe(frame.tail(60),use_container_width=True)
            _source_publication({latest:series},title=latest,
                                subtitle="Calculated series · "+series.frequency,
                                prefix="transformed-result")
        except Exception as exc:
            st.warning("Result preview unavailable: "+str(exc))


def _advanced_tools():
    """Additional normalization, signals and multi-step plans under one expander."""
    ws=_state()
    if not ws:return
    names=list(ws)
    st.markdown("##### Unit and frequency normalization")
    u1,u2,u3=st.columns(3)
    chosen=u1.selectbox("Dataset to transform",names,key="normalize_series")
    kind=u2.selectbox("Transformation",
        ["Unit conversion","Resample to slower frequency",
         "Align observation-date convention"],key="normalize_method")
    incoming=ws[chosen]["series"]
    if kind=="Unit conversion":
        valid=[name for name,(dimension,_) in UNITS.items()
               if incoming.unit in UNITS and UNITS[incoming.unit][0]==dimension]
        destination=u3.selectbox("Target unit",valid,key="normalize_unit") if valid else None
    elif kind=="Resample to slower frequency":
        targets=[freq for freq,order in ORDER.items()
                 if incoming.frequency in ORDER and order>ORDER[incoming.frequency]]
        destination=u3.selectbox("Target frequency",targets,key="normalize_freq") if targets else None
        aggregator=st.selectbox("Stock vs flow aggregation",AGGREGATIONS,key="normalize_aggregate")
    else:destination="observation_date"
    if st.button("Apply normalization",key="apply_normalization"):
        try:
            if not destination:raise ValueError("No valid conversion for this dataset")
            if kind=="Unit conversion":out=convert_units(incoming,destination)
            elif kind=="Resample to slower frequency":out=resample(incoming,destination,aggregator)
            else:out=align_date_convention(incoming)
            new_key=("norm_"+chosen).replace(":","_")[:85]
            out=replace(out,key=new_key)
            _add(new_key,out,_entry_desc(new_key,out),datetime.now(timezone.utc).isoformat(),
                 "Analyst requested explicit conversion; confirm vintage and aggregation semantics.")
            st.success("Normalized series saved: "+new_key)
        except Exception as exc:st.error("Normalization rejected: "+str(exc))
    st.markdown("##### Research signal check")
    st.caption("On-demand evaluation; scheduled notifications are not deployed.")
    rule_cols=st.columns(3)
    signal=rule_cols[0].selectbox("Dataset",names,key="signal_series")
    comparator=rule_cols[1].selectbox("Condition",sorted(COMPARES),key="signal_condition")
    threshold=rule_cols[2].number_input("Threshold (source unit)",value=0.0,key="signal_level")
    horizon=st.number_input("Comparison lookback (non-null observations)",
              min_value=1,max_value=365,value=1,key="signal_horizon")
    if st.button("Evaluate signal",key="evaluate_signal"):
        try:
            rule=Rule("User-defined signal",ws[signal]["series"].key,
                      comparator,threshold,int(horizon))
            result=evaluate(rule,ws[signal]["series"])
            st.json(result)
            st.download_button("Export evaluated signal JSON",json.dumps(result,indent=2),
                file_name="liquidity-signal-evaluation.json",mime="application/json",
                key="signal_json_download")
        except Exception as exc:st.error("Signal rejected: "+str(exc))
    st.markdown("##### Multi-step operator plan")
    example={"steps":[{"id":"change","operator":"first_difference",
                       "inputs":[names[0]]}],"result_id":"change"}
    payload=st.text_area("Plan JSON",value=json.dumps(example,indent=2),
                         height=150,key="studio_jsonplan")
    if st.button("Compute validated plan",key="compute_multistep_plan"):
        try:
            service=AnalyticalService({k:v["series"] for k,v in ws.items()})
            st.json(service.run(json.loads(payload)))
        except Exception as exc:st.error("Plan rejected: "+str(exc))


def _workspace_management():
    ws=_state()
    st.markdown("##### Working datasets")
    summary=[{"Series ID":key,"Provider":entry["descriptor"].provider,
              "Unit":entry["series"].unit,"Frequency":entry["series"].frequency,
              "Observations":len(entry["series"].points),
              "Latest":max(p.date for p in entry["series"].points).isoformat()}
             for key,entry in ws.items()]
    if summary:
        st.dataframe(pd.DataFrame(summary),hide_index=True,use_container_width=True)
    st.caption("Portable research workspaces include data and provenance, not API credentials.")
    try:
        st.download_button("Download workspace JSON",export_workspace(ws),
            file_name="liquidity-research-workspace.json",mime="application/json",
            key="save_research_workspace")
    except Exception as exc:st.warning("Cannot export workspace: "+str(exc))
    uploaded=st.file_uploader("Restore saved workspace JSON",type=["json"],
                              key="restore_research_workspace")
    if uploaded is not None and st.button("Restore verified workspace",key="restore_confirm"):
        try:
            recovered=import_workspace(uploaded.getvalue())
            st.session_state["analyst_workspace"]=recovered
            st.session_state.pop("latest_transform_result",None)
            st.rerun()
        except Exception as exc:st.error("Restore rejected: "+str(exc))
    if ws:
        remove=st.multiselect("Remove datasets",list(ws),key="remove_series")
        if st.button("Remove selected datasets",key="remove_selected"):
            for key in remove:ws.pop(key,None)
            st.rerun()


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
        _publication_buttons(frame,spec,unit,chosen,warnings,prefix="custom-chart")
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
                    st.caption("Apply these documented selections above. Chart recipes are inert JSON.")
                except Exception as exc:
                    st.error("Chart recipe unavailable: "+str(exc))
    except Exception as exc:
        st.error("Chart validation: "+str(exc))

def render():
    """Live sources, their charts, previews and exports only."""
    st.caption("LIVE DATA / SOURCE EXPLORATION")
    st.title("Live Data Studio")
    st.write("Discover and retrieve publisher data. Preview the results and export research-ready source charts.")
    st.caption("Live requests are on demand; observation dates and revisions are not a real-time market feed.")
    fred_tab,ny_tab,fdic_tab,breadth_tab,gold_tab,upload_tab=st.tabs(
        ["FRED","NY Fed","FDIC bank financials","Bank contraction breadth",
         "Databricks Gold","FFIEC / CSV upload"])
    with fred_tab:_load_fred()
    with ny_tab:_load_nyfed()
    with fdic_tab:_load_fdic()
    with breadth_tab:_load_breadth()
    with gold_tab:_load_gold()
    with upload_tab:_load_upload()
    count=len(_state())
    if count:
        st.caption(f"{count} datasets ready for analysis. Select **Data transformations** in the sidebar to apply operators, build comparison charts and manage the workspace.")


def render_transformations():
    """Single operator-first workspace shared with Live Data Studio."""
    st.caption("ANALYSIS / TRANSFORMATIONS / CHART DESIGN")
    st.title("Data transformations")
    st.write("Choose an operator, select the input datasets, and calculate. "+
             "Build comparison charts or export the result without leaving this workspace.")
    _operators()
    st.divider()
    _chart_builder()
    with st.expander("Advanced transformations and research signals",expanded=False):
        _advanced_tools()
    with st.expander("Manage working datasets and saved workspaces",expanded=False):
        _workspace_management()
