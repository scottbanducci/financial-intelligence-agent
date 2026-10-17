"""Four-tool financial assistant. The LLM can only choose a tool, never render numbers."""
import json
import re
from dataclasses import dataclass
from datetime import date

from financial_intelligence_agent.data.csv_rates import CsvRatesReader
from financial_intelligence_agent.tools.funding_conditions import FundingConditionsTool
from financial_intelligence_agent.tools.snapshot_insights import MacroSnapshot, FROZEN_CUTOFF

TOOLS = {
    "get_funding_conditions": "SOFR, EFFR, IORB and short-term funding spreads",
    "get_fed_liquidity": "Federal Reserve balance sheet, reserves, TGA and ON RRP",
    "get_bank_credit": "H.8 commercial bank credit and deposits",
    "get_data_freshness": "Observation dates, provenance and stale-data checks",
}

@dataclass
class Engine:
    macro: MacroSnapshot
    rates: CsvRatesReader

    @staticmethod
    def route(question):
        text = question.lower()
        if re.search(r"\b(fresh|stale|date|source|provenance|updated|coverage)\b", text):
            return "get_data_freshness"
        if re.search(r"\b(bank|loans?|deposits?|credit|ndfi|commercial)\b", text):
            return "get_bank_credit"
        if re.search(r"\b(reserves?|balance.sheet|tga|rrp|qt|mbs|treasur|fed assets)\b", text):
            return "get_fed_liquidity"
        return "get_funding_conditions"

    @staticmethod
    def route_llm(question, model):
        """Optional model-based function call: arguments MUST be an empty object."""
        from openai import OpenAI
        tools = [{
            "type": "function",
            "function": {
                "name": name, "description": description,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        } for name, description in TOOLS.items()]
        completion = OpenAI(timeout=15.0, max_retries=0).chat.completions.create(
            model=model, temperature=0, tool_choice="required", tools=tools,
            messages=[
                {"role": "system", "content": "Select exactly one tool. Do not write analysis or invent data. All tool arguments are {}."},
                {"role": "user", "content": question[:1000]},
            ],
        )
        calls = completion.choices[0].message.tool_calls or []
        if len(calls) != 1 or calls[0].function.name not in TOOLS:
            raise ValueError("Model did not return one allowlisted tool")
        if json.loads(calls[0].function.arguments or "{}") != {}:
            raise ValueError("Model supplied unexpected tool arguments")
        return calls[0].function.name

    def invoke(self, name, *, cutoff=FROZEN_CUTOFF, lookback_days=30):
        if name not in TOOLS:
            raise ValueError("Unknown tool")
        self.macro.check_cutoff(cutoff)
        if name == "get_funding_conditions":
            if type(lookback_days) is not int or not 1 <= lookback_days <= 365:
                raise ValueError("lookback_days must be an integer in [1,365]")
            return FundingConditionsTool(self.rates, snapshot_id=self.rates.snapshot_id).get_funding_conditions(
                cutoff, lookback_days
            )
        return getattr(self.macro, name)(cutoff)

    def answer(self, question, *, cutoff=FROZEN_CUTOFF, lookback_days=30, use_llm=False, model="gpt-4.1-mini"):
        if not question or not question.strip():
            raise ValueError("Question must not be empty")
        name = self.route_llm(question, model) if use_llm else self.route(question)
        result = self.invoke(name, cutoff=cutoff, lookback_days=lookback_days)
        return {
            "question": question[:1000],
            "route_mode": "model-selected" if use_llm else "rule-selected",
            "selected_tool": name,
            "report": render(result),
            "result": result,
            "trace": [
                {"step": "select", "tool": name},
                {"step": "execute", "snapshot_id": result["snapshot_id"]},
                {"step": "render", "model_generated_numbers": False},
            ],
        }

def render(r):
    """Deterministic text from calculated fields only, no model-generated claims."""
    tool = r["tool"]
    if tool == "get_funding_conditions":
        spreads = r["spreads"]
        primary = spreads["sofr_iorb_spread_bps"]
        secondary = spreads["sofr_effr_spread_bps"]
        delta = primary["change_bps"]
        change = "unavailable" if delta is None else f"{delta:+.2f} bp"
        return (f'**Funding — {r["window"]["actual_end_date"]}**. SOFR minus IORB '
                f'**{primary["end_bps"]:+.2f} bp**, window change **{change}**. '
                f'SOFR minus EFFR **{secondary["end_bps"]:+.2f} bp**. '
                'These differences are descriptive, not a causal stress forecast.')
    if tool == "get_fed_liquidity":
        level, delta = r["levels"], r["weekly_changes"]
        return (f'**Fed balance sheet — {r["observation_date"]}**. '
                f'Total assets **USD {level["total_assets"]:,.2f}B**; '
                f'reserves **USD {level["reserve_balances"]:,.2f}B** '
                f'(reported weekly change **USD {delta["reserve_balances_wow"]:+,.2f}B**). '
                f'TGA **USD {level["tga_balance"]:,.2f}B**; ON RRP **USD {level["on_rrp"]:,.2f}B**. '
                'Do not treat these non-synchronized series as a weekly liquidity impulse.')
    if tool == "get_bank_credit":
        level, change = r["levels"], r["reported_changes"]
        return (f'**Bank credit — {r["observation_date"]}**. '
                f'Aggregate credit **USD {level["bank_credit"]:,.2f}B** '
                f'(**{r["bank_credit_yoy_pct"]:+.2f}% YoY**, '
                f'USD {change["bank_credit_yoy"]:+,.2f}B YoY). '
                f'Deposits **USD {level["deposits"]:,.2f}B**. '
                'This cannot establish the number of individual banks in contraction.')
    if tool == "get_data_freshness":
        sources = r["sources"]
        return (f'**Frozen sample cutoff: {r["frozen_cutoff"]}**. '
                f'H.4.1 date **{sources["fed_balance_sheet"]["observation_date"]}**; '
                f'H.8 date **{sources["bank_balance_sheet"]["observation_date"]}**; '
                f'rates CSV last row **{sources["rates"]["observation_date"]}**. '
                'This is historical demonstration data, not live data.')
    raise ValueError("Unsupported tool result")
