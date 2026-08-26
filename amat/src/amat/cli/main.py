"""Command-line entry point.

Every subcommand is read-only with respect to remote state. Most are offline
outright; ``simulate`` additionally reads current bars through
:mod:`amat.data.alpaca`, which is restricted to one historical market-data host.
No subcommand can reach an account, and none exists that could.

Exit codes:

* ``0`` success
* ``1`` configuration, data, or accounting error
* ``2`` usage error, or a refused environment
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import os
import re
import sys
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from amat import __version__
from amat.config.loading import load_strategy_config
from amat.errors import AmatError, ConfigurationError
from amat.paths import RepositoryLayout
from amat.provenance import current_python_version, detect_code_revision

if TYPE_CHECKING:
    # Type-only. The runtime imports stay deferred inside each command because
    # they pull in numpy and scikit-learn, which `amat --version` should not pay.
    from collections.abc import Mapping

    import numpy as np

    from amat.backtest.panel import MarketPanel
    from amat.backtest.sleeves import SleeveEngine
    from amat.config.loading import ResolvedConfig
    from amat.config.models import CostConfig, ExecutionConfig, StrategyConfig
    from amat.config.switching import ResolvedSwitchingConfig, SwitchingStrategyConfig
    from amat.data.snapshot import SnapshotManifest
    from amat.domain.actions import CorporateAction
    from amat.domain.types import SessionDate
    from amat.metrics import PerformanceSummary
    from amat.reporting import PerformanceReport
    from amat.reporting.sleeves import SleeveReport
    from amat.validation.falsification import FalsificationResult, UniverseFalsification
    from amat.validation.walkforward import HoldoutDeclaration

#: One engine's finished study: what it was, and the three artifacts it produced.
#: The first element is the engine literal rather than a bare ``str`` so that a
#: renderer cannot be handed an engine name the study layer never produced.
type StudyRendering = tuple[
    "SleeveEngine", "PerformanceReport", "PerformanceSummary", tuple["SleeveReport", ...]
]

__all__ = ["build_parser", "main"]

_EXIT_OK = 0
_EXIT_ERROR = 1
_EXIT_REFUSED = 2

_MODE_VARIABLE = "AMAT_EXECUTION_MODE"
_LIVE_VARIABLE = "AMAT_LIVE_TRADING_ENABLED"
_PERMITTED_MODES = frozenset({"", "backtest"})
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})


def _refuse_non_backtest_environment() -> str | None:
    """Return a refusal message if the environment is not backtest-only.

    This is a defence-in-depth check, not a security boundary: the repository
    contains no execution path to enable. It exists so that a mis-set variable
    is caught loudly instead of being ignored.
    """
    mode = os.environ.get(_MODE_VARIABLE, "").strip().lower()
    if mode not in _PERMITTED_MODES:
        return (
            f"{_MODE_VARIABLE} is {mode!r}; this repository is backtest-only and "
            "refuses to run under any other execution mode."
        )
    live = os.environ.get(_LIVE_VARIABLE, "0").strip().lower()
    if live in _ENABLED_VALUES:
        return (
            f"{_LIVE_VARIABLE} is {live!r}; this repository has no live-trading path "
            "and refuses to run with that flag set."
        )
    return None


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="amat",
        description=(
            "AMAT backtesting platform. Inspection, backtesting, and paper "
            "simulation. It reads historical market data and cannot reach an account."
        ),
    )
    parser.add_argument("--version", action="version", version=f"amat {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate-config",
        help="Validate a strategy configuration against the v1 contract.",
    )
    validate.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")

    show = subparsers.add_parser(
        "show-config",
        help="Print a validated configuration as JSON, with its content digest.",
    )
    show.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")

    universe = subparsers.add_parser(
        "show-universe",
        help="Print the universe, cluster membership, and cluster gross caps.",
    )
    universe.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")

    layout = subparsers.add_parser(
        "show-layout",
        help="Print the expected repository layout and which directories exist.",
    )
    layout.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: current working directory).",
    )

    subparsers.add_parser(
        "show-environment",
        help="Print the interpreter and code revision recorded in run manifests.",
    )

    backtest = subparsers.add_parser(
        "backtest-synthetic",
        help=(
            "Run the engine on deterministic SYNTHETIC data. This verifies the "
            "engine end to end; its numbers are not a market result."
        ),
    )
    backtest.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    backtest.add_argument(
        "--sessions", type=int, default=1500, help="Synthetic sessions to generate."
    )
    backtest.add_argument("--seed", type=int, default=20260808, help="Reproducibility seed.")
    backtest.add_argument(
        "--trend-strength",
        type=float,
        default=0.0,
        help="Planted trend amplitude. 0 (default) is a driftless random walk.",
    )
    backtest.add_argument(
        "--initial-cash",
        type=Decimal,
        default=Decimal("100000"),
        help="Opening cash balance (default 100,000).",
    )
    backtest.add_argument(
        "--cost-ladder",
        action="store_true",
        help="Also run the mandatory cost-stress ladder from the configuration.",
    )
    backtest.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")

    report = subparsers.add_parser(
        "report",
        help=(
            "Full performance report with per-asset attribution and concentration "
            "gates, on SYNTHETIC data. Not a market result."
        ),
    )
    report.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    report.add_argument("--sessions", type=int, default=1200, help="Synthetic sessions.")
    report.add_argument("--seed", type=int, default=20260808, help="Reproducibility seed.")
    report.add_argument(
        "--trend-strength", type=float, default=0.35, help="Planted trend amplitude."
    )
    report.add_argument(
        "--initial-cash",
        type=Decimal,
        default=Decimal("100000"),
        help="Opening cash balance (default 100,000).",
    )
    report.add_argument(
        "--engine",
        choices=("v1",),
        default="v1",
        help=(
            "Which runner(s) to report on (default: v1). v2 was rejected on "
            "2026-08-10 for failing the year-concentration gate; it stays "
            "selectable so the failed experiment remains reproducible, but it "
            "is no longer a candidate and is no longer reported by default."
        ),
    )
    report.add_argument(
        "--html", type=Path, default=None, help="Also write a self-contained HTML report."
    )
    report.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    quick = subparsers.add_parser(
        "backtest",
        help=(
            "Run a strategy version on real market data in one line: "
            "`amat backtest -v v1`. A thin front end over the study pipeline "
            "with defaults for everything else -- the newest snapshot under "
            "data/snapshots/, the version's checked-in configuration, and a "
            "dated PDF under outputs/reports/. Every guardrail, holdout "
            "check, and run record is identical to the underlying command."
        ),
    )
    quick.add_argument(
        "-v",
        "--strategy-version",
        dest="strategy_version",
        required=True,
        choices=("v1", "v2", "v3", "v4", "v5", "v6"),
        help=(
            "Strategy version: v1 frozen trend core; v2 refuses (rejected "
            "2026-08-10, runner removed -- the registry entry is the record); "
            "v3 = v1 conditioned by --scalars (without the file it is "
            "numerically identical to v1); v4 stance switching; v5 breadth "
            "universe (rejected, still measurable); v6 research overlay "
            "(static 6%% target + regime/liquidity gates; research only)."
        ),
    )
    quick.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "PDF report path. Default: outputs/reports/<version>-<YYYYMMDD>.pdf with today's date."
        ),
    )
    quick.add_argument(
        "--start",
        type=_month_start,
        default=None,
        metavar="YYYY-MM",
        help=(
            "First session (inclusive): a year+month like 2019-06 (resolved to "
            "its first day) or a full YYYY-MM-DD date."
        ),
    )
    quick.add_argument(
        "--end",
        type=_month_end,
        default=None,
        metavar="YYYY-MM",
        help=(
            "Last session (inclusive): a year+month like 2024-12 (resolved to "
            "its last day) or a full YYYY-MM-DD date."
        ),
    )
    quick.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="Snapshot directory. Default: the newest under data/snapshots/.",
    )
    quick.add_argument(
        "--initial-cash",
        type=Decimal,
        default=Decimal("100000"),
        help="Opening cash for the combined portfolio (default 100,000).",
    )
    quick.add_argument(
        "--no-sleeves",
        action="store_true",
        help="Run only the combined portfolio, skipping the per-asset sleeves.",
    )
    quick.add_argument(
        "--scalars",
        type=Path,
        default=None,
        help=(
            "v3 only: CSV of per-session weight scalars in [0,1], as produced "
            "by `newsdesk export-scalars`."
        ),
    )
    quick.add_argument(
        "--advisory",
        action="store_true",
        help="Append the labelled LLM advisory page to the PDF (v1/v3/v4/v5).",
    )
    _add_record_arguments(quick)
    _add_holdout_argument(quick)
    quick.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    _add_market_data_commands(subparsers)
    _add_validation_commands(subparsers)
    return parser


def _add_record_arguments(parser: argparse.ArgumentParser) -> None:
    """Run-record persistence options, shared by every measuring command."""
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("outputs/runs"),
        help="Directory that receives the run record (default outputs/runs).",
    )
    parser.add_argument(
        "--no-run-record",
        action="store_true",
        help=(
            "Skip writing the run record. A result without a record cannot be "
            "cited by the registry; use only for throwaway exploration."
        ),
    )


def _add_window_arguments(parser: argparse.ArgumentParser) -> None:
    """Snapshot selection and sample-window options."""
    parser.add_argument(
        "--snapshot", type=Path, required=True, help="Snapshot directory under data/snapshots/."
    )
    parser.add_argument(
        "--start", type=dt.date.fromisoformat, default=None, help="First session (inclusive)."
    )
    parser.add_argument(
        "--end", type=dt.date.fromisoformat, default=None, help="Last session (inclusive)."
    )
    parser.add_argument(
        "--initial-cash",
        type=Decimal,
        default=Decimal("100000"),
        help="Opening cash balance (default 100,000).",
    )
    _add_holdout_argument(parser)


def _add_holdout_argument(parser: argparse.ArgumentParser) -> None:
    """The holdout escape hatch, carried by every panel-measuring command.

    Every command that loads a panel enforces the declared boundary through
    ``_enforce_holdout``; this flag is the one sanctioned way past it, and the
    reason travels into the run manifest.
    """
    parser.add_argument(
        "--allow-holdout",
        metavar="REASON",
        default=None,
        help=(
            "Authorize reading sessions past the declared holdout boundary. The "
            "reason is recorded in the run manifest; without it a panel that "
            "crosses the boundary fails closed."
        ),
    )


def _parse_target_list(raw: str) -> tuple[float, ...]:
    """Comma-separated volatility targets, each strictly inside (0, 1]."""
    targets: list[float] = []
    for piece in raw.split(","):
        text = piece.strip()
        if not text:
            continue
        try:
            value = float(text)
        except ValueError as exc:
            message = f"targets must be numbers, got {text!r}"
            raise argparse.ArgumentTypeError(message) from exc
        if not 0.0 < value <= 1.0:
            message = f"each target must lie in (0, 1], got {value}"
            raise argparse.ArgumentTypeError(message)
        targets.append(value)
    if not targets:
        message = "at least one volatility target is required"
        raise argparse.ArgumentTypeError(message)
    return tuple(targets)


def _add_validation_commands(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the walk-forward, holdout, Monte Carlo, and experiment commands."""
    walkforward = subparsers.add_parser(
        "walkforward",
        help=(
            "Expanding walk-forward on an immutable snapshot: one full-history run "
            "of the frozen core, sliced into calendar folds, with per-regime "
            "performance and the research gates a walk-forward can measure. "
            "Consistency evidence, NOT out-of-sample proof."
        ),
    )
    walkforward.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    _add_window_arguments(walkforward)
    _add_record_arguments(walkforward)
    walkforward.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    holdout = subparsers.add_parser(
        "declare-holdout",
        help=(
            "Freeze the locked-holdout boundary at a snapshot's last session. The "
            "declaration is immutable and append-only; sessions after the boundary "
            "may then be read only with a recorded reason."
        ),
    )
    holdout.add_argument(
        "--snapshot", type=Path, required=True, help="Snapshot directory under data/snapshots/."
    )
    holdout.add_argument(
        "--reason",
        required=True,
        help="Why the boundary is being declared now; recorded verbatim.",
    )

    montecarlo = subparsers.add_parser(
        "montecarlo",
        help=(
            "Stationary block-bootstrap Monte Carlo: resample the snapshot's own "
            "joint session moves into N alternative markets, run the frozen "
            "strategy on each, and place the observed result inside the resampled "
            "distribution. A robustness measurement, not a forecast."
        ),
    )
    montecarlo.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    _add_window_arguments(montecarlo)
    montecarlo.add_argument(
        "--paths", type=int, default=200, help="Resampled paths to run (default 200)."
    )
    montecarlo.add_argument(
        "--seed",
        type=int,
        default=20260812,
        help="Base seed; path i uses seed+i, so any path can be regenerated alone.",
    )
    montecarlo.add_argument(
        "--block-sessions",
        type=int,
        default=21,
        help="Mean bootstrap block length in sessions (default 21, one month).",
    )
    montecarlo.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Worker processes (default: all cores but two). Results are identical at any count.",
    )
    montecarlo.add_argument(
        "--pdf", type=Path, default=None, help="Also write the three-page Monte Carlo PDF."
    )
    _add_record_arguments(montecarlo)
    montecarlo.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    _add_projection_command(subparsers)
    _add_stress_command(subparsers)
    _add_heavytail_command(subparsers)
    _add_experiment_commands(subparsers)


def _add_stress_command(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the full robustness stress battery command."""
    stress = subparsers.add_parser(
        "stress",
        help=(
            "The full robustness battery in one recorded run: configured cost "
            "ladder, execution delays (lag 2/3), deterministic 5%%/10%% "
            "missed-fill injection, session-close fills, leave-one-out over "
            "every universe symbol (the measurable survivorship bound), "
            "top-5/10 trade and best-year removal restatements, the "
            "signal-free allocator control, and SPY / 60-40 correlations. "
            "Surviving stress is necessary, never sufficient."
        ),
    )
    stress.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    _add_window_arguments(stress)
    _add_record_arguments(stress)
    stress.add_argument("--json", action="store_true", help="Emit JSON instead of text.")


def _add_heavytail_command(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the heavy-tailed regime-switching synthetic stress."""
    heavytail = subparsers.add_parser(
        "stress-heavytail",
        help=(
            "SYNTHETIC stress: generate markets with Pareto-distributed jumps "
            "and a four-state regime-switching process, calibrated per symbol "
            "to the snapshot's own price levels, volatilities, betas and "
            "correlation, then run the strategy on them. Answers what the "
            "history-resampling batteries cannot: how the strategy behaves in "
            "moves larger than anything the sample contains. Not a backtest "
            "result, not a forecast."
        ),
    )
    heavytail.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    _add_window_arguments(heavytail)
    heavytail.add_argument(
        "--scenarios",
        type=int,
        default=24,
        help="Number of independent generated markets (default 24).",
    )
    heavytail.add_argument(
        "--sessions",
        type=int,
        default=756,
        help=(
            "Sessions per generated market (default 756, three years). Must "
            "exceed the strategy's 252-session warm-up."
        ),
    )
    heavytail.add_argument(
        "--tail-index",
        type=float,
        default=2.5,
        metavar="ALPHA",
        help=(
            "Pareto tail index of the jump magnitudes (default 2.5). Lower is "
            "wilder; below 2.0 the process has infinite theoretical variance."
        ),
    )
    heavytail.add_argument(
        "--drift-annual",
        type=float,
        default=0.0,
        help=(
            "Baseline annualized drift added to every regime (default 0.0, so "
            "the strategy cannot inherit the sample's bull market)."
        ),
    )
    heavytail.add_argument(
        "--seed",
        type=int,
        default=20260815,
        help="Base seed; scenario i uses seed+i (default 20260815).",
    )
    _add_record_arguments(heavytail)
    heavytail.add_argument("--json", action="store_true", help="Emit JSON instead of text.")


def _add_projection_command(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the forward performance-metric projection command."""
    project = subparsers.add_parser(
        "project",
        help=(
            "Forward performance-metric projection: run the strategy on the "
            "snapshot, then stationary-bootstrap its OWN net session returns "
            "out to the requested horizons and report the outcome "
            "distributions from a chosen funding. Every number is a "
            "within-resampling frequency, never a forecast."
        ),
    )
    project.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    _add_window_arguments(project)
    project.add_argument(
        "--funding",
        type=Decimal,
        default=None,
        help=(
            "Starting funds every projected path compounds from (default: "
            "--initial-cash, i.e. the source run's own funding)."
        ),
    )
    project.add_argument(
        "--horizon-years",
        type=float,
        nargs="+",
        default=(1.0, 2.0, 5.0, 10.0),
        metavar="YEARS",
        help="Forward horizons in years (default: 1 2 5 10).",
    )
    project.add_argument(
        "--paths",
        type=int,
        default=2000,
        help="Resampled paths (default 2000); percentile resolution is 1/paths.",
    )
    project.add_argument(
        "--seed",
        type=int,
        default=20260815,
        help="Base seed; path i uses seed+i, so any path can be regenerated alone.",
    )
    project.add_argument(
        "--block-sessions",
        type=int,
        default=21,
        help="Mean bootstrap block length in sessions (default 21, one month).",
    )
    project.add_argument(
        "--pdf", type=Path, default=None, help="Also write the two-page projection PDF."
    )
    _add_record_arguments(project)
    project.add_argument("--json", action="store_true", help="Emit JSON instead of text.")


def _add_experiment_commands(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the regime, target-volatility, and exit-rules experiment commands."""
    regime = subparsers.add_parser(
        "regime-experiment",
        help=(
            "REGISTERED EXPERIMENT: causal volatility-state conditioning (halve "
            "size while reference realized volatility exceeds its own expanding "
            "quantile), judged by the falsification battery. Two regime designs "
            "already died there; register before running, record the verdict "
            "either way."
        ),
    )
    regime.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    _add_window_arguments(regime)
    regime.add_argument(
        "--reference", default="SPY", help="Reference symbol for the volatility state."
    )
    regime.add_argument(
        "--vol-window", type=int, default=21, help="Trailing realized-volatility window."
    )
    regime.add_argument(
        "--quantile",
        type=float,
        default=0.80,
        help="Expanding quantile above which volatility counts as elevated.",
    )
    regime.add_argument(
        "--reduced-scalar",
        type=float,
        default=0.50,
        help="Sizing multiplier applied during elevated-volatility sessions.",
    )
    regime.add_argument(
        "--seed", type=int, default=0, help="Seed for the matched worthless controls."
    )
    _add_record_arguments(regime)
    regime.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    sweep = subparsers.add_parser(
        "sweep-target-vol",
        help=(
            "REGISTERED EXPERIMENT: rank nearby volatility targets by "
            "training-window fold consistency (split fixed in advance), then "
            "report the validation window. A non-frozen winner earns a registry "
            "entry and the full protocol -- never an in-place change."
        ),
    )
    sweep.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    _add_window_arguments(sweep)
    sweep.add_argument(
        "--train-end",
        type=dt.date.fromisoformat,
        required=True,
        help="Last training session (inclusive). Choose before looking at results; never move it.",
    )
    sweep.add_argument(
        "--targets",
        type=_parse_target_list,
        default=(0.05, 0.06, 0.10, 0.12),
        help=(
            "Comma-separated annualized volatility targets, e.g. '0.06,0.10,0.12'. "
            "The configuration's own target is always included as the baseline."
        ),
    )
    _add_record_arguments(sweep)
    sweep.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    exits = subparsers.add_parser(
        "sweep-exit-rules",
        help=(
            "REGISTERED EXPERIMENT: does any stop-loss/take-profit overlay beat "
            "the frozen no-exit core? Full grid of {none, stops} x {none, "
            "profits} on the adjusted entry basis with a fixed re-entry "
            "embargo, ranked on training folds only (split fixed in advance). "
            "A surviving winner earns a registry entry -- never an in-place "
            "change."
        ),
    )
    exits.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    _add_window_arguments(exits)
    exits.add_argument(
        "--train-end",
        type=dt.date.fromisoformat,
        required=True,
        help="Last training session (inclusive). Choose before looking at results; never move it.",
    )
    exits.add_argument(
        "--stop-losses",
        type=_parse_target_list,
        default=(0.05, 0.10, 0.15),
        help=(
            "Comma-separated stop-loss fractions from the adjusted entry basis, "
            "e.g. '0.05,0.10,0.15'. 'No stop' is always included as a row."
        ),
    )
    exits.add_argument(
        "--take-profits",
        type=_parse_target_list,
        default=(0.10, 0.20),
        help=(
            "Comma-separated take-profit fractions from the adjusted entry "
            "basis, e.g. '0.10,0.20'. 'No take-profit' is always included."
        ),
    )
    exits.add_argument(
        "--embargo-sessions",
        type=int,
        default=21,
        help=(
            "Re-entry embargo after a breach, in decisions (default 21 -- one "
            "month, the shortest signal horizon; fixed at registration, not "
            "part of the search grid)."
        ),
    )
    exits.add_argument(
        "--random-control",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Also measure N seeded UNIFORM-RANDOM stop/take-profit pairs "
            "(stop 3-30%%, take-profit 5-60%%, same embargo and split) beside "
            "the no-exit core. A robustness control on the grid's "
            "conclusion, not a search: draws and seed are recorded, and no "
            "random rule can be promoted."
        ),
    )
    exits.add_argument(
        "--control-seed",
        type=int,
        default=20260815,
        help="Seed for --random-control draws (default 20260815).",
    )
    _add_record_arguments(exits)
    exits.add_argument("--json", action="store_true", help="Emit JSON instead of text.")


def _add_market_data_commands(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the commands that read an immutable snapshot or current bars."""
    study = subparsers.add_parser(
        "study",
        help=(
            "Run the strategy on an immutable market-data snapshot: the combined "
            "portfolio plus one independently funded sleeve per asset, on the "
            "v1-frozen engine."
        ),
    )
    study.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    study.add_argument(
        "--engine",
        choices=("v1",),
        default="v1",
        help=(
            "Which runner to study (default, and currently only: v1). The v2 "
            "overlay was rejected on 2026-08-10 and deleted; its record is kept "
            "in docs/experiment-registry.md."
        ),
    )
    study.add_argument(
        "--snapshot", type=Path, required=True, help="Snapshot directory under data/snapshots/."
    )
    study.add_argument(
        "--initial-cash",
        type=Decimal,
        default=Decimal("100000"),
        help=(
            "Opening cash for the combined portfolio (default 100,000). The "
            "allocator spreads it across assets; nothing is pre-assigned per symbol."
        ),
    )
    study.add_argument(
        "--sleeve-equity",
        type=Decimal,
        default=Decimal("100000"),
        help="Opening equity for each per-asset sleeve (default 100,000).",
    )
    study.add_argument(
        "--start", type=dt.date.fromisoformat, default=None, help="First session (inclusive)."
    )
    study.add_argument(
        "--end", type=dt.date.fromisoformat, default=None, help="Last session (inclusive)."
    )
    study.add_argument(
        "--no-sleeves",
        action="store_true",
        help="Run only the combined portfolio, skipping the per-asset sleeves.",
    )
    study.add_argument(
        "--html", type=Path, default=None, help="Also write a self-contained HTML report."
    )
    study.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help=(
            "Also write a full PDF report: the all-asset comparison chart, per-asset "
            "pages with every trade marked and its recorded rationale, individual and "
            "cumulative statistics, and episode diagnostics."
        ),
    )
    study.add_argument(
        "--scalars",
        type=Path,
        default=None,
        help=(
            "CSV of per-session weight scalars in [0,1] ('session,scalar' rows), as "
            "produced by `newsdesk export-scalars`. This is how strategy v3 conditions "
            "the v1 core on point-in-time news. Applied to the combined portfolio only; "
            "sleeves stay unconditioned diagnostics."
        ),
    )
    study.add_argument(
        "--advisory",
        action="store_true",
        help=(
            "Append an LLM-generated advisory commentary page to the PDF (requires "
            "--pdf, the GEMINI_API_KEY environment variable, and network access). The "
            "page is clearly labelled; nothing in the run derives from it."
        ),
    )
    study.add_argument(
        "--falsify-universe",
        type=Path,
        default=None,
        metavar="BASELINE_CONFIG",
        help=(
            "Also run the universe falsification battery: the candidate against "
            "BASELINE_CONFIG (identical non-universe core required) and against "
            "leave-one-out variants over every added symbol, all on identical "
            "data, costs, and funding. A candidate a smaller universe matches "
            "is not a candidate."
        ),
    )
    _add_record_arguments(study)
    _add_holdout_argument(study)
    study.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    study_v4 = subparsers.add_parser(
        "study-v4",
        help=(
            "Run the v4 stance-switching strategy on an immutable snapshot: the v1 "
            "trend core and a defensive stance of the same core, blended per session "
            "by the configured breadth rule."
        ),
    )
    study_v4.add_argument("config", type=Path, help="Path to a TOML v4 switching configuration.")
    study_v4.add_argument(
        "--snapshot", type=Path, required=True, help="Snapshot directory under data/snapshots/."
    )
    study_v4.add_argument(
        "--initial-cash",
        type=Decimal,
        default=Decimal("100000"),
        help="Opening cash balance (default 100,000, matching the v1 study funding).",
    )
    study_v4.add_argument(
        "--start", type=dt.date.fromisoformat, default=None, help="First session (inclusive)."
    )
    study_v4.add_argument(
        "--end", type=dt.date.fromisoformat, default=None, help="Last session (inclusive)."
    )
    study_v4.add_argument(
        "--falsify",
        action="store_true",
        help=(
            "Also run the falsification controls: shuffled lambda, turnover-matched "
            "random lambda, static-mean lambda, and defensive-only. A v4 result that "
            "does not beat every control is not a candidate."
        ),
    )
    study_v4.add_argument("--pdf", type=Path, default=None, help="Also write the full PDF report.")
    study_v4.add_argument(
        "--advisory",
        action="store_true",
        help="Append the labelled Gemini advisory page to the PDF (requires --pdf).",
    )
    _add_record_arguments(study_v4)
    _add_holdout_argument(study_v4)
    study_v4.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    simulate = subparsers.add_parser(
        "simulate",
        help=(
            "Step the simulated paper account: refresh market data, replay the "
            "strategy through the latest completed session, and journal the result. "
            "No account is connected and nothing is submitted anywhere."
        ),
    )
    simulate.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    simulate.add_argument(
        "--snapshot", type=Path, required=True, help="Snapshot directory under data/snapshots/."
    )
    simulate.add_argument(
        "--equity", type=Decimal, default=Decimal("100000"), help="Opening paper equity."
    )
    simulate.add_argument(
        "--offline",
        action="store_true",
        help="Replay the snapshot only; do not fetch sessions observed since it was taken.",
    )
    simulate.add_argument(
        "--journal",
        type=Path,
        default=Path("outputs/paper"),
        help="Directory for the JSON-lines journal.",
    )
    simulate.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    plan = subparsers.add_parser(
        "plan",
        help=(
            "Emit the execution plan formed from the latest completed session: "
            "target weights and the trades that would reach them. It describes "
            "intent only. Nothing is submitted and no account is contacted."
        ),
    )
    plan.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    plan.add_argument(
        "--snapshot", type=Path, required=True, help="Snapshot directory under data/snapshots/."
    )
    plan.add_argument(
        "--engine",
        choices=("v1-frozen",),
        default="v1-frozen",
        help=(
            "Which engine forms the decision (default, and currently only: "
            "v1-frozen). The v2 overlay was rejected on 2026-08-10 and deleted; "
            "its record is kept in docs/experiment-registry.md."
        ),
    )
    plan.add_argument(
        "--equity",
        type=Decimal,
        required=True,
        help="Account equity the plan is sized against. Supplied, never fetched.",
    )
    plan.add_argument(
        "--positions",
        type=Path,
        help=(
            'JSON file mapping symbol to share count, e.g. {"SPY": "120.5"}. '
            "Omit only when the account is genuinely flat."
        ),
    )
    plan.add_argument(
        "--offline",
        action="store_true",
        help="Use the snapshot as-is; do not fetch sessions observed since it was taken.",
    )
    plan.add_argument(
        "--news-assets",
        type=Path,
        help=(
            "Short-lived JSON artifact from `newsdesk discover-assets`. Valid positive "
            "candidates are added to a separate 10%%-gross-capped cluster, then traded "
            "only when the normal trend/risk engine assigns them weight."
        ),
    )
    plan.add_argument("--out", type=Path, help="Write the plan JSON to this path.")
    plan.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    _add_scan_command(subparsers)


def _add_scan_command(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the point-in-time scanner.

    Its own function because the scanner takes six arguments and pushed the
    market-data registrar past the statement limit -- a signal that it is a
    separate concern, not that the limit is wrong.
    """
    scan = subparsers.add_parser(
        "scan",
        help=(
            "Point-in-time asset scan: rank every symbol by the strategy's own "
            "multi-horizon, volatility-normalized trend as of one session, with the "
            "per-horizon breakdown that produced the rank. Uses the same features the "
            "allocator consumes, computed from the panel prefix only, so a scan dated "
            "in the past is what the strategy actually saw then. A rank measures trend "
            "strength -- it is not a forecast and not a valuation."
        ),
    )
    scan.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    scan.add_argument(
        "--snapshot",
        type=Path,
        help="Snapshot directory. Default: the newest under data/snapshots/.",
    )
    scan.add_argument(
        "--session",
        type=dt.date.fromisoformat,
        help="Decision session (YYYY-MM-DD). Default: the panel's last session.",
    )
    scan.add_argument(
        "--symbols",
        help=(
            "Comma-separated symbols to scan instead of the configuration's universe. "
            "Scanning wider than the strategy trades is the point of a scanner."
        ),
    )
    scan.add_argument(
        "--all-snapshot-symbols",
        action="store_true",
        help="Scan every symbol the snapshot carries, not just the configured universe.",
    )
    scan.add_argument(
        "--top",
        type=int,
        help="Show only the strongest N. Default: every asset with a live trend.",
    )
    scan.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")


def _command_scan(args: argparse.Namespace) -> int:
    """Rank the universe as of one session, showing why each asset ranks."""
    from amat.data.snapshot import load_manifest, load_panel  # noqa: PLC0415
    from amat.scanner import scan_universe  # noqa: PLC0415

    resolved = _load_any_strategy_config(args.config)
    strategy = resolved.strategy
    snapshot = args.snapshot or _newest_snapshot()

    if args.symbols:
        symbols = [token.strip().upper() for token in args.symbols.split(",") if token.strip()]
    elif args.all_snapshot_symbols:
        symbols = list(load_manifest(snapshot).symbols)
    else:
        symbols = [str(symbol) for symbol in strategy.universe.symbols]

    panel, manifest = load_panel(snapshot, symbols)
    result = scan_universe(strategy, panel, session=args.session)

    if args.json:
        payload = result.as_dict()
        payload["snapshot_id"] = manifest.snapshot_id
        payload["config_sha256"] = resolved.content_sha256
        print(json.dumps(payload, indent=2, sort_keys=True))
        return _EXIT_OK

    horizons = " ".join(f"z{horizon:<7}" for horizon in result.horizons)
    print(f"Scan {result.session.isoformat()}  snapshot {manifest.snapshot_id}")
    print(f"  strategy    {strategy.name} v{strategy.version}")
    print(f"  scanned     {len(result.assets)} symbols")
    deadband = strategy.trend.deadband_abs
    print(f"  with trend  {len(result.eligible)}  (dead-band |score| < {deadband})")
    if result.selection_enabled:
        rule = f"top {result.max_positions}" if result.max_positions else "all eligible"
        if result.min_horizon_agreement:
            rule += (
                f", >= {result.min_horizon_agreement} of {len(result.horizons)} horizons positive"
            )
        print(f"  selection   {rule} -> {len(result.selected)} held")
    else:
        print("  selection   off (every asset with a live trend is sized)")

    rows = result.eligible if args.top is None else result.eligible[: args.top]
    if not rows:
        print("\nNo asset has a trend outside the dead-band at this session: 100% cash.")
        return _EXIT_OK

    print(f"\n  {'#':>3} {'SYM':<6} {'score':>7} {horizons} {'+h':>3} {'vol':>7}  {'':1} cluster")
    for asset in rows:
        zs = " ".join(f"{value:>+8.3f}" for value in asset.horizon_z)
        mark = "*" if asset.selected else " "
        print(
            f"  {asset.rank:>3} {asset.symbol:<6} {asset.score:>7.3f} {zs} "
            f"{asset.horizons_positive:>3} {asset.annualized_volatility:>6.1%}  "
            f"{mark} {asset.cluster}"
        )
    if result.selection_enabled:
        print("\n  * = would be held. Others have a trend but lost the cross-sectional ranking.")
    print(
        "\n  A rank orders trend strength on data through this session only. It is not a "
        "\n  forecast; the frozen core's own history wins 32.7% of round trips."
    )
    return _EXIT_OK


def _load_positions(path: Path | None) -> dict[str, Decimal]:
    """Read an account's share counts from a JSON file.

    Fails closed on anything it cannot interpret. A position file that half-parses
    is more dangerous than one that does not parse at all: the missing half looks
    exactly like a flat position, and a plan built on it would emit a buy for
    something already held.
    """
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        message = f"could not read positions from {path}: {exc}"
        raise ConfigurationError(message) from exc
    if not isinstance(payload, dict):
        message = f"{path}: expected a JSON object mapping symbol to share count"
        raise ConfigurationError(message)

    positions: dict[str, Decimal] = {}
    for symbol, raw in payload.items():
        if not isinstance(symbol, str) or not symbol.strip():
            message = f"{path}: every key must be a non-empty symbol"
            raise ConfigurationError(message)
        try:
            quantity = Decimal(str(raw))
        except (ArithmeticError, ValueError) as exc:
            message = f"{path}: {symbol} has a non-numeric share count {raw!r}"
            raise ConfigurationError(message) from exc
        if not quantity.is_finite():
            message = f"{path}: {symbol} has a non-finite share count"
            raise ConfigurationError(message)
        if quantity < 0:
            message = (
                f"{path}: {symbol} holds {quantity}, but this strategy is long-or-cash "
                "and cannot plan against a short position"
            )
            raise ConfigurationError(message)
        positions[symbol.strip().upper()] = quantity
    return positions


def _command_validate_config(path: Path) -> int:
    """Validate any checked-in strategy configuration, plain or v6 superset.

    These three inspection commands used the plain loader directly, which meant
    they could not validate, print, or list the universe of *any* v6 file: the
    v6 research schema adds ``[asset_regime]``, ``[liquidity]`` and
    ``[risk_learning]``, and the plain model rejects unknown tables by design.
    "Validate this configuration" answering "invalid" for a configuration the
    engine runs every day is a defect in the checker, not in the file, so they
    now resolve through the same v6-aware helper the measuring commands use.
    The schema that matched is printed, because "valid" without saying *against
    what* is the ambiguity that hid this in the first place.
    """
    resolved, extra_sections = _resolve_config_for_inspection(path)
    schema = _schema_label(extra_sections)
    detail = f" (+{', '.join(extra_sections)})" if extra_sections else ""
    print(
        f"Configuration valid: {resolved.strategy.name} "
        f"v{resolved.strategy.version} "
        f"({len(resolved.strategy.universe.symbols)} symbols, "
        f"sha256 {resolved.content_sha256[:12]}) "
        f"[{schema}{detail}]"
    )
    return _EXIT_OK


def _command_show_config(path: Path) -> int:
    resolved, extra_sections = _resolve_config_for_inspection(path)
    payload: dict[str, object] = {
        "source": str(resolved.source_path) if resolved.source_path else None,
        "content_sha256": resolved.content_sha256,
        "schema": _schema_key(extra_sections),
        "strategy": resolved.strategy.model_dump(mode="json"),
    }
    if extra_sections == _V6_EXTRA_SECTIONS:
        # Printing only the strategy core for a v6 file would silently drop the
        # three tables that make it v6, so the superset sections are emitted
        # alongside it rather than being quietly summarized away.
        from amat.config.v6 import load_v6_config  # noqa: PLC0415

        v6_config = load_v6_config(path).config
        payload["v6"] = {
            section: getattr(v6_config, section).model_dump(mode="json")
            for section in extra_sections
        }
    elif extra_sections == _V4_EXTRA_SECTIONS:
        # ``strategy`` above is v4's [trend] core; the blend rule and the
        # defensive stance are the rest of the strategy and must appear too.
        from amat.config.switching import load_switching_config  # noqa: PLC0415

        v4_config = load_switching_config(path).strategy
        payload["v4"] = {
            "switching": v4_config.switching.model_dump(mode="json"),
            "defensive": v4_config.defensive.model_dump(mode="json"),
        }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return _EXIT_OK


def _schema_key(extra_sections: tuple[str, ...]) -> str:
    """Short schema identifier for machine-readable output."""
    if extra_sections == _V6_EXTRA_SECTIONS:
        return "v6"
    if extra_sections == _V4_EXTRA_SECTIONS:
        return "v4"
    return "v1"


def _schema_label(extra_sections: tuple[str, ...]) -> str:
    """Human-readable schema name for the validation line."""
    return {
        "v6": "v6 research schema",
        "v4": "v4 switching schema",
        "v1": "v1 strategy schema",
    }[_schema_key(extra_sections)]


#: Superset tables per schema, in the order each loader declares them. Kept
#: beside the inspection commands because they are the only thing that
#: distinguishes these files from a plain v1 file at the schema level.
_V6_EXTRA_SECTIONS: tuple[str, ...] = ("asset_regime", "liquidity", "risk_learning")
_V4_EXTRA_SECTIONS: tuple[str, ...] = ("trend", "switching", "defensive")


def _resolve_config_for_inspection(path: Path) -> tuple[ResolvedConfig, tuple[str, ...]]:
    """Resolve any checked-in configuration and report its extra sections.

    Three schemas exist in ``configs/strategy/``: the plain v1 model, the v6
    superset, and the v4 switching model whose strategy core lives under
    ``[trend]``. All three are files the engine runs, so all three must be
    inspectable; only the plain loader was wired in before.

    Ordering matters. The plain loader is tried first so a genuinely malformed
    v1-style file still fails with its own precise error rather than a confusing
    report from a schema it was never meant to satisfy. The v6 and v4 fallbacks
    are attempted only after the plain loader rejects unknown tables, and if
    neither matches, the plain loader's error is what surfaces.
    """
    try:
        plain = load_strategy_config(path)
    except ConfigurationError as plain_error:
        if "Extra inputs are not permitted" not in str(plain_error):
            raise
        from amat.config.loading import ResolvedConfig  # noqa: PLC0415
        from amat.config.switching import load_switching_config  # noqa: PLC0415
        from amat.config.v6 import load_v6_config  # noqa: PLC0415

        try:
            v6 = load_v6_config(path)
        except Exception:  # noqa: S110 - not v6; the v4 schema is tried next
            pass
        else:
            return (
                ResolvedConfig(
                    strategy=v6.config.strategy,
                    source_path=v6.source_path,
                    content_sha256=v6.content_sha256,
                ),
                _V6_EXTRA_SECTIONS,
            )

        try:
            v4 = load_switching_config(path)
        except Exception:  # surface the PLAIN loader's error, not the fallback's
            raise plain_error from None
        return (
            ResolvedConfig(
                strategy=v4.strategy.trend,
                source_path=v4.source_path,
                content_sha256=v4.content_sha256,
            ),
            _V4_EXTRA_SECTIONS,
        )
    return plain, ()


def _command_show_universe(path: Path) -> int:
    resolved, _extra = _resolve_config_for_inspection(path)
    universe = resolved.strategy.to_universe()
    print(f"Universe: {len(universe)} symbols in {len(universe.clusters)} clusters")
    for cluster in universe.clusters:
        symbols = " ".join(cluster.symbols)
        print(f"  {cluster.name:<16} gross cap {cluster.gross_cap:>6.2%}  {symbols}")
    print(f"  {'symbol cap':<16} {resolved.strategy.constraints.max_symbol_weight:>16.2%}")
    print(f"  {'gross cap':<16} {resolved.strategy.constraints.max_gross_exposure:>16.2%}")
    return _EXIT_OK


def _command_show_layout(root: Path) -> int:
    layout = RepositoryLayout.from_root(root)
    print(f"Repository root: {layout.root}")
    for directory in layout.all_directories():
        relative = directory.relative_to(layout.root).as_posix()
        marker = "present" if directory.is_dir() else "missing"
        print(f"  [{marker:>7}] {relative}")
    print("\nImmutable roots (append-only, never rewritten in place):")
    print("  data/raw")
    print("  outputs/validated")
    return _EXIT_OK


def _command_show_environment() -> int:
    revision = detect_code_revision(Path.cwd())
    payload = {
        "python_version": current_python_version(),
        "code_revision": revision.model_dump(mode="json"),
        "promotable_revision": revision.is_promotable,
        "execution_mode": os.environ.get(_MODE_VARIABLE, "<unset>"),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return _EXIT_OK


_SYNTHETIC_BANNER = (
    "SYNTHETIC DATA -- NOT A MARKET RESULT.\n"
    "  These figures describe a hand-written simulated process, not any real\n"
    "  instrument. They verify that the engine runs, accounts, and respects its\n"
    "  timing constraints. They are not evidence of profitability and must never\n"
    "  be reported as measured performance."
)


def _command_backtest_synthetic(args: argparse.Namespace) -> int:
    """Run the engine on synthetic data and print the measured summary."""
    # Deferred imports: these pull in numpy and scikit-learn, which would
    # otherwise be paid by every invocation including `amat --version`.
    from amat.backtest.runner import run_backtest  # noqa: PLC0415
    from amat.data.synthetic import generate_panel  # noqa: PLC0415
    from amat.metrics import summarize  # noqa: PLC0415

    resolved = load_strategy_config(args.config)
    strategy = resolved.strategy
    universe = strategy.to_universe()
    panel = generate_panel(
        universe,
        sessions=args.sessions,
        seed=args.seed,
        trend_strength=args.trend_strength,
    )

    # ``None`` means "use the configured baseline"; the stress values follow it.
    ladder: list[float | None] = [None]
    if args.cost_ladder:
        ladder.extend(sorted(strategy.costs.stress_one_way_bps))

    summaries = []
    for one_way_bps in ladder:
        result = run_backtest(
            strategy,
            panel,
            universe,
            initial_cash=args.initial_cash,
            one_way_bps=one_way_bps,
        )
        summaries.append(summarize(result))
        for warning in result.warnings[:5]:
            print(f"  warning: {warning}", file=sys.stderr)

    if args.json:
        payload = {
            "data_source": panel.source,
            "disclaimer": _SYNTHETIC_BANNER,
            "config_sha256": resolved.content_sha256,
            "strategy": f"{strategy.name} v{strategy.version}",
            "seed": args.seed,
            "trend_strength": args.trend_strength,
            "runs": [summary.as_dict() for summary in summaries],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return _EXIT_OK

    print(_SYNTHETIC_BANNER)
    print()
    print(f"Strategy      : {strategy.name} v{strategy.version}")
    print(f"Config sha256 : {resolved.content_sha256[:16]}")
    print(f"Data source   : {panel.source} (seed {args.seed}, trend {args.trend_strength})")
    print(
        f"Sample        : {summaries[0].first_session} .. {summaries[0].last_session} "
        f"({summaries[0].sessions} sessions)"
    )
    print()
    header = (
        f"{'cost_bps':>9} {'primary':>8} {'ann_ret':>9} {'ann_vol':>8} {'sharpe':>8} "
        f"{'max_dd':>8} {'fills':>7} {'missed':>7} {'costs':>12}"
    )
    print(header)
    print("-" * len(header))
    for summary in summaries:
        print(
            f"{summary.one_way_bps:>9.1f} {summary.is_primary!s:>8} "
            f"{summary.annualized_return:>9.4f} {summary.annualized_volatility:>8.4f} "
            f"{summary.sharpe:>8.2f} {summary.max_drawdown:>8.4f} "
            f"{summary.total_fills:>7} {summary.total_missed_fills:>7} "
            f"{summary.total_costs:>12,.2f}"
        )
    return _EXIT_OK


def _command_report(args: argparse.Namespace) -> int:
    """Build a full per-asset performance report from a synthetic run."""
    from amat.backtest.runner import RunResult, run_backtest  # noqa: PLC0415
    from amat.data.synthetic import generate_panel  # noqa: PLC0415
    from amat.metrics import summarize  # noqa: PLC0415
    from amat.reporting import build_report, render_html, render_text  # noqa: PLC0415

    resolved = load_strategy_config(args.config)
    strategy = resolved.strategy
    universe = strategy.to_universe()
    panel = generate_panel(
        universe, sessions=args.sessions, seed=args.seed, trend_strength=args.trend_strength
    )

    caveats = (
        "SYNTHETIC DATA -- describes a hand-written simulated process, not any real "
        "instrument. Not evidence of profitability.",
        f"Generator seed {args.seed}, planted trend amplitude {args.trend_strength}.",
        "No walk-forward, no locked holdout, no benchmark decomposition has been run.",
    )

    engines: list[tuple[str, RunResult]] = [
        ("v1-frozen", run_backtest(strategy, panel, universe, initial_cash=args.initial_cash))
    ]

    reports = []
    for name, run in engines:
        report = build_report(
            run,
            strategy=f"{strategy.name} v{strategy.version}",
            config_sha256=resolved.content_sha256,
            data_source=panel.source,
            engine=name,
            caveats=caveats,
        )
        reports.append((report, summarize(run)))

    if args.json:
        payload = {
            "reports": [
                {**report.as_dict(), "performance": metrics.as_dict()}
                for report, metrics in reports
            ]
        }
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return _EXIT_OK

    for report, metrics in reports:
        print(render_text(report))
        print(f"  annualized return {metrics.annualized_return:>16.2%}")
        print(f"  annualized vol    {metrics.annualized_volatility:>16.2%}")
        print(f"  Sharpe            {metrics.sharpe:>16.2f}")
        print()

    if args.html is not None:
        html = render_html(reports)
        args.html.write_text(html, encoding="utf-8")
        print(f"HTML report written to {args.html}")
    return _EXIT_OK


def _load_study_scalars(
    args: argparse.Namespace, panel: object, say: Callable[[str], None]
) -> np.ndarray | None:
    """Load and align the v3 conditioning series, when one was requested."""
    if args.scalars is None:
        return None
    from amat.backtest.panel import MarketPanel  # noqa: PLC0415
    from amat.data.scalars import load_weight_scalars  # noqa: PLC0415

    assert isinstance(panel, MarketPanel)  # noqa: S101 - narrowing
    weight_scalars = load_weight_scalars(args.scalars, panel.sessions)
    say(
        f"weight scalars: {args.scalars} "
        f"(mean {float(weight_scalars.mean()):.3f}, "
        f"min {float(weight_scalars.min()):.3f})"
    )
    return weight_scalars


def _study_caveats(
    args: argparse.Namespace,
    strategy: StrategyConfig,
    manifest: SnapshotManifest,
    action_count: int,
    weight_scalars: np.ndarray | None,
) -> list[str]:
    """The study report's caveat block, identical for every engine it runs."""
    caveats = [
        "Historical simulation on immutable vendor data. A backtest is not evidence "
        "of future profitability.",
        f"Data: {manifest.provider}/{manifest.feed} snapshot {manifest.snapshot_id}, "
        f"window coverage {manifest.window_coverage:.4f}.",
        f"Costs: {strategy.costs.baseline_one_way_bps:.1f} bps one-way. Market impact, "
        "queue position, borrow, and taxes are not modelled.",
        "Survivorship: the universe was selected for continuous coverage over this "
        "sample, which favours instruments that persisted.",
        f"Corporate actions: {action_count} dividends and splits are applied to the ledger. "
        "Returns are therefore total-return and directly comparable to a total-return "
        "benchmark. Results produced before 2026-08-10 credited none of them.",
    ]
    if weight_scalars is not None:
        caveats.append(
            f"Conditioning: per-session weight scalars from {args.scalars} were applied "
            "to the combined portfolio's targets (v3). Sleeves are unconditioned."
        )
    return caveats


_YEAR_MONTH = re.compile(r"(\d{4})-(\d{2})")


def _month_start(text: str) -> dt.date:
    """Parse ``YYYY-MM`` to the month's first day; a full date passes through."""
    match = _YEAR_MONTH.fullmatch(text)
    if match is None:
        return dt.date.fromisoformat(text)
    return dt.date(int(match[1]), int(match[2]), 1)


def _month_end(text: str) -> dt.date:
    """Parse ``YYYY-MM`` to the month's last day; a full date passes through."""
    match = _YEAR_MONTH.fullmatch(text)
    if match is None:
        return dt.date.fromisoformat(text)
    year, month = int(match[1]), int(match[2])
    if month == 12:  # noqa: PLR2004 - December has no successor month in-year
        return dt.date(year, 12, 31)
    return dt.date(year, month + 1, 1) - dt.timedelta(days=1)


#: The checked-in configuration each `amat backtest -v` version resolves to.
_BACKTEST_VERSION_CONFIGS: dict[str, Path] = {
    "v1": Path("configs/strategy/amat_v1.toml"),
    "v3": Path("configs/strategy/amat_v3.toml"),
    "v4": Path("configs/strategy/amat_v4.toml"),
    "v5": Path("configs/strategy/amat_v5.toml"),
    "v6": Path("configs/strategy/amat_v6.toml"),
}

_SNAPSHOT_ROOT = Path("data/snapshots")


def _newest_snapshot(root: Path = _SNAPSHOT_ROOT) -> Path:
    """The lexically newest snapshot directory (names are date-stamped)."""
    candidates = (
        sorted(entry for entry in root.iterdir() if entry.is_dir()) if root.is_dir() else []
    )
    if not candidates:
        message = (
            f"no snapshot directories under {root}; ingest one first or pass --snapshot explicitly"
        )
        raise ConfigurationError(message)
    return candidates[-1]


def _command_backtest(args: argparse.Namespace) -> int:
    """One-line real-data backtest: resolve defaults, dispatch to the pipeline.

    This command adds no measurement code of its own -- it builds the argument
    namespace the underlying study command would have received and calls its
    handler, so there is exactly one code path per strategy version and the
    shortcut cannot drift from the long form.
    """
    version: str = args.strategy_version
    if version == "v2":
        print(
            "amat: refused: v2 (managed overlay) was rejected 2026-08-10 and its "
            "runner removed; docs/experiment-registry.md is the durable record. "
            "Its configuration remains only so the rejection stays reproducible "
            "from the registry entry.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    if args.scalars is not None and version != "v3":
        message = "--scalars is the v3 conditioning series; run `amat backtest -v v3 --scalars ...`"
        raise ConfigurationError(message)

    out: Path = (
        args.out
        if args.out is not None
        else Path("outputs/reports") / f"{version}-{dt.datetime.now(tz=dt.UTC).date():%Y%m%d}.pdf"
    )
    snapshot: Path = args.snapshot if args.snapshot is not None else _newest_snapshot()
    config = _BACKTEST_VERSION_CONFIGS[version]
    print(
        f"  backtest {version}: config={config}  snapshot={snapshot}  pdf={out}",
        file=sys.stderr,
        flush=True,
    )
    if version == "v3" and args.scalars is None:
        print(
            "  note: v3 without --scalars runs the identical v1 core -- the news "
            "conditioning IS the scalars file (`newsdesk export-scalars`).",
            file=sys.stderr,
        )

    common: dict[str, object] = {
        "config": config,
        "snapshot": snapshot,
        "start": args.start,
        "end": args.end,
        "initial_cash": args.initial_cash,
        "pdf": out,
        "advisory": args.advisory,
        "json": args.json,
        "run_root": args.run_root,
        "no_run_record": args.no_run_record,
        "allow_holdout": args.allow_holdout,
    }
    if version in ("v1", "v3", "v5"):
        study_args = argparse.Namespace(
            engine="v1",
            sleeve_equity=Decimal("100000"),
            no_sleeves=args.no_sleeves,
            html=None,
            scalars=args.scalars,
            falsify_universe=None,
            **common,
        )
        return _command_study(study_args)
    if version == "v4":
        v4_args = argparse.Namespace(falsify=False, **common)
        return _command_study_v4(v4_args)
    return _command_backtest_v6(args, out=out, snapshot=snapshot)


def _command_backtest_v6(args: argparse.Namespace, *, out: Path, snapshot: Path) -> int:
    """The v6 branch: the study pipeline with the regime/liquidity overlay.

    v6 has no long-form command to delegate to (its overlay needs the context
    panel), so this branch assembles the same pieces the study handler uses --
    sleeve study, report builder, PDF writer, run record -- around a combined
    run that carries the v6 :class:`~amat.backtest.runner.DecisionOverlay`.
    Sleeves stay unconditioned diagnostics, exactly as with v3 scalars.
    """
    from amat.backtest.sleeves import run_sleeve_study  # noqa: PLC0415
    from amat.config.v6 import load_v6_config  # noqa: PLC0415
    from amat.data.snapshot import load_corporate_actions, load_panel  # noqa: PLC0415
    from amat.metrics import summarize  # noqa: PLC0415
    from amat.regime.asset import build_asset_regime_history  # noqa: PLC0415
    from amat.reporting import build_report  # noqa: PLC0415
    from amat.reporting.sleeves import build_sleeve_reports, render_study_text  # noqa: PLC0415

    started_at = dt.datetime.now(dt.UTC)
    resolved = load_v6_config(_BACKTEST_VERSION_CONFIGS["v6"])
    config = resolved.config
    strategy = config.strategy
    symbols = [str(symbol) for symbol in strategy.universe.symbols]
    panel, manifest = load_panel(
        snapshot, symbols, start=args.start, end=args.end, with_context=True
    )

    def say(message: str) -> None:
        print(f"  {message}", file=sys.stderr, flush=True)

    say(f"snapshot {manifest.snapshot_id}: {len(panel)} sessions, {len(symbols)} symbols")
    actions = load_corporate_actions(snapshot, symbols, sessions=panel.sessions)
    say(f"corporate actions: {sum(len(items) for items in actions.values())} across sessions")
    _declaration, holdout_accessed, holdout_reason = _enforce_holdout(
        panel, args.allow_holdout, say
    )

    regimes = build_asset_regime_history(panel, config.asset_regime, config.liquidity)
    overlay = regimes.overlay if config.asset_regime.capital_control_enabled else None
    say(
        "v6 overlay: regime/liquidity capital control ACTIVE"
        if overlay is not None
        else "v6 overlay: capital control disabled; static 6% target only"
    )
    if args.advisory:
        say("note: --advisory is not wired for v6; the flag is ignored on this branch")

    study = run_sleeve_study(
        strategy,
        panel,
        combined_equity=args.initial_cash,
        symbols=[] if args.no_sleeves else None,
        actions_by_session=actions,
        decision_overlay=overlay,
        progress=say,
    )
    caveats = (
        "v6 is a RESEARCH candidate on burned history: not promoted, not a "
        "profitability claim; see docs/experiment-registry.md.",
        "The regime/liquidity overlay conditions the combined portfolio only; "
        "sleeves are the unconditioned per-asset diagnostic.",
    )
    # Engine label: the runner really is the frozen v1 engine -- the v6 overlay
    # is conditioning applied to it, exactly as v3's scalars are. The strategy
    # field carries the v6 identity; inventing a second engine name here would
    # claim a runner the study layer does not have.
    report = build_report(
        study.combined,
        strategy=f"{strategy.name} v{strategy.version}",
        config_sha256=resolved.content_sha256,
        data_source=panel.source,
        engine="v1-frozen",
        caveats=caveats,
        actions_by_session=actions,
    )
    metrics = summarize(study.combined)
    sleeves = build_sleeve_reports(study, actions)
    rendering: StudyRendering = ("v1-frozen", report, metrics, sleeves)

    if args.json:
        payload = {
            "snapshot": manifest.snapshot_id,
            "initial_cash": str(args.initial_cash),
            "studies": [_study_payload(rendering)],
        }
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(render_study_text(report, metrics, sleeves, sleeve_equity=float(study.sleeve_equity)))

    clusters = {
        cluster.name: [str(symbol) for symbol in cluster.symbols]
        for cluster in strategy.universe.clusters
    }
    _write_study_pdf(
        out,
        request_advisory_page=False,
        clusters=clusters,
        report=report,
        metrics=metrics,
        sleeves=sleeves,
        result=study.combined,
        panel=panel,
        actions=actions,
        say=say,
    )
    _write_run_record(
        args,
        command="backtest",
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=resolved.content_sha256,
        config_source=str(resolved.source_path) if resolved.source_path else None,
        execution=strategy.execution,
        costs=strategy.costs,
        snapshot_manifest=manifest,
        sections={
            "studies": [_study_payload(rendering)],
            "parameters": {
                "initial_cash": str(args.initial_cash),
                "start": args.start.isoformat() if args.start else None,
                "end": args.end.isoformat() if args.end else None,
                "capital_control_enabled": config.asset_regime.capital_control_enabled,
            },
        },
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_reason,
        say=say,
    )
    return _EXIT_OK


def _command_study(args: argparse.Namespace) -> int:
    """Run the requested engines and their per-asset sleeves on real market data."""
    from amat.backtest.sleeves import (  # noqa: PLC0415
        STUDY_ENGINES,
        SleeveStudy,
        run_sleeve_study,
    )
    from amat.metrics import summarize  # noqa: PLC0415
    from amat.reporting import build_report  # noqa: PLC0415
    from amat.reporting.sleeves import (  # noqa: PLC0415
        build_sleeve_reports,
        render_study_html,
        render_study_text,
    )

    started_at = dt.datetime.now(dt.UTC)
    (
        resolved,
        strategy,
        panel,
        manifest,
        actions,
        _declaration,
        holdout_accessed,
        holdout_reason,
        say,
    ) = _load_measurement_inputs(args)
    action_count = sum(len(items) for items in actions.values())

    if args.advisory and args.pdf is None:
        message = "--advisory writes a page into the PDF report; pass --pdf as well"
        raise ConfigurationError(message)

    weight_scalars = _load_study_scalars(args, panel, say)
    caveats = _study_caveats(args, strategy, manifest, action_count, weight_scalars)

    rendered: list[StudyRendering] = []
    combined_by_engine: dict[str, object] = {}
    for engine in STUDY_ENGINES[args.engine]:
        study: SleeveStudy = run_sleeve_study(
            strategy,
            panel,
            combined_equity=args.initial_cash,
            sleeve_equity=args.sleeve_equity,
            symbols=[] if args.no_sleeves else None,
            engine=engine,
            actions_by_session=actions,
            weight_scalars=weight_scalars,
            progress=say,
        )
        combined_by_engine[engine] = study.combined
        report = build_report(
            study.combined,
            strategy=f"{strategy.name} v{strategy.version}",
            config_sha256=resolved.content_sha256,
            data_source=panel.source,
            engine=engine,
            caveats=caveats,
            actions_by_session=actions,
        )
        rendered.append(
            (
                engine,
                report,
                summarize(study.combined),
                build_sleeve_reports(study, actions),
            )
        )

    equity = float(args.sleeve_equity)
    if args.json:
        payload = {
            "snapshot": manifest.snapshot_id,
            "initial_cash": str(args.initial_cash),
            "sleeve_equity": str(args.sleeve_equity),
            "studies": [_study_payload(entry) for entry in rendered],
        }
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        for _engine, report, metrics, sleeves in rendered:
            print(render_study_text(report, metrics, sleeves, sleeve_equity=equity))
            print()
        if len(rendered) > 1:
            print(_engine_comparison(rendered))

    if args.html is not None:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        for engine, report, metrics, sleeves in rendered:
            # One file per engine. Merging two engines into one page would put
            # two different strategies' tables under one heading, which is the
            # specific confusion this command exists to prevent.
            target = (
                args.html
                if len(rendered) == 1
                else args.html.with_name(f"{args.html.stem}.{engine}{args.html.suffix}")
            )
            target.write_text(
                render_study_html(report, metrics, sleeves, sleeve_equity=equity), encoding="utf-8"
            )
            print(f"HTML report written to {target}", file=sys.stderr)

    if args.pdf is not None:
        clusters = {
            cluster.name: [str(symbol) for symbol in cluster.symbols]
            for cluster in strategy.universe.clusters
        }
        for engine, report, metrics, sleeves in rendered:
            target = (
                args.pdf
                if len(rendered) == 1
                else args.pdf.with_name(f"{args.pdf.stem}.{engine}{args.pdf.suffix}")
            )
            _write_study_pdf(
                target,
                request_advisory_page=args.advisory,
                clusters=clusters,
                report=report,
                metrics=metrics,
                sleeves=sleeves,
                result=combined_by_engine[engine],
                panel=panel,
                actions=actions,
                say=say,
            )

    battery = None
    if args.falsify_universe is not None:
        battery = _run_universe_falsification(args, strategy, panel, actions, say)

    _persist_study_record(
        args,
        rendered=rendered,
        battery=battery,
        resolved=resolved,
        strategy=strategy,
        manifest=manifest,
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_reason,
        say=say,
    )
    return _EXIT_OK


def _persist_study_record(
    args: argparse.Namespace,
    *,
    rendered: Sequence[StudyRendering],
    battery: UniverseFalsification | None,
    resolved: ResolvedConfig,
    strategy: StrategyConfig,
    manifest: SnapshotManifest,
    started_at: dt.datetime,
    holdout_accessed: bool = False,
    holdout_access_reason: str | None = None,
    say: Callable[[str], None],
) -> None:
    """Assemble and write the study command's run record."""
    sections: dict[str, object] = {
        "studies": [_study_payload(entry) for entry in rendered],
        "parameters": {
            "initial_cash": str(args.initial_cash),
            "sleeve_equity": str(args.sleeve_equity),
            "scalars": str(args.scalars) if args.scalars is not None else None,
            "start": args.start.isoformat() if args.start else None,
            "end": args.end.isoformat() if args.end else None,
        },
    }
    if battery is not None:
        from amat.reporting.artifacts import universe_battery_payload  # noqa: PLC0415

        sections["universe_falsification"] = universe_battery_payload(battery)
    _write_run_record(
        args,
        command="study",
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=resolved.content_sha256,
        config_source=str(resolved.source_path) if resolved.source_path else None,
        execution=strategy.execution,
        costs=strategy.costs,
        snapshot_manifest=manifest,
        sections=sections,
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_access_reason,
        say=say,
    )


def _run_universe_falsification(
    args: argparse.Namespace,
    strategy: StrategyConfig,
    panel: MarketPanel,
    actions: Mapping[SessionDate, Sequence[CorporateAction]],
    say: Callable[[str], None],
) -> UniverseFalsification:
    """Run and print the leave-one-out battery against the named baseline."""
    from amat.validation.falsification import falsify_universe  # noqa: PLC0415

    baseline_resolved = load_strategy_config(args.falsify_universe)
    say(
        f"universe falsification: {strategy.name} vs "
        f"{baseline_resolved.strategy.name} plus leave-one-out variants"
    )
    if getattr(args, "scalars", None) is not None:
        say(
            "note: the universe battery runs UNCONDITIONED -- the --scalars series "
            "applies to the study above, not to any battery row"
        )
    battery = falsify_universe(
        strategy,
        baseline_resolved.strategy,
        panel,
        initial_cash=args.initial_cash,
        actions_by_session=actions,
    )
    _print_universe_falsification(battery)
    return battery


def _study_payload(entry: StudyRendering) -> dict[str, object]:
    """Serialize one engine's study for the JSON output.

    Written as a function with an explicit return annotation rather than a dict
    display inside a comprehension: the ``**report.as_dict()`` unpack there gave
    mypy no target type to check against and it inferred a nonsensical one.
    """
    engine, report, metrics, sleeves = entry
    combined: dict[str, object] = dict(report.as_dict())
    combined["performance"] = metrics.as_dict()
    return {
        "engine": engine,
        "combined": combined,
        "sleeves": [sleeve.as_dict() for sleeve in sleeves],
    }


def _engine_comparison(rendered: Sequence[StudyRendering]) -> str:
    """Side-by-side summary of every engine run, on identical data and funding.

    Printed only when more than one engine ran. The overlay can only shrink or
    veto the v1 target, so this table is the whole basis for judging whether it
    paid for what it cost.
    """
    lines = ["=" * 92, "ENGINE COMPARISON — identical data, costs, and starting capital", "=" * 92]
    lines.append(
        f"  {'ENGINE':<12}{'START':>14}{'END':>14}{'TOTAL':>10}"
        f"{'ANN':>9}{'VOL':>8}{'SHARPE':>8}{'MAXDD':>9}"
    )
    for engine, report, metrics, _sleeves in rendered:
        lines.append(
            f"  {engine:<12}{report.initial_equity:>14,.0f}{report.final_equity:>14,.0f}"
            f"{report.total_return:>+10.2%}{metrics.annualized_return:>+9.2%}"
            f"{metrics.annualized_volatility:>8.2%}{metrics.sharpe:>8.2f}"
            f"{report.max_drawdown:>9.2%}"
        )
    lines.append("")
    lines.append("  A higher return at a higher drawdown is not automatically worse, and a lower")
    lines.append(
        "  drawdown bought with most of the return is not automatically better. Read both."
    )
    lines.append("=" * 92)
    return "\n".join(lines)


def _write_study_pdf(
    target: Path,
    *,
    request_advisory_page: bool,
    clusters: dict[str, list[str]],
    report: PerformanceReport,
    metrics: PerformanceSummary,
    sleeves: tuple[SleeveReport, ...],
    result: object,
    panel: object,
    actions: object,
    say: Callable[[str], None],
) -> None:
    """Build insights, optionally fetch the advisory page, and render the PDF.

    Shared by ``study`` and ``study-v4`` so both documents are produced by one
    code path. The advisory request degrades to a recorded reason; the measured
    pages never depend on it.
    """
    from amat.backtest.panel import MarketPanel  # noqa: PLC0415
    from amat.backtest.runner import RunResult  # noqa: PLC0415
    from amat.reporting.insights import build_insights  # noqa: PLC0415
    from amat.reporting.pdf import render_study_pdf  # noqa: PLC0415

    assert isinstance(result, RunResult)  # noqa: S101 - protocol narrowing
    assert isinstance(panel, MarketPanel)  # noqa: S101
    say("building episode diagnostics")
    insights = build_insights(
        result,
        panel,
        actions_by_session=actions,  # type: ignore[arg-type]
        clusters=clusters,
    )
    advisory_text: str | None = None
    advisory_provenance = ""
    if request_advisory_page:
        from amat.reporting.advisory import (  # noqa: PLC0415
            advisory_payload,
            request_advisory,
        )

        say("advisory: requesting Gemini commentary (aggregate statistics only)")
        outcome = request_advisory(advisory_payload(report, metrics, insights, sleeves))
        if outcome.available:
            advisory_text = outcome.text
            advisory_provenance = outcome.provenance
            say("advisory: received")
        else:
            say(f"advisory unavailable: {outcome.failure}")
    render_study_pdf(
        target,
        report=report,
        metrics=metrics,
        sleeves=sleeves,
        result=result,
        panel=panel,
        insights=insights,
        advisory=advisory_text,
        advisory_provenance=advisory_provenance,
    )
    say(f"PDF report written to {target}")


def _print_universe_falsification(falsification: object) -> None:
    """The universe battery table, printed the same way every run."""
    from amat.validation.falsification import UniverseFalsification  # noqa: PLC0415

    assert isinstance(falsification, UniverseFalsification)  # noqa: S101 - narrowing
    print()
    print("UNIVERSE FALSIFICATION — identical data, costs, funding; only the universe differs")
    header = (
        f"  {'RUN':<32}{'SYMS':>5}{'TOTAL':>9}{'ANN':>8}{'VOL':>8}"
        f"{'SHARPE':>8}{'MAXDD':>9}{'FILLS':>7}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    rows = (falsification.baseline, falsification.candidate, *falsification.leave_one_out)
    for outcome in rows:
        print(
            f"  {outcome.name:<32}{len(outcome.symbols):>5}{outcome.total_return:>+9.2%}"
            f"{outcome.annualized_return:>+8.2%}{outcome.annualized_volatility:>8.2%}"
            f"{outcome.sharpe:>8.2f}{outcome.max_drawdown:>9.2%}{outcome.fills:>7}"
        )
    print(f"\n  VERDICT: {falsification.verdict}")


def _print_scalar_falsification(falsification: object, *, difference: str) -> None:
    """The falsification table, printed the same way every run.

    ``difference`` names the one thing that varies between the rows -- the
    blend lambda for v4, the conditioning scalar series for a conditioning
    experiment -- because the table's whole claim is that nothing else does.
    """
    from amat.validation.falsification import FalsificationResult  # noqa: PLC0415

    assert isinstance(falsification, FalsificationResult)  # noqa: S101 - narrowing
    print()
    print(f"FALSIFICATION — identical data, costs, funding; only the {difference} differs")
    header = (
        f"  {'RUN':<28}{'TOTAL':>9}{'ANN':>8}{'VOL':>8}{'SHARPE':>8}"
        f"{'MAXDD':>9}{'FILLS':>7}{'MEAN':>7}{'FLIPS':>7}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for outcome in (falsification.baseline, falsification.candidate, *falsification.controls):
        print(
            f"  {outcome.name:<28}{outcome.total_return:>+9.2%}"
            f"{outcome.annualized_return:>+8.2%}{outcome.annualized_volatility:>8.2%}"
            f"{outcome.sharpe:>8.2f}{outcome.max_drawdown:>9.2%}"
            f"{outcome.fills:>7}{outcome.mean_scalar:>7.3f}{outcome.flips:>7}"
        )
    print(f"\n  VERDICT: {falsification.verdict}")


def _v4_caveats(
    config: SwitchingStrategyConfig,
    manifest: SnapshotManifest,
    action_count: int,
    *,
    mean_lambda: float,
) -> tuple[str, ...]:
    """The v4 study report's caveat block."""
    return (
        "Historical simulation on immutable vendor data. A backtest is not evidence "
        "of future profitability.",
        f"Data: {manifest.provider}/{manifest.feed} snapshot {manifest.snapshot_id}, "
        f"window coverage {manifest.window_coverage:.4f}.",
        f"Costs: {config.trend.costs.baseline_one_way_bps:.1f} bps one-way. Market "
        "impact, queue position, borrow, and taxes are not modelled.",
        "Survivorship: the universe was selected for continuous coverage over this "
        "sample, which favours instruments that persisted.",
        f"Corporate actions: {action_count} dividends and splits are applied to the ledger.",
        "v4 blends two stances of the one strategy core by a declared breadth rule "
        f"(mean lambda {mean_lambda:.3f}). It is an EXPERIMENT: it is a "
        "candidate only if it survives the falsification controls and the "
        "walk-forward protocol. Session records carry the trend stance's "
        "risk diagnostics; the blend changes targets only.",
    )


def _load_v4_inputs(
    args: argparse.Namespace,
) -> tuple[
    ResolvedSwitchingConfig,
    SwitchingStrategyConfig,
    MarketPanel,
    SnapshotManifest,
    Mapping[SessionDate, Sequence[CorporateAction]],
    int,
    bool,
    str | None,
    Callable[[str], None],
]:
    """Load the v4 switching configuration, panel, and corporate actions.

    Enforces the locked-holdout boundary like every other panel loader, and
    returns the ``(holdout_accessed, holdout_access_reason)`` pair the run
    manifest must record.
    """
    from amat.config.switching import load_switching_config  # noqa: PLC0415
    from amat.data.snapshot import load_corporate_actions, load_panel  # noqa: PLC0415

    resolved = load_switching_config(args.config)
    config = resolved.strategy
    symbols = [str(symbol) for symbol in config.trend.universe.symbols]
    panel, manifest = load_panel(args.snapshot, symbols, start=args.start, end=args.end)

    def say(message: str) -> None:
        print(f"  {message}", file=sys.stderr, flush=True)

    say(f"snapshot {manifest.snapshot_id}: {len(panel)} sessions, {len(symbols)} symbols")
    actions = load_corporate_actions(args.snapshot, symbols, sessions=panel.sessions)
    action_count = sum(len(items) for items in actions.values())
    say(f"corporate actions: {action_count} across {len(actions)} sessions")
    _, accessed, reason = _enforce_holdout(panel, args.allow_holdout, say)
    return resolved, config, panel, manifest, actions, action_count, accessed, reason, say


def _command_study_v4(args: argparse.Namespace) -> int:
    """Run the v4 stance-switching strategy and, optionally, its controls."""
    import numpy as np  # noqa: PLC0415

    from amat.backtest.runner import run_backtest  # noqa: PLC0415
    from amat.backtest.stances import assemble_stance, blend_series  # noqa: PLC0415
    from amat.metrics import summarize  # noqa: PLC0415
    from amat.reporting import build_report, render_text  # noqa: PLC0415

    started_at = dt.datetime.now(dt.UTC)
    (
        resolved,
        config,
        panel,
        manifest,
        actions,
        action_count,
        holdout_accessed,
        holdout_reason,
        say,
    ) = _load_v4_inputs(args)

    if args.advisory and args.pdf is None:
        message = "--advisory writes a page into the PDF report; pass --pdf as well"
        raise ConfigurationError(message)

    lam = blend_series(config, panel)
    stance = assemble_stance(config, panel, weights=lam)
    tilted = float(np.mean(lam < 1.0))
    say(
        f"blend lambda: mean {float(lam.mean()):.3f}, min {float(lam.min()):.3f}, "
        f"defensive-tilted on {tilted:.1%} of sessions"
    )
    caveats = _v4_caveats(config, manifest, action_count, mean_lambda=float(lam.mean()))

    result = run_backtest(
        config.trend,
        panel,
        config.trend.to_universe(),
        initial_cash=args.initial_cash,
        actions_by_session=actions,
        stance=stance,
    )
    metrics = summarize(result)
    report = build_report(
        result,
        strategy=f"{config.name} v{config.version}",
        config_sha256=resolved.content_sha256,
        data_source=panel.source,
        engine="v4-switching",
        caveats=caveats,
        actions_by_session=actions,
    )

    falsification = None
    if args.falsify:
        from amat.validation.falsification import falsify_blend  # noqa: PLC0415

        say("falsification: running baseline, candidate, and 4 controls (6 backtests)")
        falsification = falsify_blend(
            config, panel, initial_cash=args.initial_cash, actions_by_session=actions
        )

    if args.json:
        payload: dict[str, object] = {
            "snapshot": manifest.snapshot_id,
            "report": {**report.as_dict(), "performance": metrics.as_dict()},
            "blend": {
                "mean_lambda": float(lam.mean()),
                "min_lambda": float(lam.min()),
                "defensive_tilted_fraction": tilted,
            },
        }
        if falsification is not None:
            payload["falsification"] = {
                "verdict": falsification.verdict,
                "runs": [
                    vars(outcome)
                    for outcome in (
                        falsification.baseline,
                        falsification.candidate,
                        *falsification.controls,
                    )
                ],
            }
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(render_text(report))
        print(f"  annualized return {metrics.annualized_return:>16.2%}")
        print(f"  annualized vol    {metrics.annualized_volatility:>16.2%}")
        print(f"  Sharpe            {metrics.sharpe:>16.2f}")
        if falsification is not None:
            _print_scalar_falsification(falsification, difference="blend lambda")

    _persist_v4_record(
        args,
        report_payload={**report.as_dict(), "performance": metrics.as_dict()},
        blend={
            "mean_lambda": float(lam.mean()),
            "min_lambda": float(lam.min()),
            "defensive_tilted_fraction": tilted,
        },
        falsification=falsification,
        resolved=resolved,
        config=config,
        manifest=manifest,
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_reason,
        say=say,
    )

    if args.pdf is not None:
        clusters = {
            cluster.name: [str(symbol) for symbol in cluster.symbols]
            for cluster in config.trend.universe.clusters
        }
        _write_study_pdf(
            args.pdf,
            request_advisory_page=args.advisory,
            clusters=clusters,
            report=report,
            metrics=metrics,
            sleeves=(),
            result=result,
            panel=panel,
            actions=actions,
            say=say,
        )
    return _EXIT_OK


def _persist_v4_record(
    args: argparse.Namespace,
    *,
    report_payload: dict[str, object],
    blend: dict[str, object],
    falsification: FalsificationResult | None,
    resolved: ResolvedSwitchingConfig,
    config: SwitchingStrategyConfig,
    manifest: SnapshotManifest,
    started_at: dt.datetime,
    holdout_accessed: bool = False,
    holdout_access_reason: str | None = None,
    say: Callable[[str], None],
) -> None:
    """Assemble and write the study-v4 command's run record."""
    sections: dict[str, object] = {
        "study": report_payload,
        "blend": blend,
        "parameters": {
            "initial_cash": str(args.initial_cash),
            "start": args.start.isoformat() if args.start else None,
            "end": args.end.isoformat() if args.end else None,
        },
    }
    if falsification is not None:
        from amat.reporting.artifacts import blend_battery_payload  # noqa: PLC0415

        sections["blend_falsification"] = blend_battery_payload(falsification)
    _write_run_record(
        args,
        command="study-v4",
        strategy_name=config.name,
        strategy_version=config.version,
        config_sha256=resolved.content_sha256,
        config_source=str(resolved.source_path) if resolved.source_path else None,
        execution=config.trend.execution,
        costs=config.trend.costs,
        snapshot_manifest=manifest,
        sections=sections,
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_access_reason,
        say=say,
    )


def _enforce_holdout(
    panel: MarketPanel,
    allow_reason: str | None,
    say: Callable[[str], None],
) -> tuple[HoldoutDeclaration | None, bool, str | None]:
    """Fail closed on holdout data, whatever command loaded the panel.

    Enforcement lives beside the panel loaders rather than inside any one
    command: the declaration's fail-closed property is only as strong as its
    weakest caller, and a measuring command that skipped this check would
    write an affirmative ``holdout_accessed: false`` into its manifest while
    reading past the boundary.
    """
    from amat.validation.walkforward import (  # noqa: PLC0415
        assert_respects_holdout,
        default_holdout_root,
        load_holdout_declaration,
    )

    declaration = load_holdout_declaration(default_holdout_root())
    accessed, reason = assert_respects_holdout(panel, declaration, access_reason=allow_reason)
    if declaration is None:
        say("no holdout declaration exists yet; `amat declare-holdout` freezes one")
    elif accessed:
        say(f"holdout access recorded: {reason}")
    return declaration, accessed, reason


def _load_any_strategy_config(path: Path) -> ResolvedConfig:
    """Resolve a plain strategy configuration, or a v6 file's strategy core.

    The v6 research configuration is a superset TOML with its own loader.
    Measuring commands (stress, Monte Carlo, projection, walk-forward) apply
    to its *strategy core* -- which for canonical v6 is the whole candidate,
    since ``capital_control_enabled`` is false there and the overlay is a
    runner argument these commands never pass. The fallback is attempted only
    when the plain loader rejects v6's extra tables, so a genuinely malformed
    v1-style file still fails with its original error.
    """
    try:
        return load_strategy_config(path)
    except ConfigurationError as plain_error:
        if "Extra inputs are not permitted" not in str(plain_error):
            raise
        from amat.config.loading import ResolvedConfig  # noqa: PLC0415
        from amat.config.v6 import load_v6_config  # noqa: PLC0415

        try:
            v6 = load_v6_config(path)
        except Exception:  # surface the PLAIN loader's error, not the fallback's
            raise plain_error from None
        return ResolvedConfig(
            strategy=v6.config.strategy,
            source_path=v6.source_path,
            content_sha256=v6.content_sha256,
        )


def _load_measurement_inputs(
    args: argparse.Namespace,
) -> tuple[
    ResolvedConfig,
    StrategyConfig,
    MarketPanel,
    SnapshotManifest,
    Mapping[SessionDate, Sequence[CorporateAction]],
    HoldoutDeclaration | None,
    bool,
    str | None,
    Callable[[str], None],
]:
    """Load the configuration, panel, and corporate actions one command needs.

    Also enforces the locked-holdout boundary for every caller, returning the
    declaration and the ``(holdout_accessed, holdout_access_reason)`` pair the
    run manifest must record.
    """
    from amat.data.snapshot import load_corporate_actions, load_panel  # noqa: PLC0415

    resolved = _load_any_strategy_config(args.config)
    strategy = resolved.strategy
    symbols = [str(symbol) for symbol in strategy.universe.symbols]
    panel, manifest = load_panel(args.snapshot, symbols, start=args.start, end=args.end)

    def say(message: str) -> None:
        print(f"  {message}", file=sys.stderr, flush=True)

    say(f"snapshot {manifest.snapshot_id}: {len(panel)} sessions, {len(symbols)} symbols")
    actions = load_corporate_actions(args.snapshot, symbols, sessions=panel.sessions)
    action_count = sum(len(items) for items in actions.values())
    say(f"corporate actions: {action_count} across {len(actions)} sessions")
    declaration, accessed, reason = _enforce_holdout(panel, args.allow_holdout, say)
    return resolved, strategy, panel, manifest, actions, declaration, accessed, reason, say


def _write_run_record(
    args: argparse.Namespace,
    *,
    command: str,
    strategy_name: str,
    strategy_version: str,
    config_sha256: str,
    config_source: str | None,
    execution: ExecutionConfig,
    costs: CostConfig,
    snapshot_manifest: object,
    sections: dict[str, object],
    seeds: Sequence[int] = (),
    started_at: dt.datetime,
    holdout_accessed: bool = False,
    holdout_access_reason: str | None = None,
    say: Callable[[str], None],
) -> None:
    """Persist the run record unless the caller explicitly opted out."""
    if args.no_run_record:
        say("run record skipped (--no-run-record); this result cannot be cited")
        return
    from amat.data.snapshot import SnapshotManifest  # noqa: PLC0415
    from amat.reporting.artifacts import (  # noqa: PLC0415
        dataset_reference,
        new_run_manifest,
        persist_run_record,
    )

    assert isinstance(snapshot_manifest, SnapshotManifest)  # noqa: S101 - narrowing
    manifest = new_run_manifest(
        command=command,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        config_sha256=config_sha256,
        config_source=config_source,
        datasets=(dataset_reference(snapshot_manifest),),
        execution_model_version=(
            f"sim-vwap-{execution.window_start.isoformat()}-{execution.window_end.isoformat()}"
            f"-lag{execution.signal_to_execution_lag_sessions}"
        ),
        cost_model_version=(
            f"one-way-{costs.baseline_one_way_bps:g}bps-primary-{costs.is_primary}"
        ),
        random_seeds=seeds,
        started_at=started_at,
        ended_at=dt.datetime.now(dt.UTC),
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_access_reason,
    )
    target = persist_run_record(args.run_root, manifest, sections)
    say(f"run record: {target}")


def _command_walkforward(args: argparse.Namespace) -> int:
    """Expanding walk-forward with per-regime slices and the measurable gates."""
    from amat.validation.regimes import classify_sessions, regime_performance  # noqa: PLC0415
    from amat.validation.walkforward import walk_forward  # noqa: PLC0415

    started_at = dt.datetime.now(dt.UTC)
    (
        resolved,
        strategy,
        panel,
        manifest,
        actions,
        declaration,
        holdout_accessed,
        holdout_reason,
        say,
    ) = _load_measurement_inputs(args)

    say("walk-forward: one expanding run over the full window")
    result = walk_forward(
        strategy,
        panel,
        strategy.to_universe(),
        initial_cash=args.initial_cash,
        actions_by_session=actions,
    )
    labels = classify_sessions(panel)
    slices = regime_performance(result.equity_curve, result.session_dates, panel, labels)

    payload: dict[str, object] = {
        "walk_forward": {
            "strategy": result.strategy,
            "caveat": result.caveat,
            "positive_fold_fraction": result.positive_fold_fraction,
            "folds": [
                {"label": fold.label, **dataclasses.asdict(fold.statistics)}
                for fold in result.folds
            ],
            "full_sample": dataclasses.asdict(result.full_sample),
            "research_gates": [dataclasses.asdict(gate) for gate in result.research_gates()],
        },
        "regimes": [dataclasses.asdict(entry) for entry in slices],
        "holdout": {
            "declared_boundary": (
                declaration.boundary_session.isoformat() if declaration else None
            ),
            "accessed": holdout_accessed,
            "reason": holdout_reason,
        },
        "parameters": {
            "initial_cash": str(args.initial_cash),
            "start": args.start.isoformat() if args.start else None,
            "end": args.end.isoformat() if args.end else None,
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        _print_walk_forward(result, slices)
    _write_run_record(
        args,
        command="walkforward",
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=resolved.content_sha256,
        config_source=str(resolved.source_path) if resolved.source_path else None,
        execution=strategy.execution,
        costs=strategy.costs,
        snapshot_manifest=manifest,
        sections=payload,
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_reason,
        say=say,
    )
    return _EXIT_OK


def _print_walk_forward(result: object, slices: Sequence[object]) -> None:
    """The fold, gate, and regime tables, printed the same way every run."""
    from amat.validation.regimes import RegimeSlice  # noqa: PLC0415
    from amat.validation.walkforward import WalkForwardResult  # noqa: PLC0415

    assert isinstance(result, WalkForwardResult)  # noqa: S101 - narrowing
    print()
    print(f"WALK-FORWARD — {result.strategy}; one expanding run, calendar folds")
    print(f"  ! {result.caveat}")
    print()
    header = (
        f"  {'FOLD':<12}{'SESSIONS':>9}{'TOTAL':>10}{'ANN':>9}{'VOL':>8}"
        f"{'SHARPE':>8}{'MAXDD':>9}{'TRADES':>8}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    rows = [(fold.label, fold.statistics) for fold in result.folds] + [
        ("full sample", result.full_sample)
    ]
    for label, statistics in rows:
        print(
            f"  {label:<12}{statistics.sessions:>9}{statistics.total_return:>+10.2%}"
            f"{statistics.annualized_return:>+9.2%}{statistics.annualized_volatility:>8.2%}"
            f"{statistics.sharpe:>8.2f}{statistics.max_drawdown:>9.2%}{statistics.trades:>8}"
        )
    positive = sum(1 for fold in result.folds if fold.positive)
    print(
        f"\n  positive folds: {positive}/{len(result.folds)} ({result.positive_fold_fraction:.0%})"
    )
    print("\n  RESEARCH GATES measurable here (necessary, never sufficient)")
    for gate in result.research_gates():
        status = "PASS" if gate.passed else ("FAIL" if gate.measurable else "NOT MEASURABLE")
        print(f"  [{status}] {gate.name}: measured {gate.measured} (limit {gate.limit})")
    print(
        "  (holdout return, cost stresses, concentration, and parameter"
        " neighbourhood are owned by other commands)"
    )
    print("\nREGIME PERFORMANCE — causal SPY trend/volatility classification")
    regime_header = (
        f"  {'REGIME':<16}{'SESSIONS':>9}{'SHARE':>8}{'ANN':>9}{'VOL':>8}{'SHARPE':>8}{'HIT':>7}"
    )
    print(regime_header)
    print("  " + "-" * (len(regime_header) - 2))
    for entry in slices:
        assert isinstance(entry, RegimeSlice)  # noqa: S101 - narrowing
        print(
            f"  {entry.label:<16}{entry.sessions:>9}{entry.share_of_sample:>8.1%}"
            f"{entry.annualized_return:>+9.2%}{entry.annualized_volatility:>8.2%}"
            f"{entry.sharpe:>8.2f}{entry.hit_rate:>7.0%}"
        )
    print(
        "  (regime sessions are interleaved, so per-regime drawdowns are"
        " undefined and deliberately absent)"
    )


def _command_declare_holdout(args: argparse.Namespace) -> int:
    """Freeze the locked-holdout boundary at a snapshot's last session."""
    from amat.data.snapshot import load_manifest  # noqa: PLC0415
    from amat.validation.walkforward import declare_holdout, default_holdout_root  # noqa: PLC0415

    manifest = load_manifest(args.snapshot)
    directory = declare_holdout(
        default_holdout_root(),
        boundary_session=manifest.last_session,
        snapshot_id=manifest.snapshot_id,
        rationale=args.reason,
        declared_at=dt.datetime.now(dt.UTC),
    )
    print(f"Holdout declared: sessions after {manifest.last_session} are locked.")
    print(f"  declaration: {directory}")
    print("  Reading past the boundary now requires an explicit, recorded reason")
    print("  (e.g. `amat walkforward ... --allow-holdout REASON`).")
    return _EXIT_OK


def _command_montecarlo(args: argparse.Namespace) -> int:
    """Bootstrap Monte Carlo: the observed run inside its resampled distribution."""
    from amat.validation.montecarlo import run_monte_carlo  # noqa: PLC0415

    started_at = dt.datetime.now(dt.UTC)
    (
        resolved,
        strategy,
        panel,
        manifest,
        actions,
        declaration,
        holdout_accessed,
        holdout_reason,
        say,
    ) = _load_measurement_inputs(args)
    study = run_monte_carlo(
        strategy,
        panel,
        strategy.to_universe(),
        paths=args.paths,
        seed=args.seed,
        mean_block_sessions=args.block_sessions,
        initial_cash=args.initial_cash,
        actions_by_session=actions,
        workers=args.workers,
        progress=say,
    )

    payload: dict[str, object] = {
        "monte_carlo": {
            "strategy": study.strategy,
            "paths": study.paths,
            "seed": study.seed,
            "mean_block_sessions": study.mean_block_sessions,
            # The exact Decimal funding, not the study's float mirror: every
            # other command records funding unrounded, and this one must not
            # be the exception.
            "initial_cash": str(args.initial_cash),
            "caveat": study.caveat,
            "observed": dataclasses.asdict(study.observed),
            "metric_percentiles": {
                metric: dict(row) for metric, row in study.metric_percentiles.items()
            },
            "observed_percentile": dict(study.observed_percentile),
            "probabilities": dict(study.probabilities),
            "outcomes": [dataclasses.asdict(outcome) for outcome in study.outcomes],
        },
        "holdout": {
            "declared_boundary": (
                declaration.boundary_session.isoformat() if declaration else None
            ),
            "accessed": holdout_accessed,
            "reason": holdout_reason,
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        _print_montecarlo(study)

    if args.pdf is not None:
        from amat.reporting.montecarlo import render_montecarlo_pdf  # noqa: PLC0415

        render_montecarlo_pdf(args.pdf, study)
        say(f"Monte Carlo PDF written to {args.pdf}")

    _write_run_record(
        args,
        command="montecarlo",
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=resolved.content_sha256,
        config_source=str(resolved.source_path) if resolved.source_path else None,
        execution=strategy.execution,
        costs=strategy.costs,
        snapshot_manifest=manifest,
        sections=payload,
        seeds=(args.seed,),
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_reason,
        say=say,
    )
    return _EXIT_OK


def _command_project(args: argparse.Namespace) -> int:
    """Forward projection from the strategy's own realized net returns."""
    from amat.backtest.runner import run_backtest  # noqa: PLC0415
    from amat.validation.projection import project_forward, session_returns  # noqa: PLC0415

    started_at = dt.datetime.now(dt.UTC)
    (
        resolved,
        strategy,
        panel,
        manifest,
        actions,
        declaration,
        holdout_accessed,
        holdout_reason,
        say,
    ) = _load_measurement_inputs(args)

    say("source run: executing the strategy on this panel to obtain the returns to resample")
    result = run_backtest(
        strategy,
        panel,
        strategy.to_universe(),
        initial_cash=args.initial_cash,
        actions_by_session=actions,
    )
    returns = session_returns(result.equity_curve)
    funding = args.funding if args.funding is not None else args.initial_cash
    study = project_forward(
        returns,
        strategy=f"{strategy.name} v{strategy.version}",
        funding=float(funding),
        horizon_years=args.horizon_years,
        paths=args.paths,
        seed=args.seed,
        annualization_sessions=strategy.volatility.annualization_sessions,
        mean_block_sessions=args.block_sessions,
    )

    payload: dict[str, object] = {
        "projection": study.as_dict(),
        "source_run": {
            "initial_cash": str(args.initial_cash),
            "final_equity": str(result.final_equity),
            "fills": result.total_fills,
            "sessions": len(result.sessions),
            "one_way_bps": result.one_way_bps,
        },
        # The exact Decimal funding, matching every other command's records.
        "funding": str(funding),
        "holdout": {
            "declared_boundary": (
                declaration.boundary_session.isoformat() if declaration else None
            ),
            "accessed": holdout_accessed,
            "reason": holdout_reason,
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        _print_projection(study)

    if args.pdf is not None:
        from amat.reporting.projection import render_projection_pdf  # noqa: PLC0415

        render_projection_pdf(args.pdf, study)
        say(f"projection PDF written to {args.pdf}")

    _write_run_record(
        args,
        command="project",
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=resolved.content_sha256,
        config_source=str(resolved.source_path) if resolved.source_path else None,
        execution=strategy.execution,
        costs=strategy.costs,
        snapshot_manifest=manifest,
        sections=payload,
        seeds=(args.seed,),
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_reason,
        say=say,
    )
    return _EXIT_OK


def _command_stress(args: argparse.Namespace) -> int:
    """Run the whole robustness battery and record every row."""
    from amat.backtest.runner import BacktestResult  # noqa: PLC0415
    from amat.reporting.insights import _coalesce_round_trips, replay_trades  # noqa: PLC0415
    from amat.validation.stress import run_stress_battery  # noqa: PLC0415

    started_at = dt.datetime.now(dt.UTC)
    (
        resolved,
        strategy,
        panel,
        manifest,
        actions,
        declaration,
        holdout_accessed,
        holdout_reason,
        say,
    ) = _load_measurement_inputs(args)

    def load_round_trips(result: BacktestResult) -> list[object]:
        # Supplied from here because the replay/coalesce implementation lives
        # in the reporting layer, below which the validation module sits.
        return list(_coalesce_round_trips(replay_trades(result, panel, actions_by_session=actions)))

    study = run_stress_battery(
        strategy,
        panel,
        initial_cash=args.initial_cash,
        actions_by_session=actions,
        round_trip_loader=load_round_trips,  # type: ignore[arg-type]
        progress=say,
    )

    payload: dict[str, object] = {
        "stress": study.as_dict(),
        "parameters": {
            "initial_cash": str(args.initial_cash),
            "start": args.start.isoformat() if args.start else None,
            "end": args.end.isoformat() if args.end else None,
        },
        "holdout": {
            "declared_boundary": (
                declaration.boundary_session.isoformat() if declaration else None
            ),
            "accessed": holdout_accessed,
            "reason": holdout_reason,
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        _print_stress(study)

    _write_run_record(
        args,
        command="stress",
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=resolved.content_sha256,
        config_source=str(resolved.source_path) if resolved.source_path else None,
        execution=strategy.execution,
        costs=strategy.costs,
        snapshot_manifest=manifest,
        sections=payload,
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_reason,
        say=say,
    )
    return _EXIT_OK


def _command_heavytail(args: argparse.Namespace) -> int:
    """Calibrate to the snapshot, generate heavy-tailed markets, run the strategy.

    The snapshot is read for *calibration only* -- per-symbol price level,
    volatility, beta and the correlation matrix. No observed return sequence
    reaches the strategy, so this is not a backtest and its output is never a
    measured result; every artifact says so.
    """
    from amat.validation.heavytail import (  # noqa: PLC0415
        calibrate_market,
        render_heavy_tail_text,
        run_heavy_tail_study,
    )

    started_at = dt.datetime.now(dt.UTC)
    (
        resolved,
        strategy,
        panel,
        manifest,
        _actions,
        declaration,
        holdout_accessed,
        holdout_reason,
        say,
    ) = _load_measurement_inputs(args)

    calibration = calibrate_market(panel)
    say(
        f"calibrated {len(calibration.symbols)} symbols on {calibration.sessions_observed} "
        f"sessions: volatility {calibration.annualized_volatility.min():.1%}"
        f"-{calibration.annualized_volatility.max():.1%}, beta "
        f"{calibration.market_betas.min():.2f}-{calibration.market_betas.max():.2f}"
    )
    say(
        f"generating {args.scenarios} markets of {args.sessions} sessions at Pareto "
        f"alpha={args.tail_index} (SYNTHETIC — not a backtest)"
    )

    study = run_heavy_tail_study(
        strategy,
        calibration,
        scenarios=args.scenarios,
        sessions=args.sessions,
        tail_index=args.tail_index,
        drift_annual=args.drift_annual,
        initial_cash=args.initial_cash,
        seed=args.seed,
        progress=say,
    )

    payload: dict[str, object] = {
        "heavy_tail": study.as_dict(),
        "parameters": {
            "initial_cash": str(args.initial_cash),
            "scenarios": args.scenarios,
            "sessions": args.sessions,
            "tail_index": args.tail_index,
            "drift_annual": args.drift_annual,
            "seed": args.seed,
        },
        "holdout": {
            "declared_boundary": (
                declaration.boundary_session.isoformat() if declaration else None
            ),
            "accessed": holdout_accessed,
            "reason": holdout_reason,
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(render_heavy_tail_text(study))

    _write_run_record(
        args,
        command="stress-heavytail",
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=resolved.content_sha256,
        config_source=str(resolved.source_path) if resolved.source_path else None,
        execution=strategy.execution,
        costs=strategy.costs,
        snapshot_manifest=manifest,
        sections=payload,
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_reason,
        say=say,
    )
    return _EXIT_OK


def _print_stress(study: object) -> None:
    """Every stress family, one aligned block per family."""
    from amat.validation.stress import StressStudy  # noqa: PLC0415

    assert isinstance(study, StressStudy)  # noqa: S101 - narrowing
    print()
    print(f"STRESS BATTERY — {study.strategy}")
    print(f"  ! {study.caveat}")

    def block(title: str, runs: Sequence[object]) -> None:
        from amat.validation.stress import StressRun  # noqa: PLC0415

        print()
        print(f"  {title}")
        header = (
            f"  {'RUN':<52}{'TOTAL':>10}{'ANN':>9}{'VOL':>8}"
            f"{'SHARPE':>8}{'MAXDD':>9}{'FILLS':>7}{'MISSED':>7}{'COSTS':>10}"
        )
        print(header)
        print("  " + "-" * (len(header) - 2))
        for run in runs:
            assert isinstance(run, StressRun)  # noqa: S101
            print(
                f"  {run.name:<52}{run.total_return:>+10.2%}{run.annualized_return:>+9.2%}"
                f"{run.annualized_volatility:>8.2%}{run.sharpe:>8.2f}{run.max_drawdown:>+9.2%}"
                f"{run.fills:>7d}{run.missed:>7d}{run.total_costs:>10,.0f}"
            )

    block("BASELINE", [study.baseline])
    block("COST LADDER", list(study.cost_ladder))
    block("EXECUTION DELAYS", list(study.delays))
    block("MISSED-FILL INJECTION", list(study.missed_fill_injections))
    block("EXECUTION ALTERNATIVE", [study.close_fill])
    block("SIGNAL-FREE ALLOCATOR CONTROL", [study.signal_free])
    block("LEAVE-ONE-OUT (survivorship bound)", list(study.leave_one_out))

    print()
    print("  CONCENTRATION RESTATEMENTS (additive, not re-runs)")
    for entry in (*study.trade_removals, *study.year_removals):
        share = (
            f"{entry.removed_share_of_net_profit:.1%} of net profit"
            if math.isfinite(entry.removed_share_of_net_profit)
            else "share n/a"
        )
        print(
            f"    {entry.name}: restated total {entry.restated_total_return:+.2%} "
            f"({share}; removed: {entry.removed})"
        )

    if study.correlations:
        print()
        print("  BENCHMARK CORRELATION (session returns)")
        for name, value in study.correlations.items():
            print(f"    vs {name}: {value:+.3f}")
    print()


def _print_projection(study: object) -> None:
    """The horizon tables, printed the same way every run."""
    from amat.validation.projection import ProjectionStudy  # noqa: PLC0415

    assert isinstance(study, ProjectionStudy)  # noqa: S101 - narrowing
    print()
    print(
        f"PROJECTION — {study.strategy}; {study.paths} resampled paths from "
        f"{study.source_sessions} realized sessions, mean block "
        f"{study.mean_block_sessions}, base seed {study.seed}, funding {study.funding:,.0f}"
    )
    print(f"  ! {study.caveat}")
    print()
    columns = "".join(f"{name:>12}" for name in ("p05", "p25", "p50", "p75", "p95"))
    header = f"  {'HORIZON':<9}{'METRIC':<20}{columns}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    keys = ("p05", "p25", "p50", "p75", "p95")
    for horizon in study.horizons:
        rows = (
            ("terminal equity", horizon.terminal_equity, "{:,.0f}"),
            ("total return", horizon.total_return, "{:+.2%}"),
            ("annualized return", horizon.annualized_return, "{:+.2%}"),
            ("max drawdown", horizon.max_drawdown, "{:+.2%}"),
        )
        for position, (name, values, fmt) in enumerate(rows):
            label = horizon.label if position == 0 else ""
            cells = "".join(f"{fmt.format(values[key]):>12}" for key in keys)
            print(f"  {label:<9}{name:<20}{cells}")
        print(
            f"  {'':<9}P(end < funding) {horizon.probability_below_funding:>7.1%}    "
            f"P(drawdown < -20%) {horizon.probability_drawdown_worse_20pct:>7.1%}"
        )
        print()


def _print_montecarlo(study: object) -> None:
    """The percentile and probability tables, printed the same way every run."""
    from amat.validation.montecarlo import MonteCarloStudy  # noqa: PLC0415

    assert isinstance(study, MonteCarloStudy)  # noqa: S101 - narrowing
    print()
    print(
        f"MONTE CARLO — {study.strategy}; {study.paths} stationary-bootstrap paths, "
        f"mean block {study.mean_block_sessions} sessions, base seed {study.seed}"
    )
    print(f"  ! {study.caveat}")
    print()
    header = (
        f"  {'METRIC':<24}{'p05':>10}{'p25':>10}{'p50':>10}{'p75':>10}{'p95':>10}"
        f"{'OBSERVED':>11}{'OBS %ILE':>10}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    percent = {"total_return", "annualized_return", "annualized_volatility", "max_drawdown"}
    for metric, row in study.metric_percentiles.items():

        def form(value: float, name: str = metric) -> str:
            return f"{value:+.2%}" if name in percent else f"{value:.2f}"

        print(
            f"  {metric:<24}{form(row['p05']):>10}{form(row['p25']):>10}"
            f"{form(row['p50']):>10}{form(row['p75']):>10}{form(row['p95']):>10}"
            f"{form(getattr(study.observed, metric)):>11}"
            f"{study.observed_percentile[metric]:>10.0%}"
        )
    print()
    print(
        f"  resampled paths ending below initial funding: "
        f"{study.probabilities['negative_total_return']:.1%}"
    )
    print(
        f"  resampled paths with drawdown worse than 20%: "
        f"{study.probabilities['max_drawdown_worse_than_20pct']:.1%}"
    )


def _command_regime_experiment(args: argparse.Namespace) -> int:
    """The registered volatility-state conditioning experiment, judged by controls."""
    import numpy as np  # noqa: PLC0415

    from amat.reporting.artifacts import conditioning_battery_payload  # noqa: PLC0415
    from amat.validation.falsification import falsify_conditioning  # noqa: PLC0415
    from amat.validation.regimes import volatility_state_scalars  # noqa: PLC0415

    started_at = dt.datetime.now(dt.UTC)
    (
        resolved,
        strategy,
        panel,
        manifest,
        actions,
        declaration,
        holdout_accessed,
        holdout_reason,
        say,
    ) = _load_measurement_inputs(args)

    scalars = volatility_state_scalars(
        panel,
        reference=args.reference,
        vol_window=args.vol_window,
        quantile=args.quantile,
        reduced_scalar=args.reduced_scalar,
    )
    reduced_share = float(np.mean(scalars < 1.0))
    say(
        f"scalars: mean {float(scalars.mean()):.3f}, reduced on {reduced_share:.1%} "
        f"of sessions ({args.reference} vol > expanding q{args.quantile:.2f})"
    )
    say("falsification: baseline, candidate, and 3 matched controls (5 backtests)")
    battery = falsify_conditioning(
        strategy,
        panel,
        strategy.to_universe(),
        scalars,
        initial_cash=args.initial_cash,
        actions_by_session=actions,
        seed=args.seed,
        candidate_name="vol-state conditioned",
    )

    payload: dict[str, object] = {
        "conditioning_falsification": conditioning_battery_payload(battery),
        "parameters": {
            "reference": args.reference,
            "vol_window": args.vol_window,
            "quantile": args.quantile,
            "reduced_scalar": args.reduced_scalar,
            "control_seed": args.seed,
            "initial_cash": str(args.initial_cash),
            "reduced_session_share": reduced_share,
        },
        "holdout": {
            "declared_boundary": (
                declaration.boundary_session.isoformat() if declaration else None
            ),
            "accessed": holdout_accessed,
            "reason": holdout_reason,
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        _print_scalar_falsification(battery, difference="conditioning scalar series")
    _write_run_record(
        args,
        command="regime-experiment",
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=resolved.content_sha256,
        config_source=str(resolved.source_path) if resolved.source_path else None,
        execution=strategy.execution,
        costs=strategy.costs,
        snapshot_manifest=manifest,
        sections=payload,
        seeds=(args.seed,),
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_reason,
        say=say,
    )
    return _EXIT_OK


def _command_sweep_target_vol(args: argparse.Namespace) -> int:
    """The registered volatility-target sweep, ranked on training folds only."""
    from amat.validation.sweep import sweep_target_volatility  # noqa: PLC0415

    started_at = dt.datetime.now(dt.UTC)
    (
        resolved,
        strategy,
        panel,
        manifest,
        actions,
        declaration,
        holdout_accessed,
        holdout_reason,
        say,
    ) = _load_measurement_inputs(args)

    targets = sorted({*args.targets, strategy.constraints.target_annualized_volatility})
    say(
        f"sweep: {len(targets)} targets x 2 runs each (training through "
        f"{args.train_end}, then full history for the validation slice)"
    )
    sweep = sweep_target_volatility(
        strategy,
        panel,
        train_end=args.train_end,
        targets=args.targets,
        initial_cash=args.initial_cash,
        actions_by_session=actions,
    )

    payload: dict[str, object] = {
        "target_volatility_sweep": {
            "strategy": sweep.strategy,
            "train_end": sweep.train_end.isoformat(),
            "baseline_target": sweep.baseline_target,
            "caveat": sweep.caveat,
            "verdict": sweep.verdict,
            "winner": sweep.winner.name,
            "winner_is_measured": sweep.winner_is_measured,
            "winner_is_tied": sweep.winner_is_tied,
            "winner_holds_on_validation": sweep.winner_holds_on_validation,
            "candidates": [
                {
                    "name": row.name,
                    "target_volatility": row.target_volatility,
                    "training": dataclasses.asdict(row.training),
                    "training_fold_score": (
                        row.training_fold_score if math.isfinite(row.training_fold_score) else None
                    ),
                    "validation": dataclasses.asdict(row.validation),
                }
                for row in sweep.candidates
            ],
        },
        "parameters": {"initial_cash": str(args.initial_cash)},
        "holdout": {
            "declared_boundary": (
                declaration.boundary_session.isoformat() if declaration else None
            ),
            "accessed": holdout_accessed,
            "reason": holdout_reason,
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        _print_sweep(sweep)
    _write_run_record(
        args,
        command="sweep-target-vol",
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=resolved.content_sha256,
        config_source=str(resolved.source_path) if resolved.source_path else None,
        execution=strategy.execution,
        costs=strategy.costs,
        snapshot_manifest=manifest,
        sections=payload,
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_reason,
        say=say,
    )
    return _EXIT_OK


def _print_sweep(sweep: object) -> None:
    """The sweep table, ranked rows first, printed the same way every run."""
    from amat.validation.sweep import TargetVolatilitySweep  # noqa: PLC0415

    assert isinstance(sweep, TargetVolatilitySweep)  # noqa: S101 - narrowing
    print()
    print(
        f"TARGET-VOLATILITY SWEEP — {sweep.strategy}; split fixed at "
        f"{sweep.train_end}; ranking uses TRAINING folds only"
    )
    print(f"  ! {sweep.caveat}")
    header = (
        f"  {'TARGET':>7}  {'— TRAINING: ANN':>16}{'VOL':>8}{'SHARPE':>8}{'MAXDD':>9}"
        f"{'TRADES':>8}{'FOLDSCORE':>11}  {'— VALIDATION: ANN':>18}{'SHARPE':>8}{'MAXDD':>9}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for row in sweep.ranked:
        marker = "*" if row.target_volatility == sweep.baseline_target else " "
        score = (
            f"{row.training_fold_score:>11.3f}"
            if math.isfinite(row.training_fold_score)
            else f"{'unmeasured':>11}"
        )
        print(
            f"  {row.target_volatility:>6.0%}{marker} {row.training.annualized_return:>+16.2%}"
            f"{row.training.annualized_volatility:>8.2%}{row.training.sharpe:>8.2f}"
            f"{row.training.max_drawdown:>9.2%}{row.training.trades:>8}{score}"
            f"  {row.validation.annualized_return:>+18.2%}{row.validation.sharpe:>8.2f}"
            f"{row.validation.max_drawdown:>9.2%}"
        )
    print("  (* = the frozen configuration's own target)")
    print(f"\n  VERDICT: {sweep.verdict}")


def _command_sweep_exit_rules(args: argparse.Namespace) -> int:
    """The registered stop-loss/take-profit sweep, ranked on training folds only."""
    from amat.validation.exitsweep import sweep_exit_rules  # noqa: PLC0415

    started_at = dt.datetime.now(dt.UTC)
    (
        resolved,
        strategy,
        panel,
        manifest,
        actions,
        declaration,
        holdout_accessed,
        holdout_reason,
        say,
    ) = _load_measurement_inputs(args)

    combos = (len(args.stop_losses) + 1) * (len(args.take_profits) + 1)
    say(
        f"exit-rule sweep: {combos} combinations x 2 runs each (training through "
        f"{args.train_end}, then full history for the validation slice)"
    )
    sweep = sweep_exit_rules(
        strategy,
        panel,
        train_end=args.train_end,
        stop_losses=args.stop_losses,
        take_profits=args.take_profits,
        reentry_embargo_sessions=args.embargo_sessions,
        initial_cash=args.initial_cash,
        actions_by_session=actions,
    )

    payload: dict[str, object] = {
        "exit_rules_sweep": {
            "strategy": sweep.strategy,
            "train_end": sweep.train_end.isoformat(),
            "reentry_embargo_sessions": sweep.reentry_embargo_sessions,
            "caveat": sweep.caveat,
            "verdict": sweep.verdict,
            "winner": sweep.winner.name,
            "winner_is_measured": sweep.winner_is_measured,
            "winner_is_tied": sweep.winner_is_tied,
            "winner_holds_on_validation": sweep.winner_holds_on_validation,
            "candidates": [
                {
                    "name": row.name,
                    "stop_loss": row.stop_loss,
                    "take_profit": row.take_profit,
                    "training": dataclasses.asdict(row.training),
                    "training_fold_score": (
                        row.training_fold_score if math.isfinite(row.training_fold_score) else None
                    ),
                    "validation": dataclasses.asdict(row.validation),
                }
                for row in sweep.candidates
            ],
        },
        "parameters": {"initial_cash": str(args.initial_cash)},
        "holdout": {
            "declared_boundary": (
                declaration.boundary_session.isoformat() if declaration else None
            ),
            "accessed": holdout_accessed,
            "reason": holdout_reason,
        },
    }

    control = None
    if args.random_control is not None:
        from amat.validation.exitsweep import random_exit_control  # noqa: PLC0415

        say(
            f"random-exit control: {args.random_control} seeded uniform draws "
            f"(seed {args.control_seed}) x 2 runs each"
        )
        control = random_exit_control(
            strategy,
            panel,
            train_end=args.train_end,
            draws=args.random_control,
            seed=args.control_seed,
            reentry_embargo_sessions=args.embargo_sessions,
            initial_cash=args.initial_cash,
            actions_by_session=actions,
        )
        payload["random_exit_control"] = {
            "seed": control.seed,
            "caveat": control.caveat,
            "fraction_beating_baseline_on_validation": (
                control.fraction_beating_baseline_on_validation
            ),
            "baseline_validation_sharpe": control.baseline.validation.sharpe,
            "draws": [
                {
                    "name": draw.name,
                    "stop_loss": draw.stop_loss,
                    "take_profit": draw.take_profit,
                    "training_sharpe": draw.training.sharpe,
                    "validation_sharpe": draw.validation.sharpe,
                    "validation_annualized_return": draw.validation.annualized_return,
                }
                for draw in control.draws
            ],
        }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        _print_exit_sweep(sweep)
        if control is not None:
            _print_random_exit_control(control)
    _write_run_record(
        args,
        command="sweep-exit-rules",
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=resolved.content_sha256,
        config_source=str(resolved.source_path) if resolved.source_path else None,
        execution=strategy.execution,
        costs=strategy.costs,
        snapshot_manifest=manifest,
        sections=payload,
        started_at=started_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_reason,
        say=say,
    )
    return _EXIT_OK


def _print_random_exit_control(control: object) -> None:
    """The random-draw control block, printed after the ranked grid."""
    from amat.validation.exitsweep import RandomExitControl  # noqa: PLC0415

    assert isinstance(control, RandomExitControl)  # noqa: S101 - narrowing
    print()
    print(
        f"RANDOM-EXIT CONTROL — {len(control.draws)} uniform draws, seed {control.seed}; "
        f"no-exit validation Sharpe {control.baseline.validation.sharpe:.2f}"
    )
    print(f"  ! {control.caveat}")
    print(
        f"  draws beating the no-exit core on validation Sharpe: "
        f"{control.fraction_beating_baseline_on_validation:.1%}"
    )
    header = f"  {'RULES':<18}{'TRAIN SHARPE':>13}{'VALID SHARPE':>13}{'VALID ANN':>11}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    ordered = sorted(control.draws, key=lambda draw: draw.validation.sharpe, reverse=True)
    for draw in ordered:
        print(
            f"  {draw.name:<18}{draw.training.sharpe:>13.2f}"
            f"{draw.validation.sharpe:>13.2f}{draw.validation.annualized_return:>+11.2%}"
        )
    print()


def _print_exit_sweep(sweep: object) -> None:
    """The exit-rule table, ranked rows first, printed the same way every run."""
    from amat.validation.exitsweep import ExitRulesSweep  # noqa: PLC0415

    assert isinstance(sweep, ExitRulesSweep)  # noqa: S101 - narrowing
    print()
    print(
        f"EXIT-RULE SWEEP — {sweep.strategy}; split fixed at {sweep.train_end}; "
        f"embargo {sweep.reentry_embargo_sessions} sessions; ranking uses TRAINING folds only"
    )
    print(f"  ! {sweep.caveat}")
    header = (
        f"  {'RULES':<22}  {'— TRAINING: ANN':>16}{'VOL':>8}{'SHARPE':>8}{'MAXDD':>9}"
        f"{'TRADES':>8}{'FOLDSCORE':>11}  {'— VALIDATION: ANN':>18}{'SHARPE':>8}{'MAXDD':>9}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for row in sweep.ranked:
        marker = "*" if row.is_baseline else " "
        score = (
            f"{row.training_fold_score:>11.3f}"
            if math.isfinite(row.training_fold_score)
            else f"{'unmeasured':>11}"
        )
        print(
            f"  {row.name:<21}{marker} {row.training.annualized_return:>+16.2%}"
            f"{row.training.annualized_volatility:>8.2%}{row.training.sharpe:>8.2f}"
            f"{row.training.max_drawdown:>9.2%}{row.training.trades:>8}{score}"
            f"  {row.validation.annualized_return:>+18.2%}{row.validation.sharpe:>8.2f}"
            f"{row.validation.max_drawdown:>9.2%}"
        )
    print("  (* = the frozen no-exit core)")
    print(f"\n  VERDICT: {sweep.verdict}")


def _command_simulate(args: argparse.Namespace) -> int:
    """Step the simulated paper account against current market data."""
    from amat.data.alpaca import AlpacaMarketData  # noqa: PLC0415
    from amat.simulation import SimulatedLiveAccount  # noqa: PLC0415

    # The v6-aware loader, so the research candidate can serve its paper
    # period (v6-audit "required next evidence" item 2). For canonical v6
    # this simulates the static 6% core exactly -- the capital-control
    # overlay is disabled there and simulate never applies one.
    resolved = _load_any_strategy_config(args.config)
    account = SimulatedLiveAccount(
        resolved.strategy,
        snapshot_directory=args.snapshot,
        config_sha256=resolved.content_sha256,
        client=None if args.offline else AlpacaMarketData(),
        initial_equity=args.equity,
        journal_directory=args.journal,
    )
    report = account.step()

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
        return _EXIT_OK

    print(f"SIMULATED PAPER ACCOUNT — {report.strategy_name} v{report.strategy_version}")
    for caveat in report.caveats:
        print(f"  ! {caveat}")
    print()
    print(f"  as of session      {report.as_of_session}")
    print(f"  snapshot           {report.snapshot_id} (+{report.appended_sessions} live)")
    print(f"  equity             {report.equity:,.2f} (from {report.initial_equity:,.2f})")
    print(f"  cash               {report.cash:,.2f}")
    print(f"  gross exposure     {report.gross_exposure:.2%}")
    print(f"  total return       {report.performance.total_return:+.2%}")
    print(f"  max drawdown       {report.performance.max_drawdown:.2%}")
    print()
    if report.positions:
        print("  HOLDINGS (weight of equity)")
        for symbol, weight in sorted(report.positions.items(), key=lambda kv: -kv[1]):
            print(f"    {symbol:<6}{weight:>8.2%}")
    else:
        print("  HOLDINGS: none — the account is fully in cash.")
    return _EXIT_OK


def _load_plan_inputs(
    args: argparse.Namespace,
    resolved: ResolvedConfig,
    generated_at: dt.datetime,
) -> tuple[
    StrategyConfig,
    MarketPanel,
    SnapshotManifest,
    int,
    str,
    tuple[str, ...],
    tuple[str, ...],
]:
    """Resolve an optional news overlay and its aligned price history."""
    from amat.data.alpaca import AlpacaMarketData  # noqa: PLC0415
    from amat.data.news_assets import load_news_plan_panel  # noqa: PLC0415
    from amat.news_assets import (  # noqa: PLC0415
        effective_config_digest,
        expand_strategy_with_news_assets,
        load_news_asset_overlay,
    )

    base_strategy = resolved.strategy
    base_symbols = tuple(str(symbol) for symbol in base_strategy.universe.symbols)
    overlay = (
        load_news_asset_overlay(
            args.news_assets,
            now=generated_at,
            base_symbols=base_symbols,
        )
        if args.news_assets is not None
        else None
    )
    requested = overlay.symbols if overlay is not None else ()
    panel_result = load_news_plan_panel(
        args.snapshot,
        list(base_symbols),
        requested,
        strategy=base_strategy,
        client=None if args.offline else AlpacaMarketData(),
    )
    panel = panel_result.panel
    manifest = panel_result.manifest
    appended = panel_result.appended_sessions
    effective_overlay = overlay
    caveats: list[str] = []
    if overlay is not None:
        effective_overlay = dataclasses.replace(
            overlay,
            candidates=tuple(
                candidate
                for candidate in overlay.candidates
                if candidate.symbol in panel_result.accepted
            ),
        )
        caveats.append(
            "NEWS UNIVERSE OVERLAY: positive news controls temporary eligibility only; "
            "the price-trend signal and all portfolio caps still determine trades. "
            f"artifact sha256 {overlay.source_sha256[:16]}, expires "
            f"{overlay.expires_at.isoformat()}."
        )
        if effective_overlay.candidates:
            detail = ", ".join(
                f"{candidate.symbol} ({candidate.theme}, {candidate.sentiment:+.2f})"
                for candidate in effective_overlay.candidates
                if candidate.active
            )
            if detail:
                caveats.append(
                    f"News candidates admitted to the 10% gross-capped cluster: {detail}."
                )
            if effective_overlay.retired_symbols:
                caveats.append(
                    "Retired news candidates are recognized exit-only and forced to zero: "
                    f"{', '.join(effective_overlay.retired_symbols)}."
                )
        for symbol, reason in panel_result.rejected.items():
            caveats.append(f"News candidate {symbol} was not admitted: {reason}.")
    strategy = (
        expand_strategy_with_news_assets(base_strategy, effective_overlay)
        if effective_overlay is not None
        else base_strategy
    )
    return (
        strategy,
        panel,
        manifest,
        appended,
        effective_config_digest(resolved.content_sha256, overlay),
        tuple(caveats),
        effective_overlay.retired_symbols if effective_overlay is not None else (),
    )


def _command_plan(args: argparse.Namespace) -> int:
    """Emit the execution plan formed from the latest completed session."""
    from amat.planning import build_plan  # noqa: PLC0415

    # The v6-aware loader, for the same reason `simulate` uses it: the research
    # candidate must be able to form a plan. For canonical v6 this plans the
    # static 6% core exactly -- `capital_control_enabled` is false there and the
    # overlay is a runner argument `plan` never passes.
    resolved = _load_any_strategy_config(args.config)
    generated_at = dt.datetime.now(tz=dt.UTC)
    (
        strategy,
        panel,
        manifest,
        appended,
        config_digest,
        caveats,
        forced_zero_symbols,
    ) = _load_plan_inputs(args, resolved, generated_at)

    revision = detect_code_revision(Path.cwd())
    plan = build_plan(
        strategy,
        panel,
        strategy.to_universe(),
        engine=args.engine,
        current_positions=_load_positions(args.positions),
        account_equity=args.equity,
        config_sha256=config_digest,
        snapshot_id=manifest.snapshot_id,
        code_revision=revision.commit or f"<undetected: {revision.reason}>",
        appended_sessions=appended,
        generated_at=generated_at,
        extra_caveats=caveats,
        forced_zero_symbols=forced_zero_symbols,
    )

    payload = json.dumps(plan.as_dict(), indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")

    if args.json:
        print(payload)
        return _EXIT_OK

    print(f"EXECUTION PLAN — {plan.strategy_name} v{plan.strategy_version} [{plan.engine}]")
    for caveat in plan.caveats:
        print(f"  ! {caveat}")
    print()
    print(f"  as of session      {plan.as_of_session}")
    print(f"  execute in the first session on or after {plan.earliest_execution_session}")
    print(
        f"  window             {plan.window_start_local} to {plan.window_end_local} "
        f"{plan.exchange_timezone}"
    )
    print(f"  expires            {plan.expires_at.isoformat()}")
    print(f"  snapshot           {plan.snapshot_id} (+{plan.appended_sessions} live)")
    print(f"  config             {plan.config_sha256[:16]}")
    print(f"  account equity     {plan.account_equity:,.2f}")
    print(f"  gross target       {plan.gross_target_exposure:.2%}")
    print()

    if plan.intents:
        print("  TRADES")
        print(f"    {'SYM':<6}{'SIDE':<6}{'QUANTITY':>14}{'NOTIONAL':>16}{'NOW':>9}{'TARGET':>9}")
        for intent in plan.intents:
            print(
                f"    {intent.symbol:<6}{intent.side:<6}{intent.quantity:>14,.4f}"
                f"{intent.notional:>16,.2f}{intent.current_weight:>9.2%}"
                f"{intent.target_weight:>9.2%}"
            )
    else:
        print("  TRADES: none — the account already matches the target.")
    print()

    if plan.withheld:
        print("  WITHHELD (formed, then declined)")
        for item in plan.withheld:
            print(f"    {item.symbol:<6}{item.side:<6}{item.quantity:>14,.4f}  {item.reason}")
        print()
    if plan.unrecognized_holdings:
        print(f"  UNRECOGNIZED HOLDINGS: {', '.join(plan.unrecognized_holdings)}")
        print("    Not traded by this plan. Resolve before acting on it.")
        print()
    if args.out is not None:
        print(f"  written to {args.out}")
    return _EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    refusal = _refuse_non_backtest_environment()
    if refusal is not None:
        print(f"amat: refused: {refusal}", file=sys.stderr)
        return _EXIT_REFUSED

    parser = build_parser()
    args = parser.parse_args(argv)

    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "validate-config": lambda ns: _command_validate_config(ns.config),
        "show-config": lambda ns: _command_show_config(ns.config),
        "show-universe": lambda ns: _command_show_universe(ns.config),
        "show-layout": lambda ns: _command_show_layout(ns.root),
        "show-environment": lambda _: _command_show_environment(),
        "backtest-synthetic": _command_backtest_synthetic,
        "backtest": _command_backtest,
        "report": _command_report,
        "study": _command_study,
        "study-v4": _command_study_v4,
        "walkforward": _command_walkforward,
        "declare-holdout": _command_declare_holdout,
        "montecarlo": _command_montecarlo,
        "project": _command_project,
        "stress": _command_stress,
        "stress-heavytail": _command_heavytail,
        "regime-experiment": _command_regime_experiment,
        "sweep-target-vol": _command_sweep_target_vol,
        "sweep-exit-rules": _command_sweep_exit_rules,
        "simulate": _command_simulate,
        "plan": _command_plan,
        "scan": _command_scan,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.error(f"unknown command: {args.command!r}")
        return _EXIT_REFUSED

    try:
        return handler(args)
    except AmatError as exc:
        print(f"amat: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
