"""Controls that a worthless conditioning signal cannot pass.

A regime overlay will almost always change a backtest's return. The question is
never "did it change something" but "did it change something a coincidence could
not have". These controls answer that by constructing signals that are worthless
*by construction* and giving them the same opportunity.

Three controls, and what each one kills
---------------------------------------
**Shuffled.** The same multipliers in a different order. It preserves the exact
distribution of position sizes -- same mean, same floor, same fraction of
sessions at reduced size -- and destroys only the timing. If the real series does
not beat this, the overlay is a static de-risking, not a regime signal, and the
same effect could be had by lowering the volatility target and paying no
turnover for it.

**Random with matched turnover.** A signal that flips as often as the real one,
between the same two levels, at random moments. This kills the explanation "the
result comes from trading less often" and isolates *when* the cuts land.

**Static mean.** A constant multiplier equal to the real series' average. It
costs no turnover at all. Any overlay that fails to beat its own average is
strictly worse than simply holding less, and the honest conclusion is to hold
less.

Every control is run at the same cost assumption as the candidate. A conditioning
signal that only wins gross is not a finding, because gross is not investable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import StanceBlend, run_backtest
from amat.backtest.stances import assemble_stance, blend_series
from amat.config.models import StrategyConfig
from amat.config.switching import SwitchingStrategyConfig
from amat.domain.actions import CorporateAction
from amat.domain.instruments import Universe
from amat.domain.types import SessionDate, Symbol
from amat.errors import ConfigurationError
from amat.validation.optimize import window_statistics

__all__ = [
    "ControlOutcome",
    "FalsificationResult",
    "UniverseFalsification",
    "UniverseOutcome",
    "falsify_blend",
    "falsify_conditioning",
    "falsify_universe",
    "matched_controls",
]


@dataclass(frozen=True, slots=True)
class ControlOutcome:
    """One run's headline statistics."""

    name: str
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    max_drawdown: float
    fills: int
    mean_scalar: float
    flips: int
    """How many times the scalar changed value. This is the turnover the
    conditioning imposes on top of the strategy's own."""


@dataclass(frozen=True, slots=True)
class FalsificationResult:
    """The candidate beside the controls it has to beat."""

    baseline: ControlOutcome
    candidate: ControlOutcome
    controls: tuple[ControlOutcome, ...]

    @property
    def beats_every_control_on_sharpe(self) -> bool:
        return all(self.candidate.sharpe > control.sharpe for control in self.controls)

    @property
    def beats_baseline_on_sharpe(self) -> bool:
        return self.candidate.sharpe > self.baseline.sharpe

    @property
    def verdict(self) -> str:
        """A sentence a reader can act on, stated conservatively.

        "Survives" is deliberately not "works". Passing these controls means the
        result is not obviously a coincidence; it is not evidence of an
        out-of-sample edge, which only a walk-forward on untouched data can
        speak to.
        """
        if not self.beats_baseline_on_sharpe:
            return "REJECTED: does not improve on unconditioned v1."
        if not self.beats_every_control_on_sharpe:
            beaten = [c.name for c in self.controls if c.sharpe >= self.candidate.sharpe]
            return f"REJECTED: a worthless control matched or beat it ({', '.join(beaten)})."
        return "SURVIVES the controls. Not evidence of an edge; walk-forward still required."


def _flips(scalars: np.ndarray) -> int:
    return int(np.count_nonzero(np.diff(scalars) != 0.0))


def matched_controls(scalars: np.ndarray, *, seed: int = 0) -> dict[str, np.ndarray]:
    """Worthless series matched to ``scalars`` on everything except timing."""
    generator = np.random.default_rng(seed)

    shuffled = scalars.copy()
    generator.shuffle(shuffled)

    # Same two levels and the same number of changes, placed at random.
    low, high = float(np.min(scalars)), float(np.max(scalars))
    random_matched = np.full_like(scalars, high)
    if high > low:
        changes = max(_flips(scalars), 1)
        cut_points = np.sort(generator.choice(scalars.size, size=changes, replace=False))
        level = high
        previous = 0
        for point in cut_points:
            random_matched[previous:point] = level
            level = low if level == high else high
            previous = int(point)
        random_matched[previous:] = level

    static = np.full_like(scalars, float(np.mean(scalars)))

    return {
        "shuffled": shuffled,
        "random_matched_turnover": random_matched,
        "static_mean": static,
    }


def _measure(
    name: str,
    strategy: StrategyConfig,
    panel: MarketPanel,
    universe: Universe,
    scalars: np.ndarray | None,
    *,
    initial_cash: Decimal,
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None,
    stance: StanceBlend | None = None,
    stance_series: np.ndarray | None = None,
) -> ControlOutcome:
    result = run_backtest(
        strategy,
        panel,
        universe,
        initial_cash=initial_cash,
        actions_by_session=actions_by_session,
        weight_scalars=scalars,
        stance=stance,
    )
    series = stance_series if stance_series is not None else scalars
    # `window_statistics` rather than `amat.metrics.summarize`: metrics is layer 5
    # and validation is layer 4, so importing it would point an import downward
    # and `tests/unit/test_dependency_direction.py` rejects the build. Both
    # functions use the contract's identical conventions -- simple session
    # returns, 252-session annualization, sqrt volatility scaling -- so the
    # numbers agree; only the layer differs.
    summary = window_statistics(
        result.equity_curve,
        result.session_dates,
        trades=result.total_fills,
        win_rate=0.0,
        expectancy_r=0.0,
    )
    return ControlOutcome(
        name=name,
        total_return=summary.total_return,
        annualized_return=summary.annualized_return,
        annualized_volatility=summary.annualized_volatility,
        sharpe=summary.sharpe,
        max_drawdown=summary.max_drawdown,
        fills=result.total_fills,
        mean_scalar=1.0 if series is None else float(np.mean(series)),
        flips=0 if series is None else _flips(series),
    )


def falsify_conditioning(
    strategy: StrategyConfig,
    panel: MarketPanel,
    universe: Universe,
    scalars: np.ndarray,
    *,
    initial_cash: Decimal = Decimal("100000"),
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
    seed: int = 0,
    baseline_name: str = "v1 unconditioned",
    candidate_name: str = "v3 regime-conditioned",
) -> FalsificationResult:
    """Run the candidate conditioning against v1 and against matched controls.

    Every run uses identical data, costs, funding and corporate actions, so the
    only thing that differs between them is the scalar series. That is what makes
    the comparison attributable. The row names default to the v3 experiment
    that built this harness; a different conditioning candidate names itself.
    """
    baseline = _measure(
        baseline_name,
        strategy,
        panel,
        universe,
        None,
        initial_cash=initial_cash,
        actions_by_session=actions_by_session,
    )
    candidate = _measure(
        candidate_name,
        strategy,
        panel,
        universe,
        scalars,
        initial_cash=initial_cash,
        actions_by_session=actions_by_session,
    )
    controls = tuple(
        _measure(
            name,
            strategy,
            panel,
            universe,
            series,
            initial_cash=initial_cash,
            actions_by_session=actions_by_session,
        )
        for name, series in matched_controls(scalars, seed=seed).items()
    )
    return FalsificationResult(baseline=baseline, candidate=candidate, controls=controls)


@dataclass(frozen=True, slots=True)
class UniverseOutcome:
    """One universe variant's headline statistics."""

    name: str
    symbols: tuple[str, ...]
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    max_drawdown: float
    fills: int


@dataclass(frozen=True, slots=True)
class UniverseFalsification:
    """An expanded-universe candidate beside the variants that test it.

    A universe change is not a conditioning overlay, so the scalar controls
    (shuffled, random, static mean) do not apply: there is no per-session
    series whose mean could be doing the work. What can still be worthless is
    the *addition itself* -- an improvement carried entirely by one lucky
    symbol is stock-picking wearing a diversification claim. The controls are
    therefore leave-one-out variants over every added symbol, each run on
    identical data, costs, funding, and corporate actions.
    """

    baseline: UniverseOutcome
    candidate: UniverseOutcome
    leave_one_out: tuple[UniverseOutcome, ...]

    @property
    def beats_baseline_on_sharpe(self) -> bool:
        return self.candidate.sharpe > self.baseline.sharpe

    @property
    def dominated_by(self) -> tuple[str, ...]:
        """Leave-one-out variants that match or beat the candidate.

        If dropping a symbol does not cost anything, that symbol's inclusion
        has no measured justification and the candidate is not the version to
        keep.
        """
        return tuple(row.name for row in self.leave_one_out if row.sharpe >= self.candidate.sharpe)

    @property
    def carried_by(self) -> tuple[str, ...]:
        """Added symbols whose removal collapses the candidate to the baseline.

        If removing one symbol erases the whole improvement, the finding is
        "that symbol was good in this sample", not "breadth helps" -- a
        concentration the verdict must name.
        """
        return tuple(
            row.name.removeprefix("without ")
            for row in self.leave_one_out
            if row.sharpe <= self.baseline.sharpe
        )

    @property
    def verdict(self) -> str:
        """Conservative, and still only an in-sample statement either way."""
        if not self.leave_one_out:
            # With nothing added there is nothing for leave-one-out to test,
            # and the guard has already forced the universes to be identical
            # in caps and membership -- any difference between the rows is a
            # harness defect, not a finding.
            return (
                "NO ADDITIONS: the candidate adds no symbols, so leave-one-out "
                "has nothing to test; candidate and baseline should be identical runs."
            )
        if not self.beats_baseline_on_sharpe:
            return "REJECTED: the expanded universe does not improve on the baseline."
        if self.dominated_by:
            return (
                f"REJECTED: a smaller universe matched or beat it "
                f"({', '.join(self.dominated_by)}); the addition is not pulling its weight."
            )
        if self.carried_by:
            return (
                f"SURVIVES leave-one-out, but the improvement is carried by "
                f"{', '.join(self.carried_by)} -- a concentration, not breadth. "
                "Walk-forward still required."
            )
        return (
            "SURVIVES leave-one-out: every added symbol contributes and none is "
            "indispensable. Not evidence of an edge; walk-forward still required."
        )


def _identical_core(candidate: StrategyConfig, baseline: StrategyConfig) -> None:
    """Require the two configurations to differ only in name/version/universe.

    A universe comparison in which the deadband or cost model also moved is not
    attributable to the universe, so the harness refuses to run it.
    """
    ignored = {"name", "version", "universe"}
    left = {key: value for key, value in candidate.model_dump().items() if key not in ignored}
    right = {key: value for key, value in baseline.model_dump().items() if key not in ignored}
    if left != right:
        differing = sorted(key for key in left if left[key] != right[key])
        message = (
            "universe falsification requires an identical non-universe core; "
            f"candidate and baseline differ in: {', '.join(differing)}"
        )
        raise ConfigurationError(message)


def _universe_extends(candidate: StrategyConfig, baseline: StrategyConfig) -> None:
    """Require the candidate universe to be a pure extension of the baseline's.

    Only then is a measured difference attributable to the *additions*: every
    baseline symbol must survive in its original cluster, and every cluster
    common to both configurations must keep its gross cap. A candidate that
    drops a baseline symbol, reshuffles membership, or moves a shared cap is a
    different portfolio contract wearing an expansion's name, and the
    leave-one-out controls would test the wrong thing.
    """
    candidate_symbols = {str(symbol) for symbol in candidate.universe.symbols}
    dropped = sorted(
        str(symbol) for symbol in baseline.universe.symbols if str(symbol) not in candidate_symbols
    )
    if dropped:
        message = (
            "universe falsification requires the candidate to contain every "
            f"baseline symbol; missing: {', '.join(dropped)}"
        )
        raise ConfigurationError(message)

    candidate_clusters = {cluster.name: cluster for cluster in candidate.universe.clusters}
    for cluster in baseline.universe.clusters:
        twin = candidate_clusters.get(cluster.name)
        if twin is None:
            message = (
                f"universe falsification requires baseline cluster {cluster.name!r} "
                "to exist in the candidate; renaming or dissolving it changes the "
                "constraint contract, not just the universe"
            )
            raise ConfigurationError(message)
        if twin.gross_cap != cluster.gross_cap:
            message = (
                f"universe falsification requires shared cluster {cluster.name!r} to "
                f"keep its gross cap ({cluster.gross_cap}, candidate has "
                f"{twin.gross_cap}); a moved cap is a constraint change the "
                "leave-one-out controls cannot attribute"
            )
            raise ConfigurationError(message)
        moved = sorted(
            str(symbol)
            for symbol in cluster.symbols
            if str(symbol) not in {str(entry) for entry in twin.symbols}
        )
        if moved:
            message = (
                f"universe falsification requires baseline symbols to keep their "
                f"cluster; {', '.join(moved)} left {cluster.name!r} in the candidate"
            )
            raise ConfigurationError(message)


def _without_symbol(candidate: StrategyConfig, symbol: str) -> StrategyConfig:
    """The candidate with one symbol removed; an emptied cluster is dropped."""
    payload = candidate.model_dump()
    clusters = []
    for cluster in payload["universe"]["clusters"]:
        kept = [entry for entry in cluster["symbols"] if entry != symbol]
        if kept:
            clusters.append({**cluster, "symbols": kept})
    payload["universe"]["clusters"] = clusters
    payload["name"] = f"{candidate.name}-without-{symbol}"
    return StrategyConfig.model_validate(payload)


def _narrow_actions(
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None,
    symbols: Sequence[str],
) -> Mapping[SessionDate, Sequence[CorporateAction]] | None:
    """Keep only actions for ``symbols``, dropping sessions left empty."""
    if actions_by_session is None:
        return None
    wanted = set(symbols)
    narrowed: dict[SessionDate, Sequence[CorporateAction]] = {}
    for session, actions in actions_by_session.items():
        kept = tuple(action for action in actions if str(action.symbol) in wanted)
        if kept:
            narrowed[session] = kept
    return narrowed


def falsify_universe(
    candidate: StrategyConfig,
    baseline: StrategyConfig,
    panel: MarketPanel,
    *,
    initial_cash: Decimal = Decimal("100000"),
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
) -> UniverseFalsification:
    """Measure an expanded-universe candidate against its baseline and
    leave-one-out variants.

    ``panel`` must cover every symbol either configuration names; each run
    slices its own columns from it, so all runs share one session axis and one
    data snapshot. Costs, funding, and corporate actions are identical across
    rows; the only thing that varies is which symbols exist.

    Raises:
        ConfigurationError: if the two configurations differ outside
            name/version/universe, or if the candidate universe is not a pure
            extension of the baseline's (dropped symbols, moved cluster
            memberships, or changed shared-cluster caps) -- either way the
            comparison would not be attributable to the additions.
        DataIntegrityError: if the panel lacks a required symbol (raised by
            ``slice_symbols``).
    """
    _identical_core(candidate, baseline)
    _universe_extends(candidate, baseline)

    def measured(name: str, config: StrategyConfig) -> UniverseOutcome:
        symbols = [str(entry) for entry in config.universe.symbols]
        result = run_backtest(
            config,
            panel.slice_symbols([Symbol(entry) for entry in symbols]),
            config.to_universe(),
            initial_cash=initial_cash,
            actions_by_session=_narrow_actions(actions_by_session, symbols),
        )
        summary = window_statistics(
            result.equity_curve,
            result.session_dates,
            trades=result.total_fills,
            win_rate=0.0,
            expectancy_r=0.0,
        )
        return UniverseOutcome(
            name=name,
            symbols=tuple(symbols),
            total_return=summary.total_return,
            annualized_return=summary.annualized_return,
            annualized_volatility=summary.annualized_volatility,
            sharpe=summary.sharpe,
            max_drawdown=summary.max_drawdown,
            fills=result.total_fills,
        )

    baseline_symbols = {str(entry) for entry in baseline.universe.symbols}
    added = [
        str(entry) for entry in candidate.universe.symbols if str(entry) not in baseline_symbols
    ]

    baseline_row = measured(f"baseline ({baseline.name})", baseline)
    candidate_row = measured(f"candidate ({candidate.name})", candidate)
    leave_one_out = tuple(
        measured(f"without {symbol}", _without_symbol(candidate, symbol)) for symbol in added
    )
    return UniverseFalsification(
        baseline=baseline_row, candidate=candidate_row, leave_one_out=leave_one_out
    )


def falsify_blend(
    config: SwitchingStrategyConfig,
    panel: MarketPanel,
    *,
    initial_cash: Decimal = Decimal("100000"),
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
    seed: int = 0,
) -> FalsificationResult:
    """Run the v4 stance blend against its baseline and matched controls.

    The candidate is the configured breadth-driven lambda. The controls are the
    standard three, applied to the *lambda series* while everything else --
    both stance definitions, data, costs, funding, corporate actions -- stays
    identical, plus one extra that only a blend has: ``defensive_only``
    (lambda = 0), so a "switching helps" claim can be checked against simply
    holding the defensive stance outright.

    The baseline is the pure trend stance (lambda = 1), which is v1 under
    v4's trend parameterization.
    """
    trend_universe = config.trend.to_universe()
    lam = blend_series(config, panel)

    def measured(name: str, series: np.ndarray | None) -> ControlOutcome:
        stance = None if series is None else assemble_stance(config, panel, weights=series)
        return _measure(
            name,
            config.trend,
            panel,
            trend_universe,
            None,
            initial_cash=initial_cash,
            actions_by_session=actions_by_session,
            stance=stance,
            stance_series=series,
        )

    baseline = measured("trend stance only (v1)", None)
    candidate = measured("v4 breadth-blended", lam)
    controls = [
        measured(f"lambda {name}", series)
        for name, series in matched_controls(lam, seed=seed).items()
    ]
    controls.append(measured("defensive_only", np.zeros_like(lam)))
    return FalsificationResult(baseline=baseline, candidate=candidate, controls=tuple(controls))
