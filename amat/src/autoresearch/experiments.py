"""Fixed-operation strategy simulation and explanatory control experiments."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import BacktestResult, DecisionOverlay, run_backtest
from amat.data.snapshot import load_corporate_actions
from amat.domain.types import Side
from amat.metrics import summarize
from amat.regime.asset import build_asset_regime_history
from autoresearch.contracts import Candidate, Program
from autoresearch.diagnostics import paired_uncertainty
from autoresearch.entry_model import (
    EntryForecast,
    EntryModelTrace,
    apply_entry_policy,
    build_entry_forecast,
    forecast_key,
)
from autoresearch.features import feature_overlay
from autoresearch.features import feature_values as legacy_values
from autoresearch.genetic.adapter import GeneticSymbolicAdapterSpec
from autoresearch.objective import anchored_slice
from autoresearch.registry import describe
from autoresearch.routing import ROUTING_COLUMNS
from autoresearch.selection import SelectionTrace, select_assets
from autoresearch.signals import SignalTrace, build_signal
from autoresearch.specs import FeatureSpec
from autoresearch.variants import ablations, neighbors

ENTRY_PARTICIPATION_CAP = 0.01


def shifted(values: np.ndarray, delay: int, fill: float = 0.0) -> np.ndarray:
    if delay == 0:
        return values
    out = np.full_like(values, fill)
    if delay < len(values):
        out[delay:] = values[:-delay]
    return out


def measured(result: BacktestResult, program: Program) -> BacktestResult:
    index = next(
        i for i, row in enumerate(result.sessions) if row.session == program.measured_start
    )
    return anchored_slice(result, index + 1, len(result.sessions))


class Simulation:
    def __init__(self, panel: MarketPanel, program: Program, snapshot: Path | None) -> None:
        self.panel, self.program = panel, program
        if snapshot is None and not panel.source.startswith(("scenario(", "bootstrap(")):
            raise ValueError("only declared hypothetical panels may omit corporate actions")
        self.actions = (
            load_corporate_actions(
                snapshot, [str(s) for s in panel.symbols], sessions=panel.sessions
            )
            if snapshot is not None
            else {}
        )
        self.traces: dict[str, SignalTrace] = {}
        self.overlays: dict[str, DecisionOverlay] = {}
        self.selections: dict[str, SelectionTrace] = {}
        self.entry_forecasts: dict[tuple[str, tuple[int, ...]], EntryForecast] = {}
        self.entry_traces: dict[str, EntryModelTrace] = {}
        self.entry_opportunities: dict[str, np.ndarray] = {}
        self.results: dict[tuple[str, float, int, float, bool, int | None], BacktestResult] = {}

    def entry_model(self, candidate: Candidate) -> EntryModelTrace | None:
        spec = candidate.entry_model
        if spec is None:
            return None
        key = candidate.digest()
        if key not in self.entry_traces:
            baseline = Candidate(config=candidate.config)
            reference = baseline.digest()
            if reference not in self.entry_opportunities:
                # Actual baseline buys define past training opportunities only.
                # Their future occurrence is never an input to today's decision.
                result = run_backtest(
                    baseline.config.strategy,
                    self.panel,
                    baseline.config.strategy.to_universe(),
                    actions_by_session=self.actions,
                    one_way_bps=2.0,
                    max_entry_window_participation=ENTRY_PARTICIPATION_CAP,
                )
                opportunities = np.zeros_like(self.panel.adjusted_closes, dtype=bool)
                dates = {session: i for i, session in enumerate(self.panel.sessions)}
                columns = {symbol: i for i, symbol in enumerate(self.panel.symbols)}
                for record in result.sessions:
                    decision = dates[record.session] - 1
                    for fill in record.fills:
                        if fill.side == Side.BUY and decision >= 0:
                            opportunities[decision, columns[fill.symbol]] = True
                self.entry_opportunities[reference] = opportunities
            cache_key = reference, forecast_key(spec)
            if cache_key not in self.entry_forecasts:
                self.entry_forecasts[cache_key] = build_entry_forecast(
                    self.panel, self.program, spec, self.entry_opportunities[reference]
                )
            self.entry_traces[key] = apply_entry_policy(
                self.entry_forecasts[cache_key], spec, self.program
            )
        return self.entry_traces[key]

    def trace(self, candidate: Candidate) -> SignalTrace:
        key = candidate.digest()
        if key not in self.traces:
            self.traces[key] = build_signal(self.panel, candidate, self.program)
        return self.traces[key]

    def overlay(self, candidate: Candidate) -> DecisionOverlay:
        key = candidate.digest()
        if key not in self.overlays:
            overlay = feature_overlay(self.panel, candidate.features)
            entry = self.entry_model(candidate)
            if entry is not None:
                overlay = replace(overlay, increase_multipliers=entry.increase_multipliers)
            if candidate.config.asset_regime.capital_control_enabled:
                original = build_asset_regime_history(
                    self.panel, candidate.config.asset_regime, candidate.config.liquidity
                ).overlay
                overlay = DecisionOverlay(
                    original.risk_multipliers * overlay.risk_multipliers,
                    original.allow_increase,
                    original.increase_multipliers,
                )
            self.overlays[key] = overlay
        return self.overlays[key]

    def selection(
        self, candidate: Candidate, *, scores: np.ndarray | None = None
    ) -> SelectionTrace | None:
        if candidate.signal is None or candidate.signal.selection is None:
            return None
        key = candidate.digest()
        if scores is None and key in self.selections:
            return self.selections[key]
        cluster_of = {
            str(symbol): cluster.name
            for cluster in candidate.config.strategy.universe.clusters
            for symbol in cluster.symbols
        }
        overlay = self.overlay(candidate)
        selection = select_assets(
            self.panel,
            self.trace(candidate).scores if scores is None else scores,
            candidate.signal.selection,
            clusters=tuple(cluster_of[str(symbol)] for symbol in self.panel.symbols),
            allow_increase=overlay.allow_increase & (overlay.risk_multipliers > 0),
        )
        if scores is None:
            self.selections[key] = selection
        return selection

    def run(
        self,
        candidate: Candidate,
        *,
        cost: float = 2.0,
        delay: int = 0,
        scalar: float = 1.0,
        shuffle: bool = False,
        missed_every: int | None = None,
    ) -> BacktestResult:
        key = (candidate.digest(), cost, delay, scalar, shuffle, missed_every)
        if key in self.results:
            return self.results[key]
        trace = self.trace(candidate)
        overlay = self.overlay(candidate)
        scores = trace.scores.copy()
        if shuffle:
            first = self.panel.sessions.index(self.program.measured_start)
            blocks = [
                np.arange(i, min(i + self.program.evaluation.block_sessions, len(self.panel)))
                for i in range(first, len(self.panel), self.program.evaluation.block_sessions)
            ]
            order = np.random.default_rng(20260911).permutation(len(blocks))
            indices = np.concatenate([blocks[i] for i in order])
            scores[first:] = scores[indices]
            multipliers = overlay.risk_multipliers.copy()
            multipliers[first:] = multipliers[indices]
            overlay = replace(overlay, risk_multipliers=multipliers)
            if overlay.increase_multipliers is not None:
                increases = overlay.increase_multipliers.copy()
                increases[first:] = increases[indices]
                overlay = replace(overlay, increase_multipliers=increases)
        selection = self.selection(candidate, scores=scores if shuffle else None)
        if selection is not None:
            if (
                candidate.signal
                and candidate.signal.selection
                and candidate.signal.selection.mode == "entries"
            ):
                # Eligibility authorizes additions, never demands a liquidation.
                # Existing holdings can still decrease under the same signal/risk policy.
                overlay = replace(
                    overlay, allow_increase=overlay.allow_increase & selection.selected
                )
            else:
                scores = selection.scores
        overlay = DecisionOverlay(
            shifted(overlay.risk_multipliers, delay) * scalar,
            shifted(overlay.allow_increase, delay),
            shifted(overlay.increase_multipliers, delay)
            if overlay.increase_multipliers is not None
            else None,
        )
        result = run_backtest(
            candidate.config.strategy,
            self.panel,
            candidate.config.strategy.to_universe(),
            actions_by_session=self.actions,
            decision_overlay=overlay,
            one_way_bps=cost,
            drop_every_nth_fill=missed_every,
            max_entry_window_participation=ENTRY_PARTICIPATION_CAP
            if self.program.schema_version
            in ("strategy-development/7", "strategy-development/8", "strategy-development/9")
            else None,
            signal_override=shifted(scores, delay)
            if candidate.signal or candidate.genetic_pattern or delay or shuffle
            else None,
        )
        self.results[key] = measured(result, self.program)
        return self.results[key]

    def diagnostic_trace(self, candidate: Candidate) -> SignalTrace:
        trace = self.trace(candidate)
        values = [trace.predictors[:, :, i] for i in range(trace.predictors.shape[-1])]
        descriptions = list(trace.features)
        first = self.panel.sessions.index(self.program.measured_start)
        edges = list(trace.fits[0]["feature_quantiles"]) if trace.fits else []
        if trace.routing_scores is not None:
            for index, name in enumerate(ROUTING_COLUMNS):
                values.append(trace.routing_scores[:, :, index])
                descriptions.append(
                    {
                        "id": f"{candidate.digest()}:{name}",
                        "recipe": {"kind": name},
                        "metadata": {"family": "routing", "units": "fraction"},
                    }
                )
                edges.append(trace.fits[0]["routing_quantiles"][index])
        if candidate.signal and trace.pattern_scores is not None:
            for index, pattern in enumerate(candidate.signal.patterns):
                values.append(trace.pattern_scores[:, :, index])
                descriptions.append(
                    {
                        "id": pattern.digest(),
                        "recipe": {"kind": "pattern", **pattern.model_dump(mode="json")},
                        "metadata": {"family": "temporal_pattern", "units": "binary_match"},
                    }
                )
                edges.append(trace.fits[0]["pattern_quantiles"][index])
        if candidate.genetic_pattern and trace.genetic_scores is not None:
            values.append(trace.genetic_scores)
            symbolic = isinstance(candidate.genetic_pattern, GeneticSymbolicAdapterSpec)
            descriptions.append(
                {
                    "id": candidate.genetic_pattern.genome_sha256,
                    "recipe": {
                        "kind": "genetic_symbolic" if symbolic else "genetic_candle",
                        **candidate.genetic_pattern.model_dump(mode="json"),
                    },
                    "metadata": {
                        "family": "genetic_pattern",
                        "units": "bounded_numerical" if symbolic else "binary_match",
                    },
                }
            )
            sample = trace.genetic_scores[:first]
            sample = sample[np.isfinite(sample)]
            edges.append(np.quantile(sample, [0.2, 0.4, 0.6, 0.8]).tolist() if len(sample) else [])
        for feature in candidate.features:
            values.append(legacy_values(self.panel, feature))
            spec = FeatureSpec(
                kind=feature.kind,
                lookback=feature.lookback,
                smoothing=feature.smoothing,
                transform="cross_sectional_rank" if feature.cross_sectional_rank else "identity",
            )
            description = describe(spec)
            description["legacy_attenuation_recipe"] = feature.model_dump(mode="json")
            descriptions.append(description)
            sample = values[-1][: max(0, first - 62)]
            sample = sample[np.isfinite(sample)]
            edges.append(np.quantile(sample, [0.2, 0.4, 0.6, 0.8]).tolist() if len(sample) else [])
        predictors = np.stack(values, axis=-1) if values else trace.predictors
        selection = self.selection(candidate)
        entry_only = bool(
            candidate.signal
            and candidate.signal.selection
            and candidate.signal.selection.mode == "entries"
        )
        return replace(
            trace,
            predictors=predictors,
            features=descriptions,
            scores=(trace.scores if selection is None or entry_only else selection.scores)
            * feature_overlay(self.panel, candidate.features).risk_multipliers,
            fits=[{"feature_quantiles": edges}],
        )


def exposure_control(
    simulation: Simulation, candidate: Candidate, *, reference: Candidate | None = None
) -> tuple[dict[str, Any], dict[str, BacktestResult]]:
    baseline = reference if reference is not None else simulation.program.baseline
    primary, control = simulation.run(candidate), simulation.run(baseline)
    candidate_exposure = summarize(primary).average_gross_exposure
    baseline_exposure = summarize(control).average_gross_exposure
    # Calibrate the more exposed strategy downward on development data only.
    # This is explicitly retrospective, and never used as an executable recipe.
    scale_candidate = candidate_exposure > baseline_exposure
    scaled = candidate if scale_candidate else baseline
    target = min(candidate_exposure, baseline_exposure)
    original = primary if scale_candidate else control
    scalar = min(1.0, target / max(summarize(original).average_gross_exposure, 1e-12))
    best, error, best_scalar = original, float("inf"), scalar
    for _ in range(4):
        result = simulation.run(scaled, scalar=scalar)
        exposure = summarize(result).average_gross_exposure
        difference = abs(exposure - target)
        if difference < error:
            best, error, best_scalar = result, difference, scalar
        if difference <= simulation.program.evaluation.exposure_tolerance:
            break
        scalar = min(1.0, scalar * target / max(exposure, 1e-12))
    left, right = (best, control) if scale_candidate else (primary, best)
    a, b = summarize(left), summarize(right)
    return {
        "interpretation": "retrospective development exposure match; not a tradeable benchmark",
        "scaled": "candidate" if scale_candidate else "baseline",
        "scalar": best_scalar,
        "candidate": a.as_dict(),
        "control": b.as_dict(),
        "exposure_error": error,
        "matched": error <= simulation.program.evaluation.exposure_tolerance,
        "annualized_return_difference": a.annualized_return - b.annualized_return,
        "sharpe_difference": a.sharpe - b.sharpe,
    }, {"exposure_candidate": left, "exposure_control": right}


def controls(
    simulation: Simulation, candidate: Candidate
) -> tuple[dict[str, Any], dict[str, BacktestResult]]:
    if candidate == simulation.program.baseline:
        return {"baseline": True}, {}
    report: dict[str, Any] = {}
    runs: dict[str, BacktestResult] = {}
    exposure, exposure_runs = exposure_control(simulation, candidate)
    report["exposure_match"] = exposure
    runs.update(exposure_runs)
    report["recipes"] = {
        "exposure_candidate": {
            "candidate": candidate.model_dump(mode="json"),
            "scalar": exposure["scalar"] if exposure["scaled"] == "candidate" else 1.0,
        },
        "exposure_control": {
            "candidate": simulation.program.baseline.model_dump(mode="json"),
            "scalar": exposure["scalar"] if exposure["scaled"] == "baseline" else 1.0,
        },
    }
    for delay in (1, 2):
        name = f"delay_{delay}"
        runs[name] = simulation.run(candidate, delay=delay)
        report["recipes"][name] = {"candidate": candidate.model_dump(mode="json"), "delay": delay}
    for name, variant in (*ablations(candidate), *neighbors(candidate)):
        runs[name] = simulation.run(variant)
        report["recipes"][name] = {"candidate": variant.model_dump(mode="json")}
    runs["block_shuffle"] = simulation.run(candidate, shuffle=True)
    report["recipes"]["block_shuffle"] = {
        "candidate": candidate.model_dump(mode="json"),
        "shuffle_seed": 20260911,
        "block_sessions": simulation.program.evaluation.block_sessions,
    }
    report["measurements"] = {name: summarize(result).as_dict() for name, result in runs.items()}
    if candidate.signal and candidate.signal.router:
        static = {
            name: result
            for name, result in runs.items()
            if name in ("router_equal_blend", "router_training_mean", "router_equal_ungated")
        }
        primary = simulation.run(candidate)
        report["router_static_uncertainty"] = {
            name: paired_uncertainty(
                daily_returns(primary), daily_returns(result), simulation.program
            )
            for name, result in static.items()
        }
        report["router_beats_static"] = bool(static) and all(
            item["passes"] for item in report["router_static_uncertainty"].values()
        )
    if candidate.signal and candidate.signal.selection:
        reference = Candidate.model_validate(report["recipes"]["selection_disabled"]["candidate"])
        match, matched_runs = exposure_control(simulation, candidate, reference=reference)
        report["selection_exposure_match"] = match
        for name, run in matched_runs.items():
            runs[f"selection_{name}"] = run
            role = "candidate" if name == "exposure_candidate" else "baseline"
            recipe = candidate if role == "candidate" else reference
            report["recipes"][f"selection_{name}"] = {
                "candidate": recipe.model_dump(mode="json"),
                "scalar": match["scalar"] if match["scaled"] == role else 1.0,
            }
        report["selection_uncertainty"] = paired_uncertainty(
            daily_returns(matched_runs["exposure_candidate"]),
            daily_returns(matched_runs["exposure_control"]),
            simulation.program,
        )
        report["selection_adds_value"] = (
            match["matched"] and report["selection_uncertainty"]["passes"]
        )
        report["measurements"].update(
            {f"selection_{name}": summarize(run).as_dict() for name, run in matched_runs.items()}
        )
    report["block_shuffle_interpretation"] = (
        "one deterministic, noncausal timing falsification; not a p-value"
    )
    nearby = [
        summarize(result).sharpe for name, result in runs.items() if name.startswith("nearby_")
    ]
    report["nearby_sharpe_floor"] = max(0.0, 0.75 * summarize(simulation.run(candidate)).sharpe)
    report["nearby_stable"] = bool(nearby) and min(nearby) > report["nearby_sharpe_floor"]
    report["delayed_positive"] = all(
        summarize(runs[f"delay_{delay}"]).sharpe > 0 for delay in (1, 2)
    )
    report["runner_warnings"] = {
        name: list(run.warnings) for name, run in runs.items() if run.warnings
    }
    return report, runs


def daily_returns(result: BacktestResult) -> np.ndarray:
    equity = np.array([float(row.equity) for row in result.sessions])
    return np.asarray(equity[1:] / equity[:-1] - 1)
