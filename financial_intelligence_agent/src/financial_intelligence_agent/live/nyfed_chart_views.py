"""Publication-aware NY Fed charts with in-figure D/W/M/Y aggregation buttons.

Aggregation rules:
  - accepted amounts and daily transaction volumes: SUM
  - percentage rates: arithmetic MEAN (not volume-weighted)
  - observed outstanding contract positions: LAST observation of period
Buttons are Plotly client-side controls, placed directly below the legend.
No API calls or artificial data points are created when changing resolution.
"""
from __future__ import annotations

from typing import Mapping
import plotly.graph_objects as go

from financial_intelligence_agent.engine.operators import Series
from financial_intelligence_agent.live.nyfed_analytics import PERIODS,aggregate_observations


def nyfed_figure(
    source_series:Mapping[str,Series], *,
    title:str, mode:str, volume:Mapping[str,Series]|None=None,
    stock:bool=False,
):
    if not source_series and not volume:
        raise ValueError("No observations to chart")
    volume=volume or {}
    # Plotly uses the right margin for a legend AND the aggregation toolbar;
    # the right Y-axis remains adjacent to the plot, not under the legend.
    all_entries=[(name,series,False) for name,series in sorted(source_series.items())]
    all_entries += [(name+" volume",series,True) for name,series in sorted(volume.items())]
    labels=len(all_entries)
    height=max(590,340+labels*32)
    legend_px=labels*24+20
    plot_height=height-140
    menu_y=max(.09,1-(legend_px+55)/plot_height)
    fig=go.Figure()
    for frequency in PERIODS:
        for name,series,is_volume in all_entries:
            method="last" if stock else ("mean" if mode=="rates" and not is_volume else "sum")
            x,y=aggregate_observations(series.points,frequency,method)
            if not x:continue
            if frequency!="D":visibility=False
            elif is_volume and not name.startswith("SOFR"):
                visibility="legendonly"
            elif name=="Gross activity (accepted par)" or name.startswith("Security: "):
                visibility="legendonly"
            else:visibility=True
            label=name
            common=dict(x=x,y=y,name=label,legendgroup=label,showlegend=True,
                        visible=visibility,
                        hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.3f}<extra>"+label+"</extra>")
            if is_volume:
                fig.add_trace(go.Bar(**common,yaxis="y2",opacity=.28))
            elif mode=="rates" or stock or name.startswith("Net ") or name.startswith("Gross "):
                fig.add_trace(go.Scatter(**common,mode="lines+markers",
                                         marker={"size":4}))
            else:
                fig.add_trace(go.Bar(**common,opacity=.76))
    # The figure records all period traces for zero-network browser toggles.
    trace_periods=[]
    for f in PERIODS:
        for name,series,is_volume in all_entries:
            method="last" if stock else ("mean" if mode=="rates" and not is_volume else "sum")
            x,_=aggregate_observations(series.points,f,method)
            if x:trace_periods.append((f,name,is_volume))
    buttons=[]
    for f in PERIODS:
        vis=[]
        for p,name,is_volume in trace_periods:
            if p!=f:vis.append(False)
            elif is_volume and not name.startswith("SOFR"):vis.append("legendonly")
            elif name=="Gross activity (accepted par)" or name.startswith("Security: "):
                vis.append("legendonly")
            else:vis.append(True)
        buttons.append(dict(label=f,method="restyle",args=[{"visible":vis}]))
    fig.update_layout(
        title=title,template="plotly_dark",height=height,
        xaxis_title="Effective date" if mode=="rates" else (
            "Settlement-date observed positions" if stock else "Operation date"),
        yaxis_title=("Rate (%)" if mode=="rates" else (
            "Reported amount (native currency)" if mode=="swaps" else (
            "Accepted amount / observed balance (publisher units)"))),
        hovermode="x unified",barmode="group",
        legend=dict(x=1.22,xanchor="left",y=1,yanchor="top",
                    title="Series",font={"size":11},bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=65,r=310,t=75,b=65),
        updatemenus=[dict(type="buttons",direction="down",active=0,
             buttons=buttons,x=1.22,xanchor="left",y=menu_y,yanchor="top",
             showactive=True,borderwidth=0,bgcolor="rgba(0,0,0,0)",
             font={"size":12},pad={"r":3,"t":3,"b":3})],
        annotations=[dict(x=1.22,y=min(1,menu_y+.065),xref="paper",yref="paper",
            text="<b>Aggregation</b>",showarrow=False,xanchor="left",yanchor="bottom",
            font={"size":11})],
    )
    if volume:
        fig.update_layout(yaxis2=dict(title="Transaction volume (USD billions)",
             overlaying="y",side="right",showgrid=False,zeroline=False,
             title_standoff=12,tickfont={"size":10}))
    return fig
