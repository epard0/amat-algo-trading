"""Nearby-parameter stability: the neighbourhoods the protocol still owed.

Two distinct things in one artifact, kept rigorously apart:

1. **Registered experiment 0005** -- the rebalance-band grid
   {1%, 2%, 3%, 4%, 5%}, RANKED under the standard split discipline because
   its registration declares a falsifiable hypothesis (wider band -> lower
   cost -> better consistency).
2. **Unranked sensitivities** -- one-at-a-time perturbations of the trend
   horizons, deadband, and covariance lookback. These were never registered
   as candidates, so nothing here ranks, wins, or becomes eligible; the
   only question is *flatness* -- does the frozen configuration sit on a
   plateau or a knife's edge? Each row reports the same split statistics for
   comparability, and the artifact states this section selects nothing.

Output: a JSON artifact under ``outputs/experiments/``, cited by the
registry.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_panel
from amat.domain.actions import CorporateAction
from amat.validation.optimize import fold_score, split_panel, window_statistics
from amat.validation.sweep import validation_slice

TIE_EPSILON = 1e-9

#: Registered 0005 grid (min_weight_delta). 0.02 is the frozen value.
BAND_GRID = (0.01, 0.02, 0.03, 0.04, 0.05)

#: Unranked one-at-a-time sensitivity variants: (label, section, field, value).
SENSITIVITIES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("horizons 16/63/252", {"trend": {"horizons_sessions": [16, 63, 252]}}),
    ("horizons 26/63/252", {"trend": {"horizons_sessions": [26, 63, 252]}}),
    ("horizons 21/50/252", {"trend": {"horizons_sessions": [21, 50, 252]}}),
    ("horizons 21/79/252", {"trend": {"horizons_sessions": [21, 79, 252]}}),
    ("horizons 21/63/202", {"trend": {"horizons_sessions": [21, 63, 202]}}),
    # A longer horizon needs the warm-up raised with it (the config validator
    # requires warm-up >= the longest horizon), so this row measures from a
    # later first decision than the others -- recorded, not hidden.
    (
        "horizons 21/63/302 (warmup 302)",
        {
            "trend": {"horizons_sessions": [21, 63, 302]},
            "warmup": {"min_completed_sessions": 302},
        },
    ),
    ("deadband 0.15", {"trend": {"deadband_abs": 0.15}}),
    ("deadband 0.25", {"trend": {"deadband_abs": 0.25}}),
    ("covariance lookback 96", {"covariance": {"lookback_sessions": 96}}),
    ("covariance lookback 144", {"covariance": {"lookback_sessions": 144}}),
)


def _variant(strategy: StrategyConfig, updates: dict[str, Any], label: str) -> StrategyConfig:
    payload = strategy.model_dump()
    for section, fields in updates.items():
        payload[section].update(fields)
    payload["name"] = f"{strategy.name}-{label.replace(' ', '-').replace('/', '-')}"
    return StrategyConfig.model_validate(payload)


def _measure(
    name: str,
    strategy: StrategyConfig,
    panel: MarketPanel,
    actions: Mapping[dt.date, Sequence[CorporateAction]],
    train_end: dt.date,
) -> dict[str, Any]:
    universe = strategy.to_universe()
    training_panel, full_panel = split_panel(panel, train_end)
    training_run = run_backtest(strategy, training_panel, universe, actions_by_session=actions)
    training = window_statistics(
        training_run.equity_curve,
        training_run.session_dates,
        trades=training_run.total_fills,
        win_rate=0.0,
        expectancy_r=0.0,
    )
    full_run = run_backtest(strategy, full_panel, universe, actions_by_session=actions)
    validation = validation_slice(
        full_run.equity_curve,
        full_run.session_dates,
        [len(record.fills) for record in full_run.sessions],
        train_end,
    )
    return {
        "name": name,
        "training_fold_score": fold_score(training),
        "training_sharpe": training.sharpe,
        "training_annualized_return": training.annualized_return,
        "validation_sharpe": validation.sharpe,
        "validation_annualized_return": validation.annualized_return,
        "validation_max_drawdown": validation.max_drawdown,
        "full_sample_fills": full_run.total_fills,
        "full_sample_costs": float(full_run.total_costs),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", type=Path, default=Path("data/snapshots/alpaca-sip-2026-08-11")
    )
    parser.add_argument("--train-end", type=dt.date.fromisoformat, default=dt.date(2022, 12, 30))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    frozen = load_strategy_config("configs/strategy/amat_v1.toml").strategy
    symbols = [str(symbol) for symbol in frozen.universe.symbols]
    panel, manifest = load_panel(args.snapshot, symbols)
    actions = load_corporate_actions(args.snapshot, symbols, sessions=panel.sessions)

    band_rows = []
    for band in BAND_GRID:
        label = f"band {band:.0%}" + (" (frozen)" if band == 0.02 else "")  # noqa: PLR2004
        print(f"  0005 band: {label}", flush=True)
        strategy = (
            frozen
            if band == 0.02  # noqa: PLR2004 - the frozen value runs unmodified
            else _variant(frozen, {"rebalance": {"min_weight_delta": band}}, label)
        )
        band_rows.append(_measure(label, strategy, panel, actions, args.train_end))

    ranked = sorted(band_rows, key=lambda row: row["training_fold_score"], reverse=True)
    frozen_row = next(row for row in band_rows if "frozen" in row["name"])
    winner, runner_up = ranked[0], ranked[1]
    tied = (
        math.isfinite(winner["training_fold_score"])
        and math.isfinite(runner_up["training_fold_score"])
        and abs(winner["training_fold_score"] - runner_up["training_fold_score"]) <= TIE_EPSILON
    )
    holds = (
        not tied
        and winner["name"] != frozen_row["name"]
        and winner["validation_sharpe"] > frozen_row["validation_sharpe"]
    )
    spread = max(r["validation_sharpe"] for r in band_rows) - min(
        r["validation_sharpe"] for r in band_rows
    )
    if tied:
        band_verdict = "Top band fold scores tied; no winner; the frozen 2% stands."
    elif winner["name"] == frozen_row["name"]:
        band_verdict = "The frozen 2% band ranks first on training; falsified per registration."
    elif not holds:
        band_verdict = (
            f"{winner['name']} wins training but not validation; the frozen 2% "
            "stands. Falsified per registration."
        )
    else:
        band_verdict = (
            f"{winner['name']} wins training AND validation "
            f"(validation-Sharpe spread across the whole grid: {spread:.3f}). "
            "Per registration: if that spread is inside one standard error, the "
            "honest verdict is 'insensitive'; otherwise eligibility only."
        )

    sensitivity_rows = []
    for label, updates in SENSITIVITIES:
        print(f"  sensitivity: {label}", flush=True)
        sensitivity_rows.append(
            _measure(label, _variant(frozen, updates, label), panel, actions, args.train_end)
        )
    frozen_reference = _measure("frozen v1 reference", frozen, panel, actions, args.train_end)

    payload = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "snapshot_id": manifest.snapshot_id,
        "train_end": args.train_end.isoformat(),
        "experiment_0005": {
            "registration": "configs/experiments/0005-rebalance-band-sensitivity.toml",
            "grid": band_rows,
            "ranked_names": [row["name"] for row in ranked],
            "winner_is_tied": tied,
            "winner_holds_on_validation": holds,
            "validation_sharpe_spread": spread,
            "verdict": band_verdict,
        },
        "unranked_sensitivities": {
            "note": (
                "One-at-a-time perturbations, never registered as candidates: "
                "nothing here ranks, wins, or becomes eligible. The question "
                "is flatness of the frozen configuration's neighbourhood."
            ),
            "frozen_reference": frozen_reference,
            "rows": sensitivity_rows,
        },
        "caveat": "Burned history; ranking-blind, not analyst-blind.",
    }
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or Path("outputs/experiments") / f"neighborhood-{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\n  0005 VERDICT: {band_verdict}")
    print(f"  artifact: {output}")


if __name__ == "__main__":
    main()
