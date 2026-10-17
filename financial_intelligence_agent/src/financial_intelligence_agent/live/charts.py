"""Analyst chart semantics, presentation, and reproducible exports.

An interactive Plotly chart and exported Matplotlib image share identical
series data and labels. Raw comparisons reject incompatible units; indexed
charts normalize each series at its own first nonzero observation.
"""
from __future__ import annotations
import csv
import io
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Mapping

import pandas as pd

from financial_intelligence_agent.engine.operators import Series

MODES=("Levels","Indexed (first observation = 100)","Change from first (%)")
STYLES=("Line","Bar","Area","Scatter")
MAX_CHART_SERIES=12
MAX_CHART_POINTS=10000

@dataclass(frozen=True)
class ChartSpec:
    title: str
    subtitle: str
    mode: str = "Levels"
    style: str = "Line"
    start: date|None = None
    end: date|None = None
    source_note: str = ""
    footnote: str = "Observation dates; vintage/publication timing may differ."

    def __post_init__(self):
        if not self.title.strip() or len(self.title)>140:
            raise ValueError("Title must have 1..140 characters")
        if len(self.subtitle)>250 or len(self.source_note)>400 or len(self.footnote)>400:
            raise ValueError("Chart labels exceed safe length")
        if self.mode not in MODES or self.style not in STYLES:
            raise ValueError("Unsupported chart transform or style")
        if self.start and self.end and self.start>self.end:
            raise ValueError("Chart range reversed")


def prepare(sources:Mapping[str,Series],spec:ChartSpec)->tuple[pd.DataFrame,str,list[str]]:
    if not sources or len(sources)>MAX_CHART_SERIES:
        raise ValueError("Choose 1..12 series")
    all_units={s.unit for s in sources.values()}
    if spec.mode=="Levels" and len(all_units)!=1:
        raise ValueError("Raw levels require matching units; choose Indexed for mixed units")
    unit=next(iter(all_units)) if spec.mode=="Levels" else ("Index (base 100)" if spec.mode==MODES[1] else "Change from baseline (%)")
    data={}
    warnings=[]
    for label,series in sources.items():
        if len(label)>140 or not label:
            raise ValueError("Invalid chart label")
        points=series.select().points
        filtered=[p for p in points if (spec.start is None or p.date>=spec.start) and
                  (spec.end is None or p.date<=spec.end)]
        if len(filtered)>MAX_CHART_POINTS:
            raise ValueError("Reduce date range; chart point limit exceeded")
        baseline=next((p for p in filtered if p.value is not None and p.value!=0),None)
        if spec.mode!="Levels" and baseline is None:
            raise ValueError("Indexed transforms require a nonzero numeric baseline")
        if spec.mode!="Levels":
            warnings.append(label+": indexed to "+baseline.date.isoformat())
        values={}
        for p in filtered:
            if p.value is None:
                v=None
            elif spec.mode=="Levels":
                v=float(p.value)
            elif spec.mode==MODES[1]:
                v=float(p.value/baseline.value*100)
            else:
                v=float((p.value/baseline.value-1)*100)
            if v is not None and not math.isfinite(v):
                raise ValueError("Nonfinite value after chart transform")
            values[p.date]=v
        data[label]=pd.Series(values,dtype="float64")
    df=pd.DataFrame(data).sort_index()
    df.index.name="Observation date"
    if df.empty:
        raise ValueError("No data in selected chart window")
    return df,unit,warnings


def interactive_chart(data:pd.DataFrame,spec:ChartSpec,unit:str):
    import plotly.graph_objects as go
    fig=go.Figure()
    for label in data.columns:
        values=data[label]
        x=data.index
        if spec.style=="Line":
            fig.add_trace(go.Scatter(x=x,y=values,mode="lines",name=label,connectgaps=False))
        elif spec.style=="Area":
            fig.add_trace(go.Scatter(x=x,y=values,mode="lines",fill="tozeroy",name=label,connectgaps=False))
        elif spec.style=="Bar":
            fig.add_trace(go.Bar(x=x,y=values,name=label))
        else:
            fig.add_trace(go.Scatter(x=x,y=values,mode="markers",name=label))
    fig.update_layout(
        title={"text":spec.title,"font":{"size":21}},
        xaxis_title="Observation date",
        yaxis_title=unit,
        legend_title_text="Series",
        template="plotly_dark",
        paper_bgcolor="#101b2b",
        plot_bgcolor="#101b2b",
        font={"color":"#dce9f4"},
        hovermode="x unified",
        margin={"l":60,"r":25,"t":75,"b":75},
        hoverlabel={"namelength":60},
    )
    fig.add_annotation(text=spec.subtitle[:250],xref="paper",yref="paper",x=0,y=1.12,
                       xanchor="left",showarrow=False,font={"size":12,"color":"#aabacb"})
    return fig


def image_bytes(data:pd.DataFrame,spec:ChartSpec,unit:str,format:str="png")->bytes:
    if format not in {"png","svg"}:raise ValueError("Supported exports: png, svg")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.dates import AutoDateLocator,ConciseDateFormatter
    fig,ax=plt.subplots(figsize=(13.2,7.5),dpi=160)
    fig.patch.set_facecolor("#0b1624")
    ax.set_facecolor("#102238")
    colors=["#42dfcc","#6faef7","#ffc773","#f27da6","#b58af4","#91d485",
            "#f58c63","#69ccde","#dee178","#c593e1","#bdc5d0","#e2a1a1"]
    for i,label in enumerate(data.columns):
        color=colors[i%len(colors)]
        values=data[label].astype(float)
        if spec.style=="Line":
            ax.plot(data.index,values,label=label,color=color,linewidth=2)
        elif spec.style=="Area":
            ax.plot(data.index,values,label=label,color=color,linewidth=1.6)
            ax.fill_between(data.index,values,0,alpha=.11,color=color)
        elif spec.style=="Bar":
            ax.bar(data.index,values,label=label,color=color,alpha=.75)
        else:
            ax.scatter(data.index,values,label=label,color=color,s=12)
    ax.set_ylabel(unit,color="#e8f1f8",fontsize=12,labelpad=12)
    ax.set_xlabel("Observation date",color="#c4d6e6",fontsize=11,labelpad=12)
    ax.grid(axis="y",color="#465b70",alpha=.35,linewidth=.8)
    ax.tick_params(colors="#bdcddd",labelsize=9)
    for spine in ax.spines.values():spine.set_visible(False)
    locator=AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(ConciseDateFormatter(locator))
    ax.legend(loc="best",fontsize=9,frameon=True,facecolor="#1a3046",edgecolor="#46566a",
              labelcolor="#e6edf5")
    fig.text(.09,.96,spec.title,fontsize=20,fontweight="bold",color="#f5fbff",ha="left")
    fig.text(.09,.92,spec.subtitle[:180],fontsize=10,color="#9db5ca",ha="left")
    fig.text(.09,.072,"Source: "+spec.source_note[:260],fontsize=9,color="#adc1d2",ha="left")
    fig.text(.09,.046,spec.footnote[:260],fontsize=8,color="#90a6b8",ha="left")
    fig.text(.91,.046,"Liquidity Intelligence · research",fontsize=8,color="#7e9aaa",ha="right")
    fig.subplots_adjust(top=.84,bottom=.18,left=.09,right=.94)
    buffer=io.BytesIO()
    fig.savefig(buffer,format=format,dpi=160,facecolor=fig.get_facecolor(),
                metadata=({"Software":"Liquidity Intelligence"} if format=="png" else None))
    plt.close(fig)
    return buffer.getvalue()


def _safe_cell(value:str)->str:
    """Prevent spreadsheet formula execution in exported metadata/labels."""
    if value.lstrip().startswith(("=","+","-","@","\t","\r","\n")):
        return "'"+value
    return value


def csv_bytes(data:pd.DataFrame)->bytes:
    file=io.StringIO(newline="")
    writer=csv.writer(file,lineterminator="\r\n")
    writer.writerow(["Observation date",*(_safe_cell(str(c)) for c in data.columns)])
    for when,row in data.iterrows():
        writer.writerow([when.isoformat(),*("" if pd.isna(v) else format(float(v),".12g") for v in row)])
    return file.getvalue().encode("utf-8-sig")


def metadata_bytes(sources:Mapping[str,Series],spec:ChartSpec,unit:str,warnings:list[str])->bytes:
    payload={"title":spec.title,"subtitle":spec.subtitle,"mode":spec.mode,"style":spec.style,
             "unit":unit,"source_note":spec.source_note,"footnote":spec.footnote,
             "date_range":{"start":str(spec.start) if spec.start else None,
                           "end":str(spec.end) if spec.end else None},
             "series":[{"label":label,"key":s.key,"source":s.source,"unit":s.unit,
                        "frequency":s.frequency,"convention":s.convention,
                        "fingerprint":s.fingerprint()} for label,s in sources.items()],
             "warnings":warnings,
             "created_at":datetime.now(timezone.utc).isoformat()}
    return json.dumps(payload,indent=2,sort_keys=True).encode("utf-8")


def research_bundle_bytes(data:pd.DataFrame,spec:ChartSpec,unit:str,
                          sources:Mapping[str,Series],warnings:list[str])->bytes:
    """Single self-contained research artifact with chart, raw values and provenance.

    ZIP member names are fixed to prevent traversal. The included CSV and
    image contain only user-selected series and window, not credentials.
    """
    from zipfile import ZipFile,ZIP_DEFLATED
    package=io.BytesIO()
    with ZipFile(package,"w",compression=ZIP_DEFLATED,compresslevel=6) as archive:
        archive.writestr("chart.png",image_bytes(data,spec,unit,"png"))
        archive.writestr("chart.svg",image_bytes(data,spec,unit,"svg"))
        archive.writestr("observations.csv",csv_bytes(data))
        archive.writestr("provenance.json",metadata_bytes(sources,spec,unit,warnings))
    return package.getvalue()


def chart_recipe_bytes(sources:Mapping[str,Series],spec:ChartSpec)->bytes:
    """Safe portable chart layout; references fingerprint-pinned workspace keys."""
    spec_data={
        "schema":"liquidity-chart-recipe/v1","title":spec.title,
        "subtitle":spec.subtitle,"mode":spec.mode,"style":spec.style,
        "start":spec.start.isoformat() if spec.start else None,
        "end":spec.end.isoformat() if spec.end else None,
        "source_note":spec.source_note,"footnote":spec.footnote,
        "sources":[{"key":name,"fingerprint":series.fingerprint()} for name,series in sources.items()],
    }
    return json.dumps(spec_data,sort_keys=True,indent=2).encode("utf-8")


def load_chart_recipe(raw:bytes,workspace:Mapping[str,Series])->tuple[dict,ChartSpec]:
    if len(raw)>100_000:
        raise ValueError("Chart recipe exceeds 100 KB")
    data=json.loads(raw)
    expected={"schema","title","subtitle","mode","style","start","end","source_note","footnote","sources"}
    if not isinstance(data,dict) or set(data)!=expected or data["schema"]!="liquidity-chart-recipe/v1":
        raise ValueError("Unsupported chart recipe schema")
    items=data["sources"]
    if not isinstance(items,list) or not 1<=len(items)<=MAX_CHART_SERIES:
        raise ValueError("Invalid chart recipe series")
    sources={}
    for item in items:
        if not isinstance(item,dict) or set(item)!={"key","fingerprint"}:
            raise ValueError("Malformed chart source reference")
        key=item["key"]
        if key not in workspace or key in sources:
            raise ValueError("Recipe requires a distinct series in the current workspace")
        s=workspace[key]
        if s.fingerprint()!=item["fingerprint"]:
            raise ValueError("Chart recipe source fingerprint mismatch; reload original source")
        sources[key]=s
    spec=ChartSpec(data["title"],data["subtitle"],data["mode"],data["style"],
                   date.fromisoformat(data["start"]) if data["start"] else None,
                   date.fromisoformat(data["end"]) if data["end"] else None,
                   data["source_note"],data["footnote"])
    return sources,spec
