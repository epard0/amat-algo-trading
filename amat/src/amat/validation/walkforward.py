"""Expanding walk-forward evaluation and the locked-holdout declaration.

What "walk-forward" means for a strategy with no fitted parameters
------------------------------------------------------------------
The frozen v1 core estimates nothing across runs: every decision at session
``t`` is a pure function of configuration constants and data through ``t``,
including the account state that data produced. An *expanding* walk-forward
never restarts the account, so it IS one full-history run, and the folds are
calendar slices of that run's equity curve. (Restarting at each fold
boundary would evaluate a different object -- a sequence of cold-started
accounts, each re-paying warm-up from flat -- which is the anchored variant
this module deliberately does not implement.) The causality tests elsewhere
are what make the slicing legitimate: they prove row ``t`` of the run cannot
depend on rows after ``t``.

What these folds are evidence of, and what they are not
-------------------------------------------------------
Fold dispersion measures **consistency**: whether the aggregate result was
earned across years or granted by one of them. It is *not* out-of-sample
evidence in the strict sense, because the configuration was frozen in 2026 by
people who had seen this history. Genuinely untouched data starts only after
the declared holdout boundary, which is why :func:`declare_holdout` exists and
why every result quoting these folds must say "walk-forward consistency", not
"out-of-sample performance".

The locked holdout
------------------
A declaration freezes a boundary session. Data at or before the boundary is
burned -- full-sample results were inspected before the declaration existed --
and data after it may be evaluated only deliberately:
:func:`assert_respects_holdout` fails any panel that crosses the boundary
unless the caller supplies a reason, which the run manifest then records
(``holdout_accessed`` / ``holdout_access_reason``). Configuration changes
motivated by anything seen past the boundary are an automatic rejection.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import run_backtest
from amat.config.models import StrategyConfig
from amat.domain.actions import CorporateAction
from amat.domain.instruments import Universe
from amat.domain.types import SessionDate
from amat.errors import DataIntegrityError, ImmutableArtifactError
from amat.paths import reserve_immutable_directory, resolve_within
from amat.validation.optimize import WindowStatistics, window_statistics

__all__ = [
    "FoldOutcome",
    "GateCheck",
    "HoldoutDeclaration",
    "WalkForwardResult",
    "assert_respects_holdout",
    "declare_holdout",
    "default_holdout_root",
    "load_holdout_declaration",
    "walk_forward",
]

#: A calendar fold with fewer sessions than this is reported as part of its
#: neighbour rather than as a fold of its own: a two-week "year" cannot carry
#: an annualized statistic anyone should read.
_MIN_FOLD_SESSIONS = 40

#: Research gates from ``.claude/references/promotion-gates.md`` measurable
#: from a walk-forward alone. The rest of that document's gates (holdout
#: return, cost stresses, concentration, parameter neighbourhood) are owned by
#: other harnesses and are deliberately NOT restated here, so that a passing
#: walk-forward cannot be misread as a passing protocol.
_GATE_MIN_SHARPE = 0.50
_GATE_MIN_POSITIVE_FOLDS = 0.60
_GATE_WORST_DRAWDOWN = -0.20

#: A positive-fold fraction over fewer folds than this is a couple of coin
#: tosses wearing a percentage; the gate reports itself unmeasurable instead.
_MIN_GATE_FOLDS = 4


@dataclass(frozen=True, slots=True)
class FoldOutcome:
    """One calendar fold of the single expanding run."""

    label: str
    statistics: WindowStatistics

    @property
    def positive(self) -> bool:
        return self.statistics.total_return > 0.0


@dataclass(frozen=True, slots=True)
class GateCheck:
    """One research gate, measured. PASS here is necessary, never sufficient.

    ``measurable`` is ``False`` when the sample cannot support the gate at all
    (for example, too few folds for a fold-fraction to mean anything); the
    gate then also reports ``passed=False``, because "could not measure" must
    never be printable as a pass.
    """

    name: str
    passed: bool
    measured: str
    limit: str
    measurable: bool = True


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    """Per-fold consistency of one frozen configuration on one sample.

    ``equity_curve`` and ``session_dates`` are the single expanding run's own
    series, carried so downstream views (the per-regime slices, a chart) can
    be computed without paying for the run again.
    """

    strategy: str
    folds: tuple[FoldOutcome, ...]
    full_sample: WindowStatistics
    equity_curve: tuple[float, ...]
    session_dates: tuple[SessionDate, ...]
    caveat: str = (
        "Walk-forward consistency, not out-of-sample evidence: the configuration "
        "was frozen after this history was known. Untouched data begins only "
        "after the declared holdout boundary."
    )

    @property
    def positive_fold_fraction(self) -> float:
        if not self.folds:
            return 0.0
        return sum(1 for fold in self.folds if fold.positive) / len(self.folds)

    @property
    def worst_fold(self) -> FoldOutcome:
        return min(self.folds, key=lambda fold: fold.statistics.sharpe)

    def research_gates(self) -> tuple[GateCheck, ...]:
        """The three gates a walk-forward can speak to, measured conservatively.

        Every name carries "in-sample analogue": the promotion gates these
        mirror are defined on *out-of-sample* data, which a burned sample
        cannot supply, and a gate name quoted into a registry without that
        qualifier would claim more than was measured. The fold gate further
        refuses to decide below ``_MIN_GATE_FOLDS`` folds.
        """
        fold_gate_measurable = len(self.folds) >= _MIN_GATE_FOLDS
        return (
            GateCheck(
                name=(
                    "aggregate net Sharpe at least 0.50 "
                    "(in-sample analogue of the out-of-sample gate)"
                ),
                passed=self.full_sample.sharpe >= _GATE_MIN_SHARPE,
                measured=f"{self.full_sample.sharpe:.2f}",
                limit=f">= {_GATE_MIN_SHARPE:.2f}",
            ),
            GateCheck(
                name=(
                    "at least 60% positive annual folds "
                    "(in-sample analogue of the out-of-sample gate)"
                ),
                passed=(
                    fold_gate_measurable and self.positive_fold_fraction >= _GATE_MIN_POSITIVE_FOLDS
                ),
                measured=(
                    f"{self.positive_fold_fraction:.0%}"
                    if fold_gate_measurable
                    else f"only {len(self.folds)} fold(s)"
                ),
                limit=f">= {_GATE_MIN_POSITIVE_FOLDS:.0%} across >= {_MIN_GATE_FOLDS} folds",
                measurable=fold_gate_measurable,
            ),
            GateCheck(
                name=(
                    "max drawdown no worse than 20% (in-sample analogue of the out-of-sample gate)"
                ),
                passed=self.full_sample.max_drawdown >= _GATE_WORST_DRAWDOWN,
                measured=f"{self.full_sample.max_drawdown:.2%}",
                limit=f">= {_GATE_WORST_DRAWDOWN:.0%}",
            ),
        )


def _fold_boundaries(dates: Sequence[SessionDate]) -> list[tuple[str, int, int]]:
    """``(label, start_index, end_index)`` per calendar year, half-open on start.

    ``start_index`` points at the *last session of the prior fold* so that the
    fold's first return is its own first session's move; a fold that began at
    its own first session would silently drop one session's return between
    every pair of folds. A trailing stub shorter than ``_MIN_FOLD_SESSIONS``
    is merged into the preceding fold and the label says so.
    """
    boundaries: list[tuple[str, int, int]] = []
    year = dates[0].year
    start = 0
    for index, date in enumerate(dates):
        if date.year != year:
            boundaries.append((str(year), start, index - 1))
            year = date.year
            start = index - 1
    boundaries.append((str(year), start, len(dates) - 1))

    merged: list[tuple[str, int, int]] = []
    for label, begin, end in boundaries:
        if merged and (end - begin) < _MIN_FOLD_SESSIONS:
            prior_label, prior_begin, _prior_end = merged[-1]
            merged[-1] = (f"{prior_label}+{label}", prior_begin, end)
        else:
            merged.append((label, begin, end))
    # A short *first* fold has no predecessor to merge into; fold it forward.
    if len(merged) > 1:
        first_label, first_begin, first_end = merged[0]
        if (first_end - first_begin) < _MIN_FOLD_SESSIONS:
            next_label, _next_begin, next_end = merged[1]
            merged[:2] = [(f"{first_label}+{next_label}", first_begin, next_end)]
    return merged


def walk_forward(
    strategy: StrategyConfig,
    panel: MarketPanel,
    universe: Universe,
    *,
    initial_cash: Decimal = Decimal("100000"),
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
) -> WalkForwardResult:
    """Run the expanding walk-forward and slice it into calendar folds.

    Raises:
        DataIntegrityError: if the run produces fewer than two foldable
            sessions (propagated from the runner or the statistics window).
    """
    result = run_backtest(
        strategy,
        panel,
        universe,
        initial_cash=initial_cash,
        actions_by_session=actions_by_session,
    )
    equity = result.equity_curve
    dates = result.session_dates

    fills_by_index = [len(record.fills) for record in result.sessions]
    folds: list[FoldOutcome] = []
    for label, begin, end in _fold_boundaries(dates):
        statistics = window_statistics(
            equity[begin : end + 1],
            dates[begin : end + 1],
            # The fold's trades are the fills that settled inside it; the
            # boundary session belongs to the prior fold, so it is excluded.
            trades=sum(fills_by_index[begin + 1 : end + 1]),
            win_rate=0.0,
            expectancy_r=0.0,
        )
        folds.append(FoldOutcome(label=label, statistics=statistics))

    full = window_statistics(
        equity,
        dates,
        trades=result.total_fills,
        win_rate=0.0,
        expectancy_r=0.0,
    )
    return WalkForwardResult(
        strategy=f"{strategy.name} v{strategy.version}",
        folds=tuple(folds),
        full_sample=full,
        equity_curve=tuple(float(value) for value in equity),
        session_dates=tuple(dates),
    )


# ---------------------------------------------------------------------------
# Locked holdout
# ---------------------------------------------------------------------------

#: Relative directory that holds every holdout declaration, newest wins.
#: ``outputs/holdouts`` rather than ``outputs/validated`` because the latter
#: is masked by a read-only placeholder in some environments (see
#: :attr:`amat.paths.RepositoryLayout.outputs_holdouts`).
_HOLDOUT_ROOT = Path("outputs/holdouts")
_HOLDOUT_PREFIX = "holdout-declaration-"
_DECLARATION_FILE = "declaration.json"


def default_holdout_root(start: Path | None = None) -> Path:
    """The repository root owning ``outputs/holdouts``, found by marker walk-up.

    A fail-closed guarantee must not depend on the shell's working directory:
    resolved cwd-relative, a command invoked from a subdirectory would find no
    declaration, report none exists, and proceed unguarded. This walks from
    ``start`` (default: the current working directory) to the nearest ancestor
    holding a ``pyproject.toml`` and anchors there, failing loud otherwise.

    Raises:
        DataIntegrityError: when no ancestor carries a repository marker.
    """
    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    message = (
        f"no repository root (pyproject.toml) found at or above {origin}; "
        "the holdout declaration cannot be located reliably from here"
    )
    raise DataIntegrityError(message)


@dataclass(frozen=True, slots=True)
class HoldoutDeclaration:
    """The frozen boundary between burned history and untouched future data."""

    declared_at: dt.datetime
    boundary_session: SessionDate
    snapshot_id: str
    rationale: str
    history_burned: bool = True
    """Whether full-sample results were inspected before this declaration.

    True for every declaration this repository can currently make: the v1-v5
    studies measured the whole sample, so no pre-boundary window is honestly
    out-of-sample. Recording it spares a future reader the archaeology.
    """

    def to_json(self) -> str:
        payload = {
            "declared_at": self.declared_at.isoformat(),
            "boundary_session": self.boundary_session.isoformat(),
            "snapshot_id": self.snapshot_id,
            "rationale": self.rationale,
            "history_burned": self.history_burned,
        }
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def declare_holdout(
    repository_root: Path,
    *,
    boundary_session: SessionDate,
    snapshot_id: str,
    rationale: str,
    declared_at: dt.datetime,
) -> Path:
    """Write an immutable holdout declaration and return its directory.

    Raises:
        ImmutableArtifactError: if a declaration directory for the same date
            already exists (append-only; a re-declaration is a new dated
            directory), or if ``boundary_session`` predates the most recent
            declared boundary -- a later declaration that moved the boundary
            *earlier* would silently unlock sessions the standing declaration
            already froze.
        ValueError: if ``declared_at`` is not timezone-aware UTC.
    """
    if declared_at.tzinfo is None or declared_at.utcoffset() != dt.timedelta(0):
        message = "declared_at must be a timezone-aware UTC datetime"
        raise ValueError(message)
    standing = load_holdout_declaration(repository_root)
    if standing is not None and boundary_session < standing.boundary_session:
        message = (
            f"boundary {boundary_session.isoformat()} predates the declared boundary "
            f"{standing.boundary_session.isoformat()}; moving a holdout boundary "
            "earlier would unlock sessions the standing declaration froze"
        )
        raise ImmutableArtifactError(message)
    declaration = HoldoutDeclaration(
        declared_at=declared_at,
        boundary_session=boundary_session,
        snapshot_id=snapshot_id,
        rationale=rationale,
    )
    directory = reserve_immutable_directory(
        repository_root,
        _HOLDOUT_ROOT / f"{_HOLDOUT_PREFIX}{declared_at:%Y-%m-%d}",
    )
    (directory / _DECLARATION_FILE).write_text(declaration.to_json(), encoding="utf-8")
    return directory


def load_holdout_declaration(repository_root: Path) -> HoldoutDeclaration | None:
    """The most recent declaration, or ``None`` when none has been made."""
    root = resolve_within(repository_root, _HOLDOUT_ROOT)
    if not root.is_dir():
        return None
    candidates = sorted(
        entry
        for entry in root.iterdir()
        if entry.is_dir() and entry.name.startswith(_HOLDOUT_PREFIX)
    )
    if not candidates:
        return None
    payload = json.loads((candidates[-1] / _DECLARATION_FILE).read_text(encoding="utf-8"))
    return HoldoutDeclaration(
        declared_at=dt.datetime.fromisoformat(payload["declared_at"]),
        boundary_session=dt.date.fromisoformat(payload["boundary_session"]),
        snapshot_id=payload["snapshot_id"],
        rationale=payload["rationale"],
        history_burned=bool(payload["history_burned"]),
    )


def assert_respects_holdout(
    panel: MarketPanel,
    declaration: HoldoutDeclaration | None,
    *,
    access_reason: str | None = None,
) -> tuple[bool, str | None]:
    """Fail closed when a panel crosses the holdout boundary without a reason.

    Returns:
        ``(holdout_accessed, holdout_access_reason)`` for the run manifest.
        ``(False, None)`` when no declaration exists or the panel stays at or
        before the boundary.

    Raises:
        DataIntegrityError: if the panel extends past the boundary and no
            ``access_reason`` was supplied. Reading holdout data must be a
            recorded decision, never a default.
    """
    if declaration is None or panel.sessions[-1] <= declaration.boundary_session:
        return False, None
    if access_reason is None or not access_reason.strip():
        message = (
            f"panel extends to {panel.sessions[-1].isoformat()}, past the declared "
            f"holdout boundary {declaration.boundary_session.isoformat()} "
            f"({declaration.snapshot_id}). Pass an explicit access reason to read "
            "holdout data; the run manifest will record it."
        )
        raise DataIntegrityError(message)
    return True, access_reason.strip()
