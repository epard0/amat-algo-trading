"""The universe falsification battery.

A universe change has no per-session series, so the scalar controls do not
apply; what can still be worthless is the addition itself. The battery must
(1) refuse comparisons that are not attributable to the universe, (2) collapse
to the baseline exactly when nothing was added, and (3) name a candidate that
a smaller universe matches for what it is.
"""

from __future__ import annotations

import pytest

from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.synthetic import generate_panel
from amat.errors import ConfigurationError
from amat.validation.falsification import (
    UniverseFalsification,
    UniverseOutcome,
    _without_symbol,
    falsify_universe,
)

CONFIG = "configs/strategy/amat_v1.toml"


@pytest.fixture(scope="module")
def baseline() -> StrategyConfig:
    return load_strategy_config(CONFIG).strategy


def _expanded(baseline: StrategyConfig, *symbols: str) -> StrategyConfig:
    payload = baseline.model_dump()
    payload["name"] = "amat-etf-expanded"
    payload["universe"]["clusters"] = [
        *payload["universe"]["clusters"],
        {"name": "additions", "gross_cap": 0.15, "symbols": list(symbols)},
    ]
    return StrategyConfig.model_validate(payload)


class TestTheGuard:
    def test_a_shared_cluster_cap_change_is_refused(self, baseline) -> None:
        """Regression: the core guard ignores the universe subtree, so a moved
        cap on a shared cluster used to slip through and the battery would
        have attributed a constraint change to 'the additions'."""
        payload = baseline.model_dump()
        payload["name"] = "amat-etf-cap-moved"
        clusters = [dict(cluster) for cluster in payload["universe"]["clusters"]]
        clusters[0] = {**clusters[0], "gross_cap": 0.80}
        payload["universe"]["clusters"] = [
            *clusters,
            {"name": "additions", "gross_cap": 0.15, "symbols": ["VNQ"]},
        ]
        tampered = StrategyConfig.model_validate(payload)
        panel = generate_panel(tampered.to_universe(), sessions=330, seed=3)
        with pytest.raises(ConfigurationError, match="keep its gross cap"):
            falsify_universe(tampered, baseline, panel)

    def test_dropping_a_baseline_symbol_is_refused(self, baseline) -> None:
        payload = baseline.model_dump()
        payload["name"] = "amat-etf-dropped"
        clusters = []
        for cluster in payload["universe"]["clusters"]:
            kept = [entry for entry in cluster["symbols"] if entry != "IWM"]
            if kept:
                clusters.append({**dict(cluster), "symbols": kept})
        payload["universe"]["clusters"] = [
            *clusters,
            {"name": "additions", "gross_cap": 0.15, "symbols": ["VNQ"]},
        ]
        tampered = StrategyConfig.model_validate(payload)
        panel = generate_panel(baseline.to_universe(), sessions=330, seed=3)
        with pytest.raises(ConfigurationError, match="missing: IWM"):
            falsify_universe(tampered, baseline, panel)

    def test_moving_a_baseline_symbol_between_clusters_is_refused(self, baseline) -> None:
        payload = baseline.model_dump()
        payload["name"] = "amat-etf-moved"
        clusters = [dict(cluster) for cluster in payload["universe"]["clusters"]]
        # Move IWM from us_equity into the new cluster: same symbol set, a
        # different constraint contract.
        clusters[0] = {**clusters[0], "symbols": ["SPY", "QQQ"]}
        payload["universe"]["clusters"] = [
            *clusters,
            {"name": "additions", "gross_cap": 0.15, "symbols": ["IWM", "VNQ"]},
        ]
        tampered = StrategyConfig.model_validate(payload)
        panel = generate_panel(tampered.to_universe(), sessions=330, seed=3)
        with pytest.raises(ConfigurationError, match="left 'us_equity'"):
            falsify_universe(tampered, baseline, panel)

    def test_a_core_difference_is_refused_not_measured(self, baseline) -> None:
        payload = baseline.model_dump()
        payload["name"] = "amat-etf-tampered"
        payload["trend"]["deadband_abs"] = 0.25
        tampered = StrategyConfig.model_validate(payload)
        panel = generate_panel(baseline.to_universe(), sessions=330, seed=3)
        with pytest.raises(ConfigurationError, match="identical non-universe core"):
            falsify_universe(tampered, baseline, panel)

    def test_the_error_names_what_differs(self, baseline) -> None:
        payload = baseline.model_dump()
        payload["trend"]["deadband_abs"] = 0.25
        payload["rebalance"]["min_weight_delta"] = 0.03
        tampered = StrategyConfig.model_validate(payload)
        panel = generate_panel(baseline.to_universe(), sessions=330, seed=3)
        with pytest.raises(ConfigurationError, match="rebalance, trend"):
            falsify_universe(tampered, baseline, panel)


class TestTheNullCase:
    def test_an_unchanged_universe_reproduces_the_baseline_exactly(self, baseline) -> None:
        """With nothing added there are no controls and the two rows agree
        bit-for-bit; any drift would mean the harness itself perturbs runs."""
        renamed = StrategyConfig.model_validate(
            {**baseline.model_dump(), "name": "amat-etf-null-check"}
        )
        panel = generate_panel(baseline.to_universe(), sessions=330, seed=5, trend_strength=0.5)
        outcome = falsify_universe(renamed, baseline, panel)
        assert outcome.leave_one_out == ()
        assert outcome.candidate.symbols == outcome.baseline.symbols
        assert outcome.candidate.total_return == outcome.baseline.total_return
        assert outcome.candidate.sharpe == outcome.baseline.sharpe
        assert outcome.candidate.fills == outcome.baseline.fills


class TestLeaveOneOut:
    def test_each_added_symbol_gets_a_variant_without_it(self, baseline) -> None:
        candidate = _expanded(baseline, "VNQ", "HYG")
        panel = generate_panel(candidate.to_universe(), sessions=330, seed=8, trend_strength=0.4)
        outcome = falsify_universe(candidate, baseline, panel)
        assert [row.name for row in outcome.leave_one_out] == ["without VNQ", "without HYG"]
        without_vnq = outcome.leave_one_out[0]
        assert "VNQ" not in without_vnq.symbols
        assert "HYG" in without_vnq.symbols
        # Baseline symbols are never candidates for removal.
        assert set(outcome.baseline.symbols) <= set(without_vnq.symbols)

    def test_an_emptied_cluster_is_dropped_not_left_hollow(self, baseline) -> None:
        candidate = _expanded(baseline, "VNQ")
        stripped = _without_symbol(candidate, "VNQ")
        cluster_names = [cluster.name for cluster in stripped.universe.clusters]
        assert "additions" not in cluster_names
        assert stripped.name == "amat-etf-expanded-without-VNQ"


def _row(name: str, sharpe: float, *, symbols: tuple[str, ...] = ("SPY",)) -> UniverseOutcome:
    return UniverseOutcome(
        name=name,
        symbols=symbols,
        total_return=0.5,
        annualized_return=0.06,
        annualized_volatility=0.07,
        sharpe=sharpe,
        max_drawdown=-0.09,
        fills=100,
    )


class TestTheVerdict:
    def test_no_additions_is_named_not_celebrated(self) -> None:
        """Regression: an empty leave-one-out with a baseline-beating candidate
        used to emit 'every added symbol contributes' -- with zero added
        symbols. The vacuous branch must be named for what it is."""
        outcome = UniverseFalsification(
            baseline=_row("baseline", 1.00),
            candidate=_row("candidate", 1.10),
            leave_one_out=(),
        )
        assert outcome.verdict.startswith("NO ADDITIONS")
        assert "SURVIVES" not in outcome.verdict

    def test_not_beating_the_baseline_is_rejection(self) -> None:
        outcome = UniverseFalsification(
            baseline=_row("baseline", 1.00),
            candidate=_row("candidate", 0.98),
            leave_one_out=(_row("without X", 0.90),),
        )
        assert outcome.verdict.startswith("REJECTED: the expanded universe does not improve")

    def test_a_dominating_smaller_universe_is_rejection(self) -> None:
        outcome = UniverseFalsification(
            baseline=_row("baseline", 1.00),
            candidate=_row("candidate", 1.05),
            leave_one_out=(_row("without X", 1.07), _row("without Y", 0.99)),
        )
        assert "REJECTED" in outcome.verdict
        assert "without X" in outcome.verdict

    def test_a_single_carrier_is_named_as_concentration(self) -> None:
        outcome = UniverseFalsification(
            baseline=_row("baseline", 1.00),
            candidate=_row("candidate", 1.10),
            leave_one_out=(_row("without X", 0.99), _row("without Y", 1.05)),
        )
        assert "carried by X" in outcome.verdict
        assert "REJECTED" not in outcome.verdict

    def test_a_clean_survival_still_demands_walk_forward(self) -> None:
        outcome = UniverseFalsification(
            baseline=_row("baseline", 1.00),
            candidate=_row("candidate", 1.10),
            leave_one_out=(_row("without X", 1.04), _row("without Y", 1.05)),
        )
        assert outcome.verdict.startswith("SURVIVES leave-one-out: every added symbol")
        assert "walk-forward" in outcome.verdict
