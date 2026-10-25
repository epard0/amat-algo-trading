"""Model reviews explain outcomes without controlling retention or fabricating fallback."""

from __future__ import annotations

import json

import pytest

from autoresearch.ledger import Ledger
from autoresearch.loop import finish, register
from autoresearch.summaries import RunSummary, render_summaries, summarize_experiment
from llmrouter.chain import Chain, Router, Target
from llmrouter.contracts import ChatResponse


class ReviewTransport:
    def __init__(self, verdict="bad", overclaim=False):
        self.calls = 0
        self.verdict = verdict
        self.overclaim = overclaim

    async def generate(self, request, *, model, api_key, base_url):
        self.calls += 1
        evidence = json.loads(request.user)
        assert evidence["decision"] == "discard"
        assert evidence["failed_gates"] == ["stress_sharpe"]
        review = RunSummary(
            verdict=self.verdict,
            headline="This proves the strategy is bad"
            if self.overclaim
            else "Costs erase the apparent improvement",
            explanation=(
                "The experiment completed, but failed its stressed-cost performance gate. "
                "It was discarded, so there is no retained improvement. "
                "Successful execution does not imply guaranteed live profits. "
                "The strategy is not proven profitable."
            ),
            strengths=("The experiment produced valid measurements.",),
            weaknesses=("Stress performance failed the frozen gate.",),
            next_experiment="Test lower turnover with the same cost assumptions.",
            evidence_limits="These are development results, not live trading evidence.",
        )
        return ChatResponse(
            provider="openai",
            model=model,
            text=review.model_dump_json(),
            input_tokens=100,
            output_tokens=100,
        )


def setup_ledger(root, limit=100000):
    ledger = Ledger(root, limit)
    index = register(
        ledger, "candidate", "parent", json.dumps({"hypothesis": "Test a proposed pattern."})
    )
    finish(
        ledger,
        index,
        "discard",
        {"train": {"sharpe": 0.5}, "stress": {"sharpe": -0.2}, "failed_gates": ["stress_sharpe"]},
    )
    return ledger, index


def test_summary_is_llm_supplied_cached_and_rendered_without_changing_decision(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    transport = ReviewTransport()
    router = Router(
        Chain(targets=(Target(provider="openai", model="fixture"),)),
        transports={"openai": transport},
    )
    ledger, index = setup_ledger(tmp_path)
    with ledger:
        first = summarize_experiment(tmp_path, ledger, index, router)
        second = summarize_experiment(tmp_path, ledger, index, router)
        assert first == second
        assert transport.calls == 1
        assert first["status"] == "complete"
        assert first["summary"]["verdict"] == "bad"
        assert ledger.history()[0]["status"] == "discard"
        summaries, lines = render_summaries(ledger)
        assert summaries == [first]
        assert "Costs erase" in "\n".join(lines)
        assert ledger.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1


@pytest.mark.parametrize("mode", ["no_provider", "budget", "contradiction", "overclaim"])
def test_summary_unavailability_is_explicit_and_does_not_fail_training(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    transport = ReviewTransport(
        "good" if mode == "contradiction" else "bad", overclaim=mode == "overclaim"
    )
    router = Router(
        Chain(targets=(Target(provider="openai", model="fixture"),)),
        transports={"openai": transport},
    )
    if mode == "no_provider":
        monkeypatch.delenv("OPENAI_API_KEY")
    ledger, index = setup_ledger(tmp_path, limit=1 if mode == "budget" else 100000)
    with ledger:
        summary = summarize_experiment(tmp_path, ledger, index, router)
        assert summary["status"] == "unavailable"
        assert "summary" not in summary
        assert ledger.history()[0]["status"] == "discard"


def test_saved_summary_tampering_is_detected(tmp_path):
    ledger, index = setup_ledger(tmp_path)
    with ledger:
        summarize_experiment(tmp_path, ledger, index, None)
        (tmp_path / "outputs/summaries/experiment-1.json").write_text("{}")
        with pytest.raises(ValueError, match="artifact changed"):
            summarize_experiment(tmp_path, ledger, index, None)


def test_run_review_receives_scenario_coverage_and_tail_losses():
    from autoresearch.summaries import evidence

    result = {
        "robustness": {
            "coverage": {"complete": False, "incomplete_scenarios": ["correlation_break"]},
            "scenarios": {
                "student_t_3": {
                    "kind": "hypothetical_synthetic",
                    "distribution": "student_t",
                    "coverage_complete": True,
                    "sharpe_difference": -0.2,
                    "candidate": {
                        "max_drawdown": -0.25,
                        "return_distribution": {"loss_cvar_99": 0.05},
                    },
                },
            },
        },
    }
    payload = evidence(
        {"id": 1, "status": "baseline", "proposal": "baseline", "result": json.dumps(result)}
    )
    assert payload["scenario_coverage"] == result["robustness"]["coverage"]
    assert payload["scenario_failures"][0]["loss_cvar_99"] == 0.05
