"""Provider-independent, deterministic time-series operator library.

The data contract does not know FRED, FFIEC, NYFed, pandas or Spark.
Observation date is NOT publication date. Point-in-time queries fail closed
when availability metadata is absent, avoiding invented historical knowledge.
No eval, dynamic Python, arbitrary SQL or forward fills.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from math import log, sqrt, exp
from statistics import median
from typing import Any, Callable, Iterable, Mapping
import hashlib
import json
import re

MAX_POINTS = 10000
MAX_WINDOW = 1000
MAX_STEPS = 40
KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,95}$")


def dec(value: Any) -> Decimal | None:
    if value is None or value == "" or value == ".":
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean cannot represent an observation")
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ValueError("Invalid numeric observation") from exc
    if not result.is_finite():
        raise ValueError("Nonfinite numeric observation")
    return result


def day(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    if type(value) is not str:
        raise ValueError("Expected an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Expected an ISO date") from exc


@dataclass(frozen=True)
class Point:
    date: date
    value: Decimal | None
    available_at: date | None = None
    revision: int = 0

    def __post_init__(self):
        object.__setattr__(self, "date", day(self.date))
        object.__setattr__(self, "available_at", day(self.available_at))
        object.__setattr__(self, "value", dec(self.value))
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        if self.available_at and self.available_at < self.date:
            # Early release ahead of stated observation period can be real, but
            # must be represented by the provider-specific adapter explicitly.
            raise ValueError("Availability cannot precede observation date")


@dataclass(frozen=True)
class Series:
    key: str
    source: str
    unit: str
    frequency: str
    points: tuple[Point, ...]
    convention: str = "observation_date"
    lineage: tuple[str, ...] = ()

    def __post_init__(self):
        if not KEY.fullmatch(self.key) or not KEY.fullmatch(self.source):
            raise ValueError("Invalid series/source identifier")
        if not self.unit or len(self.unit) > 100:
            raise ValueError("Unit is required")
        if self.frequency not in {"daily", "weekly", "monthly", "quarterly", "annual", "irregular"}:
            raise ValueError("Unsupported frequency")
        pts = tuple(self.points)
        if not pts or len(pts) > MAX_POINTS:
            raise ValueError("Series must contain 1..10000 observations")
        if any(not isinstance(p, Point) for p in pts):
            raise ValueError("Points must be Point instances")
        unique = {(p.date, p.available_at, p.revision) for p in pts}
        if len(unique) != len(pts):
            raise ValueError("Duplicate observation and revision")
        object.__setattr__(self, "points", pts)

    def select(self, as_of: date | str | None = None) -> "Series":
        """Resolve revisions, optionally requiring verifiable availability.

        Without as_of: latest *provided* release for each observation (not a
        historically available vintage). With as_of: unpublished/unknown
        availability values are excluded, no fallback to the latest revision.
        """
        cutoff = day(as_of)
        by_date: dict[date, Point] = {}
        for point in self.points:
            if cutoff is not None and (point.date > cutoff or point.available_at is None or point.available_at > cutoff):
                continue
            prev = by_date.get(point.date)
            rank = (point.available_at or date.min, point.revision)
            if prev is None or rank > (prev.available_at or date.min, prev.revision):
                by_date[point.date] = point
        if not by_date:
            raise ValueError("No observations satisfy as-of/vintage requirements")
        return replace(self, points=tuple(by_date[k] for k in sorted(by_date)))

    def pairs(self) -> list[tuple[date, Decimal | None]]:
        return [(p.date, p.value) for p in self.select().points]

    def fingerprint(self) -> str:
        payload = {
            "key": self.key, "source": self.source, "unit": self.unit,
            "frequency": self.frequency, "convention": self.convention,
            "points": [(p.date.isoformat(), str(p.value), str(p.available_at), p.revision) for p in self.points],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class OpSpec:
    name: str
    group: str
    arity: int
    unit_policy: str
    windowed: bool = False
    description: str = ""


UNARY = {
    "identity": "identity", "absolute": "absolute", "negative": "negative",
    "sign": "sign", "square": "square", "square_root": "square_root",
    "natural_log": "natural_log", "log10": "log10", "reciprocal": "reciprocal",
    "cumulative_sum": "cumulative_sum", "cumulative_product": "cumulative_product",
    "first_difference": "first_difference", "percent_change": "percent_change",
    "basis_point_change": "basis_point_change", "log_return": "log_return",
    "second_difference": "second_difference", "year_over_year_change": "year_over_year_change",
    "year_over_year_percent": "year_over_year_percent",
    "drawdown_percent": "drawdown_percent", "expanding_mean": "expanding_mean",
    "running_max": "running_max", "running_min": "running_min",
}
ROLLING = {
    "rolling_sum", "rolling_mean", "rolling_min", "rolling_max",
    "rolling_std", "rolling_zscore", "rolling_median",
    "rolling_range", "rolling_percentile_rank", "rolling_count",
    "rolling_change", "rolling_percent_change", "ema",
    "annualized_volatility", "rolling_compound_return",
}
BINARY = {
    "add", "subtract", "multiply", "divide", "ratio", "spread_basis_points",
    "percent_difference", "rolling_correlation", "rolling_covariance",
    "rolling_beta", "relative_strength",
}

OPERATOR_SPECS = {
    **{name: OpSpec(name,"transformation",1,"derived") for name in UNARY},
    **{name: OpSpec(name,"rolling_statistics",1,"derived",True) for name in ROLLING},
    **{name: OpSpec(name,"cross_series",2,"derived", name.startswith("rolling_")) for name in BINARY},
}
SAME_UNITS = {"add", "subtract", "spread_basis_points", "percent_difference", "relative_strength"}
DIMENSIONLESS = {
    "sign", "natural_log", "log10", "percent_change", "log_return", "year_over_year_percent",
    "drawdown_percent", "rolling_zscore", "rolling_percentile_rank", "rolling_percent_change",
    "annualized_volatility", "rolling_compound_return", "rolling_correlation",
    "rolling_beta", "ratio", "divide", "percent_difference", "relative_strength",
}
PERCENT_UNITS = {"percent_change", "year_over_year_percent", "drawdown_percent",
                 "rolling_percent_change", "percent_difference", "rolling_compound_return"}


def _window(values: list[Decimal | None], i: int, window: int) -> list[Decimal] | None:
    if i + 1 < window:
        return None
    selected = values[i - window + 1:i + 1]
    if any(v is None for v in selected):
        return None
    return selected  # type: ignore[return-value]


def _std(values: list[Decimal]) -> Decimal:
    if len(values) < 2:
        return Decimal(0)
    m = sum(values) / len(values)
    return (sum((v-m)**2 for v in values) / (len(values)-1)).sqrt()


def _series_result(name: str, sources: tuple[Series, ...], points: list[Point], unit: str) -> Series:
    return Series(
        key="result."+name, source="operator", unit=unit,
        frequency=sources[0].frequency, convention=sources[0].convention,
        points=tuple(points),
        lineage=tuple(s.fingerprint() for s in sources) + (name,),
    )


def execute(name: str, *sources: Series, window: int = 5, periods_per_year: int = 252,
            as_of: date | str | None = None) -> Series:
    """Execute a typed, allowlisted operation with exact-date alignment.

    Windows are observation counts, never implicitly calendar days.
    Zero denominators, absent baselines and missing windows result in null,
    not zero, infinity or look-ahead/forward fill.
    """
    if name not in OPERATOR_SPECS:
        raise ValueError("Unknown operator")
    spec = OPERATOR_SPECS[name]
    if len(sources) != spec.arity:
        raise ValueError("Wrong operator arity")
    if type(window) is not int or not 2 <= window <= MAX_WINDOW:
        raise ValueError("window must be an integer in [2,1000]")
    if type(periods_per_year) is not int or not 1 <= periods_per_year <= 366:
        raise ValueError("periods_per_year must be in [1,366]")
    if any(s.frequency != sources[0].frequency for s in sources):
        raise ValueError("Explicit resampling required for mixed frequencies")
    if len(sources) == 2 and sources[0].convention != sources[1].convention:
        raise ValueError("Incompatible observation conventions")
    if name in SAME_UNITS and len(sources) == 2 and sources[0].unit != sources[1].unit:
        raise ValueError("Incompatible units; explicit conversion required")
    if name in {"spread_basis_points", "basis_point_change"} and sources[0].unit != "percent":
        raise ValueError("Basis-point conversion requires percent-valued input")
    resolved = [s.select(as_of) for s in sources]
    reference = {p.date: p for p in resolved[0].points}
    other = {p.date: p for p in resolved[1].points} if len(resolved) == 2 else {}
    dates = sorted(set(reference) & set(other) if other else reference)
    if not dates:
        raise ValueError("No exactly aligned observations")
    left = [reference[d].value for d in dates]
    right = [other[d].value for d in dates] if other else []
    result: list[Point] = []
    previous_peak: Decimal | None = None
    cumul = Decimal(0)
    compound = Decimal(1)
    ema_state: Decimal | None = None

    for i, d in enumerate(dates):
        a = left[i]
        b = right[i] if right else None
        v: Decimal | None = None
        with localcontext() as ctx:
            ctx.prec = 34
            try:
                if name in BINARY:
                    if a is not None and b is not None:
                        if name == "add": v = a + b
                        elif name == "subtract": v = a - b
                        elif name == "multiply": v = a * b
                        elif name in {"divide", "ratio"}: v = a / b if b else None
                        elif name == "relative_strength": v = a / b if b else None
                        elif name == "spread_basis_points": v = (a - b) * 100
                        elif name == "percent_difference": v = (a - b) / b * 100 if b else None
                        else:
                            aa, bb = _window(left,i,window), _window(right,i,window)
                            if aa is not None and bb is not None:
                                ma,mb = sum(aa)/window,sum(bb)/window
                                cov = sum((x-ma)*(y-mb) for x,y in zip(aa,bb))/(window-1)
                                if name == "rolling_covariance": v = cov
                                elif name == "rolling_correlation":
                                    denom = _std(aa)*_std(bb)
                                    v = cov/denom if denom else None
                                else:
                                    var_b = _std(bb)**2
                                    v = cov/var_b if var_b else None
                elif name in ROLLING:
                    seq = _window(left, i, window)
                    if name == "ema":
                        if a is None: ema_state = None
                        elif ema_state is None: ema_state = a
                        else: ema_state = a*Decimal(2)/(window+1) + ema_state*(window-1)/(window+1)
                        v = ema_state if i+1 >= window else None
                    elif seq is not None:
                        if name == "rolling_sum": v = sum(seq)
                        elif name == "rolling_mean": v = sum(seq)/window
                        elif name == "rolling_min": v = min(seq)
                        elif name == "rolling_max": v = max(seq)
                        elif name == "rolling_std": v = _std(seq)
                        elif name == "rolling_median": v = Decimal(str(median(seq)))
                        elif name == "rolling_range": v = max(seq)-min(seq)
                        elif name == "rolling_count": v = Decimal(window)
                        elif name == "rolling_zscore":
                            std = _std(seq)
                            v = (a-sum(seq)/window)/std if std else None
                        elif name == "rolling_percentile_rank":
                            v = Decimal(sum(x <= a for x in seq)) * 100 / window
                        elif name == "rolling_change": v = seq[-1] - seq[0]
                        elif name == "rolling_percent_change": v = (seq[-1]/seq[0]-1)*100 if seq[0] else None
                        elif name == "rolling_compound_return":
                            v = (seq[-1]/seq[0]-1)*100 if seq[0] else None
                        elif name == "annualized_volatility":
                            returns = [(seq[j]/seq[j-1]-1) for j in range(1,len(seq)) if seq[j-1]]
                            v = _std(returns)*Decimal(periods_per_year).sqrt()*100 if len(returns)==window-1 else None
                elif a is not None:
                    if name == "identity": v = a
                    elif name == "absolute": v = abs(a)
                    elif name == "negative": v = -a
                    elif name == "sign": v = Decimal((a>0)-(a<0))
                    elif name == "square": v = a*a
                    elif name == "square_root": v = a.sqrt() if a>=0 else None
                    elif name == "natural_log": v = Decimal(str(log(float(a)))) if a>0 else None
                    elif name == "log10": v = Decimal(str(log(float(a),10))) if a>0 else None
                    elif name == "reciprocal": v = 1/a if a else None
                    elif name == "cumulative_sum": cumul += a; v = cumul
                    elif name == "cumulative_product": compound *= a; v = compound
                    elif name == "running_max":
                        previous_peak = a if previous_peak is None else max(previous_peak,a); v=previous_peak
                    elif name == "running_min":
                        previous_peak = a if previous_peak is None else min(previous_peak,a); v=previous_peak
                    elif name == "drawdown_percent":
                        previous_peak = a if previous_peak is None else max(previous_peak,a)
                        v = (a/previous_peak-1)*100 if previous_peak else None
                    elif name == "expanding_mean":
                        values = [x for x in left[:i+1] if x is not None]
                        v = sum(values)/len(values)
                    elif name in {"first_difference","percent_change","basis_point_change","log_return","second_difference",
                                  "year_over_year_change","year_over_year_percent"}:
                        if name.startswith("year_over_year"):
                            if sources[0].frequency == "irregular":
                                raise ValueError("Year-over-year change requires explicit frequency")
                            if sources[0].frequency == "weekly":
                                comparison = d - timedelta(days=364)
                            else:
                                try:
                                    comparison = d.replace(year=d.year-1)
                                except ValueError:
                                    comparison = d.replace(year=d.year-1,day=28)
                            baseline = reference.get(comparison)
                            prev = baseline.value if baseline else None
                        else:
                            prev = left[i-1] if i else None
                        if name == "second_difference": prev = None
                        if name == "second_difference" and i>=2 and left[i-1] is not None and left[i-2] is not None:
                            v = a-2*left[i-1]+left[i-2]
                        elif prev is not None:
                            if name in {"first_difference","year_over_year_change"}: v = a-prev
                            elif name == "basis_point_change": v = (a-prev)*100
                            elif name in {"percent_change","year_over_year_percent"}: v = (a/prev-1)*100 if prev else None
                            elif name == "log_return": v = Decimal(str(log(float(a/prev)))) if a>0 and prev>0 else None
            except (InvalidOperation, ZeroDivisionError, OverflowError, ValueError):
                v = None
        if v is not None and not v.is_finite():
            v = None
        first = reference[d]
        second = other.get(d)
        # Propagate the publication dates of the *actual dependency window*.
        # Prior versions always scanned the full prefix: that was quadratic
        # and incorrectly tainted independent single-date transformations.
        prefix_ops = {"cumulative_sum","cumulative_product","running_max",
                      "running_min","drawdown_percent","expanding_mean","ema"}
        change_ops = {"first_difference","percent_change",
                      "basis_point_change","log_return"}
        if name in prefix_ops:
            starts = 0
        elif name in ROLLING or name.startswith("rolling_"):
            starts = max(0,i-window+1)
        elif name == "second_difference":
            starts = max(0,i-2)
        elif name in change_ops:
            starts = max(0,i-1)
        else:
            starts = i
        depends = [reference[dates[j]] for j in range(starts,i+1)]
        if other:
            depends.extend(other[dates[j]] for j in range(starts,i+1))
        if name.startswith("year_over_year") and i>0:
            if sources[0].frequency=="weekly":
                comparison=d-timedelta(days=364)
            else:
                try:
                    comparison=d.replace(year=d.year-1)
                except ValueError:
                    comparison=d.replace(year=d.year-1,day=28)
            if comparison in reference:
                depends.append(reference[comparison])
        known = [p.available_at for p in depends]
        available = max(known) if all(x is not None for x in known) else None
        result.append(Point(d,v,available))
    unit = sources[0].unit
    if name in PERCENT_UNITS: unit = "percent"
    elif name in DIMENSIONLESS: unit = "dimensionless"
    elif name in {"spread_basis_points","basis_point_change"}: unit = "basis_points"
    elif name in {"rolling_covariance"}: unit = sources[0].unit+" * "+sources[1].unit
    elif name == "multiply": unit = sources[0].unit+" * "+sources[1].unit
    return _series_result(name,sources,result,unit)


def execute_plan(inputs: Mapping[str,Series], steps: list[Mapping[str,Any]], as_of: date | str | None = None) -> dict[str,Series]:
    """Bounded JSON-like execution DAG, in topological order; no code evaluation.

    Each step: {'id': 'spread', 'operator': 'subtract',
                'inputs':['SOFR','IORB'], 'window': 5}
    """
    if not isinstance(steps,list) or len(steps)>MAX_STEPS:
        raise ValueError("At most 40 plan steps")
    environment = dict(inputs)
    for step in steps:
        if not isinstance(step,dict) or set(step)-{"id","operator","inputs","window","periods_per_year"}:
            raise ValueError("Unexpected plan fields")
        key = step.get("id")
        if not isinstance(key,str) or not KEY.fullmatch(key) or key in environment:
            raise ValueError("Invalid or duplicate plan step ID")
        refs = step.get("inputs")
        if not isinstance(refs,list) or any(not isinstance(r,str) or r not in environment for r in refs):
            raise ValueError("Plan inputs must reference existing series")
        name = step.get("operator")
        result = execute(name,*(environment[r] for r in refs),
                         window=step.get("window",5),
                         periods_per_year=step.get("periods_per_year",252),as_of=as_of)
        environment[key] = replace(result,key=key)
    return environment
