"""Evidence-backed H.4.1/H.8 tools against a historical fixture, not live data."""
from __future__ import annotations
import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from financial_intelligence_agent.analytics.series import parse_date

FROZEN_CUTOFF = date(2026, 10, 10)

def number(value, field):
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field} is missing or invalid")
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field} is invalid") from exc
    if not d.is_finite():
        raise ValueError(f"{field} is non-finite")
    return d

def rounded(value):
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

class MacroSnapshot:
    def __init__(self, path):
        raw = Path(path).read_bytes()
        self.id = "fixture-sha256:" + hashlib.sha256(raw).hexdigest()
        self.data = json.loads(raw)
        for key in ("fed_balance_sheet", "bank_balance_sheet", "rates"):
            if not isinstance(self.data.get(key), dict):
                raise ValueError(f"Missing group {key}")
            parse_date(self.data[key]["observation_date"])

    def check_cutoff(self, as_of):
        cutoff = parse_date(as_of)
        if cutoff > FROZEN_CUTOFF:
            raise ValueError(f"Frozen sample ends {FROZEN_CUTOFF}; cannot answer later as-of dates")
        return cutoff

    def row(self, group, cutoff):
        row = self.data[group]
        if parse_date(row["observation_date"]) > cutoff:
            raise ValueError(f"No frozen observation for {group} at or before {cutoff}")
        return row

    def evidence(self, group, fields):
        row = self.data[group]
        return [{
            "id": f'{self.id}:{group}:{row["observation_date"]}',
            "source": "Federal Reserve H.4.1" if group == "fed_balance_sheet" else "Federal Reserve H.8",
            "observation_date": row["observation_date"],
            "fields": list(fields),
            "origin": "Repository test fixture, not a live query or historical-vintage verification",
        }]

    def get_fed_liquidity(self, as_of):
        row = self.row("fed_balance_sheet", self.check_cutoff(as_of))
        levels = ("total_assets", "reserve_balances", "tga_balance", "on_rrp")
        changes = ("total_assets_wow", "reserve_balances_wow", "tga_balance_wow", "on_rrp_wow")
        def billions(field):
            return rounded(number(row[field], field) / Decimal(1000))
        return {
            "tool": "get_fed_liquidity", "snapshot_id": self.id,
            "observation_date": row["observation_date"], "unit": "USD billions",
            "levels": {k: billions(k) for k in levels},
            "weekly_changes": {k: billions(k) for k in changes},
            "evidence": self.evidence("fed_balance_sheet", levels + changes),
            "warnings": [
                "H.4.1 TGA weekly-average convention is not synchronized with Wednesday level series; do not combine into a single weekly liquidity impulse.",
                "Weekly differences are reported fixture values, not independently recomputed from history.",
                "Frozen test fixture, not a live release or historically point-in-time dataset.",
            ],
        }

    def get_bank_credit(self, as_of):
        row = self.row("bank_balance_sheet", self.check_cutoff(as_of))
        levels = ("bank_credit", "deposits", "ci_loans", "real_estate_loans", "consumer_loans", "nondep_fi_loans")
        changes = ("bank_credit_wow", "bank_credit_yoy", "deposits_wow", "deposits_yoy")
        current = number(row["bank_credit"], "bank_credit")
        previous = current - number(row["bank_credit_yoy"], "bank_credit_yoy")
        if previous <= 0:
            raise ValueError("Year-ago bank credit must be positive")
        return {
            "tool": "get_bank_credit", "snapshot_id": self.id,
            "observation_date": row["observation_date"], "unit": "USD billions",
            "levels": {k: rounded(number(row[k], k)) for k in levels},
            "reported_changes": {k: rounded(number(row[k], k)) for k in changes},
            "bank_credit_yoy_pct": rounded(100 * (current - previous) / previous),
            "evidence": self.evidence("bank_balance_sheet", levels + changes),
            "warnings": [
                "H.8 series aggregate commercial banks, not individual institutions.",
                "Loan-category contraction does not establish breadth of contraction across banks.",
                "Frozen test fixture, not a live release or historically point-in-time dataset.",
            ],
        }

    def get_data_freshness(self, as_of):
        cutoff = self.check_cutoff(as_of)
        sources = {}
        for group in ("fed_balance_sheet", "bank_balance_sheet", "rates"):
            observed = parse_date(self.data[group]["observation_date"])
            sources[group] = {
                "observation_date": observed.isoformat(),
                "age_days_at_cutoff": (cutoff - observed).days if observed <= cutoff else None,
                "available_at_cutoff": observed <= cutoff,
            }
        return {
            "tool": "get_data_freshness", "snapshot_id": self.id,
            "frozen_cutoff": FROZEN_CUTOFF.isoformat(),
            "sources": sources,
            "evidence": self.evidence("fed_balance_sheet", ("observation_date",)) +
                        self.evidence("bank_balance_sheet", ("observation_date",)),
            "warnings": [
                "Observation dates are not publication timestamps or historical knowledge dates.",
                "Rates' last CSV row may lack SOFR/EFFR; use funding analysis for latest complete rates.",
            ],
        }
