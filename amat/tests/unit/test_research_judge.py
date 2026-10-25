"""The evaluator is evidence bounded, durable, and unable to alter decisions."""

import json

import pytest

from autoresearch.judge import ResearchJudgment, judge_experiment
from autoresearch.ledger import Ledger
from autoresearch.observability import agent_panel
from llmrouter.chain import Chain, Router, Target
from llmrouter.contracts import ChatResponse


def seed(ledger, status="discard"):
    with ledger.db:
        ledger.db.execute(
            "INSERT INTO experiments VALUES(1,'candidate','parent',?,?,?,NULL)",
            (json.dumps({"hypothesis": "Test a causal mechanism"}), status, "{}"),
        )


def judgment(index=1, assessment="concerns"):
    return ResearchJudgment(
        assessment=assessment,
        rationale="The evidence has limited independent validation.",
        concerns=("Adaptive selection",),
        falsification_test="Repeat with frozen controls.",
        next_experiment="Ablate the feature on training data.",
        evidence_ids=(index,),
    ).canonical()


def test_judge_excludes_proposer_and_persists_once(tmp_path, monkeypatch):
    calls = []

    async def complete(self, request, _budget, *, validate, **_kwargs):
        calls.append(request)
        assert [t.model for t in self.chain.targets] == ["critic"]
        validate(judgment())
        return ChatResponse(
            provider="groq", model="critic", text=judgment(), input_tokens=2, output_tokens=3
        )

    monkeypatch.setattr(Router, "complete", complete)
    chain = Chain(
        targets=(Target(provider="gemini", model="proposer"),),
        judge_targets=(
            Target(provider="gemini", model="proposer"),
            Target(provider="groq", model="critic"),
        ),
    )
    with Ledger(tmp_path, 10000) as ledger:
        seed(ledger)
        ledger.put("proposal_model:1", {"provider": "gemini", "model": "proposer"})
        result = judge_experiment(ledger, 1, chain, stage="outcome")
        assert result["status"] == "complete"
        assert judge_experiment(ledger, 1, chain, stage="outcome") == result
        assert len(calls) == 1
        assert calls[0].persistence == "metadata_only"
        assert ledger.get("lesson:1")["decision"] == "discard"
        assert ledger.history()[0]["status"] == "discard"


@pytest.mark.parametrize(
    "stage,status,output",
    [
        ("proposal", "running", judgment(assessment="supported")),
        ("outcome", "discard", judgment(assessment="supported")),
        ("outcome", "crash", judgment()),
        ("outcome", "keep", judgment(index=999)),
    ],
)
def test_judge_rejects_unsupported_claims(tmp_path, monkeypatch, stage, status, output):
    async def complete(_self, _request, _budget, *, validate, **_kwargs):
        with pytest.raises(ValueError):
            validate(output)

    monkeypatch.setattr(Router, "complete", complete)
    target = Target(provider="groq", model="critic")
    with Ledger(tmp_path, 10000) as ledger:
        seed(ledger, status)
        result = judge_experiment(
            ledger, 1, Chain(targets=(target,), judge_targets=(target,)), stage=stage
        )
        assert result["status"] == "unavailable"
        assert ledger.history()[0]["status"] == status


def test_absent_route_and_exhausted_budget_are_nonfatal(tmp_path, monkeypatch):
    target = Target(provider="groq", model="critic")
    monkeypatch.setenv("GROQ_API_KEY", "fixture")
    with Ledger(tmp_path, 1) as ledger:
        seed(ledger)
        assert (
            judge_experiment(ledger, 1, None, stage="proposal")["reason"]
            == "no_distinct_judge_route"
        )
        result = judge_experiment(
            ledger, 1, Chain(targets=(target,), judge_targets=(target,)), stage="outcome"
        )
        assert result["reason"] == "model_budget_exhausted"
        assert ledger.history()[0]["status"] == "discard"


def test_agent_panel_escapes_model_authored_text():
    html = agent_panel(
        {
            "research_judgments": [
                {
                    "experiment": 1,
                    "stage": "outcome",
                    "status": "complete",
                    "judgment": "<script>alert(1)</script>",
                }
            ]
        }
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
