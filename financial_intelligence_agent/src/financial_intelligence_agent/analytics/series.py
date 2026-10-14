"""Pure rate-series parsing and alignment, without Spark or DB dependencies."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Mapping, Iterable, Any


def parse_date(value: date | str) -> date:
    if isinstance(value, date) and not isinstance(value, str):
        return value
    if not isinstance(value, str):
        raise ValueError("Date must be ISO YYYY-MM-DD or datetime.date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO date: {value!r}") from exc


def numeric(value: Any, *, name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} cannot be boolean")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric value for {name}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Non-finite value for {name}")
    return result


def index_daily_rows(rows: Iterable[Mapping[str, Any]]) -> dict[date, dict[str, Any]]:
    """Index source rows and reject duplicated dates (no silent last-write-wins)."""
    indexed: dict[date, dict[str, Any]] = {}
    for row in rows:
        d = parse_date(row["observation_date"])
        if d in indexed:
            raise ValueError(f"Duplicate observation_date: {d.isoformat()}")
        indexed[d] = dict(row)
    return indexed


def align_subtract(
    left: Mapping[date, Decimal],
    right: Mapping[date, Decimal],
    *,
    right_lag_calendar_days: int = 0,
) -> dict[date, Decimal]:
    """Compute left[t] - right[t-lag_days], requiring exact dates on both sides.

    A 30-day calendar lag is NOT a 30-row shift. Missing dates stay missing.
    Future-looking (negative) lags are rejected in this first version.
    """
    if isinstance(right_lag_calendar_days, bool) or not isinstance(right_lag_calendar_days, int):
        raise ValueError("Lag must be an integer count of calendar days")
    if not 0 <= right_lag_calendar_days <= 3650:
        raise ValueError("Lag must be between 0 and 3650 days")
    lag = timedelta(days=right_lag_calendar_days)
    return {
        d: value - right[d - lag]
        for d, value in sorted(left.items())
        if d - lag in right
    }
