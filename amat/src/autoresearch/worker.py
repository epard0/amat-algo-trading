"""Credential-free fixed-operation backtest worker; invoked only by the harness."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.runner import BacktestResult
from amat.data.snapshot import load_panel
from amat.metrics import summarize
from amat.provenance import CodeRevision, RunManifest
from amat.reporting.trade_monitor import write_monitor
from autoresearch.contracts import Candidate, validate_candidate
from autoresearch.diagnostics import feature_diagnostics
from autoresearch.entry_model import MIN_POSITIVE_FOLDS, REGIMES, entry_diagnostics, forecast_key
from autoresearch.experiments import Simulation, controls
from autoresearch.extensions import validate_daily_core
from autoresearch.genetic.adapter import symbolic_complexity
from autoresearch.genetic.models import CandleGenome
from autoresearch.objective import anchored_slice, apply_incremental_score, score_run
from autoresearch.robustness import evaluate_robustness
from autoresearch.routing import ROUTING_COLUMNS
from autoresearch.trade_monitor import build_monitor
from autoresearch.workspace import load_program, write_json

ENGINE = "v1-frozen+research-signals/8"


def validation_slice(result: BacktestResult, train_end: dt.date) -> BacktestResult:
    """Carry the preceding close as a zero-activity valuation anchor.

    This preserves the first validation return without counting training fills
    or fabricating a return from the original initial cash balance.
    """
    index = next(
        (i for i, row in enumerate(result.sessions) if row.session > train_end),
        len(result.sessions),
    )
    return anchored_slice(result, index, len(result.sessions))


def persist_run(
    root: Path,
    target: Path,
    result: BacktestResult,
    candidate: Candidate,
    contract_hash: str,
    *,
    intervention: dict[str, Any] | None = None,
) -> None:
    target.mkdir(parents=True, exist_ok=True)
    # Deliberately a research envelope, not a deployable strategy TOML.
    config_bytes = ("candidate_json = " + json.dumps(candidate.canonical()) + "\n").encode()
    if intervention:
        config_bytes += (
            "intervention_json = " + json.dumps(json.dumps(intervention, sort_keys=True)) + "\n"
        ).encode()
    (target / "config.toml").write_bytes(config_bytes)
    manifest = RunManifest(
        run_id=target.name,
        strategy_name=candidate.config.strategy.name,
        strategy_version=candidate.config.strategy.version,
        config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        config_source=str((target / "config.toml").relative_to(root)),
        code_revision=CodeRevision(
            detected=False,
            reason=f"frozen source manifest in program {contract_hash}; not a promoted git release",
        ),
        python_version=sys.version.split()[0],
        execution_model_version=ENGINE,
        cost_model_version=f"{result.one_way_bps}bps",
        started_at=dt.datetime.now(dt.UTC),
    )
    (target / "manifest.json").write_text(manifest.to_json())
    summary = summarize(result).as_dict()
    combined = {
        **summary,
        "performance": summary,
        "sample": {key: summary[key] for key in ("first_session", "last_session", "sessions")},
        "engine": ENGINE,
        "config_sha256": manifest.config_sha256,
        "total_missed": result.total_missed,
    }
    write_json(
        target / "results.json",
        {
            "run_id": manifest.run_id,
            "manifest_sha256": manifest.digest(),
            "results": {"studies": [{"engine": ENGINE, "combined": combined}]},
            "program_sha256": contract_hash,
            "candidate_sha256": candidate.digest(),
        },
    )
    daily = []
    previous = float(result.initial_equity)
    for record in result.sessions:
        equity = float(record.equity)
        daily.append(
            {
                "session": str(record.session),
                "equity": equity,
                "return": equity / previous - 1,
                "cash": str(record.cash),
                "gross_exposure": record.gross_exposure,
                "forecast_volatility_pre": record.forecast_volatility_pre
                if np.isfinite(record.forecast_volatility_pre)
                else None,
                "forecast_volatility_post": record.forecast_volatility_post
                if np.isfinite(record.forecast_volatility_post)
                else None,
                "binding_constraints": list(record.binding_constraints),
                "attribution": {k: str(v) for k, v in asdict(record.attribution).items()},
                "fills": json.loads(json.dumps([asdict(f) for f in record.fills], default=str)),
                "missed": json.loads(json.dumps([asdict(f) for f in record.missed], default=str)),
                "instructions": json.loads(
                    json.dumps([asdict(order) for order in record.instructions], default=str)
                ),
            }
        )
        previous = equity
    write_json(target / "daily.json", daily)


def complexity(candidate: Candidate) -> int:
    size = len(candidate.features)
    if candidate.genetic_pattern:
        genome = candidate.genetic_pattern.pattern.genome
        if isinstance(genome, CandleGenome):
            size += len(genome.rules)
        else:
            size += symbolic_complexity(candidate.genetic_pattern.pattern)
    if candidate.entry_model:
        size += 12  # Nine fixed inputs plus timing, regime and health policy mechanisms.
    if candidate.signal:
        size += len(candidate.signal.terms) + len(candidate.signal.interactions)
        size += sum(len(pattern.conditions) for pattern in candidate.signal.patterns)
        size += 3 if candidate.signal.router else 0
        size += 3 if candidate.signal.selection else 0
    return size


def persist_entry_model(target: Path, simulation: Simulation, candidate: Candidate) -> None:
    trace = simulation.entry_model(candidate)
    if trace is None or candidate.entry_model is None:
        return
    panel = simulation.panel
    forecast = simulation.entry_forecasts[
        Candidate(config=candidate.config).digest(), forecast_key(candidate.entry_model)
    ]
    write_json(
        target / "entry-model.json",
        {
            **trace.report(panel, panel.sessions.index(simulation.program.measured_start)),
            "recipe": candidate.entry_model.model_dump(mode="json"),
            "diagnostics": entry_diagnostics(
                forecast,
                trace,
                panel.sessions.index(simulation.program.measured_start),
                len(panel),
                candidate.entry_model,
            ),
        },
    )
    np.savez_compressed(
        target / "entry-model.npz",
        increase_multipliers=trace.increase_multipliers,
        reasons=trace.reasons,
        regimes=trace.regimes,
        predictions=trace.predictions,
        utility_bps=trace.utility_bps,
        feature_z=trace.feature_z,
        raw_warnings=trace.raw_warnings,
        recent_gain_bps=trace.recent_gain_bps,
        features=trace.features,
        baseline_opportunities=forecast.opportunities,
        outcome_net_return=forecast.outcomes.net_return,
        outcome_adverse_excursion=forecast.outcomes.adverse_excursion,
        outcome_deferral_advantage=forecast.outcomes.deferral_advantage,
        outcome_markout_one=forecast.outcomes.markout_one,
        outcome_markout_five=forecast.outcomes.markout_five,
        sessions=np.array([str(s) for s in panel.sessions]),
        symbols=np.array([str(s) for s in panel.symbols]),
    )


def entry_feedback(simulation: Simulation, candidate: Candidate) -> dict[str, Any] | None:
    """Bound development evidence for the next proposer, without forward labels."""
    trace = simulation.entry_model(candidate)
    if trace is None:
        return None
    first = simulation.panel.sessions.index(simulation.program.measured_start)
    report = trace.report(simulation.panel, first)
    return {
        "status": "inactive" if report["reduced_asset_sessions"] == 0 else "research_active",
        "reduced_asset_sessions": report["reduced_asset_sessions"],
        "reason_counts": report["reason_counts"],
        "regime_evidence": [
            {
                "regime": regime,
                "refits": len(trace.fits),
                "permitted_refits": sum(
                    evidence["permitted"]
                    for block in trace.fits
                    for evidence in block["policy_evidence"]
                    if evidence["regime"] == regime
                ),
                "refits_with_two_sufficient_folds": sum(
                    sum(fold["sufficient"] for fold in evidence["folds"]) >= MIN_POSITIVE_FOLDS
                    for block in trace.fits
                    for evidence in block["policy_evidence"]
                    if evidence["regime"] == regime
                ),
            }
            for regime in REGIMES
        ],
        "interpretation": (
            "No interventions means no demonstrated portfolio contribution. Sparse evidence "
            "is distinct from negative measured deferral value; changing guards needs a new "
            "development experiment, not a performance claim."
        ),
    }


def persist_robustness(
    root: Path, target: Path, simulation: Simulation, candidate: Candidate
) -> dict[str, Any]:
    study = evaluate_robustness(simulation, candidate)
    write_json(target / "robustness.json", study.report)
    for name, (recipe, result, metadata) in study.runs.items():
        persist_run(
            root,
            target / "robustness" / name,
            result,
            recipe,
            simulation.program.digest(),
            intervention=metadata,
        )
    return study.report


def run(  # noqa: PLR0912, PLR0915 - persist a complete evaluation in the worker
    root: Path, candidate_path: Path, target: Path, *, validation: bool = False
) -> None:
    program = load_program(root)
    candidate = Candidate.model_validate_json(candidate_path.read_text())
    validate_candidate(program.baseline, candidate)
    if program.extensions_only:
        validate_daily_core(program.baseline, candidate)
    strategy = candidate.config.strategy
    end = program.validation_end if validation else program.train_end
    symbols = [str(s) for s in strategy.universe.symbols]
    panel, _ = load_panel(
        root / "snapshot", symbols, start=program.load_start, end=end, with_context=True
    )
    simulation = Simulation(panel, program, root / "snapshot")
    runs = [simulation.run(candidate, cost=cost) for cost in (2.0, 6.0)]
    target.mkdir(parents=True, exist_ok=True)
    persist_entry_model(target, simulation, candidate)
    if validation:
        runs = [validation_slice(result, program.train_end) for result in runs]
        write_json(
            target / "validation.json",
            {
                "candidate_sha256": candidate.digest(),
                "valuation_anchor": str(runs[0].sessions[0].session),
                "validation_start": str(runs[0].sessions[1].session),
                "validation": summarize(runs[0]).as_dict(),
                "stress": summarize(runs[1]).as_dict(),
                "previously_inspected_history": True,
                "execution_authority": "none",
            },
        )
    else:
        result = score_run(runs[0], runs[1], program, complexity(candidate))
        trace = simulation.trace(candidate)
        selection = simulation.selection(candidate)
        if selection is not None and candidate.signal and candidate.signal.selection:
            write_json(
                target / "selection.json",
                {
                    **selection.report(panel, panel.sessions.index(program.measured_start)),
                    "recipe": candidate.signal.selection.model_dump(mode="json"),
                },
            )
            np.savez_compressed(
                target / "selection.npz",
                scores=selection.scores,
                eligible=selection.eligible,
                selected=selection.selected,
                rank=selection.rank,
                reasons=selection.reasons,
                dollar_volume=selection.dollar_volume,
                relative_volume=selection.relative_volume,
                window_dollar_volume=selection.window_dollar_volume,
                sessions=np.array([str(s) for s in panel.sessions]),
                symbols=np.array([str(s) for s in panel.symbols]),
            )
        diagnostic_trace = simulation.diagnostic_trace(candidate)
        diagnostic = feature_diagnostics(panel, diagnostic_trace, program)
        control_report, control_runs = controls(simulation, candidate)
        write_json(target / "diagnostics.json", diagnostic)
        write_json(target / "controls.json", control_report)
        write_json(target / "fits.json", trace.fits)
        np.savez_compressed(
            target / "signals.npz",
            scores=trace.scores,
            raw_scores=trace.raw_scores,
            effective_scores=diagnostic_trace.scores,
            predictors=trace.predictors,
            routing_scores=trace.routing_scores
            if trace.routing_scores is not None
            else np.empty((*trace.scores.shape, 0)),
            routing_columns=np.array(ROUTING_COLUMNS if trace.routing_scores is not None else ()),
            pattern_scores=trace.pattern_scores
            if trace.pattern_scores is not None
            else np.empty((*trace.scores.shape, 0)),
            genetic_scores=trace.genetic_scores
            if trace.genetic_scores is not None
            else np.empty((*trace.scores.shape, 0)),
            baseline=trace.baseline,
            sessions=np.array([str(s) for s in panel.sessions]),
            symbols=np.array([str(s) for s in panel.symbols]),
        )
        result["diagnostics"] = diagnostic
        result["control_summary"] = {
            key: value for key, value in control_report.items() if key != "recipes"
        }
        result["evaluation_use"] = "adaptive_chronological_development"
        result["fit_count"] = len(trace.fits)
        result["entry_quality"] = entry_feedback(simulation, candidate)
        entry = simulation.entry_model(candidate)
        result["entry_model_fit_count"] = (
            sum(fit["status"] == "fitted" for block in entry.fits for fit in block["training"])
            if entry is not None
            else 0
        )
        result["robustness"] = persist_robustness(root, target, simulation, candidate)
        result["failed_gates"].extend(
            [] if result["robustness"]["integrity_passed"] else ["scenario_integrity_failure"]
        )
        if not control_report.get("baseline"):
            gates = result["failed_gates"]
            exposure = control_report["exposure_match"]
            if not exposure["matched"]:
                gates.append("exposure_match_unresolved")
            elif exposure["sharpe_difference"] <= 0:
                gates.append("no_incremental_exposure_matched_information")
            if not control_report["nearby_stable"]:
                gates.append("nearby_parameter_instability")
            if not control_report["delayed_positive"]:
                gates.append("execution_delay_sensitivity")
            if control_report.get("router_beats_static") is False:
                gates.append("router_has_no_incremental_static_blend_value")
            if control_report.get("selection_adds_value") is False:
                gates.append("selection_has_no_incremental_value")
            if any(run.warnings for run in control_runs.values()):
                gates.append("control_runner_warning")
        apply_incremental_score(
            result, control_report, program, complexity(candidate), complexity(program.baseline)
        )
        result["eligible"] = not result["failed_gates"]
        for name, control_run in control_runs.items():
            recipe = control_report["recipes"][name]
            persist_run(
                root,
                target / "controls" / name,
                control_run,
                Candidate.model_validate(recipe["candidate"]),
                program.digest(),
                intervention={key: value for key, value in recipe.items() if key != "candidate"},
            )
        if "exposure_candidate" in control_runs:
            from autoresearch.validation_contract import matched_periods  # noqa: PLC0415

            left = json.loads((target / "controls/exposure_candidate/daily.json").read_text())
            right = json.loads((target / "controls/exposure_control/daily.json").read_text())
            calendar_path = root / "runtime/development-calendar.json"
            calendar = (
                json.loads(calendar_path.read_text())["sessions"]
                if calendar_path.exists()
                else [str(day.session) for day in control_runs["exposure_candidate"].sessions]
            )
            paired = matched_periods(left, right, calendar)
            for period in paired:
                period["exposure_matching"] = control_report["exposure_match"]["interpretation"]
            write_json(target / "matched-periods.json", paired)
        write_json(target / "objective.json", result)
    persist_run(root, target / "primary", runs[0], candidate, program.digest())
    persist_run(root, target / "stress", runs[1], candidate, program.digest())
    for name, recorded in zip(("primary", "stress"), runs, strict=True):
        write_monitor(
            target / name,
            build_monitor(simulation, candidate, recorded, run_id=name),
        )


if __name__ == "__main__":
    run(
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        Path(sys.argv[3]),
        validation="--validation" in sys.argv[4:],
    )
