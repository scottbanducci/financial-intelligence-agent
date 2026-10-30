"""Common, accessible analyst date selectors for Plotly time-series charts.

Borderless Streamlit tertiary buttons are used instead of Plotly rangeselector
so selecting a longer period can request additional publisher history on the
server. They do not initiate API requests themselves.
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

def _choose_preset(prefix:str,selected:str,earliest:date,
                   all_start:Callable[[],date]|None=None)->None:
    """Tertiary button callback, independent of Streamlit widget-owned state."""
    st.session_state[prefix+"_preset"]=selected
    _select_preset(prefix,earliest,all_start)


def _manual_window(prefix:str)->None:
    st.session_state[prefix+"_preset"]=None

def _init_window(prefix:str,earliest:date)->date:
    """Initialize a range even when no chart or selector is rendered."""
    today=_browser_today()
    if prefix+"_start" not in st.session_state:
        a,b=preset_window(DEFAULT,today,earliest=earliest)
        st.session_state[prefix+"_start"]=a
        st.session_state[prefix+"_end"]=b
    if prefix+"_preset" not in st.session_state:
        st.session_state[prefix+"_preset"]=DEFAULT
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
    selected=st.session_state.get(prefix+"_preset")
    st.caption("Chart period")
    # No segmented-control DOM or grouped border: these are native
    # borderless tertiary buttons, each with its own stable widget key.
    if selected in PRESETS:
        st.markdown(
            "<style>.st-key-"+prefix+"_preset_"+selected+
            " button{background:rgba(22,143,136,.25)!important;"
            "color:#41ded0!important}</style>",unsafe_allow_html=True)
    with st.container(horizontal=True,gap="small",border=False,
                      key=prefix+"_period_toolbar"):
        for option in PRESETS:
            st.button(option,key=prefix+"_preset_"+option,
                      type="tertiary",width="content",
                      on_click=_choose_preset,
                      args=(prefix,option,earliest,all_start))
