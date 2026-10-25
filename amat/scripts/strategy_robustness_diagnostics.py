"""Reproducible fixed-policy diagnostics; does not optimize or authorize trading.

Run with the isolated P13 src directory on PYTHONPATH. Existing receipts are
verified before reuse. New market-path replays are distinguished from bootstrap
uncertainty calculations over already realized strategy returns.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import multiprocessing as mp
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import polars as pl
from autoresearch.genetic.continuous_study import continuous_metrics

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.snapshot import load_panel
from autoresearch.genetic.symbolic_study import _source_hashes
from autoresearch.genetic.walkforward_study import _read
from autoresearch.market_scenarios import SCENARIOS, panel_digest, scenario_panel


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text() != raw:
            raise ValueError(f"Refusing to overwrite different evidence: {path}")
        return
    with path.open("x") as handle:
        handle.write(raw)


def block_indices(n: int, draws: int, length: int, seed: int) -> np.ndarray:
    """Paired circular moving-block bootstrap; preserves within-block ordering."""
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(draws, (n + length - 1) // length))
    return ((starts[:, :, None] + np.arange(length)) % n).reshape(draws, -1)[:, :n]


def historical(args: argparse.Namespace) -> None:
    study = args.runtime / "outputs/genetic-patterns/2026-09-30-p13-sensitivity/study"
    summary = _read(study / "summary.json")
    rows = summary["records"]
    tables = {}
    inputs = {str(study / "summary.json"): sha(study / "summary.json")}
    for row in rows:
        folder = study / "runs" / row["run"]
        if _read(folder / "receipt.json") != row:
            raise ValueError("Summary/receipt mismatch")
        for name, digest in row["files"].items():
            path = folder / name
            if sha(path) != digest:
                raise ValueError(f"Historical evidence checksum mismatch: {path}")
            inputs[str(path)] = digest
        table = pl.read_parquet(folder / "sessions.parquet").filter(
            pl.col("session").cast(pl.String) > "2023-12-29"
        )
        tables[row["run"]] = table
    first = tables["baseline-2bps"]
    n = len(first)
    results, component_rows = [], []
    for block in (5, 21, 63):
        indices = block_indices(n, args.bootstrap_draws, block, 20261007 + block)
        if not np.array_equal(np.zeros(n)[indices].mean(axis=1), np.zeros(args.bootstrap_draws)):
            raise ValueError("Bootstrap self-comparison must remain zero")
        for row in rows:
            name = row["run"]
            reference = f"baseline-{row['cost_bps']:g}bps"
            table, base = tables[name], tables[reference]
            if table["session"].to_list() != base["session"].to_list():
                raise ValueError("Unpaired historical calendars")
            returns, baseline = table["return"].to_numpy(), base["return"].to_numpy()
            diff = np.log1p(returns) - np.log1p(baseline)
            distribution = diff[indices].mean(axis=1) * 252
            lo, hi = np.quantile(distribution, [0.025, 0.975])
            tolerance = 0.005
            classification = (
                "helpful"
                if lo > tolerance
                else "harmful"
                if hi < -tolerance
                else "practically_equivalent"
                if lo >= -tolerance and hi <= tolerance
                else "inconclusive"
            )
            design = np.column_stack([np.ones(n), baseline])
            intercept, beta = np.linalg.lstsq(design, returns, rcond=None)[0]
            results.append(
                {
                    "run": name,
                    "cost_bps": row["cost_bps"],
                    "block_sessions": block,
                    "sessions": n,
                    "draws": args.bootstrap_draws,
                    "annualized_log_growth_difference": float(diff.mean() * 252),
                    "paired_95pct_interval": [float(lo), float(hi)],
                    "fraction_resampled_differences_positive": float((distribution > 0).mean()),
                    "classification_at_50bps_practical_margin": classification,
                    "practical_margin_status": (
                        "descriptive sensitivity; not a user-approved promotion rule"
                    ),
                    "intercept_annualized_descriptive": float(intercept * 252),
                    "baseline_beta_descriptive": float(beta),
                    "post_selection": row["post_selection"],
                    "misses": int(table["misses"].sum()),
                    "mean_daily_cost_fraction": float(
                        np.mean(
                            (table["fees"].to_numpy() + table["slippage"].to_numpy())
                            / (table["equity"].to_numpy() / (1 + returns))
                        )
                    ),
                }
            )
    for cost in (2, 5, 10, 20):
        for policy in ("balanced", "return", "defensive"):
            names = {
                control: f"{policy}{suffix}-{cost}bps"
                for control, suffix in (
                    ("full", ""),
                    ("no_mtf", "-no_mtf"),
                    ("no_exits", "-no_exits"),
                    ("neither", "-neither"),
                )
            }
            values = {
                k: float(np.log1p(tables[v]["return"].to_numpy()).mean() * 252)
                for k, v in names.items()
            }
            component_rows.append(
                {
                    "policy": policy,
                    "cost_bps": cost,
                    "mtf_effect_with_exits": values["full"] - values["no_mtf"],
                    "mtf_effect_without_exits": values["no_exits"] - values["neither"],
                    "exit_effect_with_mtf": values["full"] - values["no_exits"],
                    "exit_effect_without_mtf": values["no_mtf"] - values["neither"],
                    "interaction": values["full"]
                    - values["no_mtf"]
                    - values["no_exits"]
                    + values["neither"],
                    "units": "annualized mean log-return difference",
                    "scope": "matched interventions on frozen recipes; MTF is not GP alone",
                }
            )
    write(
        args.output / "historical-diagnostics.json",
        {
            "source": "verified existing 76-path P13 sensitivity campaign",
            "new_portfolio_replays": 0,
            "bootstrap": "paired circular moving blocks",
            "draws_per_comparison": args.bootstrap_draws,
            "interval_scope": (
                "conditional on frozen previously selected recipes; not selection-adjusted"
            ),
            "evidence_limit": (
                "Previously researched historical period, not pristine holdout. "
                "Bootstrap fractions are not future success probabilities."
            ),
            "input_sha256": inputs,
            "comparisons": results,
            "components": component_rows,
        },
    )
    print(
        json.dumps(
            {
                "verified_paths": len(rows),
                "bootstrap_comparisons": len(results),
                "matched_component_contrasts": len(component_rows),
            }
        ),
        flush=True,
    )


def market_task(task: dict) -> dict:
    started = time.monotonic()
    strategy = load_strategy_config(Path(task["config"])).strategy
    panel, _ = load_panel(
        Path(task["snapshot"]), [str(s) for s in strategy.universe.symbols], with_context=True
    )
    anchor = dt.date(2024, 1, 2)
    first = panel.sessions.index(anchor)
    market = scenario_panel(panel, first=first, kind=task["scenario"], seed=task["seed"])
    # scenario_panel restates the whole prefix on a total-return price basis;
    # replaying corporate actions here would count them twice.
    result = run_backtest(
        strategy,
        market,
        strategy.to_universe(),
        actions_by_session={},
        one_way_bps=task["cost_bps"],
        max_entry_window_participation=0.01,
        decision_schedule=(),
        full_history=True,
    )
    folder = Path(task["output"]) / "market-pilot" / task["id"]
    folder.mkdir(parents=True, exist_ok=False)
    np.savez_compressed(
        folder / "path.npz",
        dates=np.asarray([str(r.session) for r in result.sessions]),
        equity=np.asarray([float(r.equity) for r in result.sessions]),
        exposure=np.asarray([r.gross_exposure for r in result.sessions]),
        adjusted_closes=market.adjusted_closes,
        raw_closes=market.raw_closes,
        window_vwaps=market.window_vwaps,
        window_volumes=market.window_volumes,
    )
    record = {
        "job": task,
        "status": "completed",
        "seconds": time.monotonic() - started,
        "market_sha256": panel_digest(market),
        "path_sha256": sha(folder / "path.npz"),
        "post_anchor": continuous_metrics(result, anchor=panel.sessions[first - 1]),
        "warnings": list(result.warnings),
        "execution_authority": "none",
    }
    write(folder / "receipt.json", record)
    return record


def market_pilot(args: argparse.Namespace) -> None:
    foundation = args.runtime / "outputs/genetic-patterns/2026-09-30-p13-foundation/study"
    registration = _read(foundation / "registration.json")
    snapshot = Path(registration["input_paths"]["snapshot_manifest"]).parent
    config = args.runtime / "configs/strategy/amat_v13_return_target.toml"
    jobs = [
        {
            "id": f"{scenario}-seed20261007-{cost}bps",
            "scenario": scenario,
            "seed": 20261007,
            "cost_bps": cost,
            "snapshot": str(snapshot),
            "config": str(config),
            "output": str(args.output),
        }
        for scenario in SCENARIOS
        for cost in (2, 10)
    ]
    protocol = {
        "kind": "fixed-baseline daily scenario pilot; no parameter selection",
        "jobs": jobs,
        "source_sha256": _source_hashes(),
        "config_sha256": sha(config),
        "script_sha256": sha(Path(__file__)),
        "snapshot_partition_sha256": {str(p): sha(p) for p in snapshot.glob("*.parquet")},
        "model": "existing scenario_panel total-return daily model; no native intraday claims",
        "calibration": "252 sessions before 2024-01-02; suffix uses no observed future prices",
        "seed_scope": (
            "one paired seed per family; scenario families are not independent probability draws"
        ),
        "brownian_scope": (
            "Gaussian log-return control has zero log drift, "
            "not a zero-arithmetic-return martingale"
        ),
        "future_probabilities_established": False,
    }
    write(args.output / "market-pilot-protocol.json", protocol)
    records = []
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context("spawn")) as pool:
        for number, record in enumerate(pool.map(market_task, jobs), 1):
            records.append(record)
            print(
                json.dumps(
                    {
                        "completed": number,
                        "total": len(jobs),
                        "job": record["job"]["id"],
                        "cagr": record["post_anchor"]["cagr"],
                    }
                ),
                flush=True,
            )
    if _source_hashes() != protocol["source_sha256"] or sha(config) != protocol["config_sha256"]:
        raise ValueError("Runtime changed during fixed-policy pilot")
    write(
        args.output / "market-pilot-summary.json", {"records": records, "new_replays": len(records)}
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("historical", "market-pilot"))
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-draws", type=int, default=5000)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    args.runtime, args.output = args.runtime.resolve(), args.output.resolve()
    if args.bootstrap_draws < 1 or args.workers not in (1, 2):
        raise ValueError("Positive draws and one or two workers required")
    if args.phase == "historical":
        historical(args)
    else:
        market_pilot(args)


if __name__ == "__main__":
    main()
