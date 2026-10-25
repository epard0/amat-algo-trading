"""Historical regime attribution and paired alternate-world strategy reruns."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from amat.backtest.runner import BacktestResult
from amat.metrics import summarize
from amat.metrics.distribution import return_distribution_summary
from amat.validation.montecarlo import bootstrap_panel
from amat.validation.regimes import classify_sessions, regime_performance
from autoresearch.contracts import Candidate
from autoresearch.experiments import Simulation
from autoresearch.market_scenarios import (
    SCENARIOS,
    panel_digest,
    scenario_metadata,
    scenario_panel,
)

MIN_REGIME_SESSIONS = 60
EXECUTION_STRESSES = (
    ("missed_5pct", 6.0, 20, 0),
    ("missed_10pct", 6.0, 10, 0),
    ("cost_12bps", 12.0, None, 0),
    ("cost_24bps", 24.0, None, 0),
    ("delayed_2_sessions", 6.0, None, 2),
    ("combined_execution", 24.0, 10, 2),
)


@dataclass(frozen=True)
class RobustnessStudy:
    report: dict[str, Any]
    runs: dict[str, tuple[Candidate, BacktestResult, dict[str, Any]]]


def historical_conditions(simulation: Simulation, candidate: Candidate) -> dict[str, Any]:
    panel = simulation.panel
    if "SPY" not in [str(s) for s in panel.symbols]:
        return {"available": False, "reason": "SPY reference absent; no substitute inferred"}
    labels = classify_sessions(panel)
    result: dict[str, Any] = {
        "available": True,
        "reference": "SPY",
        "interpretation": (
            "prior-session causal labels; noncontiguous cohorts, not tradeable portfolios"
        ),
    }
    for name, recipe in (("candidate", candidate), ("baseline", simulation.program.baseline)):
        run = simulation.run(recipe)
        slices = regime_performance(
            [float(value) for value in run.equity_curve], run.session_dates, panel, labels
        )
        result[name] = [
            {**asdict(item), "sufficient_observations": item.sessions >= MIN_REGIME_SESSIONS}
            for item in slices
        ]
    return result


def evaluate_robustness(simulation: Simulation, candidate: Candidate) -> RobustnessStudy:
    program, source = simulation.program, simulation.panel
    if source.sessions[-1] > program.train_end:
        raise ValueError("scenario suite must not consume checkpoint history")
    report: dict[str, Any] = {
        "schema_version": "market-robustness/2",
        "mode": program.evaluation.robustness,
        "historical_conditions": historical_conditions(simulation, candidate),
        "scenarios": {},
        "selection_use": (
            "diagnostics only; hypothetical profitability does not improve the objective"
        ),
        "source_panel_sha256": panel_digest(source),
        "source_end": str(source.sessions[-1]),
        "integrity_passed": True,
    }
    runs: dict[str, tuple[Candidate, BacktestResult, dict[str, Any]]] = {}
    if program.evaluation.robustness != "extended":
        return RobustnessStudy(report, runs)

    def evaluate(name: str, engine: Simulation, metadata: dict[str, Any], **settings: Any) -> None:
        paired: dict[str, Any] = {}
        for role, recipe in (("candidate", candidate), ("baseline", program.baseline)):
            run = engine.run(recipe, **settings)
            trace = engine.trace(recipe)
            scores_finite = bool(np.isfinite(trace.scores).all())
            equity = np.asarray(run.equity_curve, dtype=float)
            if not np.isfinite(equity).all() or np.any(equity <= 0):
                raise ValueError(f"invalid scenario equity: {name}/{role}")
            paired[role] = {
                **summarize(run).as_dict(),
                "return_distribution": return_distribution_summary(equity[1:] / equity[:-1] - 1),
                "missed_fills": run.total_missed,
                "warnings": list(run.warnings),
                "scores_finite": scores_finite,
            }
            report["integrity_passed"] &= scores_finite and not bool(run.warnings)
            # Fits are saved alongside results, never reused from the real path.
            runs[f"{name}/{role}"] = (recipe, run, {**metadata, **settings, "fits": trace.fits})
        report["integrity_passed"] &= metadata.get("coverage_complete", True)
        report["scenarios"][name] = {
            **metadata,
            **paired,
            "sharpe_difference": paired["candidate"]["sharpe"] - paired["baseline"]["sharpe"],
            "return_difference": paired["candidate"]["total_return"]
            - paired["baseline"]["total_return"],
        }

    # Execution-only stresses keep real prices, actions, and calibrated labels.
    for name, cost, cadence, delay in EXECUTION_STRESSES:
        evaluate(
            name,
            simulation,
            {
                "kind": "historical_execution_stress",
                "one_way_bps": cost,
                "drop_every_nth_fill": cadence,
                "additional_signal_delay_sessions": delay,
            },
            cost=cost,
            missed_every=cadence,
            delay=delay,
        )

    first = source.sessions.index(program.measured_start)
    seed = program.evaluation.scenario_seed
    for index, kind in enumerate(SCENARIOS):
        for path in range(program.evaluation.scenario_synthetic_paths):
            world_seed = seed + index * 100 + path * 100000
            world = scenario_panel(source, first=first, kind=kind, seed=world_seed)
            engine = Simulation(world, program, None)
            cost = 24.0 if kind == "liquidity_crisis" else 6.0
            market_returns = (
                world.adjusted_closes[first + 1 :] / world.adjusted_closes[first:-1] - 1
            )
            evaluate(
                kind if path == 0 else f"{kind}_path_{path}",
                engine,
                {
                    **scenario_metadata(
                        kind, count=len(world) - first, with_candles=source.has_candle_inputs
                    ),
                    "kind": "hypothetical_synthetic",
                    "scenario": kind,
                    "path_index": path,
                    "seed": world_seed,
                    "source": world.source,
                    "panel_sha256": panel_digest(world),
                    "calibration_stop_exclusive": str(program.measured_start),
                    "corporate_actions": "none; total returns embedded in prices",
                    "one_way_bps": cost,
                    "market_return_distributions": {
                        str(symbol): return_distribution_summary(market_returns[:, column])
                        for column, symbol in enumerate(world.symbols)
                    },
                },
                cost=cost,
            )

    for index in range(program.evaluation.scenario_bootstrap_paths):
        world_seed = seed + 10000 + index
        # Different dependence lengths exercise sensitivity to block choice.
        block = (20, 60, 126)[index % 3]
        world = bootstrap_panel(source, seed=world_seed, mean_block_sessions=block)
        evaluate(
            f"bootstrap_{index}",
            Simulation(world, program, None),
            {
                "kind": "hypothetical_joint_bootstrap",
                "seed": world_seed,
                "mean_block_sessions": block,
                "source": world.source,
                "panel_sha256": panel_digest(world),
                "one_way_bps": 6.0,
                "corporate_actions": "none; total returns embedded in prices",
                "interpretation": (
                    "resampled development history; not an independent sample or forecast"
                ),
            },
            cost=6.0,
        )
    hypothetical = [
        row for row in report["scenarios"].values() if row["kind"].startswith("hypothetical")
    ]
    incomplete = [
        name for name, row in report["scenarios"].items() if not row.get("coverage_complete", True)
    ]
    expected = (
        len(EXECUTION_STRESSES)
        + len(SCENARIOS) * program.evaluation.scenario_synthetic_paths
        + program.evaluation.scenario_bootstrap_paths
    )
    report["coverage"] = {
        "expected_cases": expected,
        "completed_cases": len(report["scenarios"]),
        "incomplete_scenarios": incomplete,
        "complete": not incomplete and len(report["scenarios"]) == expected,
        "synthetic_paths_per_scenario": program.evaluation.scenario_synthetic_paths,
    }
    report["hypothetical_summary"] = {
        "count": len(hypothetical),
        "worst_max_drawdown": min(row["candidate"]["max_drawdown"] for row in hypothetical),
        "positive_return_cases": sum(row["candidate"]["total_return"] > 0 for row in hypothetical),
        "interpretation": "counts across declared scenarios, not probabilities of future success",
    }
    return RobustnessStudy(report, runs)
