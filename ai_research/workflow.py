from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ai_research.agents import CriticAgent, Hypothesis, HypothesisAgent
from ai_research.registry import ResearchRegistry
from backtest.competition import competition


class ExperimentRunner:
    def __init__(self, registry, budget=48):
        if budget < 17 or budget > 100:
            raise ValueError("research budget must be 17..100")
        self.registry, self.budget = registry, budget

    def run(self, proposals, frames, symbol, info, start, end, output, calendar):
        before = set(Path(output).glob("*/experiments.jsonl"))
        specs = [{"name": "no_trade", "period": 20}, {"name": "smc_baseline_v1", "period": 20}]
        for proposal in proposals:
            identifier = self.registry.begin("hypothesis", proposal)
            try:
                hypothesis = Hypothesis(**proposal)
                spec = {"name": hypothesis.strategy, "period": hypothesis.period}
                if spec not in specs:
                    specs.append(spec)
                self.registry.finish(identifier, "completed", {"status": "queued_not_proven", "hypothesis": asdict(hypothesis)})
            except (TypeError, ValueError) as exc:
                self.registry.finish(identifier, "rejected", {"error": str(exc)})
        identifier = self.registry.begin("competition", {"specs": specs, "budget": self.budget,
            "start": str(start), "end": str(end), "symbol": symbol, "calendar": calendar, "paper_only": True})
        try:
            result = competition(frames, symbol, info, start, end, output, calendar,
                                 candidates=specs, max_experiments=self.budget)
            critique = CriticAgent().review(result)
            self.registry.finish(identifier, "completed", {"result": result, "critique": critique})
            return {"registry_id": identifier, "competition": result, "critique": critique}
        except Exception as exc:
            self.registry.finish(identifier, "failed", {"error": str(exc)})
            raise
        finally:
            for ledger in sorted(set(Path(output).glob("*/experiments.jsonl")) - before):
                for index, line in enumerate(ledger.read_text(encoding="utf-8").splitlines()):
                    row = json.loads(line)
                    trial = self.registry.begin("strategy_experiment", {"origin": str(ledger), "row": index, **row})
                    status = "completed" if row.get("status") == "completed" else "failed" if row.get("status") == "failed" else "rejected"
                    self.registry.finish(trial, status, row)


def run_workflow(frames, symbol, info, start, end, output, calendar="fx_ny", *, budget=48,
                 proposal_file=None, model=None, endpoint="http://127.0.0.1:11434"):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    registry = ResearchRegistry(root / "research.sqlite3")
    proposal_id = registry.begin("proposal_generation", {"model": model, "proposal_file": str(proposal_file) if proposal_file else None,
                                                       "data_cutoff": str(start), "budget": budget})
    try:
        source, proposals = HypothesisAgent().propose(proposal_file=proposal_file, model=model, endpoint=endpoint,
            context={"data_cutoff": str(start), "budget": budget,
                     "note": "No final-period results or forward observations are supplied to the proposer."})
        registry.finish(proposal_id, "completed", {"source": source, "proposals": proposals})
        result = ExperimentRunner(registry, budget).run(proposals, frames, symbol, info, start, end, root, calendar)
        result["proposal_source"] = source
        result["llm_used"] = model is not None
        (root / "workflow_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    except Exception as exc:
        pending = next(row for row in registry.records() if row["id"] == proposal_id)
        if pending["status"] == "pending":
            registry.finish(proposal_id, "failed", {"error": str(exc)})
        raise
    finally:
        registry.export(root / "research_registry.jsonl")
