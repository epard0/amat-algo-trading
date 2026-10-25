"""Fit-only whole-search evidence, null mapping, and deterministic recovery."""

import datetime as dt
import json
from dataclasses import replace

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.domain.types import Symbol
from autoresearch.genetic import nested_search
from autoresearch.genetic.data import CandleDataset
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.symbolic_search import SymbolicSearchProtocol, SymbolicTerminal
from autoresearch.genetic.symbolic_study import SymbolicScreen, SymbolicScreenConfig, _template


@pytest.fixture
def fit(monkeypatch):
    monkeypatch.setattr(nested_search, "_source_hashes", lambda: {"fixture": "frozen"})
    t = np.arange(110)[:, None]
    closes = 100 * np.exp(0.0005 * t + 0.03 * np.sin(t / np.array([[5, 8]])))
    opens = closes * (1 + 0.005 * np.cos(t / np.array([[2, 3]])))
    fields = {
        "open": opens,
        "high": np.maximum(opens, closes) * 1.01,
        "low": np.minimum(opens, closes) * 0.99,
        "close": closes,
    }
    days = tuple(dt.date(2018, 1, 1) + dt.timedelta(days=i) for i in range(len(t)))
    symbols = ("SPY", "AAA")
    panel = MarketPanel(
        sessions=days,
        symbols=tuple(Symbol(s) for s in symbols),
        adjusted_closes=closes,
        raw_closes=closes,
        window_vwaps=opens,
        window_volumes=np.full_like(closes, 1e6),
        source="scenario(nested-fit)",
        adjusted_opens=opens,
        adjusted_highs=fields["high"],
        adjusted_lows=fields["low"],
        session_volumes=np.full_like(closes, 1e7),
    )
    data = CandleDataset(
        tuple(d.isoformat() for d in days),
        symbols,
        fields,
        opens,
        "1d",
        "corporate_action_adjusted",
        {"fit_start": str(days[0]), "fit_end": str(days[-1])},
    )
    protocol = SymbolicSearchProtocol(
        population_size=4,
        generations=1,
        max_depth=2,
        max_nodes=7,
        feature_terminals=(SymbolicTerminal("momentum", lookback=5),),
    )
    return data, panel, protocol, SymbolicScreenConfig(min_effect_sessions=2)


def discover(fit, output, **kwargs):
    return nested_search.discover_fold(
        *fit, seeds=(11, 12), shortlist_size=3, output=output, contract_hash="frozen", **kwargs
    )


def test_paired_equal_budget_complete_evidence_and_deterministic_ranking(fit, tmp_path):
    report = discover(fit, tmp_path)
    assert report["recorded_slots"] == 32
    assert len(report["searches"]) == 4
    for row in report["searches"]:
        assert row["recorded_slots"] == row["budget"] == 8
        state = json.loads((tmp_path / row["checkpoint"]).read_text())["state"]
        assert len(state["ledger"]) == 8
        assert all("failure" in slot or slot["fitness"] is not None for slot in state["ledger"])
    for method in ("genetic", "random"):
        ranks = report["ranked_contenders"][method]
        assert len(ranks) <= 3
        assert ranks == sorted(ranks, key=lambda row: (-row["fitness"], row["genome_sha256"]))
        assert len({row["genome_sha256"] for row in ranks}) == len(ranks)
        assert report["contenders"][method] == [row["pattern"] for row in ranks]
    assert discover(fit, tmp_path) == report


def test_population_checkpoint_resume_matches_full_search(fit, tmp_path, monkeypatch):
    expected = discover(fit, tmp_path / "full")
    original = nested_search.run_symbolic_search
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("simulated interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(nested_search, "run_symbolic_search", interrupted)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        discover(fit, tmp_path / "partial")
    checkpoint = tmp_path / "partial/genetic/seed-11/checkpoint.json"
    assert json.loads(checkpoint.read_text())["state"]["next_slot"] == 4
    monkeypatch.setattr(nested_search, "run_symbolic_search", original)
    assert discover(fit, tmp_path / "partial") == expected
    for row in expected["searches"]:
        relative = row["checkpoint"]
        assert (tmp_path / "partial" / relative).read_bytes() == (
            tmp_path / "full" / relative
        ).read_bytes()


def test_failed_populations_retain_every_slot_and_seed(fit, tmp_path):
    data, panel, protocol, config = fit
    restricted = replace(protocol, max_depth=0, max_nodes=1)
    impossible = replace(config, min_output_std=0.9)
    report = discover((data, panel, restricted, impossible), tmp_path)
    assert report["status"] == "ineligible"
    assert report["contenders"] == {"genetic": [], "random": []}
    assert report["failed_slots"] == report["recorded_slots"] == 32
    assert all(row["status"] == "ineligible" for row in report["searches"])


def test_null_remaps_features_jointly_but_keeps_original_labels(fit):
    screen = SymbolicScreen(*fit)
    leaf = fit[2].terminals()[0].model_dump(mode="json")
    pattern = parse_pattern(
        {
            **_template(),
            "genome": {"kind": "symbolic_indicator", "root": leaf, "outputTransform": "tanh"},
        }
    )
    original_values = screen.evaluator.evaluate(pattern).values.copy()
    original_labels = screen.labels.copy()
    mapping = np.arange(len(screen.rows))
    mapping[screen.rows] = np.roll(mapping[screen.rows], 7)
    nested_search._calendar(screen, None, mapping)
    mapped = screen.evaluator.evaluate(pattern).values
    np.testing.assert_array_equal(mapped, original_values[mapping])
    np.testing.assert_array_equal(screen.labels, original_labels)
    expected = (mapped[screen.rows] * 0.5 * original_labels[screen.rows]).mean() * 10000 - 0.01
    assert screen(pattern).fitness == pytest.approx(expected)


@pytest.mark.parametrize("kind", ["duplicate", "out_of_fit", "warmup", "float", "shape"])
def test_invalid_null_mapping_is_rejected_before_search(fit, tmp_path, kind):
    screen = SymbolicScreen(*fit)
    mapping = np.arange(len(screen.rows))
    if kind == "duplicate":
        mapping[0] = 1
    elif kind == "out_of_fit":
        mapping[-1] = len(mapping)
    elif kind == "warmup":
        eligible = np.flatnonzero(screen.rows)[0]
        mapping[0], mapping[eligible] = mapping[eligible], mapping[0]
    elif kind == "float":
        mapping = mapping.astype(float)
    else:
        mapping = mapping[:-1]
    with pytest.raises(ValueError, match="feature_mapping"):
        discover(fit, tmp_path, feature_mapping=mapping)
    assert not (tmp_path / "protocol.json").exists()


def test_fit_identity_and_null_calendar_are_bound_on_resume(fit, tmp_path):
    discover(fit, tmp_path)
    data, panel, protocol, config = fit
    mutated = replace(
        data, execution_prices=data.execution_prices * np.linspace(1, 1.01, 110)[:, None]
    )
    with pytest.raises(ValueError, match="protocol differs"):
        discover((mutated, panel, protocol, config), tmp_path)
    screen = SymbolicScreen(*fit)
    mapping = np.arange(len(screen.rows))
    mapping[screen.rows] = np.roll(mapping[screen.rows], 3)
    with pytest.raises(ValueError, match="protocol differs"):
        discover(fit, tmp_path, feature_mapping=mapping)
    rows = screen.rows.copy()
    rows[np.flatnonzero(rows)[0]] = False
    with pytest.raises(ValueError, match="protocol differs"):
        discover(fit, tmp_path, common_rows=rows)


def test_common_calendar_intersection_limits_every_expression(fit, tmp_path):
    rows = SymbolicScreen(*fit).rows
    rows[np.flatnonzero(rows)[::2]] = False
    report = discover(fit, tmp_path, common_rows=rows)
    checkpoint = tmp_path / report["searches"][0]["checkpoint"]
    state = json.loads(checkpoint.read_text())["state"]
    assert all(
        row["diagnostics"]["complete_training_sessions"] == int(rows.sum())
        for row in state["ledger"]
        if row["pattern"] is not None
    )


def test_unmatured_label_rows_can_donate_valid_fit_features(fit):
    screen = SymbolicScreen(*fit)
    features = np.ones(len(screen.rows), dtype=bool)
    for terminal in fit[2].terminals():
        features &= np.isfinite(screen.evaluator.feature(terminal)).all(axis=1)
    assert features[-2:].all()
    assert not screen.rows[-2:].any()
    mapping = np.arange(len(features))
    mapping[features] = np.roll(mapping[features], 2)
    before = screen.rows.copy()
    nested_search._calendar(screen, features, mapping)
    np.testing.assert_array_equal(screen.rows, before)
    assert np.any(mapping[screen.rows] >= len(features) - 2)
