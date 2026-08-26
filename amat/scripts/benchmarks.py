"""Reproducible passive benchmarks measured on the same sample as a strategy run.

Why this exists
---------------
``docs/experiment-registry.md`` has cited a SPY comparison since 2026-08-10 with
no script behind it: the numbers were computed ad hoc and could not be
regenerated or checked. A benchmark that cannot be reproduced is not evidence,
and ``.claude/rules/30-quant-methodology.md`` requires comparison against SPY,
60/40, equal-weight and cash. This makes those numbers citable.

What it measures, and the one thing to be careful about
-------------------------------------------------------
Buy-and-hold curves are built from **adjusted** closes, which embed dividends
and splits, so every benchmark here is a **total-return** series. Until
2026-08-10 the strategy it was compared against credited no dividends at all --
its ledger marked positions at raw closes and dropped every distribution. So the
published comparison put a total-return benchmark against a price-return
strategy and read the difference as skill. Both sides are total return only now
that :func:`amat.data.snapshot.load_corporate_actions` is wired into the runner.

Conventions match :func:`amat.metrics.summarize` exactly -- simple session
returns, 252-session annualization, ``sqrt`` scaling for volatility, Sharpe as
annualized return over annualized volatility under the run's 0% cash assumption,
drawdown peak-to-trough on the equity curve. They are recomputed here rather
than imported because ``summarize`` takes a ``RunResult``, and a passive holding
has no sessions, fills, or costs to fabricate.

These are **frictionless** curves: no spread, no commission, no rebalancing cost,
and for the mixed portfolios no rebalancing at all beyond the stated rule. That
biases them *in the benchmark's favour* relative to a strategy that pays 2 bps a
side, which is the right direction for a comparison the strategy has to beat.

Usage::

    python scripts/benchmarks.py --snapshot data/snapshots/alpaca-sip-2026-08-08
    python scripts/benchmarks.py --snapshot <dir> --symbols SPY QQQ --json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amat.data.snapshot import load_panel
from amat.errors import DataIntegrityError

#: Sessions per year, matching the strategy contract's annualization.
SESSIONS_PER_YEAR = 252

#: Below two sessions every return-based statistic is undefined, not merely noisy.
_MIN_SESSIONS = 2

#: The classic balanced portfolio. IEF rather than AGG because IEF is in the
#: frozen v1 universe, so it needs no extra symbol coverage.
SIXTY_FORTY = {"SPY": 0.60, "IEF": 0.40}


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """One passive curve, measured on the strategy's own session axis."""

    name: str
    sessions: int
    first_session: str
    last_session: str
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    max_drawdown: float
    max_drawdown_start: str
    max_drawdown_trough: str
    hit_rate: float
    rebalanced: str


def _measure(
    name: str, equity: np.ndarray, sessions: list[dt.date], rebalanced: str
) -> BenchmarkResult:
    """Summarize an equity curve using the project's stated conventions."""
    if equity.size < _MIN_SESSIONS:
        message = f"{name}: need at least 2 sessions to measure anything"
        raise DataIntegrityError(message)
    if np.any(equity <= 0):
        message = f"{name}: equity curve reached zero or below"
        raise DataIntegrityError(message)

    returns = equity[1:] / equity[:-1] - 1.0
    years = len(returns) / SESSIONS_PER_YEAR
    annualized_volatility = float(returns.std(ddof=1) * np.sqrt(SESSIONS_PER_YEAR))
    annualized_return = float((equity[-1] / equity[0]) ** (1.0 / years) - 1.0) if years > 0 else 0.0

    running_peak = np.maximum.accumulate(equity)
    drawdowns = equity / running_peak - 1.0
    trough = int(np.argmin(drawdowns))
    peak = int(np.argmax(equity[: trough + 1])) if trough > 0 else 0

    return BenchmarkResult(
        name=name,
        sessions=len(sessions),
        first_session=sessions[0].isoformat(),
        last_session=sessions[-1].isoformat(),
        total_return=float(equity[-1] / equity[0] - 1.0),
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        sharpe=annualized_return / annualized_volatility if annualized_volatility > 0 else 0.0,
        max_drawdown=float(drawdowns.min()),
        max_drawdown_start=sessions[peak].isoformat(),
        max_drawdown_trough=sessions[trough].isoformat(),
        hit_rate=float((returns > 0).mean()),
        rebalanced=rebalanced,
    )


def buy_and_hold(closes: np.ndarray, column: int) -> np.ndarray:
    """Equity of one dollar held in one instrument, indexed to its first close."""
    series = closes[:, column]
    return series / series[0]


def fixed_weight_drifting(closes: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Buy the target weights once and never rebalance.

    The weights are the *initial* allocation; the mix then drifts with relative
    performance. This is the honest zero-cost version of a static portfolio --
    a rebalanced one would incur turnover the curve does not charge for.
    """
    normalized = closes / closes[0, :]
    return normalized @ weights


def rebalanced_monthly(
    closes: np.ndarray, weights: np.ndarray, sessions: list[dt.date]
) -> np.ndarray:
    """Fixed weights restored at each month boundary, frictionlessly.

    Reported alongside the drifting version because the gap between them is the
    rebalancing premium, and quoting only the flattering one would be a choice
    the reader cannot see.
    """
    returns = closes[1:, :] / closes[:-1, :] - 1.0
    equity = np.empty(len(sessions))
    equity[0] = 1.0
    held = weights.copy()
    for index in range(1, len(sessions)):
        held = held * (1.0 + returns[index - 1, :])
        equity[index] = equity[index - 1] * held.sum()
        held = held / held.sum()
        if sessions[index].month != sessions[index - 1].month:
            held = weights.copy()
    return equity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True, help="Snapshot directory.")
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=["SPY", "QQQ", "IEF", "TLT", "GLD"],
        help="Instruments to measure buy-and-hold for.",
    )
    parser.add_argument("--start", type=dt.date.fromisoformat, default=None)
    parser.add_argument("--end", type=dt.date.fromisoformat, default=None)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args()

    # The session axis is the intersection of coverage across every symbol
    # loaded, so requesting the 60/40 legs alongside the singles keeps all
    # curves on one axis and makes the comparison exact rather than approximate.
    wanted = sorted({*args.symbols, *SIXTY_FORTY})
    panel, manifest = load_panel(args.snapshot, wanted, start=args.start, end=args.end)
    closes = panel.adjusted_closes
    sessions = list(panel.sessions)
    column = {str(symbol): index for index, symbol in enumerate(panel.symbols)}

    results = [
        _measure(f"{symbol} buy-and-hold", buy_and_hold(closes, column[symbol]), sessions, "never")
        for symbol in args.symbols
    ]

    equal = np.full(len(wanted), 1.0 / len(wanted))
    results.append(
        _measure(
            f"equal-weight {'/'.join(wanted)}",
            fixed_weight_drifting(closes, equal),
            sessions,
            "never",
        )
    )

    mix = np.zeros(len(wanted))
    for symbol, weight in SIXTY_FORTY.items():
        mix[column[symbol]] = weight
    results.append(
        _measure("60/40 SPY/IEF (drifting)", fixed_weight_drifting(closes, mix), sessions, "never")
    )
    results.append(
        _measure(
            "60/40 SPY/IEF (monthly)",
            rebalanced_monthly(closes, mix, sessions),
            sessions,
            "monthly",
        )
    )

    if args.json:
        print(
            json.dumps(
                {"snapshot": manifest.snapshot_id, "benchmarks": [asdict(r) for r in results]},
                indent=2,
            )
        )
        return 0

    print(f"Passive benchmarks — snapshot {manifest.snapshot_id}")
    print(f"{len(sessions)} sessions, {sessions[0]} .. {sessions[-1]}")
    print("Total-return series (adjusted closes). Frictionless: no spread, commission, or tax.\n")
    header = f"{'Benchmark':<32}{'Total':>10}{'Ann.':>9}{'Vol':>9}{'Sharpe':>9}{'Max DD':>10}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.name:<32}{r.total_return:>9.2%} {r.annualized_return:>8.2%} "
            f"{r.annualized_volatility:>8.2%} {r.sharpe:>8.2f} {r.max_drawdown:>9.2%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
