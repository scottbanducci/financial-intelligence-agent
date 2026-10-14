"""Allowlisted source metric definitions, independent of the data engine."""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class MetricSpec:
    name: str
    fred_id: str
    source_table: str
    source_unit: str
    frequency: str


# FRED's percentage rates are percentages, not fractions or basis points.
# Missing weekends/nonpublication dates must not be forward-filled implicitly.
RATE_SPECS: Mapping[str, MetricSpec] = {
    name: MetricSpec(name, fred_id, "gold.rates_daily", "percent", "daily_observation")
    for name, fred_id in {
        "sofr": "SOFR",
        "effr": "EFFR",
        "iorb": "IORB",
        "dtb3": "DTB3",
    }.items()
}

# Each spread is (left rate - right rate) * 100 basis points.
SPREADS = {
    "sofr_iorb_spread_bps": ("sofr", "iorb"),
    "sofr_effr_spread_bps": ("sofr", "effr"),
    "effr_iorb_spread_bps": ("effr", "iorb"),
    "dtb3_effr_spread_bps": ("dtb3", "effr"),
}
