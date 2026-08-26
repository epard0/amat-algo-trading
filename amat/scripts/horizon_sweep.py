"""Trailing-horizon comparison across strategy versions, on one consistent basis.

The question this answers is "how did each version do over the last 6 months,
1, 2, 5 and 10 years?" -- and the only defensible way to answer it is to run
each version ONCE over the full snapshot and slice the resulting equity curve.
Two properties follow from that choice and neither survives the alternative
(one windowed re-run per horizon):

1. **Warm-up and positions are carried in.** A windowed re-run starts flat and
   spends its first ``warmup.min_completed_sessions`` sessions holding cash.
   Those flat sessions land inside the reported window, deflating volatility
   and annualized return by an amount that depends on the window length -- so
   the horizons would not be comparable to each other, let alone across
   versions. Slicing a single curve reports the strategy as an investor holding
   it would have experienced: already invested on day one of the window.
2. **Every horizon is measured by the same code on the same sample.** Each row
   below is :func:`amat.validation.optimize.window_statistics` over a slice of
   one curve. No per-horizon estimator, no per-version estimator.

On hindsight bias, stated plainly. Every window *ends* on the snapshot's last
session -- the same session for every version and every horizon -- and every
start is derived mechanically from that end by subtracting calendar months.
Nothing here searches over start dates, and no configuration is chosen from
what the windows show: the versions are the ones already registered, frozen
before this ran. What this does NOT escape is that the whole sample is burned
history that the frozen configuration was developed on; a trailing window is a
restatement of that sample, not out-of-sample evidence. The recent windows in
particular are short (a 6-month Sharpe is dominated by its own noise) and are
reported because they were asked for, not because they are informative.

Usage::

    uv run python scripts/horizon_sweep.py
    uv run python scripts/horizon_sweep.py --versions v1 v6 --initial-cash 250000

Output: a JSON artifact under ``outputs/experiments/`` plus a printed table.
Measurement only -- this script changes nothing and selects nothing.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import BacktestResult, run_backtest
from amat.backtest.stances import assemble_stance, blend_series
from amat.config.loading import load_strategy_config
from amat.config.switching import load_switching_config
from amat.config.v6 import load_v6_config
from amat.data.scalars import load_weight_scalars
from amat.data.snapshot import load_corporate_actions, load_panel
from amat.domain.types import Symbol
from amat.regime.asset import build_asset_regime_history
from amat.validation.optimize import window_statistics
from amat.validation.walkforward import (
    assert_respects_holdout,
    default_holdout_root,
    load_holdout_declaration,
)

#: Strategy version -> checked-in configuration. v2 is absent on purpose: it was
#: rejected 2026-08-10 and its runner removed; the registry entry is the record.
VERSION_CONFIGS: dict[str, Path] = {
    "v1": Path("configs/strategy/amat_v1.toml"),
    "v3": Path("configs/strategy/amat_v3.toml"),
    "v4": Path("configs/strategy/amat_v4.toml"),
    "v5": Path("configs/strategy/amat_v5.toml"),
    "v6": Path("configs/strategy/amat_v6.toml"),
}

#: Horizon label -> calendar months subtracted from the common end session.
#: Calendar months, not session counts, so "1y" means the same span of market
#: time in every version regardless of how many sessions that span contained.
HORIZON_MONTHS: dict[str, int] = {
    "6m": 6,
    "1y": 12,
    "2y": 24,
    "5y": 60,
    "10y": 120,
}

#: Per-version notes that belong beside every number the version produces.
VERSION_NOTES: dict[str, str] = {
    "v1": "Frozen trend core; the promotion candidate.",
    "v3": (
        "v1 conditioned by a news attention scalar series. The scalars ARE the "
        "strategy: the configuration alone differs from v1 only in name and "
        "version string, so a v3 row run without --scalars is v1 under a v3 "
        "label and is recorded as such in this artifact."
    ),
    "v4": "Stance switching (trend blended toward a defensive stance).",
    "v5": "Breadth universe. Rejected 2026-08-10; still measurable.",
    "v6": (
        "Research overlay: static 6% volatility target plus the causal "
        "regime/liquidity capital control. Research only, not promoted."
    ),
}

_SNAPSHOT_ROOT = Path("data/snapshots")
_ARTIFACT_ROOT = Path("outputs/experiments")

#: A return series needs two equity marks to exist at all; ``window_statistics``
#: rejects anything shorter, so a window below this is refused here by name.
_MIN_WINDOW_SESSIONS = 2


def _newest_snapshot(root: Path = _SNAPSHOT_ROOT) -> Path:
    """The lexically newest snapshot directory (names are date-stamped)."""
    candidates = sorted(path for path in root.iterdir() if path.is_dir())
    if not candidates:
        message = f"no snapshot directories under {root}"
        raise SystemExit(f"horizon_sweep: {message}")
    return candidates[-1]


def _months_before(anchor: dt.date, months: int) -> dt.date:
    """``anchor`` minus ``months`` calendar months, clamping a short month.

    Written out rather than pulled from dateutil: it is three lines against the
    standard library, the dependency is not in the lockfile, and clamping
    31 -> 28/30 is the only edge case a trailing window can hit.
    """
    total = (anchor.year * 12 + anchor.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    return dt.date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def _run_version(
    version: str,
    snapshot: Path,
    *,
    initial_cash: Decimal,
    scalars: Path | None,
    say: Any,
) -> tuple[BacktestResult, str, str, dict[str, Any] | None]:
    """Run one version over the full snapshot; return (result, snapshot_id, sha).

    Each branch mirrors the corresponding ``amat`` command's own combined-run
    call exactly -- same loader, same overlay construction, same ``run_backtest``
    arguments -- so a row here is the same number that command would print.
    Sleeves are skipped: they are per-asset diagnostics and contribute nothing
    to a combined-portfolio horizon table.
    """
    config_path = VERSION_CONFIGS[version]

    if version == "v4":
        resolved = load_switching_config(config_path)
        config = resolved.strategy
        strategy = config.trend
        symbols = [str(symbol) for symbol in strategy.universe.symbols]
        panel, manifest = load_panel(snapshot, symbols)
        _guard_holdout(panel, say)
        actions = load_corporate_actions(snapshot, symbols, sessions=panel.sessions)
        stance = assemble_stance(config, panel, weights=blend_series(config, panel))
        result = run_backtest(
            strategy,
            panel.slice_symbols([Symbol(s) for s in strategy.to_universe().symbols]),
            strategy.to_universe(),
            initial_cash=initial_cash,
            actions_by_session=actions,
            stance=stance,
        )
        return result, manifest.snapshot_id, resolved.content_sha256, None

    if version == "v6":
        resolved_v6 = load_v6_config(config_path)
        v6_config = resolved_v6.config
        strategy = v6_config.strategy
        symbols = [str(symbol) for symbol in strategy.universe.symbols]
        panel, manifest = load_panel(snapshot, symbols, with_context=True)
        _guard_holdout(panel, say)
        actions = load_corporate_actions(snapshot, symbols, sessions=panel.sessions)
        regimes = build_asset_regime_history(panel, v6_config.asset_regime, v6_config.liquidity)
        overlay = regimes.overlay if v6_config.asset_regime.capital_control_enabled else None
        result = run_backtest(
            strategy,
            panel.slice_symbols([Symbol(s) for s in strategy.to_universe().symbols]),
            strategy.to_universe(),
            initial_cash=initial_cash,
            actions_by_session=actions,
            decision_overlay=overlay,
        )
        return result, manifest.snapshot_id, resolved_v6.content_sha256, None

    resolved_plain = load_strategy_config(config_path)
    strategy = resolved_plain.strategy
    symbols = [str(symbol) for symbol in strategy.universe.symbols]
    panel, manifest = load_panel(snapshot, symbols)
    _guard_holdout(panel, say)
    actions = load_corporate_actions(snapshot, symbols, sessions=panel.sessions)

    # v3 IS the scalar series: the configuration alone is the identical v1 core,
    # so a v3 row without one measures v1 under a v3 label. The series is loaded
    # through the same validator the CLI uses, which aligns it to the session
    # axis and rejects a value outside [0, 1] or a session it cannot cover.
    #
    # Passing a file is NOT the same as being conditioned. A series that never
    # leaves 1.0 de-risks nothing, and the run is then bit-identical to v1 --
    # so the series is measured here and the artifact reports what it actually
    # did, rather than asserting "conditioned" from the presence of a path.
    weight_scalars = None
    scalar_stats: dict[str, Any] | None = None
    if version == "v3" and scalars is not None:
        weight_scalars = load_weight_scalars(scalars, panel.sessions)
        conditioned_sessions = int((weight_scalars < 1.0).sum())
        scalar_stats = {
            "path": str(scalars),
            "sessions": int(weight_scalars.size),
            "mean": round(float(weight_scalars.mean()), 6),
            "min": round(float(weight_scalars.min()), 6),
            "sessions_below_one": conditioned_sessions,
            "binds": conditioned_sessions > 0,
        }
        if conditioned_sessions == 0:
            say(
                f"v3 conditioning: {scalars} is CONSTANT 1.0 across all "
                f"{weight_scalars.size} sessions -- it de-risks nothing, so this "
                "run is bit-identical to v1 and is not independent evidence"
            )
        else:
            say(
                f"v3 conditioning: {scalars} binds on {conditioned_sessions} of "
                f"{weight_scalars.size} sessions (mean {float(weight_scalars.mean()):.3f}, "
                f"min {float(weight_scalars.min()):.3f})"
            )

    result = run_backtest(
        strategy,
        panel.slice_symbols([Symbol(s) for s in strategy.to_universe().symbols]),
        strategy.to_universe(),
        initial_cash=initial_cash,
        actions_by_session=actions,
        weight_scalars=weight_scalars,
    )
    return result, manifest.snapshot_id, resolved_plain.content_sha256, scalar_stats


def _guard_holdout(panel: MarketPanel, say: Any) -> None:
    """Fail closed on holdout data, exactly as the CLI commands do.

    A script that loaded a panel directly would otherwise be the weakest caller
    of a fail-closed boundary, which is the same as not having one.
    """
    declaration = load_holdout_declaration(default_holdout_root())
    assert_respects_holdout(panel, declaration, access_reason=None)
    if declaration is None:
        say("no holdout declaration exists yet; `amat declare-holdout` freezes one")


def _fills_between(result: BacktestResult, first: dt.date, last: dt.date) -> tuple[int, float]:
    """Fill count and booked cost inside the window, for context beside returns."""
    fills = 0
    costs = Decimal(0)
    for record in result.sessions:
        if first <= record.session <= last:
            fills += len(record.fills)
            costs += record.attribution.fees
    return fills, float(costs)


def _slice_horizon(
    result: BacktestResult,
    label: str,
    months: int,
    *,
    end_session: dt.date,
) -> dict[str, Any]:
    """One trailing window of a single equity curve, or a recorded refusal."""
    requested_start = _months_before(end_session, months)
    sessions = result.session_dates
    equity = result.equity_curve

    index = next((i for i, day in enumerate(sessions) if day >= requested_start), None)
    if index is None or len(sessions) - index < _MIN_WINDOW_SESSIONS:
        return {
            "horizon": label,
            "requested_start": requested_start.isoformat(),
            "measured": False,
            "reason": "the run's equity curve does not reach back this far",
        }

    stats = window_statistics(
        equity[index:],
        sessions[index:],
        trades=0,
        win_rate=0.0,
        expectancy_r=0.0,
    )
    fills, costs = _fills_between(result, sessions[index], sessions[-1])
    # "Truncated" means the curve does not reach back to the requested calendar
    # start, so the window is genuinely shorter than its label claims. A start
    # that merely lands on a weekend or holiday is NOT truncation -- the first
    # session at or after it is exactly the intended boundary.
    truncated = requested_start < sessions[0]
    return {
        "horizon": label,
        "measured": True,
        "requested_start": requested_start.isoformat(),
        "first_session": stats.first_session.isoformat(),
        "last_session": stats.last_session.isoformat(),
        "sessions": stats.sessions,
        "truncated_by_available_history": truncated,
        "start_equity": float(equity[index]),
        "end_equity": float(equity[-1]),
        "total_return": round(stats.total_return, 6),
        "annualized_return": round(stats.annualized_return, 6),
        "annualized_volatility": round(stats.annualized_volatility, 6),
        "sharpe": round(stats.sharpe, 4),
        "max_drawdown": round(stats.max_drawdown, 6),
        "fills": fills,
        "costs_booked": round(costs, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Trailing-horizon comparison across strategy versions: one full-history "
            "run each, sliced to common windows with one metric implementation."
        )
    )
    parser.add_argument(
        "--versions",
        nargs="+",
        default=list(VERSION_CONFIGS),
        choices=list(VERSION_CONFIGS),
        help="Strategy versions to measure (default: all; v2 is rejected and absent).",
    )
    parser.add_argument(
        "--horizons",
        nargs="+",
        default=list(HORIZON_MONTHS),
        choices=list(HORIZON_MONTHS),
        help="Trailing windows to report (default: all).",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="Snapshot directory (default: the newest under data/snapshots/).",
    )
    parser.add_argument(
        "--initial-cash",
        type=Decimal,
        default=Decimal("100000"),
        help="Opening cash for every version (default 100,000). Identical across versions.",
    )
    parser.add_argument(
        "--scalars",
        type=Path,
        default=None,
        help=(
            "v3 only: CSV of per-session weight scalars in [0,1]. Without it the "
            "v3 row is the identical v1 core and is labelled as such."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSON artifact path (default: outputs/experiments/horizon-sweep-<UTC>.json).",
    )
    args = parser.parse_args()

    if args.scalars is not None and not args.scalars.is_file():
        print(f"horizon_sweep: no scalars file at {args.scalars}", flush=True)
        return 2

    snapshot = args.snapshot if args.snapshot is not None else _newest_snapshot()
    started = dt.datetime.now(dt.UTC)

    def say(message: str) -> None:
        print(f"  {message}", flush=True)

    print(f"horizon sweep: snapshot={snapshot} initial_cash={args.initial_cash}", flush=True)

    versions: list[str] = list(args.versions)
    horizons: list[str] = [label for label in HORIZON_MONTHS if label in set(args.horizons)]

    records: dict[str, Any] = {}
    end_sessions: set[dt.date] = set()
    snapshot_ids: set[str] = set()

    for version in versions:
        say(f"running {version} ({VERSION_CONFIGS[version]}) over full history...")
        result, snapshot_id, sha, scalar_stats = _run_version(
            version,
            snapshot,
            initial_cash=args.initial_cash,
            scalars=args.scalars,
            say=say,
        )
        snapshot_ids.add(snapshot_id)
        end_sessions.add(result.session_dates[-1])
        say(
            f"  {len(result.sessions)} sessions "
            f"{result.session_dates[0]}..{result.session_dates[-1]}, "
            f"{result.total_fills} fills, final equity {float(result.final_equity):,.0f}"
        )
        records[version] = {
            "config": str(VERSION_CONFIGS[version]),
            "config_sha256": sha,
            "note": VERSION_NOTES[version],
            "scalars": scalar_stats,
            # True only if the series actually bound on some session. A constant
            # 1.0 export is a file, not a strategy difference.
            "conditioned": bool(scalar_stats and scalar_stats["binds"]),
            "one_way_bps": result.one_way_bps,
            "is_primary": result.is_primary,
            "full_history": {
                "first_session": result.session_dates[0].isoformat(),
                "last_session": result.session_dates[-1].isoformat(),
                "sessions": len(result.sessions),
                "initial_equity": float(result.initial_equity),
                "final_equity": float(result.final_equity),
                "fills": result.total_fills,
                "missed_fills": result.total_missed,
                "costs_booked": round(float(result.total_costs), 2),
            },
            "horizons": {},
        }
        for label in horizons:
            records[version]["horizons"][label] = _slice_horizon(
                result, label, HORIZON_MONTHS[label], end_session=result.session_dates[-1]
            )

    if len(end_sessions) != 1:
        detail = ", ".join(sorted(day.isoformat() for day in end_sessions))
        print(
            "horizon_sweep: versions ended on different sessions, so their windows "
            f"are not comparable: {detail}",
            flush=True,
        )
        return 1
    end_session = end_sessions.pop()

    artifact: dict[str, Any] = {
        "generated_at": started.isoformat(),
        "snapshot": str(snapshot),
        "snapshot_ids": sorted(snapshot_ids),
        "end_session": end_session.isoformat(),
        "initial_cash": str(args.initial_cash),
        "horizon_months": {label: HORIZON_MONTHS[label] for label in horizons},
        "method": (
            "Each version ran ONCE over the full snapshot; every horizon is a "
            "trailing slice of that single equity curve, measured by "
            "amat.validation.optimize.window_statistics. Warm-up and positions "
            "are carried into every window. All windows end on the same session."
        ),
        "caveats": (
            "Burned history: the frozen configuration was developed on this "
            "sample, so no window here is out-of-sample evidence.",
            "Short windows are dominated by their own noise; a 6-month Sharpe "
            "is not a property of the strategy.",
            "Costs are the configured one-way bps on both sides of every fill; "
            "no zero-cost row appears in this artifact.",
            "v3 rows carry a 'conditioned' flag: false means the run was the "
            "identical v1 core under a v3 label, not an independent result.",
        ),
        "versions": records,
    }

    out = args.out
    if out is None:
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        out = _ARTIFACT_ROOT / f"horizon-sweep-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, sort_keys=True, default=str), encoding="utf-8")

    _print_table(records, versions, horizons, end_session=end_session)
    print(f"\nartifact: {out}")
    return 0


def _print_table(
    records: Mapping[str, Any],
    versions: Sequence[str],
    horizons: Sequence[str],
    *,
    end_session: dt.date,
) -> None:
    """One block per horizon, every version side by side on identical windows."""
    print()
    print("=" * 100)
    print(f"TRAILING HORIZONS — every window ends {end_session}, identical costs and funding")
    print("=" * 100)
    for label in horizons:
        first = next(
            (
                records[v]["horizons"][label]["first_session"]
                for v in versions
                if records[v]["horizons"][label].get("measured")
            ),
            None,
        )
        header = f"  {label}" + (f"   ({first} .. {end_session})" if first else "")
        print()
        print(header)
        print(
            f"  {'VERSION':<9}{'SESSIONS':>10}{'TOTAL':>10}{'ANN':>9}"
            f"{'VOL':>8}{'SHARPE':>8}{'MAXDD':>9}{'FILLS':>8}{'END EQUITY':>14}"
        )
        for version in versions:
            row = records[version]["horizons"][label]
            if not row.get("measured"):
                print(f"  {version:<9}{'not measured — ' + row['reason']:>60}")
                continue
            flag = " *" if row["truncated_by_available_history"] else ""
            print(
                f"  {version:<9}{row['sessions']:>10}{row['total_return']:>+10.2%}"
                f"{row['annualized_return']:>+9.2%}{row['annualized_volatility']:>8.2%}"
                f"{row['sharpe']:>8.2f}{row['max_drawdown']:>9.2%}{row['fills']:>8}"
                f"{row['end_equity']:>14,.0f}{flag}"
            )
    print()
    print("  * window truncated by available history: it begins at the curve's first")
    print("    session, not the requested calendar start. Sessions column is the truth.")

    # Duplicate rows are the expected consequence of a non-binding overlay, but
    # a reader scanning the table sees two versions agreeing and reads it as
    # corroboration. Say what it actually is.
    scalars = records.get("v3", {}).get("scalars")
    if scalars is not None and not scalars["binds"]:
        print()
        print(f"  v3 ran with {scalars['path']}, which is CONSTANT 1.0 on all")
        print(f"  {scalars['sessions']} sessions: it de-risks nothing, so the v3 rows above are")
        print("  bit-identical to v1 by construction. They corroborate nothing.")
    print()
    print("  Read both columns. A higher return at a higher drawdown is not automatically")
    print("  better, and every row is burned history the configuration was developed on.")


if __name__ == "__main__":
    raise SystemExit(main())
