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

def _init_window(prefix:str,earliest:date)->date:
    """Initialize a range even when no chart or selector is rendered."""
    today=_browser_today()
    if prefix+"_start" not in st.session_state:
        a,b=preset_window(DEFAULT,today,earliest=earliest)
        st.session_state[prefix+"_start"]=a
        st.session_state[prefix+"_end"]=b
    return today


def window_controls(prefix:str,*,earliest:date,show_dates:bool=True,
                    all_start:Callable[[],date]|None=None,
                    show_controls:bool=True)->tuple[date,date]:
    """Render the From/Through inputs before loading a chart.

    The period buttons are intentionally separate: call period_controls()
    immediately before the visible chart. Keeping the selectors apart also
    allows the initial publisher request to use the default six-month window.
    A manually edited date range clears the selected period preset.
    """
    today=_init_window(prefix,earliest)
    if not show_controls:
        return (st.session_state[prefix+"_start"],
                st.session_state[prefix+"_end"])
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


def period_controls(prefix:str,*,earliest:date,
                    all_start:Callable[[],date]|None=None)->None:
    """Render borderless date presets immediately above an existing chart.

    The caller must check that there are observations to plot before calling.
    Preset callbacks run before the next Streamlit rerun and update the same
    date keys consumed by window_controls().
    """
    _init_window(prefix,earliest)
    st.segmented_control("Chart period",PRESETS,default=DEFAULT,
                         key=prefix+"_preset",selection_mode="single",
                         on_change=_select_preset,args=(prefix,earliest,all_start))
