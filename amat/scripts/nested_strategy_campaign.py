"""Registered daily strategy search; separate fitting, selection, and evaluation.

Run using the study's isolated runtime on PYTHONPATH. The original P13 runtime
and deployed trader are never edited. Every economic evaluation uses its native
one-account runner. Training scores are explicitly in-sample; subsequent windows
activate frozen decisions only after their selection cutoff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import random
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.exits import ExitRules
from amat.backtest.panel import MarketPanel
from amat.backtest.runner import BacktestResult, ScheduledDecisionContext, run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_panel
from amat.portfolio import precompute_decision_inputs
from amat.validation.regimes import classify_sessions
from autoresearch.genetic.data import CandleDataset, digest_json
from autoresearch.genetic.models import parse_pattern
from autoresearch.genetic.nested_replay import panel_prefix
from autoresearch.genetic.symbolic import evaluate_symbolic
from autoresearch.genetic.symbolic_search import (
    SymbolicSearchProtocol,
    SymbolicTerminal,
    run_symbolic_search,
)
from autoresearch.genetic.symbolic_study import (
    SymbolicScreen,
    SymbolicScreenConfig,
    _source_hashes,
    _template,
)
from autoresearch.labels import adjusted_window_prices
from autoresearch.market_scenarios import SCENARIOS, panel_digest, scenario_panel

FAMILIES = {
    "portfolio": ["none"],
    "trend": ["efficiency:21", "donchian_position:63"],
    "momentum": ["momentum:63", "rsi:21"],
    "volatility": ["variance_ratio:21", "volatility_shock:63"],
    "liquidity": ["illiquidity:21", "volume_ratio:21"],
    "candle": ["close_location:21", "body_atr:21"],
    "gp": ["gp:0", "gp:1"],
}
NUMERIC = {
    "deadband": [0.1, 0.35],
    "effect": [0.025, 0.25],
    "rebalance": [0.005, 0.04],
    "target_vol": [0.12, 0.24],
    "stop": [0.04, 0.15],
    "reward": [1.5, 4.0],
    "mix": [0.2, 0.8],
}
CATEGORICAL = {
    "horizons": [[21, 63, 126], [42, 84, 168], [63, 126, 252]],
    "cadence": [1, 5, 10],
    "positions": [3, 4, 6],
    "vol_power": [0.0, 0.5, 1.0],
    "polarity": [-1, 1],
    "exit_mode": ["none", "stop", "both"],
    "embargo": [1, 5, 21],
}
FIELDS = (
    "adjusted_closes",
    "raw_closes",
    "window_vwaps",
    "window_volumes",
    "adjusted_highs",
    "adjusted_lows",
    "session_volumes",
    "adjusted_opens",
)
_CACHE = {}
_STARTED = time.monotonic()
MUTATION_CHANCE = 0.25
ACCOUNTING_TOLERANCE = 1e-8
MIN_FEATURE_ROWS = 63
SHORTLIST_SIZE = 2
MIN_SELECTION_IMPROVEMENT = 0.005
WALL_BUDGET_SECONDS = 7200
ARTIFACT_BUDGET_BYTES = 10_000_000_000


def sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path: str | Path) -> Any:
    envelope = json.loads(Path(path).read_text())
    if digest_json(envelope["payload"]) != envelope["sha256"]:
        raise ValueError(f"Evidence checksum mismatch: {path}")
    return envelope["payload"]


def write(path: str | Path, payload: Any) -> None:
    path = Path(path)
    if path.exists():
        if read(path) != payload:
            raise ValueError(f"Refusing changed evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(
            {"payload": payload, "sha256": digest_json(payload)},
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    temporary = path.with_suffix(".pending")
    temporary.write_text(raw)
    temporary.replace(path)


def baseline_recipe() -> dict[str, Any]:
    return {
        "deadband": 0.2,
        "effect": 0.0,
        "rebalance": 0.02,
        "target_vol": 0.2,
        "stop": 0.05,
        "reward": 2.0,
        "mix": 1.0,
        "horizons": [63, 126, 252],
        "cadence": 5,
        "positions": 4,
        "vol_power": 0.0,
        "polarity": 1,
        "exit_mode": "none",
        "embargo": 1,
        "feature_a": "none",
        "feature_b": "none",
    }


def canonical(recipe: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(recipe))
    if result["effect"] == 0 or result["feature_a"] == "none":
        result.update(effect=0.0, feature_a="none", feature_b="none", polarity=1, mix=1.0)
    elif result["feature_b"] in ("none", result["feature_a"]):
        result.update(feature_b="none", mix=1.0)
    if result["exit_mode"] == "none":
        result.update(stop=0.05, reward=2.0, embargo=1)
    elif result["exit_mode"] == "stop":
        result["reward"] = 2.0
    return result


def proposal(
    family: str,
    slot: int,
    seed: int,
    *,
    parents: list[dict[str, Any]] | tuple = (),
    progress: float = 0.0,
    joint_seeds: list[dict[str, Any]] | tuple = (),
) -> dict[str, Any]:
    rng = random.Random(seed + 104729 * slot)  # noqa: S311 - seeded research proposals.
    methods = FAMILIES.get(
        family, [v for values in FAMILIES.values() for v in values if v != "none"]
    )
    if parents:
        recipe = json.loads(json.dumps(rng.choice(parents)))
        scale = 0.2 * (1 - progress) + 0.02 * progress
        for key, (lo, hi) in NUMERIC.items():
            recipe[key] = min(hi, max(lo, rng.gauss(recipe[key], scale * (hi - lo))))
        for key, values in CATEGORICAL.items():
            if rng.random() < MUTATION_CHANCE:
                recipe[key] = rng.choice(values)
        if rng.random() < MUTATION_CHANCE:
            recipe["feature_a"] = rng.choice(methods)
    else:
        recipe = {key: rng.uniform(lo, hi) for key, (lo, hi) in NUMERIC.items()}
        recipe.update({key: rng.choice(values) for key, values in CATEGORICAL.items()})
        # Full feature x polarity x exit coverage in each 12-slot global family.
        recipe.update(
            feature_a=methods[slot % len(methods)],
            feature_b="none",
            polarity=(-1, 1)[(slot // len(methods)) % 2],
            exit_mode=("none", "stop", "both")[(slot // 4) % 3],
        )
        if family == "joint":
            if joint_seeds and slot < len(joint_seeds):
                recipe.update(joint_seeds[slot])
            recipe["feature_a"] = rng.choice(methods)
            recipe["feature_b"] = rng.choice([m for m in methods if m != recipe["feature_a"]])
    if family == "portfolio":
        recipe.update(feature_a="none", effect=0.0)
    if family != "joint":
        recipe["feature_b"] = "none"
    return canonical(recipe)


def configured(base: StrategyConfig, recipe: dict[str, Any]) -> StrategyConfig:
    value = base.model_dump(mode="json")
    value["trend"].update(horizons_sessions=recipe["horizons"], deadband_abs=recipe["deadband"])
    value["rebalance"]["min_weight_delta"] = recipe["rebalance"]
    value["constraints"]["target_annualized_volatility"] = recipe["target_vol"]
    value["cross_sectional"].update(
        selection_interval_sessions=recipe["cadence"], max_positions=recipe["positions"]
    )
    value.setdefault("allocation", {})["volatility_power"] = recipe["vol_power"]
    return StrategyConfig.model_validate(value)


def exits(recipe: dict[str, Any]) -> ExitRules | None:
    if recipe["exit_mode"] == "none":
        return None
    return ExitRules(
        stop_loss=recipe["stop"],
        take_profit=recipe["stop"] * recipe["reward"] if recipe["exit_mode"] == "both" else None,
        reentry_embargo_sessions=recipe["embargo"],
    )


def score(metrics: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Risk constraints are applied identically during training and selection."""
    if metrics["dates"] != baseline["dates"]:
        raise ValueError("Unpaired evaluation calendars")
    delta = np.log1p(metrics["returns"]) - np.log1p(baseline["returns"])
    quarters = [float(x.mean() * 252) for x in np.array_split(delta, 4)]
    value = float(delta.mean() * 252 - 0.25 * np.std(quarters))
    failures = []
    if metrics["max_drawdown"] < baseline["max_drawdown"] - 1e-10:
        failures.append("drawdown_worse_than_baseline")
    if metrics["expected_shortfall_95"] > baseline["expected_shortfall_95"] + 1e-10:
        failures.append("tail_loss_worse_than_baseline")
    if metrics["warnings"]:
        failures.append("runner_warning")
    return {
        "score": value,
        "eligible": not failures,
        "failed_gates": failures,
        "annual_log_growth_difference": float(delta.mean() * 252),
        "quarter_differences": quarters,
    }


def metrics(result: BacktestResult, panel: MarketPanel, lower: str, upper: str) -> dict[str, Any]:
    days = np.array([str(row.session) for row in result.sessions])
    indices = np.where((days > lower) & (days <= upper))[0]
    if not len(indices) or indices[0] == 0:
        raise ValueError("Metrics require prior observed anchor equity")
    equity = np.array([float(row.equity) for row in result.sessions])
    if not np.isfinite(equity).all() or np.any(equity <= 0):
        raise ValueError("Nonfinite/nonpositive equity")
    returns = equity[indices] / equity[indices - 1] - 1
    path = np.r_[equity[indices[0] - 1], equity[indices]]
    tail = returns[returns <= np.quantile(returns, 0.05)]
    labels = classify_sessions(panel)
    regimes, reasons, missed = {}, Counter(), Counter()
    turnover, costs, residual_max = 0.0, 0.0, 0.0
    for i, ret in zip(indices, returns, strict=True):
        row = result.sessions[i]
        label = labels[i - 1]
        bucket = regimes.setdefault(label, {"sessions": 0, "log_growth": 0.0, "pnl": 0.0})
        bucket["sessions"] += 1
        bucket["log_growth"] += float(np.log1p(ret))
        bucket["pnl"] += float(row.equity - result.sessions[i - 1].equity)
        turnover += sum(float(f.gross_notional) for f in row.fills) / equity[i - 1]
        costs += float(row.attribution.fees + row.attribution.slippage)
        reasons.update(str(f.reason) for f in row.fills)
        missed.update(str(m.reason) for m in row.missed)
    previous = result.initial_equity
    for row in result.sessions:
        a = row.attribution
        residual = (
            row.equity
            - previous
            - (a.market_pnl + a.distributions + a.cash_yield - a.fees - a.slippage)
        )
        residual_max = max(residual_max, abs(float(residual)))
        previous = row.equity
    if residual_max > ACCOUNTING_TOLERANCE:
        raise ValueError("Unreconciled portfolio accounting")
    years = (
        result.sessions[indices[-1]].session - result.sessions[indices[0] - 1].session
    ).days / 365.2425
    return {
        "start": days[indices[0] - 1],
        "end": days[indices[-1]],
        "n": len(returns),
        "cagr": float((path[-1] / path[0]) ** (1 / years) - 1),
        "annual_log_growth": float(np.log1p(returns).mean() * 252),
        "max_drawdown": float(np.min(path / np.maximum.accumulate(path) - 1)),
        "expected_shortfall_95": float(max(0.0, -tail.mean())),
        "annualized_volatility": float(returns.std(ddof=1) * np.sqrt(252)),
        "mean_exposure": float(np.mean([result.sessions[i].gross_exposure for i in indices])),
        "annualized_turnover": float(turnover * 252 / len(indices)),
        "costs": costs,
        "fills": sum(len(result.sessions[i].fills) for i in indices),
        "fill_reasons": dict(reasons),
        "miss_reasons": dict(missed),
        "regimes": regimes,
        "accounting_residual_max": residual_max,
        "warnings": list(result.warnings),
        "dates": days[indices].tolist(),
        "returns": returns.tolist(),
    }


def inputs(root: Path) -> tuple:
    key = str(root)
    if key not in _CACHE:
        protocol = read(root / "protocol.json")
        base = load_strategy_config(root / "runtime/baseline.toml").strategy
        panel, _ = load_panel(
            Path(protocol["snapshot"]), [str(x) for x in base.universe.symbols], with_context=True
        )
        actions = load_corporate_actions(
            Path(protocol["snapshot"]),
            [str(x) for x in base.universe.symbols],
            sessions=panel.sessions,
        )
        _CACHE[key] = protocol, base, panel, actions
    return _CACHE[key]


def market(root: Path, world: str) -> tuple:
    protocol, _base, panel, actions = inputs(root)
    if world == "historical":
        return panel, actions
    key = (str(root), world)
    if key not in _CACHE:
        specification = protocol["worlds"][world]
        first = next(
            i for i, day in enumerate(panel.sessions) if str(day) >= specification["anchor"]
        )
        generated = scenario_panel(
            panel, first=first, kind=specification["scenario"], seed=specification["seed"]
        )
        _CACHE[key] = generated
    return _CACHE[key], {}


def feature_bank(root: Path, world: str, fold: str) -> dict[str, np.ndarray]:
    key = (str(root), world, fold, "features")
    if key not in _CACHE:
        folder = root / "fits" / world / fold
        receipt = read(folder / "receipt.json")
        if sha(folder / "features.npz") != receipt["features_sha256"]:
            raise ValueError("Feature bank checksum mismatch")
        with np.load(folder / "features.npz", allow_pickle=False) as archive:
            _CACHE[key] = {k: archive[k] for k in archive.files}
    return _CACHE[key]


def signal(
    panel: MarketPanel,
    strategy: StrategyConfig,
    recipe: dict[str, Any],
    features: dict[str, np.ndarray],
) -> np.ndarray | None:
    if recipe["effect"] == 0:
        return None
    scores = precompute_decision_inputs(strategy, panel.adjusted_closes).long_scores.copy()
    a = features[recipe["feature_a"]][: len(panel)]
    b = features[recipe["feature_b"]][: len(panel)] if recipe["feature_b"] != "none" else a
    # No renormalization when a component is missing.
    contribution = recipe["mix"] * np.nan_to_num(a) + (1 - recipe["mix"]) * np.nan_to_num(b)
    active = np.isfinite(scores) & (scores > 0)
    scores[active] = np.clip(
        scores[active] + recipe["effect"] * recipe["polarity"] * contribution[active], 0, 1
    )
    # The runner requires a finite override, then reapplies the original trend
    # availability mask. Zero placeholders cannot enable pre-warmup trading.
    return np.where(np.isfinite(scores), scores, 0.0)


def job(task: dict[str, Any]) -> dict[str, Any]:
    try:
        return _job(task)
    except Exception as exc:
        write(
            Path(task["root"]) / "failures" / f"{digest_json(task)}.json",
            {
                "task": task,
                "status": "failed",
                "type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise


def _job(task: dict[str, Any]) -> dict[str, Any]:
    root = Path(task["root"])
    folder = root / "runs" / digest_json(task)
    receipt_path = folder / "receipt.json"
    if receipt_path.exists():
        cached = read(receipt_path)
        if cached["task"] != task or sha(folder / "path.npz") != cached["path_sha256"]:
            raise ValueError("Cached replay mismatch")
        return cached
    started = time.monotonic()
    _, base, _, _ = inputs(root)
    panel, actions = market(root, task["world"])
    stop = sum(str(day) <= task["upper"] for day in panel.sessions)
    panel = panel_prefix(panel, stop)
    features = feature_bank(root, task["fit_world"], task["fold"])
    # A frozen real-history formula must be re-evaluated on each generated market.
    if task["fit_world"] != task["world"]:
        features = infer_bank(root, task["fit_world"], task["fold"], panel)
    recipe = task["recipe"]
    strategy = configured(base, recipe)
    scores = signal(panel, strategy, recipe, features)
    schedule = []
    if task["phase"] == "train":
        result = run_backtest(
            strategy,
            panel,
            strategy.to_universe(),
            actions_by_session=actions,
            one_way_bps=task["cost"],
            max_entry_window_participation=0.01,
            signal_override=scores,
            exit_rules=exits(recipe),
            decision_schedule=(),
            full_history=True,
        )
    else:
        activation = next(day for day in panel.sessions if str(day) > task["lower"])
        cutoff = panel.sessions[panel.sessions.index(activation) - 1]
        schedule.append(
            ScheduledDecisionContext(
                "selected",
                activation,
                cutoff,
                strategy,
                signal_override=scores,
                exit_rules=exits(recipe),
                weight_scalars=np.full(len(panel), task.get("risk_scalar", 1.0)),
            )
        )
        result = run_backtest(
            base,
            panel,
            base.to_universe(),
            actions_by_session=actions,
            one_way_bps=task["cost"],
            max_entry_window_participation=0.01,
            decision_schedule=schedule,
            full_history=True,
        )
    outcome = metrics(result, panel, task["lower"], task["upper"])
    folder.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        folder / "path.npz",
        dates=np.array([str(row.session) for row in result.sessions]),
        equity=np.array([float(row.equity) for row in result.sessions]),
        exposure=np.array([row.gross_exposure for row in result.sessions]),
    )
    receipt = {
        "task": task,
        "metrics": outcome,
        "seconds": time.monotonic() - started,
        "path_sha256": sha(folder / "path.npz"),
        "market_sha256": panel_digest(panel),
    }
    write(receipt_path, receipt)
    print(
        json.dumps(
            {
                "event": "replay",
                "phase": task["phase"],
                "world": task["world"],
                "fold": task["fold"],
                "cost": task["cost"],
                "cagr": outcome["cagr"],
            }
        ),
        flush=True,
    )
    return receipt


def infer_bank(
    root: Path, fit_world: str, fold_name: str, panel: MarketPanel
) -> dict[str, np.ndarray]:
    receipt = read(root / "fits" / fit_world / fold_name / "receipt.json")
    values = {}
    for name, definition in receipt["patterns"].items():
        array = evaluate_symbolic(parse_pattern(definition), panel)
        array[:, ~np.array(receipt["eligible_assets"][name], dtype=bool)] = np.nan
        values[name] = array
    return values


def fit(root: Path, world: str, fold: dict[str, str]) -> dict[str, Any]:
    folder = root / "fits" / world / fold["name"]
    if (folder / "receipt.json").exists():
        result = read(folder / "receipt.json")
        if sha(folder / "features.npz") != result["features_sha256"]:
            raise ValueError("Fit cache checksum mismatch")
        return result
    protocol = read(root / "protocol.json")
    panel, _ = market(root, world)
    stop = sum(str(day) <= fold["train_end"] for day in panel.sessions)
    training = panel_prefix(panel, stop)
    fields = dict(zip(("open", "high", "low", "close"), training.candle_inputs(), strict=True))
    data = CandleDataset(
        tuple(str(d) for d in training.sessions),
        tuple(str(s) for s in training.symbols),
        fields,
        adjusted_window_prices(training),
        "1d",
        "corporate_action_adjusted",
        {"panel_sha256": panel_digest(training)},
    )
    terminals = tuple(
        SymbolicTerminal(
            name.rsplit(":", 1)[0], lookback=int(name.rsplit(":", 1)[1]), transform="rolling_zscore"
        )
        for family, methods in FAMILIES.items()
        if family not in ("portfolio", "gp")
        for name in methods
    )
    search = SymbolicSearchProtocol(
        population_size=16, generations=5, max_depth=3, max_nodes=15, feature_terminals=terminals
    )
    screen = SymbolicScreen(data, training, search, SymbolicScreenConfig(min_effect_sessions=63))
    # Label at t uses execution t+1 -> t+2. Final two feature rows must be purged.
    if np.any(screen.rows[-2:]):
        raise ValueError("Forward label overlaps training boundary")
    contenders = {}
    folder.mkdir(parents=True, exist_ok=True)
    for seed in protocol["gp_seeds"]:
        configured_search = replace(search, seed=seed)
        state = run_symbolic_search(
            configured_search,
            screen,
            pattern_template=_template(),
            evaluator_hash=digest_json(
                {"parent": digest_json(protocol), "fold": fold, "world": world}
            ),
            data_hash=data.digest,
        )
        write(folder / f"gp-seed{seed}.json", state)
        for row in state["ledger"]:
            if row["fitness"] is not None:
                contenders[row["genome_hash"]] = row
    ranked = sorted(contenders.values(), key=lambda row: (-row["fitness"], row["genome_hash"]))
    if len(ranked) < SHORTLIST_SIZE:
        raise ValueError("Insufficient eligible GP programs; retain failure, do not redraw")
    patterns = {f"gp:{i}": ranked[i]["pattern"] for i in range(2)}
    for terminal in terminals:
        definition = {
            **_template(),
            "genome": {
                "kind": "symbolic_indicator",
                "outputTransform": "tanh",
                "root": terminal.feature_node().model_dump(mode="json"),
            },
        }
        patterns[f"{terminal.name}:{terminal.lookback}"] = definition
    values, eligibility = {}, {}
    for name, definition in patterns.items():
        array = evaluate_symbolic(parse_pattern(definition), panel)
        eligible = np.isfinite(array[:stop]).sum(axis=0) >= MIN_FEATURE_ROWS
        array[:, ~eligible] = np.nan
        values[name], eligibility[name] = array, eligible.tolist()
    np.savez_compressed(folder / "features.npz", **values)
    result = {
        "world": world,
        "fold": fold,
        "fit_panel_sha256": panel_digest(training),
        "dataset_sha256": data.digest,
        "patterns": patterns,
        "eligible_assets": eligibility,
        "features_sha256": sha(folder / "features.npz"),
        "gp_slots": 192,
        "label_horizon_with_execution_lag": 2,
        "purged_final_feature_rows": 2,
        "last_eligible_feature_date": str(training.sessions[np.where(screen.rows)[0][-1]]),
        "training_end": str(training.sessions[-1]),
        "training_only": True,
    }
    write(folder / "receipt.json", result)
    print(
        json.dumps(
            {"event": "fit_complete", "world": world, "fold": fold["name"], "gp_slots": 192}
        ),
        flush=True,
    )
    return result


def task(
    root: Path,
    fold: dict[str, str],
    recipe: dict[str, Any],
    phase: str,
    *,
    cost: int = 2,
    world: str = "historical",
    fit_world: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    lower, upper = {
        "train": ("2017-12-29", fold["train_end"]),
        "selection": (fold["train_end"], fold["selection_end"]),
        "test": (fold["selection_end"], fold["test_end"]),
    }[phase]
    return {
        "root": str(root),
        "fold": fold["name"],
        "recipe": canonical(recipe),
        "phase": phase,
        "cost": cost,
        "world": world,
        "fit_world": fit_world or world,
        "lower": lower,
        "upper": upper,
        **extra,
    }


def batch(pool: ProcessPoolExecutor, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for offset in range(0, len(tasks), 2):
        root = Path(tasks[offset]["root"])
        check_budget(root)
        results.extend(pool.map(job, tasks[offset : offset + 2]))
    return results


def check_budget(root: Path) -> None:
    prior = sum(read(p)["seconds"] for p in (root / "phase-times").glob("*.json"))
    if prior + time.monotonic() - _STARTED > WALL_BUDGET_SECONDS:
        raise RuntimeError("Registered two-hour research budget exhausted; evidence retained")
    used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    if used > ARTIFACT_BUDGET_BYTES:
        raise RuntimeError("Registered artifact budget exhausted; evidence retained")


def ranked(records: list[dict[str, Any]], reference: dict[str, Any]) -> list[dict[str, Any]]:
    annotated = [
        {
            "recipe": r["task"]["recipe"],
            "run": digest_json(r["task"]),
            **score(r["metrics"], reference["metrics"]),
        }
        for r in records
    ]
    return sorted(
        annotated, key=lambda r: (not r["eligible"], -r["score"], digest_json(r["recipe"]))
    )


def search_fold(
    root: Path, fold: dict[str, str], pool: ProcessPoolExecutor, *, world: str = "historical"
) -> dict[str, Any]:
    folder = root / "search" / world / fold["name"]
    if (folder / "selection.json").exists():
        return read(folder / "selection.json")
    fit(root, world, fold)
    reference = job(task(root, fold, baseline_recipe(), "train", world=world))
    protocol = read(root / "protocol.json")
    chosen = {}
    training_winners = []
    for family_index, family in enumerate([*FAMILIES, "joint"]):
        seed = protocol["optimizer_seed"] + family_index * 1000 + int(fold["name"][-4:])
        global_recipes = [
            proposal(family, i, seed, joint_seeds=training_winners) for i in range(12)
        ]
        write(folder / f"{family}-global-proposals.json", global_recipes)
        records = batch(pool, [task(root, fold, r, "train", world=world) for r in global_recipes])
        ranking = ranked(records, reference)
        parents = [r["recipe"] for r in ranking[:2]]
        local_recipes = [
            proposal(family, 12 + i, seed, parents=parents, progress=i / 3) for i in range(4)
        ]
        write(folder / f"{family}-local-proposals.json", local_recipes)
        records += batch(pool, [task(root, fold, r, "train", world=world) for r in local_recipes])
        ranking = ranked(records, reference)
        write(folder / f"{family}-training.json", ranking)
        training_winners.append(ranking[0]["recipe"])
        # Finalists are frozen before any validation callback for this family.
        finalists = [baseline_recipe()]
        seen = {digest_json(finalists[0])}
        for row in ranking:
            key = digest_json(row["recipe"])
            if row["eligible"] and key not in seen:
                finalists.append(row["recipe"])
                seen.add(key)
            if len(finalists) == SHORTLIST_SIZE + 1:
                break
        write(folder / f"{family}-finalists.json", finalists)
        runs = batch(
            pool,
            [
                task(root, fold, r, "selection", cost=cost, world=world)
                for cost in (2, 10)
                for r in finalists
            ],
        )
        by_cost = {cost: [r for r in runs if r["task"]["cost"] == cost] for cost in (2, 10)}
        primary = ranked(by_cost[2], by_cost[2][0])
        candidates = []
        for row in primary:
            key = digest_json(row["recipe"])
            stress = next(r for r in by_cost[10] if digest_json(r["task"]["recipe"]) == key)
            stress_score = score(stress["metrics"], by_cost[10][0]["metrics"])
            is_baseline = key == digest_json(baseline_recipe())
            if is_baseline or (
                row["eligible"]
                and row["annual_log_growth_difference"] >= MIN_SELECTION_IMPROVEMENT
                and stress_score["eligible"]
                and stress_score["annual_log_growth_difference"] >= 0
            ):
                candidates.append(row)
        winner = sorted(candidates, key=lambda r: (-r["score"], digest_json(r["recipe"])))[0]
        chosen[family] = {
            "selected": winner,
            "training_slots": 16,
            "finalists": len(finalists),
            "selection_runs": [digest_json(r["task"]) for r in runs],
        }
        print(
            json.dumps(
                {
                    "event": "family_selected",
                    "world": world,
                    "fold": fold["name"],
                    "family": family,
                    "baseline_selected": winner["recipe"] == baseline_recipe(),
                }
            ),
            flush=True,
        )
    result = {"world": world, "fold": fold, "families": chosen, "outer_opened": False}
    write(folder / "selection.json", result)
    return result


def register(root: Path, snapshot: Path) -> None:
    base = load_strategy_config(root / "runtime/baseline.toml").strategy
    panel, manifest = load_panel(
        snapshot, [str(x) for x in base.universe.symbols], with_context=True
    )
    folds = [
        {
            "name": f"outer-{year}",
            "train_end": f"{year - 3}-12-31",
            "selection_end": f"{year - 1}-12-31",
            "test_end": min(f"{year}-12-31", str(panel.sessions[-1])),
        }
        for year in (2024, 2025, 2026)
    ]
    worlds = {}
    for scenario in SCENARIOS:
        for seed in (20261008101, 20261008102):
            worlds[f"final-{scenario}-{seed}"] = {
                "scenario": scenario,
                "seed": seed,
                "anchor": "2024-01-02",
                "role": "final_evaluation_only",
            }
    for scenario, seed in (("gaussian", 20261008301), ("range", 20261008302)):
        worlds[f"refit-{scenario}-{seed}"] = {
            "scenario": scenario,
            "seed": seed,
            "anchor": "2019-01-02",
            "role": "repeat_whole_search",
        }
    protocol = {
        "schema": "nested-strategy-campaign/1",
        "snapshot": str(snapshot),
        "snapshot_partitions": manifest.partition_sha256,
        "panel_sha256": panel_digest(panel),
        "runtime_source_sha256": _source_hashes(),
        "driver_sha256": sha(__file__),
        "baseline_sha256": sha(root / "runtime/baseline.toml"),
        "baseline_recipe": baseline_recipe(),
        "folds": folds,
        "families": FAMILIES,
        "numeric_domains": NUMERIC,
        "categorical_domains": CATEGORICAL,
        "global_slots_per_family": 12,
        "local_slots_per_family": 4,
        "shortlist_per_family": 2,
        "optimizer_seed": 20261008,
        "gp_seeds": [871, 872],
        "gp_slots_per_fold": 192,
        "gp_grammar": {
            "population": 16,
            "generations": 5,
            "max_depth": 3,
            "max_nodes": 15,
            "terminals": (
                "the ten declared conventional features, rolling_zscore, lag zero, self source"
            ),
            "constants": [-1, -0.5, 0, 0.5, 1],
            "operators": "unchanged SymbolicSearchProtocol defaults",
        },
        "objective": {
            "rank": (
                "annual mean paired log-return difference - 0.25 * SD of four "
                "time-block annualized differences"
            ),
            "constraints": (
                "drawdown and daily 95% expected shortfall no worse than cost-matched v13"
            ),
            "selection": (
                "primary annual log-growth improvement >=0.005; stress "
                "improvement >=0; both pass risk gates; otherwise baseline"
            ),
            "ties": "ascending canonical recipe SHA256",
            "training": "in-sample screening; not evidence of forward performance",
        },
        "costs": {
            "primary_one_way_bps": 2,
            "stress_one_way_bps": 10,
            "cash_rate": 0,
            "entry_window_participation": 0.01,
            "execution": "next session 09:30-09:35 VWAP; sells not volume-capped",
        },
        "purge": {
            "label": "execution-window t+1 to t+2 net round trip",
            "final_feature_rows": 2,
            "implementation": (
                "training dataset physically ends at cutoff; unavailable forward labels excluded"
            ),
        },
        "worlds": worlds,
        "limits": {"workers": 2, "wall_seconds": 7200, "artifacts_bytes": 10_000_000_000},
        "diagnostics": {
            "factorial": (
                "feature enabled/removed x exits enabled/removed x cadence 1/5 "
                "for every selected recipe"
            ),
            "retuned_removal": (
                "portfolio-only and each independent feature family receive equal "
                "16-slot budgets; joint removals reference those searches"
            ),
            "retuned_exit_removal": (
                "same 12 global plus 4 local slots with exits disabled; train/selection only"
            ),
            "risk_match": (
                "baseline scalar calibrated only on validation target volatility; "
                "clamp <=1, no releveraging"
            ),
            "neighborhood": "effect and rebalance +/-10%, clipped to declared domains",
            "final_markets": (
                "all 17 families, 2 unseen seeds, 2 costs; baseline and each "
                "distinct outer-2024 selected recipe"
            ),
            "whole_process": (
                "all eight 16-slot family searches refit in two simulated worlds, first outer fold"
            ),
        },
        "minute_study": {
            "status": "not_implemented",
            "refit_interval_completed_1m_bars": 250,
            "comparators": ["baseline", "conventional", "frozen_gp", "adaptive_gp"],
            "clocks": ["ingestion", "inference", "risk_checks", "order_management", "training"],
            "reason": (
                "current symbolic trainer is daily-only and runner is daily; no "
                "minute performance claims"
            ),
        },
        "evidence_limits": [
            "historical dates previously researched",
            "two final seeds per family are breadth, not precise tail probabilities",
            "fixed-policy stress does not test adaptive training",
            "whole-process simulations test annual fold fitting, not 250-minute refitting",
        ],
        "execution_authority": "none",
    }
    write(root / "protocol.json", protocol)
    print(json.dumps({"event": "registered", "protocol_sha256": digest_json(protocol)}), flush=True)


def verify(root: Path) -> dict[str, Any]:
    protocol = read(root / "protocol.json")
    if (
        protocol["driver_sha256"] != sha(__file__)
        or protocol["runtime_source_sha256"] != _source_hashes()
    ):
        raise ValueError("Registered source changed")
    if protocol["baseline_sha256"] != sha(root / "runtime/baseline.toml"):
        raise ValueError("Baseline configuration changed")
    for name, digest in protocol["snapshot_partitions"].items():
        if sha(Path(protocol["snapshot"]) / name) != digest:
            raise ValueError("Snapshot changed")
    return protocol


def evaluate(root: Path, pool: ProcessPoolExecutor, selections: list[dict[str, Any]]) -> None:
    tasks = []
    for selection in selections:
        fold = selection["fold"]
        recipes = [baseline_recipe()] + [
            r["selected"]["recipe"] for r in selection["families"].values()
        ]
        recipes = list({digest_json(r): r for r in recipes}.values())
        for cost in (2, 10):
            tasks.extend(
                task(root, fold, r, "test", cost=cost, world=selection["world"]) for r in recipes
            )
    write(root / "outer-evaluation-plan.json", tasks)
    runs = batch(pool, tasks)
    write(root / "outer-evaluation.json", {"runs": [digest_json(r["task"]) for r in runs]})


def diagnostics(  # noqa: PLR0912 - explicit registered interventions
    root: Path, pool: ProcessPoolExecutor, selections: list[dict[str, Any]]
) -> None:
    all_tasks = []
    links = []
    for selection in selections:
        fold = selection["fold"]
        recipes = {
            digest_json(r["selected"]["recipe"]): r["selected"]["recipe"]
            for r in selection["families"].values()
        }
        for parent, recipe in recipes.items():
            interventions = {}
            if recipe["effect"] > 0:
                interventions["retuned_remove_all_features"] = selection["families"]["portfolio"][
                    "selected"
                ]["recipe"]
            if recipe["feature_b"] != "none":
                for removed, kept, fraction in (
                    ("a", "feature_b", 1 - recipe["mix"]),
                    ("b", "feature_a", recipe["mix"]),
                ):
                    interventions[f"fixed_remove_feature_{removed}"] = canonical(
                        {
                            **recipe,
                            "feature_a": recipe[kept],
                            "feature_b": "none",
                            "effect": recipe["effect"] * fraction,
                            "mix": 1.0,
                        }
                    )
                    family = next(
                        name for name, methods in FAMILIES.items() if recipe[kept] in methods
                    )
                    interventions[f"retuned_remove_feature_{removed}"] = selection["families"][
                        family
                    ]["selected"]["recipe"]
            if recipe["exit_mode"] != "none":
                family = next(
                    name
                    for name, item in selection["families"].items()
                    if item["selected"]["recipe"] == recipe
                )
                interventions["retuned_remove_exits"] = retune_without_exits(
                    root, pool, fold, family
                )
            for feature in (False, True):
                for exit_on in (False, True):
                    for cadence in (1, 5):
                        modified = {
                            **recipe,
                            "effect": recipe["effect"] if feature else 0.0,
                            "exit_mode": recipe["exit_mode"] if exit_on else "none",
                            "cadence": cadence,
                        }
                        interventions[
                            f"feature{int(feature)}-exits{int(exit_on)}-cadence{cadence}"
                        ] = canonical(modified)
            for parameter in ("effect", "rebalance"):
                for factor in (0.9, 1.1):
                    modified = {
                        **recipe,
                        parameter: np.clip(recipe[parameter] * factor, *NUMERIC[parameter]),
                    }
                    if parameter == "effect" and recipe["effect"] == 0:
                        modified[parameter] = 0.0
                    interventions[f"{parameter}-x{factor}"] = canonical(modified)
            for name, r in interventions.items():
                t = task(root, fold, r, "test")
                all_tasks.append(t)
                links.append(
                    {
                        "fold": fold["name"],
                        "parent": parent,
                        "intervention": name,
                        "run": digest_json(t),
                    }
                )
            # Calibrate matched-risk control exclusively on the selection interval.
            selected = job(task(root, fold, recipe, "selection"))
            reference = job(task(root, fold, baseline_recipe(), "selection"))
            base_vol = reference["metrics"]["annualized_volatility"]
            scalar = (
                min(1.0, selected["metrics"]["annualized_volatility"] / base_vol)
                if base_vol
                else 0.0
            )
            t = task(root, fold, baseline_recipe(), "test", risk_scalar=scalar)
            all_tasks.append(t)
            links.append(
                {
                    "fold": fold["name"],
                    "parent": parent,
                    "intervention": "ex_ante_risk_match",
                    "run": digest_json(t),
                    "scalar": scalar,
                    "calibration": "selection only; no releveraging",
                }
            )
    unique = list({digest_json(t): t for t in all_tasks}.values())
    write(root / "diagnostic-plan.json", {"links": links, "tasks": unique})
    batch(pool, unique)
    write(root / "diagnostic-complete.json", {"links": links, "unique_runs": len(unique)})


def retune_without_exits(
    root: Path, pool: ProcessPoolExecutor, fold: dict[str, str], family: str
) -> dict[str, Any]:
    """Equal 16-slot reduced-family search; no outer outcomes used."""
    folder = root / "retuned-no-exits" / fold["name"] / family
    if (folder / "selection.json").exists():
        return read(folder / "selection.json")["recipe"]
    seed = read(root / "protocol.json")["optimizer_seed"] + int(fold["name"][-4:]) + 90000
    reference = job(task(root, fold, baseline_recipe(), "train"))
    proposals = [canonical({**proposal(family, i, seed), "exit_mode": "none"}) for i in range(12)]
    write(folder / "global-proposals.json", proposals)
    runs = batch(pool, [task(root, fold, r, "train") for r in proposals])
    parents = [r["recipe"] for r in ranked(runs, reference)[:SHORTLIST_SIZE]]
    local = [
        canonical(
            {**proposal(family, 12 + i, seed, parents=parents, progress=i / 3), "exit_mode": "none"}
        )
        for i in range(4)
    ]
    write(folder / "local-proposals.json", local)
    runs += batch(pool, [task(root, fold, r, "train") for r in local])
    ranking = ranked(runs, reference)
    write(folder / "training.json", ranking)
    finalists = [
        baseline_recipe(),
        *[r["recipe"] for r in ranking if r["eligible"]][:SHORTLIST_SIZE],
    ]
    finalists = list({digest_json(r): r for r in finalists}.values())
    write(folder / "finalists.json", finalists)
    primary = batch(pool, [task(root, fold, r, "selection") for r in finalists])
    stress_runs = batch(pool, [task(root, fold, r, "selection", cost=10) for r in finalists])
    eligible = []
    for row in ranked(primary, primary[0]):
        stressed = next(r for r in stress_runs if r["task"]["recipe"] == row["recipe"])
        stress_score = score(stressed["metrics"], stress_runs[0]["metrics"])
        if row["recipe"] == baseline_recipe() or (
            row["eligible"]
            and row["annual_log_growth_difference"] >= MIN_SELECTION_IMPROVEMENT
            and stress_score["eligible"]
            and stress_score["annual_log_growth_difference"] >= 0
        ):
            eligible.append(row)
    winner = sorted(eligible, key=lambda r: (-r["score"], digest_json(r["recipe"])))[0]
    write(
        folder / "selection.json",
        {
            "recipe": winner["recipe"],
            "training_slots": 16,
            "rule": "same primary/stress risk and improvement gates as complete family",
            "selection_runs": [digest_json(r["task"]) for r in [*primary, *stress_runs]],
        },
    )
    return winner["recipe"]


def stress(
    root: Path, pool: ProcessPoolExecutor, selection: dict[str, Any], protocol: dict[str, Any]
) -> None:
    fold = selection["fold"]
    recipes = [baseline_recipe()] + [
        r["selected"]["recipe"] for r in selection["families"].values()
    ]
    recipes = list({digest_json(r): r for r in recipes}.values())
    tasks = []
    for world, spec in protocol["worlds"].items():
        if spec["role"] != "final_evaluation_only":
            continue
        for cost in (2, 10):
            for recipe in recipes:
                t = task(root, fold, recipe, "test", cost=cost, world=world, fit_world="historical")
                t["upper"] = protocol["folds"][-1]["test_end"]
                tasks.append(t)
    write(root / "market-evaluation-plan.json", tasks)
    runs = batch(pool, tasks)
    write(root / "market-evaluation.json", {"runs": [digest_json(r["task"]) for r in runs]})


def main() -> None:  # noqa: PLR0912 - explicit research phases
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=("register", "search", "evaluate", "diagnostics", "stress", "process")
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.phase == "register":
        if args.snapshot is None:
            raise ValueError("Registration requires a frozen snapshot")
        register(root, args.snapshot.resolve())
        return
    protocol = verify(root)
    phase_started = time.monotonic()
    try:
        with ProcessPoolExecutor(max_workers=2, mp_context=mp.get_context("spawn")) as pool:
            if args.phase == "search":
                selections = [search_fold(root, fold, pool) for fold in protocol["folds"]]
                write(root / "historical-selections.json", selections)
            elif args.phase == "process":
                results = []
                for world, spec in protocol["worlds"].items():
                    if spec["role"] == "repeat_whole_search":
                        results.append(search_fold(root, protocol["folds"][0], pool, world=world))
                write(root / "process-selections.json", results)
                tasks = []
                for result in results:
                    recipes = [baseline_recipe()] + [
                        r["selected"]["recipe"] for r in result["families"].values()
                    ]
                    for recipe in {digest_json(r): r for r in recipes}.values():
                        tasks.extend(
                            task(
                                root, result["fold"], recipe, "test", cost=c, world=result["world"]
                            )
                            for c in (2, 10)
                        )
                write(root / "process-evaluation-plan.json", tasks)
                batch(pool, tasks)
                write(root / "process-complete.json", {"runs": [digest_json(t) for t in tasks]})
            else:
                selections = read(root / "historical-selections.json")
                if args.phase == "evaluate":
                    evaluate(root, pool, selections)
                elif args.phase == "diagnostics":
                    diagnostics(root, pool, selections)
                else:
                    stress(root, pool, selections[0], protocol)
    finally:
        write(
            root / "phase-times" / f"{args.phase}-{time.time_ns()}.json",
            {"phase": args.phase, "seconds": time.monotonic() - phase_started},
        )
    verify(root)
    print(json.dumps({"event": "phase_complete", "phase": args.phase}), flush=True)


if __name__ == "__main__":
    main()
