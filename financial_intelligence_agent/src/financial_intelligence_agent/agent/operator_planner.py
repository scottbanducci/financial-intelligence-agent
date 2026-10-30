"""Validated LLM-to-operator plans; the model chooses math, never computes values.

A plan is a small allowlisted DAG. Clients can submit a plan directly without
an LLM; model planning is strictly optional and requires external credentials.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

from financial_intelligence_agent.engine.operators import Series,OPERATOR_SPECS,execute_plan,MAX_POINTS

MAX_AGENT_STEPS=12

@dataclass(frozen=True)
class AnalyticalService:
    catalog: Mapping[str,Series]

    def __post_init__(self):
        if not self.catalog or len(self.catalog)>150:
            raise ValueError("Expected 1..150 allowlisted series")
        for key,series in self.catalog.items():
            if key!=series.key:
                raise ValueError("Catalog key must match Series key")
            if not isinstance(series,Series):
                raise ValueError("Invalid catalog entry")

    def capabilities(self) -> dict[str,Any]:
        return {
            "series":[{
                "key":key,"source":s.source,"unit":s.unit,"frequency":s.frequency,
                "convention":s.convention,"count":len(s.points),
                "sha256":s.fingerprint()
            } for key,s in sorted(self.catalog.items())],
            "operators":[{
                "name":name,"arity":spec.arity,"family":spec.group,
                "windowed":spec.windowed
            } for name,spec in sorted(OPERATOR_SPECS.items())],
            "maximum_steps":MAX_AGENT_STEPS,
            "guarantees":["no arbitrary SQL","no dynamic code","exact date intersection",
                          "explicit units","frozen input fingerprints"],
        }

    def run(self, plan: Mapping[str,Any], *, as_of: date | str | None = None) -> dict[str,Any]:
        if not isinstance(plan,dict) or set(plan)!={"steps","result_id"}:
            raise ValueError("Plan must contain exactly steps and result_id")
        steps=plan["steps"]
        if not isinstance(steps,list) or not 1<=len(steps)<=MAX_AGENT_STEPS:
            raise ValueError("Agent plan requires 1..12 steps")
        if not isinstance(plan["result_id"],str):
            raise ValueError("Result id must be string")
        # No provider network calls, no runtime query text; only pinned input series.
        values=execute_plan(self.catalog,steps,as_of=as_of)
        key=plan["result_id"]
        if key not in {step.get("id") for step in steps if isinstance(step,dict)}:
            raise ValueError("Result must name an output step")
        result=values[key]
        observations=[p for p in result.points if p.value is not None]
        last=observations[-1] if observations else None
        trace=[{"step":i+1,"operator":step["operator"],"inputs":step["inputs"],
                "output_id":step["id"]} for i,step in enumerate(steps)]
        return {
            "result_id":key,"unit":result.unit,"frequency":result.frequency,
            "observation_count":len(result.points),
            "non_null_count":len(observations),
            "latest_observation":{
                "date":last.date.isoformat(),"value":str(last.value)
            } if last else None,
            "sample":[{"date":p.date.isoformat(),"value":str(p.value) if p.value is not None else None}
                     for p in result.points[-60:]],
            "source_series":[{"key":k,"source":s.source,"fingerprint":s.fingerprint(),
                              "unit":s.unit,"frequency":s.frequency}
                             for k,s in sorted(self.catalog.items())],
            "result_lineage":list(result.lineage),
            "execution_trace":trace,
            "warnings":[
                "Operator results are deterministic for the supplied snapshot; this is not a live source.",
                "Without validated publication/revision metadata, these results cannot claim point-in-time historical availability.",
                "No financial forecasts or causal claims are derived from descriptive operators alone.",
            ],
        }

    def run_llm(self, question: str, *, model: str = "gpt-4.1-mini") -> dict[str,Any]:
        """LLM returns only a bounded JSON tool call, never accepted prose/numbers."""
        if not isinstance(question,str) or not 1<=len(question.strip())<=1000:
            raise ValueError("Question must contain 1..1000 characters")
        from openai import OpenAI
        schema = {
            "type":"function",
            "function":{
                "name":"evaluate_plan",
                "description":"Build a small deterministic analytical DAG over available named series.",
                "parameters":{
                    "type":"object",
                    "properties":{
                        "steps":{
                            "type":"array","minItems":1,"maxItems":MAX_AGENT_STEPS,
                            "items":{"type":"object","properties":{
                                "id":{"type":"string"},"operator":{"type":"string","enum":sorted(OPERATOR_SPECS)},
                                "inputs":{"type":"array","items":{"type":"string"}},
                                "window":{"type":"integer","minimum":2,"maximum":1000},
                            },"required":["id","operator","inputs"],"additionalProperties":False},
                        },
                        "result_id":{"type":"string"},
                    },
                    "required":["steps","result_id"],"additionalProperties":False,
                },
            },
        }
        capabilities=self.capabilities()
        available=[{"key":s["key"],"unit":s["unit"],"frequency":s["frequency"]}
                   for s in capabilities["series"]]
        response=OpenAI(timeout=15,max_retries=0).chat.completions.create(
            model=model,temperature=0,tools=[schema],
            tool_choice={"type":"function","function":{"name":"evaluate_plan"}},
            messages=[
                {"role":"system","content":"Return one plan. Use only supplied series names and operator enums. "
                  "Do not invent data, prose, math output, SQL, source names or unsupported transformations. "
                  "Respect units and avoid claiming forecasts."},
                {"role":"user","content":"Available series: "+json.dumps(available)+
                 "\nOperator definitions: "+json.dumps(capabilities["operators"])+
                 "\nQuestion: "+question},
            ],
        )
        calls=response.choices[0].message.tool_calls or []
        if len(calls)!=1 or calls[0].function.name!="evaluate_plan":
            raise ValueError("LLM did not return one allowlisted plan")
        plan=json.loads(calls[0].function.arguments)
        output=self.run(plan)
        output["selection_mode"]="LLM-generated plan; Python-calculated results"
        output["prompt"]=question
        return output
