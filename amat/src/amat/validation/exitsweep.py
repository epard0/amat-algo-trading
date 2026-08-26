"""Exit-rule selection: does any stop-loss/take-profit beat no exits at all?

The question, stated before the answer
--------------------------------------
The frozen v1 core exits through its signal deadband and volatility target;
it has no per-trade stop or profit target. Registered experiment 0003 asks
whether an explicit :class:`~amat.backtest.exits.ExitRules` overlay improves
on that -- under exactly the discipline of
:mod:`amat.validation.sweep`: the panel splits once at a pre-chosen
``train_end``; candidates rank on training-window fold consistency only; the
validation window is measured for every candidate, ranking-blind; the
candidate count is recorded for multiple-testing discount; and a surviving
winner earns a registry entry, never an in-place change.

The overfitting posture, stated in advance
------------------------------------------
A stop/take-profit grid is a classic overfitting surface -- two continuous
parameters, an embargo, and a plausible story for every cell. The surface is
deliberately kept small: a fixed grid, a fixed embargo (21 sessions, the
shortest signal horizon, chosen at registration and not swept), and the
prior stated at registration: for a volatility-targeted trend design, stops
usually *lose* -- they realize losses the position sizing already sized for,
and interrupt exactly the multi-month trends the signal exists to ride. The
baseline "no exits" row is always included; beating it on training and
holding on validation is the minimum bar, not proof.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from amat.backtest.exits import ExitRules
from amat.backtest.panel import MarketPanel
from amat.backtest.runner import run_backtest
from amat.config.models import StrategyConfig
from amat.domain.actions import CorporateAction
from amat.domain.types import SessionDate
from amat.errors import DataIntegrityError
from amat.validation.optimize import (
    WindowStatistics,
    fold_score,
    split_panel,
    window_statistics,
)
from amat.validation.sweep import validation_slice

__all__ = [
    "ExitCandidate",
    "ExitRulesSweep",
    "RandomExitControl",
    "random_exit_control",
    "sweep_exit_rules",
]

#: Same tie logic as the volatility-target sweep, same reason: a stable sort
#: would otherwise hand exact ties to whichever candidate entered first.
_SCORE_TIE_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class ExitCandidate:
    """One exit-rule combination, measured on both sides of the split."""

    name: str
    stop_loss: float | None
    take_profit: float | None
    training: WindowStatistics
    training_fold_score: float
    validation: WindowStatistics

    @property
    def is_baseline(self) -> bool:
        return self.stop_loss is None and self.take_profit is None


@dataclass(frozen=True, slots=True)
class ExitRulesSweep:
    """Every exit-rule candidate beside the no-exit baseline."""

    strategy: str
    train_end: dt.date
    reentry_embargo_sessions: int
    candidates: tuple[ExitCandidate, ...]
    caveat: str = (
        "Both windows are burned history: the configuration was frozen after all "
        "of it was known, so the validation window checks that a training ranking "
        "is not an artefact of its own window -- it is not out-of-sample evidence."
    )

    @property
    def ranked(self) -> tuple[ExitCandidate, ...]:
        """Candidates by training fold score, best first. Training data only."""
        return tuple(sorted(self.candidates, key=lambda row: row.training_fold_score, reverse=True))

    @property
    def winner(self) -> ExitCandidate:
        return self.ranked[0]

    @property
    def baseline(self) -> ExitCandidate:
        for row in self.candidates:
            if row.is_baseline:
                return row
        message = (  # pragma: no cover - the sweep always includes the baseline
            "the no-exit baseline was not among the candidates"
        )
        raise DataIntegrityError(message)

    @property
    def winner_is_measured(self) -> bool:
        """Whether the top training fold score is a measurement at all."""
        return math.isfinite(self.winner.training_fold_score)

    @property
    def winner_is_tied(self) -> bool:
        """Whether first and second place are indistinguishable on training."""
        if len(self.ranked) == 1 or not self.winner_is_measured:
            return False
        runner_up = self.ranked[1].training_fold_score
        return (
            math.isfinite(runner_up)
            and abs(self.winner.training_fold_score - runner_up) <= _SCORE_TIE_EPSILON
        )

    @property
    def winner_holds_on_validation(self) -> bool:
        """Whether the training winner also beats no-exits on validation.

        ``False`` whenever the ranking is unmeasured or tied, for the same
        reason as the volatility-target sweep: this flag is persisted, and a
        promotion-shaped claim must not rest on a non-ranking.
        """
        if not self.winner_is_measured or self.winner_is_tied:
            return False
        return self.winner.validation.sharpe > self.baseline.validation.sharpe

    @property
    def verdict(self) -> str:
        """Conservative, and never a promotion: at most a registration hint."""
        prefix = (
            f"{len(self.candidates)} candidates evaluated (multiple-testing risk rises with each)"
        )
        if not self.winner_is_measured:
            return (
                f"{prefix}. No candidate produced a measurable training fold score "
                "(minimum trade count not reached); the sweep ranks nothing and "
                "the frozen no-exit core stands."
            )
        if self.winner_is_tied:
            return (
                f"{prefix}. The top training fold scores are indistinguishable "
                f"(within {_SCORE_TIE_EPSILON:g}); no winner is declared and the "
                "frozen no-exit core stands."
            )
        if self.winner.is_baseline:
            return (
                f"{prefix}. The frozen no-exit core ranks first on training-window "
                "consistency; no stop-loss or take-profit is indicated."
            )
        if not self.winner_holds_on_validation:
            return (
                f"{prefix}. {self.winner.name} outranks the no-exit core on training "
                "consistency but does not hold on the validation window; keep the "
                "frozen no-exit core."
            )
        return (
            f"{prefix}. {self.winner.name} outranks the no-exit core on training "
            "consistency and holds on validation. That earns a registry entry and "
            "the full falsification protocol as a new version -- it does not "
            "change the frozen configuration."
        )


def _candidate_grid(
    stop_losses: Sequence[float],
    take_profits: Sequence[float],
) -> list[tuple[float | None, float | None]]:
    """The full cross of ``{None, *stop_losses} x {None, *take_profits}``.

    ``(None, None)`` -- the frozen no-exit core -- is always the first row.
    """
    stops: list[float | None] = [None, *sorted(set(stop_losses))]
    profits: list[float | None] = [None, *sorted(set(take_profits))]
    return [(stop, profit) for stop in stops for profit in profits]


def _measure_rules(
    strategy: StrategyConfig,
    training_panel: MarketPanel,
    full_panel: MarketPanel,
    universe: object,
    *,
    rules: ExitRules | None,
    name: str,
    stop: float | None,
    profit: float | None,
    train_end: dt.date,
    initial_cash: Decimal,
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None,
) -> ExitCandidate:
    """One rule set measured on both sides of the split, sweep-identically."""
    training_run = run_backtest(
        strategy,
        training_panel,
        universe,  # type: ignore[arg-type]
        initial_cash=initial_cash,
        actions_by_session=actions_by_session,
        exit_rules=rules,
    )
    training = window_statistics(
        training_run.equity_curve,
        training_run.session_dates,
        trades=training_run.total_fills,
        win_rate=0.0,
        expectancy_r=0.0,
    )
    full_run = run_backtest(
        strategy,
        full_panel,
        universe,  # type: ignore[arg-type]
        initial_cash=initial_cash,
        actions_by_session=actions_by_session,
        exit_rules=rules,
    )
    validation = validation_slice(
        full_run.equity_curve,
        full_run.session_dates,
        [len(record.fills) for record in full_run.sessions],
        train_end,
    )
    return ExitCandidate(
        name=name,
        stop_loss=stop,
        take_profit=profit,
        training=training,
        training_fold_score=fold_score(training),
        validation=validation,
    )


@dataclass(frozen=True, slots=True)
class RandomExitControl:
    """Seeded random exit rules beside the no-exit core: the grid's control.

    The registered grid (experiment 0003) sampled 12 fixed points of a
    two-parameter surface. This control asks whether its conclusion -- no
    overlay holds its training advantage on validation -- was an artifact of
    *those points* by drawing rules uniformly at random (stop in
    [3%, 30%], take-profit in [5%, 60%], the same fixed embargo) and
    measuring each under the identical split. Every draw and the seed are
    recorded; the summary statistic is the fraction of random rules whose
    validation Sharpe beats the no-exit core's.
    """

    seed: int
    baseline: ExitCandidate
    draws: tuple[ExitCandidate, ...]
    fraction_beating_baseline_on_validation: float
    caveat: str = (
        "A control of an already-falsified experiment on the same burned "
        "history: it can strengthen or weaken the 0003 conclusion, never "
        "promote an overlay by itself."
    )


#: The uniform sampling domain for random stop/take-profit draws.
_RANDOM_STOP_RANGE = (0.03, 0.30)
_RANDOM_PROFIT_RANGE = (0.05, 0.60)


def random_exit_control(
    strategy: StrategyConfig,
    panel: MarketPanel,
    *,
    train_end: dt.date,
    draws: int,
    seed: int,
    reentry_embargo_sessions: int = 21,
    initial_cash: Decimal = Decimal("100000"),
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
) -> RandomExitControl:
    """Measure ``draws`` random stop/take-profit pairs against the no-exit core.

    Raises:
        DataIntegrityError: if ``draws`` is not strictly positive.
    """
    if draws < 1:
        message = f"draws must be at least 1, received {draws}"
        raise DataIntegrityError(message)
    import numpy as np  # noqa: PLC0415 - the only numpy use in this module

    universe = strategy.to_universe()
    training_panel, full_panel = split_panel(panel, train_end)
    baseline = _measure_rules(
        strategy,
        training_panel,
        full_panel,
        universe,
        rules=None,
        name="no exits (frozen core)",
        stop=None,
        profit=None,
        train_end=train_end,
        initial_cash=initial_cash,
        actions_by_session=actions_by_session,
    )

    rng = np.random.default_rng(seed)
    measured: list[ExitCandidate] = []
    for _index in range(draws):
        stop = float(rng.uniform(*_RANDOM_STOP_RANGE))
        profit = float(rng.uniform(*_RANDOM_PROFIT_RANGE))
        rules = ExitRules(
            stop_loss=stop,
            take_profit=profit,
            reentry_embargo_sessions=reentry_embargo_sessions,
        )
        measured.append(
            _measure_rules(
                strategy,
                training_panel,
                full_panel,
                universe,
                rules=rules,
                name=rules.label,
                stop=stop,
                profit=profit,
                train_end=train_end,
                initial_cash=initial_cash,
                actions_by_session=actions_by_session,
            )
        )

    beating = sum(1 for draw in measured if draw.validation.sharpe > baseline.validation.sharpe)
    return RandomExitControl(
        seed=seed,
        baseline=baseline,
        draws=tuple(measured),
        fraction_beating_baseline_on_validation=beating / len(measured),
    )


def sweep_exit_rules(
    strategy: StrategyConfig,
    panel: MarketPanel,
    *,
    train_end: dt.date,
    stop_losses: Sequence[float],
    take_profits: Sequence[float],
    reentry_embargo_sessions: int = 21,
    initial_cash: Decimal = Decimal("100000"),
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
) -> ExitRulesSweep:
    """Measure every exit-rule combination on both sides of the split.

    Raises:
        DataIntegrityError: if the split leaves either window unusable, or a
            candidate produces an unmeasurable run.
        ConfigurationError: if a grid value lies outside the
            :class:`~amat.backtest.exits.ExitRules` domain.
    """
    universe = strategy.to_universe()
    training_panel, full_panel = split_panel(panel, train_end)

    candidates: list[ExitCandidate] = []
    for stop, profit in _candidate_grid(stop_losses, take_profits):
        if stop is None and profit is None:
            rules = None
            name = "no exits (frozen core)"
        else:
            rules = ExitRules(
                stop_loss=stop,
                take_profit=profit,
                reentry_embargo_sessions=reentry_embargo_sessions,
            )
            name = rules.label
        candidates.append(
            _measure_rules(
                strategy,
                training_panel,
                full_panel,
                universe,
                rules=rules,
                name=name,
                stop=stop,
                profit=profit,
                train_end=train_end,
                initial_cash=initial_cash,
                actions_by_session=actions_by_session,
            )
        )

    return ExitRulesSweep(
        strategy=f"{strategy.name} v{strategy.version}",
        train_end=train_end,
        reentry_embargo_sessions=reentry_embargo_sessions,
        candidates=tuple(candidates),
    )
