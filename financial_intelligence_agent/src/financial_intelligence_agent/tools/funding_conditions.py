"""Evidence-bearing funding analysis, with injected data access and no LLM arithmetic."""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Protocol, Sequence

from financial_intelligence_agent.analytics.series import (
    align_subtract, index_daily_rows, numeric, parse_date
)
from financial_intelligence_agent.contracts.metrics import RATE_SPECS, SPREADS


class RatesReader(Protocol):
    """Production adapters may use DuckDB snapshots or Databricks SQL."""

    def read_rates_through(self, cutoff: date) -> Sequence[Mapping[str, Any]]: ...


class InMemoryRatesReader:
    """Local test and demo adapter; does not contact Databricks."""

    def __init__(self, rows: Sequence[Mapping[str, Any]]):
        self.rows = tuple(dict(row) for row in rows)

    def read_rates_through(self, cutoff: date) -> Sequence[Mapping[str, Any]]:
        return [row for row in self.rows if parse_date(row["observation_date"]) <= cutoff]


def _rounded_bp(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _percent(value: Decimal) -> float:
    return float(value)


def _validate_stored_spreads(d: date, row: Mapping[str, Any], rates: Mapping[str, Decimal | None]) -> None:
    for field, (left, right) in SPREADS.items():
        if rates[left] is None or rates[right] is None:
            continue
        expected = (rates[left] - rates[right]) * 100
        if field not in row:
            continue  # Pure synthetic inputs may carry only raw source rates.
        stored = numeric(row[field], name=field)
        if stored is None:
            raise ValueError(f"{d}: {field} is null despite complete source rates")
        if abs(stored - expected) > Decimal("0.000001"):
            raise ValueError(f"{d}: inconsistent {field}: stored={stored}, computed={expected}")


class FundingConditionsTool:
    """The LLM-visible call has only (as_of_date, lookback_days) arguments.

    Data reader and snapshot are injected at construction and cannot be changed
    through model-generated tool arguments. All results use the same snapshot.
    """

    def __init__(self, reader: RatesReader, *, snapshot_id: str):
        if not snapshot_id.strip():
            raise ValueError("snapshot_id cannot be blank")
        self.reader = reader
        self.snapshot_id = snapshot_id

    def get_funding_conditions(self, as_of_date: date | str, lookback_days: int = 30) -> dict[str, Any]:
        requested = parse_date(as_of_date)
        if isinstance(lookback_days, bool) or not isinstance(lookback_days, int):
            raise ValueError("lookback_days must be an integer")
        if not 1 <= lookback_days <= 3650:
            raise ValueError("lookback_days must be between 1 and 3650")

        indexed = index_daily_rows(self.reader.read_rates_through(requested))
        parsed: dict[date, dict[str, Decimal | None]] = {}
        for d, row in indexed.items():
            rates = {name: numeric(row.get(name), name=name) for name in RATE_SPECS}
            _validate_stored_spreads(d, row, rates)
            parsed[d] = rates

        core = ("sofr", "effr", "iorb")
        complete = {d: rates for d, rates in parsed.items() if all(rates[m] is not None for m in core)}
        if not complete:
            raise ValueError("No observations with complete SOFR/EFFR/IORB inputs at or before the requested date")

        end = max(complete)
        target_start = end - timedelta(days=lookback_days)
        # The latest available core observation at or before the target boundary.
        eligible_start = [d for d in complete if d <= target_start]
        start = max(eligible_start) if eligible_start else None
        in_window = sorted(d for d in complete if target_start <= d <= end)
        warnings: list[str] = []
        if end < requested:
            warnings.append(f"Latest complete core observation predates cutoff by {(requested-end).days} calendar days")
        if start is None:
            warnings.append("No complete baseline observation on or before the target start date")
        elif start < target_start:
            warnings.append(f"Baseline uses an observation {(target_start-start).days} days before target date")
        if len(in_window) < 2:
            warnings.append("Fewer than two complete core observations inside the requested window")

        current = complete[end]
        result: dict[str, Any] = {
            "tool": "get_funding_conditions",
            "tool_version": "1.0.0",
            "snapshot_id": self.snapshot_id,
            "requested_as_of_date": requested.isoformat(),
            "lookback_days": lookback_days,
            "window": {
                "target_start_date": target_start.isoformat(),
                "actual_baseline_date": start.isoformat() if start else None,
                "actual_end_date": end.isoformat(),
                "end_date_gap_days": (requested - end).days,
                "baseline_date_gap_days": (target_start - start).days if start else None,
                "complete_core_observations_in_window": len(in_window),
            },
            "latest_rates_percent": {
                name: _percent(current[name]) if current[name] is not None else None
                for name in RATE_SPECS
            },
            "spreads": {},
            "evidence": [],
            "warnings": warnings,
            "limitations": [
                "Latest downloaded observations may incorporate revisions; this is not a point-in-time historical reconstruction.",
                "Differences in funding spreads do not establish causality or forecast equity returns.",
                "DTB3 is optional; missing values are not forward-filled.",
            ],
        }

        # Evidence for latest published source values, with full source IDs.
        for name, spec in RATE_SPECS.items():
            if current[name] is not None:
                result["evidence"].append({
                    "id": f"{self.snapshot_id}:rates_daily:{end.isoformat()}:{name}",
                    "source_series_id": spec.fred_id,
                    "source_table": spec.source_table,
                    "observation_date": end.isoformat(),
                    "unit": "percent",
                    "value": _percent(current[name]),
                })

        # Align source rates on identical dates; compute in decimal, display to 0.01 bp.
        for spread_name, (left, right) in SPREADS.items():
            left_series = {d: r[left] for d, r in complete.items() if r[left] is not None}
            right_series = {d: r[right] for d, r in complete.items() if r[right] is not None}
            difference = align_subtract(left_series, right_series)
            spread_bps = {d: value * Decimal(100) for d, value in difference.items()}
            if end not in spread_bps:
                result["spreads"][spread_name] = {"status": "unavailable_at_end_date"}
                if spread_name == "dtb3_effr_spread_bps":
                    warnings.append("DTB3−EFFR unavailable on the latest complete core date")
                continue

            dates = [d for d in in_window if d in spread_bps]
            values = [spread_bps[d] for d in dates]
            min_date = min(dates, key=lambda d: (spread_bps[d], d))
            max_date = max(dates, key=lambda d: (spread_bps[d], -d.toordinal()))
            baseline = spread_bps.get(start) if start else None
            endpoint = spread_bps[end]
            info: dict[str, Any] = {
                "status": "ok" if baseline is not None else "insufficient_baseline",
                "unit": "basis_points",
                "source_series_ids": [RATE_SPECS[left].fred_id, RATE_SPECS[right].fred_id],
                "formula": f"({left.upper()} - {right.upper()}) * 100",
                "end_date": end.isoformat(),
                "end_bps": _rounded_bp(endpoint),
                "baseline_date": start.isoformat() if baseline is not None else None,
                "baseline_bps": _rounded_bp(baseline) if baseline is not None else None,
                "change_bps": _rounded_bp(endpoint - baseline) if baseline is not None else None,
                "min_bps": _rounded_bp(spread_bps[min_date]),
                "min_date": min_date.isoformat(),
                "max_bps": _rounded_bp(spread_bps[max_date]),
                "max_date": max_date.isoformat(),
                "observations": len(values),
                "evidence_id": f"{self.snapshot_id}:spread:{spread_name}:{end.isoformat()}",
            }
            result["spreads"][spread_name] = info
            # These are the minimal rows that support the end, baseline, and
            # extrema claims. Snapshot + formula + window permit re-computation.
            for evidence_date in sorted({end, min_date, max_date} | ({start} if baseline is not None else set())):
                result["evidence"].append({
                    "id": f"{self.snapshot_id}:spread:{spread_name}:{evidence_date.isoformat()}",
                    "observation_date": evidence_date.isoformat(),
                    "source_table": "gold.rates_daily",
                    "source_fields": [left, right],
                    "source_series_ids": info["source_series_ids"],
                    "source_values_percent": {
                        left: _percent(complete[evidence_date][left]),
                        right: _percent(complete[evidence_date][right]),
                    },
                    "formula": info["formula"],
                    "value": _rounded_bp(spread_bps[evidence_date]),
                    "unit": "basis_points",
                })
        return result
