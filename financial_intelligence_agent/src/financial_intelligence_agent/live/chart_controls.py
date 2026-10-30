"""Common, accessible analyst date selectors for Plotly time-series charts.

Streamlit segmented buttons are used instead of Plotly rangeselector so
selecting a longer period can request additional publisher history on
the server. They do not initiate API requests themselves.
"""
from __future__ import annotations
from datetime import date
import streamlit as st
from .date_ranges import PRESETS,DEFAULT,preset_window

def _select_preset(prefix:str,earliest:date)->None:
    selected=st.session_state[prefix+"_preset"]
    if selected is None:return
    a,b=preset_window(selected,date.today(),earliest=earliest)
    st.session_state[prefix+"_start"]=a
    st.session_state[prefix+"_end"]=b

def _manual_window(prefix:str)->None:
    st.session_state[prefix+"_preset"]=None

def window_controls(prefix:str,*,earliest:date,show_dates:bool=True)->tuple[date,date]:
    """Nine compact Yahoo-style buttons, default six months; optional dates.

    A date input sets Custom mode (none of the nine preset buttons remains
    selected). Streamlit callbacks update dates before widgets instantiate.
    """
    today=date.today()
    if prefix+"_start" not in st.session_state:
        a,b=preset_window(DEFAULT,today,earliest=earliest)
        st.session_state[prefix+"_start"]=a
        st.session_state[prefix+"_end"]=b
    st.segmented_control("Chart period",PRESETS,default=DEFAULT,
                         key=prefix+"_preset",selection_mode="single",
                         on_change=_select_preset,args=(prefix,earliest))
    if show_dates:
        a,b=st.columns(2)
        start=a.date_input("From",key=prefix+"_start",on_change=_manual_window,args=(prefix,))
        end=b.date_input("Through",key=prefix+"_end",on_change=_manual_window,args=(prefix,))
    else:
        start=st.session_state[prefix+"_start"]
        end=st.session_state[prefix+"_end"]
    if start>end:
        st.error("Date range is reversed. Choose an earlier start date.")
    return start,end
