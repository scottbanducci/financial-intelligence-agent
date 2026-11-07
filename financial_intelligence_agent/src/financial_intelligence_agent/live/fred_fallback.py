"""Fall back to full FRED history only when a date window has no observations.

The coverage loader already splits history into bounded publisher requests and
caches empty intervals. This policy checks the requested window explicitly:
a cached result from some *other* interval must not count as a successful load.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, TypeVar

T = TypeVar("T")


def _has_observations(result: object | None, start: date, end: date) -> bool:
    if result is None:
        return False
    return any(start <= point.date <= end for point in result.series.points)


def fetch_with_full_history_fallback(
    series_id: str,
    start: date,
    end: date,
    *,
    load: Callable[[str, date, date], T | None],
    history_start: Callable[[str], date],
    today: date,
) -> tuple[T, date, date, bool]:
    """Return a series and effective display range.

    First try the requested dates. On *empty observations only*, retrieve
    official FRED observation_start metadata and fetch the complete span
    through today, using the injected coverage-aware/batched loader.
    Publisher, credential, and pagination failures still propagate.
    """
    result = load(series_id, start, end)
    if _has_observations(result, start, end):
        return result, start, end, False

    first = history_start(series_id)
    if first > today:
        raise ValueError("FRED series has no observations dated through today")
    result = load(series_id, first, today)
    if not _has_observations(result, first, today):
        raise ValueError("FRED reports no numeric observations in its available history")
    return result, first, today, True
