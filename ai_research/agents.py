"""LLMs may propose constrained hypotheses; only replay computes trading results."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Hypothesis:
    statement: str
    strategy: str
    period: int
    falsification: str

    def __post_init__(self):
        if self.strategy not in {"trend_momentum_v1", "atr_breakout_v1"} or self.period not in {10,20,30,40,60}:
            raise ValueError("hypothesis is outside the predeclared strategy/parameter space")
        if not 10 <= len(self.statement) <= 2000 or not 10 <= len(self.falsification) <= 2000:
            raise ValueError("hypothesis requires a specific statement and falsification rule")


class HypothesisAgent:
    schema = {"type": "object", "additionalProperties": False, "required": ["hypotheses"],
        "properties": {"hypotheses": {"type": "array", "maxItems": 4, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["statement", "strategy", "period", "falsification"],
            "properties": {"statement": {"type": "string"}, "falsification": {"type": "string"},
                "strategy": {"enum": ["trend_momentum_v1", "atr_breakout_v1"]},
                "period": {"enum": [10,20,30,40,60]}}}}}}

    def propose(self, *, proposal_file=None, model=None, endpoint="http://127.0.0.1:11434", context=None):
        if proposal_file and model:
            raise ValueError("choose supplied proposals or an LLM, not both")
        if proposal_file:
            document = json.loads(Path(proposal_file).read_text(encoding="utf-8"))
            source = "supplied_structured_proposals"
        elif model:
            prompt = {"task": "Propose at most four falsifiable paper-trading research hypotheses within this schema. Never supply returns or trade instructions.",
                      "schema": self.schema, "context": context or {}}
            request = Request(endpoint.rstrip("/") + "/api/chat", data=json.dumps({"model": model, "stream": False,
                "format": self.schema, "options": {"temperature": 0},
                "messages": [{"role": "user", "content": json.dumps(prompt)}]}).encode(), headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=45) as response:
                reply = json.loads(response.read(1_000_000))
            document = json.loads(reply["message"]["content"])
            source = "ollama:" + model
        else:
            source = "deterministic_catalog_no_llm"
            document = {"hypotheses": [asdict(Hypothesis(
                f"{strategy} with period {period} may retain positive validation expectancy after declared costs.",
                strategy, period, "Reject if net validation return is nonpositive, sample count is inadequate, or data/chronology checks fail."))
                for strategy in ("trend_momentum_v1", "atr_breakout_v1") for period in (20,30)]}
        if set(document) != {"hypotheses"} or not isinstance(document["hypotheses"], list) or len(document["hypotheses"]) > 4:
            raise ValueError("proposal document violates bounded schema")
        return source, document["hypotheses"]


class CriticAgent:
    def review(self, result):
        concerns = []
        if not result.get("selected"):
            concerns.append("No candidate passed the predeclared validation gates.")
        if result.get("experiments", 0) > 1:
            concerns.append("Multiple candidates were tried; apparent winners remain exploratory.")
        final = result.get("final_evaluation")
        if final and final.get("net_return", 0) <= 0:
            concerns.append("The frozen selected candidate failed its final-period return check; do not choose an alternative on that period.")
        if result.get("uncertainty", {}).get("status") != "estimated":
            concerns.append("Too few independent trade observations for the configured uncertainty report.")
        concerns.append("OHLC fills, configured costs and constant tick economics require independent forward-paper comparison.")
        status = "rejected" if not result.get("selected") or final and final.get("net_return", 0) <= 0 else "unproven"
        return {"status": status, "concerns": concerns, "paper_only": True, "llm_opinion_is_alpha_evidence": False}
