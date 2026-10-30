"""Common, accessible analyst date selectors for Plotly time-series charts.

Streamlit segmented buttons are used instead of Plotly rangeselector so
selecting a longer period can request additional publisher history on
the server. They do not initiate API requests themselves.
"""
from __future__ import annotations
from datetime import date,datetime,timezone
from typing import Callable
from zoneinfo import ZoneInfo
import streamlit as st
from .date_ranges import PRESETS,DEFAULT,preset_window

def _browser_today()->date:
    """Date in the analyst's browser timezone, not the cloud worker's UTC."""
    try:
        tz=st.context.timezone
        if tz:return datetime.now(ZoneInfo(tz)).date()
    except (AttributeError,ValueError,KeyError):
        pass
    return datetime.now(timezone.utc).date()

def _select_preset(prefix:str,earliest:date,
                   all_start:Callable[[],date]|None=None)->None:
    selected=st.session_state[prefix+"_preset"]
    if selected is None:return
    if selected=="ALL" and all_start is not None:
        earliest=all_start()
    a,b=preset_window(selected,_browser_today(),earliest=earliest)
    st.session_state[prefix+"_start"]=a
    st.session_state[prefix+"_end"]=b

def _manual_window(prefix:str)->None:
    st.session_state[prefix+"_preset"]=None

def window_controls(prefix:str,*,earliest:date,show_dates:bool=True,
                    all_start:Callable[[],date]|None=None,
                    show_controls:bool=True)->tuple[date,date]:
    """Nine compact Yahoo-style buttons, default six months; optional dates.

    A date input sets Custom mode (none of the nine preset buttons remains
    selected). Streamlit callbacks update dates before widgets instantiate.
    With show_controls=False, initialize and return the range without rendering
    chart widgets, allowing the initial publisher fetch to precede the chart.
    """
    today=_browser_today()
    if prefix+"_start" not in st.session_state:
        a,b=preset_window(DEFAULT,today,earliest=earliest)
        st.session_state[prefix+"_start"]=a
        st.session_state[prefix+"_end"]=b
    if not show_controls:
        return (st.session_state[prefix+"_start"],
                st.session_state[prefix+"_end"])
    st.segmented_control("Chart period",PRESETS,default=DEFAULT,
                         key=prefix+"_preset",selection_mode="single",
                         on_change=_select_preset,args=(prefix,earliest,all_start))
    if show_dates:
        a,b=st.columns(2)
        start=a.date_input("From",key=prefix+"_start",
                           min_value=date(1600,1,1),max_value=today,
                           on_change=_manual_window,args=(prefix,))
        end=b.date_input("Through",key=prefix+"_end",
                         min_value=date(1600,1,1),max_value=today,
                         on_change=_manual_window,args=(prefix,))
    else:
        start=st.session_state[prefix+"_start"]
        end=st.session_state[prefix+"_end"]
    if start>end:
        st.error("Date range is reversed. Choose an earlier start date.")
    return start,end
