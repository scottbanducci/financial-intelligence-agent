"""Read an exported rates_daily CSV as a fixed local analytical snapshot.

This is a *manual export adapter*, not a connection to live Databricks and not a
publication manifest. Source bytes are hashed so identical exports receive the
same snapshot ID. No third-party packages or credentials are needed.
"""

import csv
import hashlib
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from financial_intelligence_agent.analytics.series import parse_date, numeric
from financial_intelligence_agent.contracts.metrics import RATE_SPECS, SPREADS


REQUIRED_COLUMNS = frozenset(("observation_date", *RATE_SPECS))
# Derived spreads are optional in a future source. Validate if present downstream.
OPTIONAL_SPREAD_COLUMNS = frozenset(SPREADS)
NULL_MARKERS = frozenset(("", "null"))


class CsvRatesReader:
    """Immutable, date-filtered reader for a manually exported Gold rates CSV.

    Construction validates headers, date uniqueness and numeric formatting.
    Arithmetic and derived-spread consistency are validated by FundingConditionsTool.
    All rows are kept in memory; intended for bounded extracts, not full FRED history.
    """

    def __init__(self, path: str | Path):
        source = Path(path)
        raw = source.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.snapshot_id = f"manual-csv-sha256:{self.sha256}"

        try:
            # utf-8-sig handles the optional UTF-8 BOM emitted by some CSV tools.
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV must be UTF-8 encoded") from exc

        from io import StringIO
        records: list[dict[str, Any]] = []
        seen: set[date] = set()
        reader = csv.DictReader(StringIO(text, newline=""))
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)):
            raise ValueError("CSV has duplicate header names")
        missing = REQUIRED_COLUMNS.difference(columns)
        if missing:
            raise ValueError(f"CSV missing required columns: {', '.join(sorted(missing))}")
        present_numeric = set(RATE_SPECS) | (OPTIONAL_SPREAD_COLUMNS & set(columns))
        for line_num, raw_row in enumerate(reader, start=2):
            if None in raw_row:  # extra delimiters / malformed record
                raise ValueError(f"CSV line {line_num} has too many fields")
            cleaned: dict[str, Any] = {}
            for key in columns:
                val = raw_row[key]
                if val is None:
                    raise ValueError(f"CSV line {line_num}: missing field {key}")
                stripped = val.strip()
                cleaned[key] = None if stripped.lower() in NULL_MARKERS else stripped
            try:
                d = parse_date(cleaned["observation_date"])
            except ValueError as exc:
                raise ValueError(f"CSV line {line_num}: {exc}") from exc
            if d in seen:
                raise ValueError(f"CSV line {line_num}: duplicate observation_date {d.isoformat()}")
            seen.add(d)
            for field in present_numeric:
                try:
                    numeric(cleaned[field], name=field)
                except ValueError as exc:
                    raise ValueError(f"CSV line {line_num}: {exc}") from exc
            records.append(cleaned)
        if not records:
            raise ValueError("CSV contains no data rows")
        self._rows = tuple(records)

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def read_rates_through(self, cutoff: date) -> list[Mapping[str, Any]]:
        cutoff = parse_date(cutoff)
        return [dict(row) for row in self._rows if parse_date(row["observation_date"]) <= cutoff]
