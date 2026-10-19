import json
from io import BytesIO
from unittest.mock import patch

import pytest

from ai_research.agents import CriticAgent, Hypothesis, HypothesisAgent
from ai_research.registry import ResearchRegistry
from ai_research.workflow import ExperimentRunner, run_workflow
from data.fixtures import diagnostic_fixture


def test_registry_preserves_failures_and_refuses_rewriting_them(tmp_path):
    registry = ResearchRegistry(tmp_path / "registry.db")
    identifier = registry.begin("test", {"hypothesis": "negative result fixture"})
    registry.finish(identifier, "failed", {"error": "invalid data"})
    with pytest.raises(ValueError, match="immutable"):
        registry.finish(identifier, "completed", {"net_return": 1000})
    assert registry.records()[0]["status"] == "failed"


def test_catalog_is_explicitly_not_an_llm():
    source, proposals = HypothesisAgent().propose()
    assert source == "deterministic_catalog_no_llm"
    assert len(proposals) == 4
    for row in proposals:
        Hypothesis(**row)


def test_ollama_adapter_requests_schema_and_rejects_narrative_only_responses():
    captured = []
    proposal = {"statement": "A causal EMA crossover may survive transaction costs.", "strategy": "trend_momentum_v1",
                "period": 20, "falsification": "Reject if validation expectancy after costs is nonpositive."}
    def reply(request, timeout):
        captured.append(json.loads(request.data))
        return BytesIO(json.dumps({"message": {"content": json.dumps({"hypotheses": [proposal]})}}).encode())
    with patch("ai_research.agents.urlopen", side_effect=reply):
        source, rows = HypothesisAgent().propose(model="synthetic-transport-fixture")
    assert source.startswith("ollama:") and rows == [proposal]
    assert captured[0]["stream"] is False
    assert captured[0]["format"]["properties"]["hypotheses"]["maxItems"] == 4
    assert captured[0]["messages"][0]["role"] == "user"


def test_invalid_strategy_proposal_is_registered_without_execution(tmp_path):
    frames, info, start, end = diagnostic_fixture(tmp_path / "data")
    registry = ResearchRegistry(tmp_path / "registry.db")
    bad = {"statement": "Execute arbitrary trading code now.", "strategy": "exec_anything", "period": 20,
           "falsification": "This is a rejected schema fixture."}
    with patch("ai_research.workflow.competition", return_value={"selected": None}):
        result = ExperimentRunner(registry).run([bad], frames, info.symbol, info, start, end, tmp_path, "24/7")
    assert registry.records()[0]["status"] == "rejected"
    assert result["critique"]["status"] == "rejected"


def test_workflow_retains_invalid_data_failure_and_all_pending_states_are_resolved(tmp_path):
    frames, info, start, end = diagnostic_fixture(tmp_path / "data")
    frames["5m"] = frames["5m"].iloc[:-5]
    with pytest.raises(ValueError):
        run_workflow(frames, info.symbol, info, start, end, tmp_path / "workflow", "24/7")
    registry = ResearchRegistry(tmp_path / "workflow/research.sqlite3")
    rows = registry.records()
    assert any(row["kind"] == "competition" and row["status"] == "failed" for row in rows)
    assert any(row["kind"] == "strategy_experiment" and row["status"] == "rejected" for row in rows)
    assert all(row["status"] != "pending" for row in rows)


def test_critic_never_treats_text_or_one_positive_result_as_proof():
    critic = CriticAgent().review({"selected": {"name": "trend"}, "experiments": 5,
                                 "final_evaluation": {"net_return": .1}, "uncertainty": {"status": "insufficient_trades"}})
    assert critic["status"] == "unproven"
    assert not critic["llm_opinion_is_alpha_evidence"]
