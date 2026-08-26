"""Risk:reward selection for the volatility target, under split discipline.

What is being searched, and why only this
-----------------------------------------
The volatility target is the strategy's one explicit risk:reward dial: it
scales every position while changing no signal, no cap structure, and no
timing. "Find the optimal risk:reward ratio" is therefore answerable as a
narrow, registered question -- *does any nearby volatility target rank above
the frozen 8% on training-window consistency, and does that ranking hold on
data the ranking never saw?* -- rather than as a license to optimize the
strategy at large, which rule 30 forbids ("do not rank configurations by
in-sample Sharpe alone", "exploration is not permission to optimize").

The discipline, restated from :mod:`amat.validation.optimize`
-------------------------------------------------------------
* the panel is split once at a ``train_end`` chosen before any result is
  seen, and the split never moves;
* every candidate is *ranked* on the training window only, by
  :func:`~amat.validation.optimize.fold_score` -- worst-fold and median-fold
  consistency, not aggregate Sharpe;
* the validation window is measured for every candidate so the neighbourhood
  is on the record (a winner on a cliff is an artefact; a winner on a plateau
  might be a property), but the ranking is a pure function of training data
  and is fixed before validation enters the verdict;
* the number of candidates is recorded, because each one consumes some of the
  sample's capacity to surprise, and a reader must be able to discount for
  multiple testing.

Whatever wins here is NOT thereby a strategy change. A non-8% winner earns
only the right to be registered as a new version and pushed through the full
falsification and walk-forward protocol; the frozen configuration changes
through that door or not at all.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

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

__all__ = [
    "CandidateOutcome",
    "TargetVolatilitySweep",
    "sweep_target_volatility",
    "validation_slice",
]

#: Two training fold scores closer than this are one score measured twice, not
#: a ranking. ``sorted`` is stable, so without the guard an exact tie would be
#: "won" by whichever candidate happened to enter the list first -- ascending
#: volatility target, an ordering with no evidential content at all.
_SCORE_TIE_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    """One volatility target, measured on both sides of the split."""

    name: str
    target_volatility: float
    training: WindowStatistics
    training_fold_score: float
    validation: WindowStatistics


@dataclass(frozen=True, slots=True)
class TargetVolatilitySweep:
    """Every candidate beside the frozen baseline, ranking fixed by training."""

    strategy: str
    train_end: dt.date
    baseline_target: float
    candidates: tuple[CandidateOutcome, ...]
    caveat: str = (
        "Both windows are burned history: the configuration was frozen after all "
        "of it was known, so the validation window checks that a training ranking "
        "is not an artefact of its own window -- it is not out-of-sample evidence."
    )

    @property
    def ranked(self) -> tuple[CandidateOutcome, ...]:
        """Candidates by training fold score, best first. Training data only."""
        return tuple(sorted(self.candidates, key=lambda row: row.training_fold_score, reverse=True))

    @property
    def winner(self) -> CandidateOutcome:
        return self.ranked[0]

    @property
    def winner_is_measured(self) -> bool:
        """Whether the top training fold score is a measurement at all.

        :func:`~amat.validation.optimize.fold_score` returns ``-inf`` for a
        candidate below the minimum trade count -- unmeasured, not weak. A
        field whose best score is ``-inf`` has ranked nothing.
        """
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
    def baseline(self) -> CandidateOutcome:
        for row in self.candidates:
            if row.target_volatility == self.baseline_target:
                return row
        message = (  # pragma: no cover - the sweep always includes the baseline
            f"baseline target {self.baseline_target} was not among the candidates"
        )
        raise DataIntegrityError(message)

    @property
    def winner_holds_on_validation(self) -> bool:
        """Whether the training winner also beats the baseline on validation.

        ``False`` whenever the training ranking itself is unmeasured or tied:
        this flag is persisted into run records, and a promotion-shaped claim
        about a winner that was never really ranked must not survive there.
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
                "the frozen target stands."
            )
        if self.winner_is_tied:
            return (
                f"{prefix}. The top training fold scores are indistinguishable "
                f"(within {_SCORE_TIE_EPSILON:g}); no winner is declared and the "
                "frozen target stands."
            )
        if self.winner.target_volatility == self.baseline_target:
            return (
                f"{prefix}. The frozen {self.baseline_target:.0%} target ranks first on "
                "training-window consistency; no change is indicated."
            )
        if not self.winner_holds_on_validation:
            return (
                f"{prefix}. Target {self.winner.target_volatility:.0%} outranks the frozen "
                f"{self.baseline_target:.0%} on training consistency but does not hold on "
                "the validation window; keep the frozen target."
            )
        return (
            f"{prefix}. Target {self.winner.target_volatility:.0%} outranks the frozen "
            f"{self.baseline_target:.0%} on training consistency and holds on validation. "
            "That earns a registry entry and the full falsification protocol as a new "
            "version -- it does not change the frozen configuration."
        )


def _with_target(strategy: StrategyConfig, target: float) -> StrategyConfig:
    """The same frozen core with only the volatility target moved."""
    payload = strategy.model_dump()
    payload["constraints"] = {
        **payload["constraints"],
        "target_annualized_volatility": target,
    }
    payload["name"] = f"{strategy.name}-tv{round(target * 10000):04d}bp"
    return StrategyConfig.model_validate(payload)


def validation_slice(
    equity: Sequence[Decimal],
    dates: Sequence[SessionDate],
    fills_by_index: Sequence[int],
    train_end: dt.date,
) -> WindowStatistics:
    """Post-split statistics of a full-history run.

    The run itself saw all history -- the strategy needs its warm-up, exactly
    as a deployment would -- and only the *measurement* is restricted to
    sessions after ``train_end``. The slice starts one session early so its
    first return is the first post-split move. Shared by every split-
    disciplined sweep (target volatility, exit rules).
    """
    start = next(
        (index for index, date in enumerate(dates) if date > train_end),
        None,
    )
    if start is None or start == 0:
        message = f"train_end {train_end} leaves no validation window in the run"
        raise DataIntegrityError(message)
    base = start - 1
    return window_statistics(
        equity[base:],
        dates[base:],
        trades=sum(fills_by_index[start:]),
        win_rate=0.0,
        expectancy_r=0.0,
    )


def sweep_target_volatility(
    strategy: StrategyConfig,
    panel: MarketPanel,
    *,
    train_end: dt.date,
    targets: Sequence[float],
    initial_cash: Decimal = Decimal("100000"),
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
) -> TargetVolatilitySweep:
    """Measure every candidate target on the training and validation windows.

    The frozen configuration's own target is always included so the baseline
    row exists whatever ``targets`` says.

    Raises:
        DataIntegrityError: if the split leaves either window unusable, or a
            candidate produces an unmeasurable run.
    """
    baseline_target = strategy.constraints.target_annualized_volatility
    ordered: list[float] = sorted({*targets, baseline_target})

    training_panel, full_panel = split_panel(panel, train_end)

    candidates: list[CandidateOutcome] = []
    for target in ordered:
        variant = _with_target(strategy, target)
        universe = variant.to_universe()

        training_run = run_backtest(
            variant,
            training_panel,
            universe,
            initial_cash=initial_cash,
            actions_by_session=actions_by_session,
        )
        training = window_statistics(
            training_run.equity_curve,
            training_run.session_dates,
            trades=training_run.total_fills,
            win_rate=0.0,
            expectancy_r=0.0,
        )

        full_run = run_backtest(
            variant,
            full_panel,
            universe,
            initial_cash=initial_cash,
            actions_by_session=actions_by_session,
        )
        validation = validation_slice(
            full_run.equity_curve,
            full_run.session_dates,
            [len(record.fills) for record in full_run.sessions],
            train_end,
        )
        candidates.append(
            CandidateOutcome(
                name=variant.name,
                target_volatility=target,
                training=training,
                training_fold_score=fold_score(training),
                validation=validation,
            )
        )

    return TargetVolatilitySweep(
        strategy=f"{strategy.name} v{strategy.version}",
        train_end=train_end,
        baseline_target=baseline_target,
        candidates=tuple(candidates),
    )
