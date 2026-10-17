"""Deterministic user-defined threshold / change / freshness signal evaluation.

These are research conditions, not LLM opinions or claims of imminent shocks.
No notification delivery is attempted; an external scheduler may periodically
re-evaluate the same serializable rules against authenticated snapshots.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict
from datetime import date
from decimal import Decimal
from typing import Any
from financial_intelligence_agent.engine.operators import Series,Point,day,dec

COMPARES={"above","below","crosses_above","crosses_below","change_above","change_below"}
@dataclass(frozen=True)
class Rule:
    name:str
    key:str
    condition:str
    threshold:Decimal
    horizon:int=1

    def __post_init__(self):
        if not self.name or len(self.name)>100:
            raise ValueError("Rule label required")
        if self.condition not in COMPARES:
            raise ValueError("Unknown signal condition")
        if type(self.horizon) is not int or not 1<=self.horizon<=365:
            raise ValueError("horizon must be 1..365 observation intervals")
        if dec(self.threshold) is None:
            raise ValueError("Threshold required")
        object.__setattr__(self,"threshold",dec(self.threshold))


def evaluate(rule:Rule,series:Series,*,as_of:date|str|None=None,
             expected_max_staleness_days:int=14,
             evaluation_date:date|str|None=None)->dict[str,Any]:
    if rule.key!=series.key:
        raise ValueError("Series key does not match rule")
    if type(expected_max_staleness_days) is not int or not 0<=expected_max_staleness_days<=365:
        raise ValueError("Invalid freshness tolerance")
    cutoff=day(as_of)
    selected=series.select(as_of)
    values=[p for p in selected.points if p.value is not None]
    if not values:
        raise ValueError("No observations for rule evaluation")
    recent=values[-1]
    past=values[-(rule.horizon+1)] if len(values)>rule.horizon else None
    trigger=None
    explanation="Not enough historical observations"
    if rule.condition=="above":
        trigger=recent.value>rule.threshold
        explanation="Current level is strictly greater than threshold"
    elif rule.condition=="below":
        trigger=recent.value<rule.threshold
        explanation="Current level is strictly less than threshold"
    elif past is not None:
        if rule.condition=="crosses_above":
            trigger=past.value<=rule.threshold and recent.value>rule.threshold
        elif rule.condition=="crosses_below":
            trigger=past.value>=rule.threshold and recent.value<rule.threshold
        elif rule.condition=="change_above":
            trigger=(recent.value-past.value)>rule.threshold
        else:
            trigger=(recent.value-past.value)<rule.threshold
        explanation="Compared two observed values separated by "+str(rule.horizon)+" non-null observations"
    # Freshness checks use a separate evaluation clock, not the as-of/vintage
    # selection date. This enables reproducible historical alert tests.
    checked=day(evaluation_date) or cutoff or date.today()
    age=(checked-recent.date).days
    return {
        "rule":{"name":rule.name,"series":rule.key,"condition":rule.condition,
                "threshold":str(rule.threshold),"horizon_observations":rule.horizon},
        "triggered":trigger,"status":"triggered" if trigger else ("clear" if trigger is False else "insufficient_history"),
        "observed_date":recent.date.isoformat(),"observed_value":str(recent.value),
        "comparison_date":past.date.isoformat() if past else None,
        "comparison_value":str(past.value) if past else None,
        "unit":series.unit,"observation_age_days":age,
        "stale":age>expected_max_staleness_days,
        "snapshot_fingerprint":series.fingerprint(),
        "interpretation":explanation,
        "warnings":[
            "Rule conditions are descriptive and do not imply financial risk causality.",
            "Observation freshness is distinct from publication or revised-data vintage.",
            "This execution does not schedule or deliver notifications.",
        ],
    }
