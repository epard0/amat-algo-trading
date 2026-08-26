"""Registered experiment 0004: TLT / rates-sleeve risk:reward variants.

Runs exactly the variants declared in
``configs/experiments/0004-tlt-risk-reward-variants.toml`` under the split
discipline every registered sweep uses: rank on training folds only,
measure validation ranking-blind, record everything. The registration file
is normative; this script implements it and refuses to invent variants.

Output: a JSON artifact under ``outputs/experiments/`` (the same
script-artifact convention as the v6 audits), cited by the registry.
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
from amat.validation.falsification import _narrow_actions, _without_symbol
from amat.validation.optimize import fold_score, split_panel, window_statistics
from amat.validation.sweep import validation_slice

#: Same tie epsilon as every registered sweep.
TIE_EPSILON = 1e-9


def _with_rates_cap(strategy: StrategyConfig, cap: float) -> StrategyConfig:
    payload = strategy.model_dump()
    for cluster in payload["universe"]["clusters"]:
        if cluster["name"] == "rates":
            cluster["gross_cap"] = cap
    payload["name"] = f"{strategy.name}-rates-cap-{cap:g}"
    return StrategyConfig.model_validate(payload)


def _measure(
    name: str,
    strategy: StrategyConfig,
    panel: MarketPanel,
    actions: Mapping[dt.date, Sequence[CorporateAction]],
    train_end: dt.date,
) -> dict[str, Any]:
    symbols = [str(symbol) for symbol in strategy.universe.symbols]
    sliced = panel.slice_symbols([s for s in panel.symbols if str(s) in set(symbols)])
    narrowed = _narrow_actions(actions, symbols) or {}
    universe = strategy.to_universe()
    training_panel, full_panel = split_panel(sliced, train_end)
    training_run = run_backtest(strategy, training_panel, universe, actions_by_session=narrowed)
    training = window_statistics(
        training_run.equity_curve,
        training_run.session_dates,
        trades=training_run.total_fills,
        win_rate=0.0,
        expectancy_r=0.0,
    )
    full_run = run_backtest(strategy, full_panel, universe, actions_by_session=narrowed)
    validation = validation_slice(
        full_run.equity_curve,
        full_run.session_dates,
        [len(record.fills) for record in full_run.sessions],
        train_end,
    )
    return {
        "name": name,
        "symbols": symbols,
        "training_fold_score": fold_score(training),
        "training": {
            "total_return": training.total_return,
            "annualized_return": training.annualized_return,
            "sharpe": training.sharpe,
            "max_drawdown": training.max_drawdown,
        },
        "validation": {
            "annualized_return": validation.annualized_return,
            "sharpe": validation.sharpe,
            "max_drawdown": validation.max_drawdown,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", type=Path, default=Path("data/snapshots/alpaca-sip-2026-08-11")
    )
    parser.add_argument("--train-end", type=dt.date.fromisoformat, default=dt.date(2022, 12, 30))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    baseline = load_strategy_config("configs/strategy/amat_v1.toml").strategy
    symbols = [str(symbol) for symbol in baseline.universe.symbols]
    panel, manifest = load_panel(args.snapshot, symbols)
    actions = load_corporate_actions(args.snapshot, symbols, sessions=panel.sessions)

    variants = [
        ("frozen v1 (baseline)", baseline),
        ("A: without TLT", _without_symbol(baseline, "TLT")),
        ("B: without TLT and IEF", _without_symbol(_without_symbol(baseline, "TLT"), "IEF")),
        ("C: rates cluster cap 0.20", _with_rates_cap(baseline, 0.20)),
        ("D: rates cluster cap 0.10", _with_rates_cap(baseline, 0.10)),
        ("control: without IEF", _without_symbol(baseline, "IEF")),
    ]
    rows = []
    for name, strategy in variants:
        print(f"  0004 variant: {name}", flush=True)
        rows.append(_measure(name, strategy, panel, actions, args.train_end))

    ranked = sorted(rows, key=lambda row: row["training_fold_score"], reverse=True)
    winner, runner_up = ranked[0], ranked[1]
    baseline_row = next(row for row in rows if row["name"].startswith("frozen"))
    tied = (
        math.isfinite(winner["training_fold_score"])
        and math.isfinite(runner_up["training_fold_score"])
        and abs(winner["training_fold_score"] - runner_up["training_fold_score"]) <= TIE_EPSILON
    )
    holds = (
        not tied
        and winner["name"] != baseline_row["name"]
        and winner["validation"]["sharpe"] > baseline_row["validation"]["sharpe"]
    )
    if tied:
        verdict = (
            f"{len(rows)} variants evaluated. Top training fold scores are "
            f"indistinguishable (within {TIE_EPSILON:g}); no winner is declared "
            "and the frozen configuration stands."
        )
    elif winner["name"] == baseline_row["name"]:
        verdict = (
            f"{len(rows)} variants evaluated. The frozen v1 configuration ranks "
            "first on training-window consistency; no rates-sleeve surgery is "
            "indicated. Falsified per registration."
        )
    elif not holds:
        verdict = (
            f"{len(rows)} variants evaluated. {winner['name']} outranks frozen v1 "
            "on training but does not hold on the validation window; the frozen "
            "configuration stands. Falsified per registration."
        )
    else:
        verdict = (
            f"{len(rows)} variants evaluated. {winner['name']} outranks frozen v1 "
            "on training AND holds on validation. Per registration this earns "
            "only eligibility for a registered new version, with the "
            "hindsight-selection caveat permanently attached (the 2022 rates "
            "shock sits inside the training window). It changes nothing in "
            "place."
        )

    payload = {
        "experiment": "0004-tlt-risk-reward-variants",
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "registration": "configs/experiments/0004-tlt-risk-reward-variants.toml",
        "snapshot_id": manifest.snapshot_id,
        "train_end": args.train_end.isoformat(),
        "variants": rows,
        "ranked_names": [row["name"] for row in ranked],
        "winner_is_tied": tied,
        "winner_holds_on_validation": holds,
        "verdict": verdict,
        "caveat": (
            "Burned history; validation is ranking-blind, not analyst-blind. "
            "The 2022 rates shock is inside the training window, mechanically "
            "favouring TLT-removal on training -- the registered trap."
        ),
    }
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or Path("outputs/experiments") / f"0004-tlt-variants-{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\n  VERDICT: {verdict}")
    print(f"  artifact: {output}")


if __name__ == "__main__":
    main()
