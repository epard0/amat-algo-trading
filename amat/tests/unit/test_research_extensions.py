"""Extension constraints reject replacement strategies and survive accumulated edits."""

import json
from pathlib import Path

import pytest

from autoresearch.baseline import load_research_config
from autoresearch.contracts import Candidate, Feature, Patch, Proposal, apply_proposal
from autoresearch.extensions import validate_daily_core, validate_daily_extension
from autoresearch.genetic.adapter import GeneticAdapterSpec
from autoresearch.genetic.models import canonical_genome_digest, parse_pattern
from autoresearch.intraday.research import executable_id, fallback
from autoresearch.intraday.specs import Term, baseline, validate_extension
from autoresearch.proposer import search_proposal
from autoresearch.specs import FeatureSpec, SignalSpec, SignalTerm

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def daily():
    return Candidate(config=load_research_config(ROOT / "configs/strategy/amat_v6.toml"))


def feature(kind="momentum"):
    return Feature(kind=kind, lookback=21, threshold=0)


def test_daily_rejects_replacement_signal_even_on_legacy_allocator(daily):
    candidate = Candidate(
        config=daily.config,
        signal=SignalSpec(terms=(SignalTerm(feature=FeatureSpec(kind="momentum")),)),
    )
    with pytest.raises(ValueError, match="baseline signal"):
        validate_daily_core(daily, candidate)


def test_daily_allows_one_feature_and_rejects_combined_rewrite(daily):
    candidate = apply_proposal(
        daily, Proposal(hypothesis="Test momentum confirmation.", features=(feature(),))
    )
    validate_daily_extension(daily, candidate)
    patched = apply_proposal(
        daily,
        Proposal(
            hypothesis="Change signal and tuning together.",
            features=(feature(),),
            patches=(Patch(path="trend.deadband_abs", value=0.4),),
        ),
    )
    with pytest.raises(ValueError, match="one component"):
        validate_daily_extension(daily, patched)
    parent = Candidate(config=daily.config, features=(feature(), feature("volatility")))
    with pytest.raises(ValueError, match="one component"):
        validate_daily_extension(parent, daily)


def test_genetic_modifier_is_allowed_and_fallback_keeps_it(daily):
    pattern = parse_pattern(
        json.loads((ROOT / "docs/genetic-patterns/candle.example.json").read_text())
    )
    spec = GeneticAdapterSpec(pattern=pattern, genome_sha256=canonical_genome_digest(pattern))
    parent = Candidate(config=daily.config, genetic_pattern=spec)
    validate_daily_extension(daily, parent)
    proposal = search_proposal(parent, {parent.digest()})
    assert proposal is not None
    child = apply_proposal(parent, proposal)
    validate_daily_extension(parent, child)
    assert child.genetic_pattern == spec


def test_intraday_support_and_ablation_preserve_original_recipe():
    original = baseline()
    support = Term(feature="volume_surprise", weight=0.2)
    child = original.model_copy(update={"terms": (*original.terms, support)})
    validate_extension(original, original, child)
    validate_extension(original, child, original)
    tuned = child.model_copy(update={"entry_threshold": 0.4})
    validate_extension(original, child, tuned)
    assert tuned.terms == child.terms
    with pytest.raises(ValueError, match="original signal"):
        validate_extension(
            original,
            child,
            child.model_copy(
                update={
                    "terms": (
                        original.terms[0].model_copy(update={"lookback": 20}),
                        *child.terms[1:],
                    )
                }
            ),
        )


@pytest.mark.parametrize(
    "update",
    [
        {"terms": (Term(feature="momentum"),), "interactions": ()},
        {"direction": "both"},
        {"allocation_per_asset": 0.25},
        {"combine": "maximum"},
        {"entry_threshold": 0.4, "cooldown_minutes": 5, "confirmation_bars": 2},
    ],
)
def test_intraday_rejects_replacement_and_multi_component_changes(update):
    original = baseline()
    with pytest.raises(ValueError):
        validate_extension(original, original, original.model_copy(update=update))


def test_intraday_rejects_dominant_support_and_bounds_growth():
    original = baseline()
    for support in [
        (Term(feature="momentum", weight=2),),
        (Term(feature="momentum", weight=0.2),) * 3,
    ]:
        with pytest.raises(ValueError, match="two supporting"):
            validate_extension(
                original,
                original,
                original.model_copy(update={"terms": (*original.terms, *support)}),
            )


def test_intraday_fallback_preserves_extensions_across_generations():
    original = baseline()
    best = original.model_copy(
        update={"terms": (*original.terms, Term(feature="efficiency", weight=0.2))}
    )
    tried = {executable_id(best)}
    for _ in range(12):
        proposal = fallback(best, tried)
        validate_extension(original, best, proposal.recipe)
        assert proposal.recipe.terms == best.terms
        assert proposal.recipe.interactions == original.interactions
        tried.add(executable_id(proposal.recipe))
        best = proposal.recipe


def test_worker_rejects_replacement_before_loading_data(daily, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from autoresearch import worker

    replacement = Candidate(
        config=daily.config,
        signal=SignalSpec(terms=(SignalTerm(feature=FeatureSpec(kind="momentum")),)),
    )
    path = tmp_path / "candidate.json"
    path.write_text(replacement.model_dump_json())
    monkeypatch.setattr(
        worker, "load_program", lambda _: SimpleNamespace(baseline=daily, extensions_only=True)
    )
    with pytest.raises(ValueError, match="baseline signal"):
        worker.run(tmp_path, path, tmp_path / "result")


def test_normal_start_freezes_extension_and_genetic_contract(tmp_path, monkeypatch):
    from autoresearch import cli

    class CapturedError(Exception):
        pass

    def initialize(*_args, **kwargs):
        assert kwargs["extensions_only"] is True
        assert kwargs["schema_version"] == "strategy-development/9"
        raise CapturedError

    monkeypatch.setattr(cli, "initialize", initialize)
    monkeypatch.setattr(cli, "active_promotion", lambda _: None)
    args = cli.argument_parser().parse_args(
        [
            "start",
            "2",
            "--source",
            str(ROOT),
            "--workspace",
            str(tmp_path / "session"),
            "--no-open",
        ]
    )
    with pytest.raises(CapturedError):
        cli.start_session(args)
