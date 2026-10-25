"""Minute causality, execution accounting, horizons, and strict best-parent contracts."""

import asyncio

import numpy as np
import pytest

from autoresearch.intraday.data import Panel
from autoresearch.intraday.engine import feature, simulate
from autoresearch.intraday.evaluation import matched, uncertainty
from autoresearch.intraday.research import executable_id, fallback, propose
from autoresearch.intraday.specs import HORIZONS, Recipe, Term, baseline
from llmrouter.contracts import ChatResponse


def panel(days=8):
    p = object.__new__(Panel)
    p.dates = [f"2024-01-{i + 2:02}" for i in range(days)]
    p.symbols = ("SPY",)
    rng = np.random.default_rng(72)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0001, (days, 390, 1)), axis=1))
    p.values = np.stack([c, c * 1.0001, c * 0.9999, c, np.full_like(c, 1e6)], axis=-1)
    p.valid = np.ones((days, 1), dtype=bool)
    p.cache = {}
    return p


def recipe(**options):
    return Recipe(
        symbols=("SPY",),
        terms=(Term(feature="momentum", lookback=5),),
        start_minute=10,
        end_minute=100,
        entry_threshold=0.1,
        **options,
    )


@pytest.mark.parametrize("kind", list(Term.model_fields["feature"].annotation.__args__))
def test_features_do_not_observe_future_or_other_session(kind):
    p = panel()
    t = Term(feature=kind, lookback=12, smoothing=3, lag=2)
    original = feature(p, t).copy()
    p.values[:, 200:, :, :4] *= 10
    p.cache = {}
    changed = feature(p, t)
    np.testing.assert_allclose(original[:, :200], changed[:, :200], equal_nan=True)
    p2 = panel()
    p2.values[1:, :, :, :4] *= 3
    np.testing.assert_allclose(original[0], feature(p2, t)[0], equal_nan=True)


@pytest.mark.parametrize("horizon", HORIZONS)
def test_horizons_next_open_costs_and_no_overnight(horizon):
    p = panel()
    r = recipe(decision_minutes=horizon, max_hold_minutes=horizon, max_trades_per_asset_day=1)
    result = simulate(p, r, scores=np.ones((8, 390, 1)))
    assert len(result["trades"]) == 8
    for trade in result["trades"]:
        assert trade["entry_minute"] == trade["decision_minute"] + 1
        assert trade["exit_minute"] - trade["entry_minute"] == horizon
        assert trade["exit_minute"] <= 389
        expected = trade["exit_price"] / trade["entry_price"] - 1
        expected -= 0.0002 * (1 + trade["exit_price"] / trade["entry_price"])
        assert trade["net_return"] == pytest.approx(expected)
    assert sum(row["return"] for row in result["daily"]) == pytest.approx(
        sum(t["portfolio_return"] for t in result["trades"])
    )


def test_stop_fills_next_open_and_gap_can_exceed_stop():
    p = panel(1)
    p.values[:, :, :, :4] = 100
    p.values[0, 12, 0, 3] = 98
    p.values[0, 13, 0, 0] = 97
    r = recipe(
        decision_minutes=5, max_hold_minutes=15, max_trades_per_asset_day=1, stop_loss_bps=50
    )
    trade = simulate(p, r, scores=np.ones((1, 390, 1)))["trades"][0]
    assert trade["entry_minute"] == 11
    assert trade["exit_minute"] == 13
    assert trade["exit_price"] == 97
    assert trade["exit_reason"] == "stop_close"
    assert trade["net_return"] < -0.03


def test_incomplete_day_abstains_and_stays_in_return_calendar():
    p = panel()
    p.valid[2, 0] = False
    result = simulate(p, recipe(), scores=np.ones((8, 390, 1)))
    assert not any(t["date"] == p.dates[2] for t in result["trades"])
    assert result["daily"][2]["return"] == 0
    assert len(result["daily"]) == 8


def test_short_pnl_and_proxy_fee():
    p = panel(1)
    p.values[:, :, :, :4] = 100
    p.values[0, 12:, 0, :4] = 99
    r = recipe(
        direction="short_only", decision_minutes=1, max_hold_minutes=1, max_trades_per_asset_day=1
    )
    trade = simulate(p, r, scores=-np.ones((1, 390, 1)))["trades"][0]
    assert trade["gross_return"] == pytest.approx(0.01)
    assert trade["net_return"] == pytest.approx(0.01 - 0.0002 * (1 + 0.99) - 0.0001)


def test_match_scales_down_and_zero_exposure_is_unavailable():
    p = panel()
    r = recipe()
    a = simulate(p, r, scores=np.ones((8, 390, 1)))
    b = simulate(p, r.model_copy(update={"allocation_per_asset": 0.1}), scores=np.ones((8, 390, 1)))
    result = matched(a, b)
    assert result["candidate_scalar"] == pytest.approx(0.5)
    assert result["control_scalar"] == 1
    assert result["incremental_annualized_mean_return"] == pytest.approx(0)
    assert not uncertainty(a, b)["passes"]
    empty = simulate(p, r, scores=np.zeros((8, 390, 1)))
    assert not matched(a, empty)["available"]


def test_duplicate_identity_ignores_style_name():
    r = baseline()
    assert executable_id(r) == executable_id(r.model_copy(update={"style": "renamed_strategy"}))


def test_fallback_always_builds_from_retained_best_and_is_distinct():
    best = baseline()
    tried = {executable_id(best)}
    for _ in range(50):
        proposal = fallback(best, tried)
        assert proposal.parent_candidate == best.digest()
        assert executable_id(proposal.recipe) not in tried
        tried.add(executable_id(proposal.recipe))
        if len(tried) % 3 == 0:
            best = proposal.recipe


def test_model_cannot_choose_losing_parent_or_see_validation(tmp_path):
    from autoresearch.ledger import Ledger

    best = baseline()
    alternative = fallback(best, {executable_id(best)})
    state = {
        "requested": 50,
        "experiments": [
            {
                "id": 0,
                "candidate": best.digest(),
                "recipe": best.model_dump(mode="json"),
                "result": {"train": {"sharpe": 1}},
                "validation": {"SECRET_VALIDATION": 123},
            }
        ],
    }

    class Router:
        async def complete(self, request, ledger, validate):
            assert "SECRET_VALIDATION" not in request.user
            bad = alternative.model_copy(update={"parent_candidate": "f" * 64})
            with pytest.raises(ValueError, match="retained best"):
                validate(bad.model_dump_json())
            replacement = alternative.model_copy(
                update={"recipe": best.model_copy(update={"interactions": ()})}
            )
            with pytest.raises(ValueError):
                validate(replacement.model_dump_json())
            validate(alternative.model_dump_json())
            return ChatResponse(
                provider="gemini",
                model="fixture",
                text=alternative.model_dump_json(),
                input_tokens=1,
                output_tokens=1,
            )

    with Ledger(tmp_path, 10000) as ledger:
        item, source = asyncio.run(propose(Router(), ledger, best, state))
    assert source == "llm" and item == alternative


@pytest.mark.parametrize("batched", [False, True])
def test_loop_escalation_changes_next_parent_and_defers_validation(tmp_path, monkeypatch, batched):
    from autoresearch.intraday import research
    from autoresearch.workspace import write_json
    from llmrouter.chain import Chain, Target

    phases = []
    program = {
        "requested": 3,
        "baseline": baseline().model_dump(mode="json"),
        "train_start": "2022-01-01",
        "train_end": "2024-12-31",
        "validation_start": "2025-01-01",
        "validation_end": "2025-12-31",
        "chain": Chain(targets=(Target(provider="gemini", model="fixture"),)).model_dump(
            mode="json"
        ),
        "max_model_tokens_per_day": 10000,
    }
    p = panel()
    p.symbols = ("SPY", "QQQ", "IWM", "GLD")
    p.values = np.repeat(p.values, 4, axis=2)
    p.valid = np.repeat(p.valid, 4, axis=1)
    p.audit = {"coverage": 1.0}

    def load_panel(_root, *, start, end):
        assert start < end
        phases.append("validation" if start.startswith("2025") else "training")
        return p

    async def proposals(_router, _ledger, best, state):
        phases.append("proposal")
        assert best.digest() == state["incumbent"]
        if len(state["experiments"]) > 1:
            assert best.digest() == state["experiments"][-1]["candidate"]
        return fallback(
            best, {executable_id(Recipe.model_validate(r["recipe"])) for r in state["experiments"]}
        ), "llm"

    async def reviews(*_args):
        return {
            "status": "complete",
            "summary": {"headline": "Measured result", "explanation": "Test review"},
        }

    def evaluation(*args):
        primary, score = args[2], args[7]
        return {
            "score": score + 1,
            "train": primary["metrics"],
            "decision": "keep",
            "failed_gates": [],
        }

    monkeypatch.setattr(research, "verify", lambda _root: program)
    monkeypatch.setattr(research, "Panel", load_panel)
    monkeypatch.setattr(research, "propose", proposals)
    monkeypatch.setattr(research, "review", reviews)
    monkeypatch.setattr(research, "evaluate", evaluation)
    if batched:
        partial = research.run(
            tmp_path,
            progress=lambda _message: None,
            max_iterations=1,
            guidance="Test a bounded intraday mechanism before extending the search.",
        )
        assert partial["status"] == "batch_completed"
        assert len(partial["experiments"]) == 2
        assert "validation" not in phases
        assert not (tmp_path / "final-validation.json").exists()
    state = research.run(tmp_path, progress=lambda _message: None)
    assert len(state["experiments"]) == 4
    assert state["status"] == "completed"
    assert (
        len({executable_id(Recipe.model_validate(r["recipe"])) for r in state["experiments"]}) == 4
    )
    expected = ["training", "proposal", "proposal", "proposal", "validation"]
    if batched:
        expected.insert(2, "training")
    assert phases == expected
    assert (tmp_path / "metrics.csv").exists()
    assert len(list((tmp_path / "runs").glob("*/signals.npz"))) == 3
    assert all("validation" in row for row in state["experiments"])
    write_json(tmp_path / "report.json", {"tampered": True})
    with pytest.raises(ValueError, match="checkpoint changed"):
        research.run(tmp_path, progress=lambda _message: None)


def test_minute_dataset_hash_and_whole_missing_session_detection(tmp_path):
    import datetime as dt

    import polars as pl

    from autoresearch.workspace import digest_file, write_json

    start = dt.datetime(2024, 1, 2, 14, 30, tzinfo=dt.UTC)
    rows = [
        {
            "t": (start + dt.timedelta(minutes=i)).isoformat(),
            "o": 100.0,
            "h": 101.0,
            "l": 99.0,
            "c": 100.0,
            "v": 1000.0,
        }
        for i in range(390)
    ]
    path = tmp_path / "SPY-2024.parquet"
    pl.DataFrame(rows).write_parquet(path)
    write_json(
        tmp_path / "manifest.json",
        {"symbols": ["SPY"], "partitions": {path.name: digest_file(path)}},
    )
    write_json(tmp_path / "calendar.json", {"dates": ["2024-01-02", "2024-01-03"]})
    p = Panel(tmp_path, start="2024-01-01", end="2024-01-05")
    assert p.valid.tolist() == [[True], [False]]
    assert p.audit["excluded_asset_days"] == 1
    assert p.dates == ["2024-01-02", "2024-01-03"]
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        Panel(tmp_path, start="2024-01-01", end="2024-01-05")
