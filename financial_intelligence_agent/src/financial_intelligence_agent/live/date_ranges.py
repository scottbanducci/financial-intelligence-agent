"""Shared analyst chart windows and inclusive coverage-aware requests.

Selecting shorter windows never fetches a source again if a previous request
covered the dates, including periods without observations. Longer requests
fetch ONLY the missing segments, split into bounded publisher calls.
"""
from __future__ import annotations
from datetime import date,timedelta
from calendar import monthrange
from dataclasses import dataclass
from typing import Callable,TypeVar

PRESETS=("5D","1M","3M","6M","YTD","1Y","5Y","10Y","ALL")
DEFAULT="6M"

def months_before(today:date,months:int)->date:
    year=today.year-(months-1)//12 if today.month==1 else today.year
    total=today.year*12+today.month-1-months
    year,month=divmod(total,12)
    return date(year,month+1,min(today.day,monthrange(year,month+1)[1]))

def preset_window(option:str,today:date,*,earliest:date|None=None)->tuple[date,date]:
    if option not in PRESETS:raise ValueError("Invalid chart range")
    if option=="ALL":
        if earliest is None:raise ValueError("Source history start is required for ALL")
        return min(earliest,today),today
    if option=="5D":start=today-timedelta(days=5)
    elif option=="1M":start=months_before(today,1)
    elif option=="3M":start=months_before(today,3)
    elif option=="6M":start=months_before(today,6)
    elif option=="YTD":start=date(today.year,1,1)
    elif option=="1Y":start=months_before(today,12)
    elif option=="5Y":start=months_before(today,60)
    else:start=months_before(today,120)
    return start,today

def missing_intervals(
    requested_start:date,requested_end:date,
    covered:list[tuple[date,date]],
)->list[tuple[date,date]]:
    if requested_start>requested_end:raise ValueError("Reversed chart window")
    normalized=sorted((max(requested_start,a),min(requested_end,b))
                      for a,b in covered if b>=requested_start and a<=requested_end)
    remaining=[]
    current=requested_start
    for a,b in normalized:
        if b<current:continue
        if a>current:remaining.append((current,a-timedelta(days=1)))
        current=max(current,b+timedelta(days=1))
        if current>requested_end:break
    if current<=requested_end:remaining.append((current,requested_end))
    return remaining

def merge_intervals(intervals:list[tuple[date,date]])->list[tuple[date,date]]:
    merged=[]
    for a,b in sorted(intervals):
        if a>b:raise ValueError("Reversed covered interval")
        if merged and a<=merged[-1][1]+timedelta(days=1):
            merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    return merged

def batched_missing(
    start:date,end:date,covered:list[tuple[date,date]],*,
    max_days:int=365*12,
)->list[tuple[date,date]]:
    if max_days<1:raise ValueError("Invalid publisher batch length")
    tasks=[]
    for a,b in missing_intervals(start,end,covered):
        while a<=b:
            z=min(b,a+timedelta(days=max_days-1))
            tasks.append((a,z))
            a=z+timedelta(days=1)
    return tasks

T=TypeVar("T")
@dataclass
class Coverage:
    """Current cached result plus exact dates requested, even if empty."""
    intervals:list[tuple[date,date]]
    result:object|None=None

def extend(
    cache:Coverage,
    start:date,end:date,
    loader:Callable[[date,date],T|None],
    merge:Callable[[T|None,T],T],
    *,batch_days:int=365*12,max_calls:int=100,
)->Coverage:
    spans=batched_missing(start,end,cache.intervals,max_days=batch_days)
    if len(spans)>max_calls:
        raise ValueError(f"Requested history needs {len(spans)} API calls; choose a shorter window")
    for a,b in spans:
        # Do not mark failed requests as covered.
        incoming=loader(a,b)
        if incoming is not None:
            cache.result=merge(cache.result,incoming)
        cache.intervals=merge_intervals(cache.intervals+[(a,b)])
    return cache
