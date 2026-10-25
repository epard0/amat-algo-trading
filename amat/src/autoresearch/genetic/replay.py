"""Frozen, matched portfolio replay of previously selected genetic patterns.

Historical chronological evaluation is development evidence, never a pristine
holdout, a promotion decision, or authorization to trade.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.runner import BacktestResult
from amat.data.action_exceptions import default_ledger_path
from amat.data.snapshot import load_panel
from amat.metrics import summarize
from autoresearch.baseline import load_research_config
from autoresearch.calendar_audit import audit_sessions
from autoresearch.contracts import Candidate, Program
from autoresearch.experiments import Simulation, exposure_control
from autoresearch.genetic.adapter import (
    SYMBOLIC_PROGRAM_VERSION,
    GeneticAdapterSpec,
    GeneticSymbolicAdapterSpec,
)
from autoresearch.genetic.data import digest_json, load_daily
from autoresearch.genetic.models import (
    PatternDefinition,
    SymbolicGenome,
    canonical_genome_digest,
    parse_pattern,
)
from autoresearch.genetic.search import EvaluationResult, SearchProtocol, run_search
from autoresearch.genetic.study import write_json
from autoresearch.genetic.symbolic import (
    SYMBOLIC_REGISTRY_VERSION,
    evaluate_symbolic,
    symbolic_registry_digest,
)
from autoresearch.genetic.symbolic_search import SymbolicSearchProtocol, run_symbolic_search
from autoresearch.market_scenarios import panel_digest
from autoresearch.objective import anchored_slice, apply_incremental_score, score_run
from autoresearch.worker import complexity, persist_run, validation_slice
from autoresearch.workspace import dependencies

REPLAY_VERSION = "genetic-portfolio-replay/1"


def _normalized(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _normalized(asdict(value))
    if isinstance(value, float) and math.isnan(value):
        return ("__nonfinite_float__", "nan")
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_normalized(item) for item in value)
    return value


def exact_replay_equal(left: Any, right: Any) -> bool:
    """Compare every record field exactly, treating missing NaN floats as equal."""
    return bool(_normalized(left) == _normalized(right))


def _actions_payload(simulation: Simulation) -> dict[str, Any]:
    return {
        str(day): [json.loads(json.dumps(asdict(action), default=str)) for action in actions]
        for day, actions in simulation.actions.items()
    }


def load_incumbent(path: Path) -> Candidate:
    if path.suffix.lower() == ".json":
        return Candidate.model_validate_json(path.read_text())
    return Candidate(config=load_research_config(path))


def _read_study(  # noqa: PLR0912 - verify both versioned discovery envelopes
    directory: Path, method: str
) -> dict[str, Any]:
    artifacts = {
        name: (directory / name).read_text()
        for name in ("protocol.json", "checkpoint.json", "report.json", "champion.pattern.json")
    }
    frozen = json.loads(artifacts["protocol.json"])
    wrapper = json.loads(artifacts["checkpoint.json"])
    state = wrapper["state"]
    report = json.loads(artifacts["report.json"])
    if wrapper["sha256"] != digest_json(state):
        raise ValueError("discovery checkpoint checksum mismatch")
    symbolic = frozen.get("schema_version") == "training-symbolic-screen/1"
    if frozen.get("schema_version") not in {
        "training-symbolic-screen/1",
        "training-candle-screen/1",
    }:
        raise ValueError("unsupported discovery study version")
    protocol = (
        SymbolicSearchProtocol.from_dict(frozen["search"])
        if symbolic
        else SearchProtocol(**frozen["search"])
    )
    if protocol.method != method:
        raise ValueError(f"expected {method} discovery study")
    if not frozen.get("training_only") or frozen.get("execution_authority") != "none":
        raise ValueError("discovery must be a training-only study")
    if (
        report.get("status") != "completed"
        or state["next_slot"] != protocol.budget
        or frozen["candidate_slot_budget"] != protocol.budget
        or report.get("protocol_sha256") != digest_json(frozen)
        or report.get("champion") != state.get("champion")
    ):
        raise ValueError("discovery is incomplete or its report provenance is inconsistent")
    champion = state.get("champion")
    if champion is None or champion.get("status") != "evaluated":
        raise ValueError("discovery has no eligible champion")
    pattern = parse_pattern(champion["pattern"])
    if canonical_genome_digest(pattern) != champion["genome_hash"]:
        raise ValueError("champion genotype hash mismatch")
    selected = parse_pattern(artifacts["champion.pattern.json"])
    if selected != pattern:
        raise ValueError("champion pattern file differs from frozen checkpoint")
    # A zero-slot resume validates both inner payload checksum and full context.
    evaluator_context = {
        "code": frozen["code_sha256"],
        "screen": frozen["screen"],
        "runtime_versions": frozen["runtime_versions"],
    }
    if symbolic:
        evaluator_context["symbolic_context"] = frozen["symbolic_context"]
        if not isinstance(pattern.genome, SymbolicGenome):
            raise ValueError("symbolic discovery champion must be symbolic")
    elif isinstance(pattern.genome, SymbolicGenome):
        raise ValueError("candle discovery champion must be a candle")
    evaluator_hash = digest_json(evaluator_context)
    if isinstance(protocol, SymbolicSearchProtocol):
        run_symbolic_search(
            protocol,
            lambda _: EvaluationResult(None, failure="not evaluated"),
            pattern_template=champion["pattern"],
            evaluator_hash=evaluator_hash,
            data_hash=frozen["dataset_sha256"],
            checkpoint=state,
            max_slots=0,
        )
    else:
        run_search(
            protocol,
            lambda _: EvaluationResult(None, failure="not evaluated"),
            pattern_template=champion["pattern"],
            evaluator_hash=evaluator_hash,
            data_hash=frozen["dataset_sha256"],
            checkpoint=state,
            max_slots=0,
        )
    return {
        "protocol": frozen,
        "protocol_sha256": digest_json(frozen),
        "checkpoint_sha256": digest_json(state),
        "champion": champion,
        "artifacts": artifacts,
        "artifact_sha256": {
            name: hashlib.sha256(raw.encode()).hexdigest() for name, raw in artifacts.items()
        },
        "search_counts": {
            name: report[name]
            for name in ("recorded_slots", "unique_genomes", "cached_slots", "failed_slots")
        },
    }


def matched_studies(ga: Path, random: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reject any unequal search degree of freedom, scoring rule or dataset."""
    left, right = _read_study(ga, "genetic"), _read_study(random, "random")
    a, b = left["protocol"], right["protocol"]
    if a["schema_version"] != b["schema_version"] or a.get("symbolic_context") != b.get(
        "symbolic_context"
    ):
        raise ValueError("discovery control mismatch: family or symbolic context")
    for key in (
        "dataset_sha256",
        "dataset",
        "screen",
        "candidate_slot_budget",
        "code_sha256",
        "runtime_versions",
    ):
        if a[key] != b[key]:
            raise ValueError(f"discovery control mismatch: {key}")
    searches = [{k: v for k, v in x["search"].items() if k != "method"} for x in (a, b)]
    if searches[0] != searches[1]:
        raise ValueError("discovery control mismatch: search grammar, seed or budget")
    return left, right


def attach_pattern(incumbent: Candidate, pattern: PatternDefinition) -> Candidate:
    spec = (
        GeneticSymbolicAdapterSpec(
            pattern=pattern,
            genome_sha256=canonical_genome_digest(pattern),
            registry_version=SYMBOLIC_REGISTRY_VERSION,
            registry_sha256=symbolic_registry_digest(),
        )
        if isinstance(pattern.genome, SymbolicGenome)
        else GeneticAdapterSpec(pattern=pattern, genome_sha256=canonical_genome_digest(pattern))
    )
    payload = incumbent.model_dump(mode="json")
    payload["genetic_pattern"] = spec.model_dump(mode="json")
    return Candidate.model_validate(payload)


def _simple_pattern(pattern: PatternDefinition) -> PatternDefinition:
    payload = pattern.model_dump(mode="json", by_alias=True)
    payload["name"] = "fixed_bullish_candle_control"
    payload["genome"] = {
        "kind": "candle_conjunction",
        "maxLag": 0,
        "rules": [
            {
                "left": {"field": "close", "lag": 0},
                "op": "gt",
                "right": {"field": "open", "lag": 0},
            }
        ],
    }
    return parse_pattern(payload)


def _code_hashes() -> dict[str, str]:
    root = Path(__file__).parents[2]
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for package in ("amat", "autoresearch", "llmrouter")
        for path in sorted((root / package).rglob("*.py"))
    }


def _check_incumbent(candidate: Candidate) -> None:
    if candidate.genetic_pattern is not None:
        raise ValueError("incumbent must not already contain a genetic pattern")
    if candidate.entry_model or candidate.signal is not None:
        raise ValueError("P2 replay requires an unfitted incumbent without signal or entry model")


def _candidate_recipes(
    incumbent: Candidate, ga: dict[str, Any], random: dict[str, Any]
) -> dict[str, Candidate]:
    pattern = parse_pattern(ga["champion"]["pattern"])
    augmented = attach_pattern(incumbent, pattern)
    removed_payload = augmented.model_dump(mode="json")
    removed_payload["genetic_pattern"] = None
    removed = Candidate.model_validate(removed_payload)
    if removed != incumbent or removed.digest() != incumbent.digest():
        raise ValueError("remove-pattern candidate does not exactly recover incumbent")
    return {
        "incumbent": incumbent,
        "genetic": augmented,
        "random": attach_pattern(incumbent, parse_pattern(random["champion"]["pattern"])),
        "bullish_candle": attach_pattern(incumbent, _simple_pattern(pattern)),
        "remove_pattern": removed,
    }


def _year_metrics(result: BacktestResult) -> list[dict[str, Any]]:
    rows = []
    for year in sorted({record.session.year for record in result.sessions[1:]}):
        indices = [
            i for i, record in enumerate(result.sessions) if record.session.year == year and i > 0
        ]
        if indices:
            rows.append(
                {
                    "year": year,
                    **summarize(anchored_slice(result, indices[0], indices[-1] + 1)).as_dict(),
                }
            )
    return rows


def _pattern_diagnostics(simulation: Simulation, candidate: Candidate) -> dict[str, Any]:
    if candidate.genetic_pattern is None:
        return {"pattern": None}
    from autoresearch.genetic.evaluator import evaluate_candles  # noqa: PLC0415

    panel = simulation.panel
    symbolic = isinstance(candidate.genetic_pattern, GeneticSymbolicAdapterSpec)
    values = (
        evaluate_symbolic(candidate.genetic_pattern.pattern, panel)
        if symbolic
        else evaluate_candles(
            candidate.genetic_pattern.pattern,
            dict(zip(("open", "high", "low", "close"), panel.candle_inputs(), strict=True)),
        )
    )
    return {
        "genome_sha256": candidate.genetic_pattern.genome_sha256,
        "per_asset": {
            str(symbol): {
                **(
                    {
                        "positive_bars": int((values[:, i] > 0).sum()),
                        "negative_bars": int((values[:, i] < 0).sum()),
                        "zero_bars": int((values[:, i] == 0).sum()),
                        "mean_value": float(np.nanmean(values[:, i]))
                        if np.isfinite(values[:, i]).any()
                        else None,
                    }
                    if symbolic
                    else {"matched_bars": int((values[:, i] == 1).sum())}
                ),
                "unavailable_bars": int((~np.isfinite(values[:, i])).sum()),
                "observed_bars": int(np.isfinite(values[:, i]).sum()),
            }
            for i, symbol in enumerate(panel.symbols)
        },
        "scope": "loaded development history including warmup; descriptive only",
    }


def _evaluate_variant(
    name: str,
    candidate: Candidate,
    training: Simulation,
    evaluation: Simulation,
    output: Path,
    *,
    contract_hash: str,
) -> dict[str, Any]:
    primary = training.run(candidate)
    stress = training.run(candidate, cost=6.0)
    delay = training.run(candidate, delay=1)
    score = score_run(primary, stress, training.program, complexity(candidate))
    if candidate == training.program.baseline:
        control: dict[str, Any] = {"baseline": True}
        matches: dict[str, BacktestResult] = {}
    else:
        match, matches = exposure_control(training, candidate)
        control = {"exposure_match": match}
    apply_incremental_score(
        score,
        control,
        training.program,
        complexity(candidate),
        complexity(training.program.baseline),
    )
    if not control.get("baseline"):
        match = control["exposure_match"]
        if not match["matched"]:
            score["failed_gates"].append("exposure_match_unresolved")
        elif match["sharpe_difference"] <= 0:
            score["failed_gates"].append("no_incremental_exposure_matched_information")
    if any(run.warnings for run in (delay, *matches.values())):
        score["failed_gates"].append("control_runner_warning")
    score["eligible"] = not score["failed_gates"]
    if summarize(delay).sharpe <= 0:
        score["failed_gates"].append("delay_1_sharpe")
        score["eligible"] = False
    runs = {"training": primary, "cost_6bps": stress, "delay_1": delay, **matches}
    full = evaluation.run(candidate)
    prefix = tuple(row for row in full.sessions if row.session <= training.program.train_end)
    # The short run cannot schedule tomorrow on its final session. The longer
    # run can; compare all realized accounting but omit only these three final
    # decision diagnostics. Earlier sessions retain exact all-field comparison.
    if prefix and primary.sessions:
        prefix = (
            *prefix[:-1],
            replace(
                prefix[-1],
                forecast_volatility_pre=primary.sessions[-1].forecast_volatility_pre,
                forecast_volatility_post=primary.sessions[-1].forecast_volatility_post,
                binding_constraints=primary.sessions[-1].binding_constraints,
            ),
        )
    if not exact_replay_equal(prefix, primary.sessions):
        raise ValueError("chronological replay changed training-prefix decisions or accounting")
    validation = validation_slice(full, training.program.train_end)
    runs["chronological_development"] = validation
    runs["development_cost_6bps"] = validation_slice(
        evaluation.run(candidate, cost=6.0), training.program.train_end
    )
    runs["development_delay_1"] = validation_slice(
        evaluation.run(candidate, delay=1), training.program.train_end
    )
    for label, result in runs.items():
        recipe = training.program.baseline if label == "exposure_control" else candidate
        intervention: dict[str, Any] = {
            "study": REPLAY_VERSION,
            "role": label,
            "execution_authority": "none",
            "cost_bps": result.one_way_bps,
            "delay": 1 if label in {"delay_1", "development_delay_1"} else 0,
        }
        if label.startswith("exposure_"):
            role = "baseline" if label == "exposure_control" else "candidate"
            intervention["scalar"] = (
                control["exposure_match"]["scalar"]
                if control["exposure_match"]["scaled"] == role
                else 1.0
            )
        persist_run(
            output,
            output / "runs" / name / label,
            result,
            recipe,
            contract_hash,
            intervention=intervention,
        )
    return {
        "status": "evaluated",
        "candidate_sha256": candidate.digest(),
        "training_objective": score,
        "training_controls": control,
        "delay_1": summarize(delay).as_dict(),
        "chronological_development": summarize(validation).as_dict(),
        "development_cost_6bps": summarize(runs["development_cost_6bps"]).as_dict(),
        "development_delay_1": summarize(runs["development_delay_1"]).as_dict(),
        "chronological_years": _year_metrics(validation),
        "pattern_diagnostics": _pattern_diagnostics(evaluation, candidate),
        "exposure_matching": "retrospective training-only; no validation calibration or matching",
    }


def run_replay(  # noqa: PLR0912, PLR0915 - atomic freeze and research replay transaction
    incumbent: Candidate,
    snapshot: Path,
    ga_study: Path,
    random_study: Path,
    *,
    start: dt.date,
    train_end: dt.date,
    evaluation_end: dt.date,
    output: Path,
) -> dict[str, Any]:
    """Freeze all inputs before comparing five preregistered portfolio recipes."""
    _check_incumbent(incumbent)
    if not start < train_end < evaluation_end:
        raise ValueError("replay requires chronological start < train_end < evaluation_end")
    if output.exists():
        raise ValueError("replay output already exists; choose a new directory")
    ga, random = matched_studies(ga_study, random_study)
    symbols = tuple(str(symbol) for symbol in incumbent.config.strategy.universe.symbols)
    discovery = ga["protocol"]["dataset"]
    if discovery.get("symbols") != list(symbols):
        raise ValueError("discovery universe must exactly match incumbent universe and order")
    if (discovery.get("training_start"), discovery.get("training_end")) != (
        start.isoformat(),
        train_end.isoformat(),
    ):
        raise ValueError("discovery training dates must match replay training dates")
    verified = load_daily(snapshot, symbols, start=start, end=train_end)
    if verified.digest != ga["protocol"]["dataset_sha256"]:
        raise ValueError("discovery training dataset differs from verified replay dataset")
    train_panel, manifest = load_panel(
        snapshot, symbols, start=start, end=train_end, with_context=True
    )
    full_panel, _ = load_panel(
        snapshot, symbols, start=start, end=evaluation_end, with_context=True
    )
    if train_panel.sessions[-1] != train_end or full_panel.sessions[-1] != evaluation_end:
        raise ValueError("training and evaluation ends must be available sessions")
    if train_panel.sessions[0] != start or full_panel.sessions[0] != start:
        raise ValueError("start must be an available session")
    calendar = audit_sessions(list(full_panel.sessions), start=start, end=evaluation_end)
    symbolic = ga["protocol"]["schema_version"] == "training-symbolic-screen/1"
    if symbolic:
        context = ga["protocol"]["symbolic_context"]
        if (
            context["feature_panel_sha256"] != panel_digest(train_panel)
            or context["registry_version"] != SYMBOLIC_REGISTRY_VERSION
            or context["registry_sha256"] != symbolic_registry_digest()
        ):
            raise ValueError("symbolic discovery panel or registry differs from replay")
    # Same warmup plus initial fitting anchor as existing development workspaces.
    anchor = incumbent.config.strategy.warmup.min_completed_sessions - 1 + 252
    if len(train_panel.sessions) <= anchor + 1:
        raise ValueError("training data does not cover warmup and initial training anchor")
    program = Program(
        schema_version=SYMBOLIC_PROGRAM_VERSION if symbolic else "strategy-development/8",
        name="genetic-pattern-replay",
        load_start=train_panel.sessions[0],
        measured_start=train_panel.sessions[anchor],
        train_end=train_end,
        validation_end=evaluation_end,
        baseline=incumbent,
        frozen_files=_code_hashes(),
        dependencies=dependencies(),
    )
    recipes = _candidate_recipes(incumbent, ga, random)
    exception_path = default_ledger_path(snapshot)
    exception_bytes = exception_path.read_bytes() if exception_path.exists() else None
    training, evaluation = (
        Simulation(train_panel, program, snapshot),
        Simulation(full_panel, program, snapshot),
    )
    if (exception_path.read_bytes() if exception_path.exists() else None) != exception_bytes:
        raise ValueError("action exceptions changed during initialization")
    discovery_artifacts = {"genetic": ga.pop("artifacts"), "random": random.pop("artifacts")}
    frozen = {
        "schema_version": REPLAY_VERSION,
        "program": program.model_dump(mode="json"),
        "source_sha256": program.frozen_files,
        "snapshot": manifest.model_dump(mode="json"),
        "training_panel_sha256": panel_digest(train_panel),
        "evaluation_panel_sha256": panel_digest(full_panel),
        "calendar": calendar,
        "resolved_actions": {
            "training": _actions_payload(training),
            "evaluation": _actions_payload(evaluation),
        },
        "action_exceptions": {
            "path": str(exception_path.resolve()),
            "sha256": hashlib.sha256(exception_bytes).hexdigest()
            if exception_bytes is not None
            else None,
        },
        "discovery": {"genetic": ga, "random": random},
        "recipes": {name: candidate.model_dump(mode="json") for name, candidate in recipes.items()},
        "recipe_hashes": {name: candidate.digest() for name, candidate in recipes.items()},
        "evaluation_status": "historical_development_not_pristine_holdout",
        "exposure_calibration": "training_only_retrospective",
        "execution_authority": "none",
        "promotion_authority": "none",
        "training_cost_bps": 2.0,
        "stress_cost_bps": 6.0,
        "delay_stress_sessions": 1,
        "adapter_calibration": "fixed pattern binding; no validation fit or tuning",
    }
    output.mkdir(parents=True, exist_ok=False)
    for method, files in discovery_artifacts.items():
        target = output / "discovery" / method
        target.mkdir(parents=True)
        for filename, raw in files.items():
            (target / filename).write_text(raw)
    if exception_bytes is not None:
        (output / "action-exceptions.toml").write_bytes(exception_bytes)
    write_json(output / "protocol.json", frozen)
    contract_hash = digest_json(frozen)
    results: dict[str, Any] = {}
    for name, candidate in recipes.items():
        try:
            # An independent replay demonstrates removal parity without a cache shortcut.
            train = (
                Simulation(train_panel, program, snapshot) if name == "remove_pattern" else training
            )
            evaluate = (
                Simulation(full_panel, program, snapshot)
                if name == "remove_pattern"
                else evaluation
            )
            train.actions, evaluate.actions = training.actions, evaluation.actions
            results[name] = _evaluate_variant(
                name, candidate, train, evaluate, output, contract_hash=contract_hash
            )
            if name == "remove_pattern":
                parity = exact_replay_equal(
                    train.run(candidate), training.run(incumbent)
                ) and exact_replay_equal(evaluate.run(candidate), evaluation.run(incumbent))
                results[name]["exact_incumbent_parity"] = parity
                if not parity:
                    raise ValueError("remove-pattern replay failed exact incumbent parity")
        except Exception as exc:
            results[name] = {
                "status": "failed",
                "candidate_sha256": candidate.digest(),
                "failure": f"{type(exc).__name__}: {exc}",
            }
        write_json(output / "progress.json", results)
    if (exception_path.read_bytes() if exception_path.exists() else None) != exception_bytes:
        raise ValueError("action exceptions changed during replay; output is incomplete")
    if _code_hashes() != program.frozen_files:
        raise ValueError("source files changed during replay; output is incomplete")
    for filename, expected in manifest.partition_sha256.items():
        if hashlib.sha256((snapshot / filename).read_bytes()).hexdigest() != expected:
            raise ValueError("snapshot changed during replay; output is incomplete")
    report = {
        "schema_version": REPLAY_VERSION,
        "protocol_sha256": contract_hash,
        "status": "completed"
        if all(row["status"] == "evaluated" for row in results.values())
        else "completed_with_failures",
        "execution_authority": "none",
        "promotion_authority": "none",
        "evidence_level": "matched_historical_portfolio_development_replay",
        "promotion_gates_not_assessed": [
            "paired incumbent uncertainty",
            "full-search null",
            "nested walk-forward search",
            "parameter-neighbor robustness",
            "shadow evaluation",
        ],
        "results": results,
        "limitations": [
            "Historical evaluation is not a pristine holdout.",
            "Incumbent configuration may already reflect later-history selection; "
            "chronology isolates pattern discovery only, "
            "not clean strategy out-of-sample evidence.",
            "Training exposure matching is retrospective, not an executable strategy.",
            "P1 champion selection used an event screen; this replay does not rerun "
            "evolution against portfolio fitness.",
            "Nested walk-forward search, full-search nulls and promotion remain unimplemented.",
        ],
    }
    write_json(output / "report.json", report)
    lines = [
        "# Genetic pattern portfolio replay",
        "",
        "Historical development evidence; no promotion or execution authority.",
        "",
        "| Recipe | Status | Training incremental score | Development Sharpe |",
        "|---|---|---:|---:|",
    ]
    for name, row in results.items():
        lines.append(
            f"| {name} | {row['status']} | {row.get('training_objective', {}).get('score')} | "
            f"{row.get('chronological_development', {}).get('sharpe')} |"
        )
    lines.extend(
        [
            "",
            "| Recipe | Development CAGR | Max drawdown | Fills | "
            "6 bps Sharpe | 1-session delay Sharpe |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name, row in results.items():
        later = row.get("chronological_development", {})
        lines.append(
            f"| {name} | {later.get('annualized_return')} | {later.get('max_drawdown')} | "
            f"{later.get('total_fills')} | {row.get('development_cost_6bps', {}).get('sharpe')} | "
            f"{row.get('development_delay_1', {}).get('sharpe')} |"
        )
    lines.extend(["", "Training gates and pattern recipes:", ""])
    for name, row in results.items():
        gates = row.get("failure") or row.get("training_objective", {}).get("failed_gates", [])
        lines.append(f"- {name}: {gates}")
        spec = recipes[name].genetic_pattern
        if spec is not None:
            lines.extend(
                [
                    "",
                    f"{name} formula (lag zero is the completed decision bar):",
                    "",
                    "```json",
                    json.dumps(
                        spec.pattern.genome.model_dump(mode="json", by_alias=True), indent=2
                    ),
                    "```",
                    "",
                    "Per-asset matches and unavailable bars:",
                    "```json",
                    json.dumps(row.get("pattern_diagnostics", {}), indent=2),
                    "```",
                    "",
                ]
            )
    lines.extend(
        [
            "",
            "Promotion gates not assessed: " + ", ".join(report["promotion_gates_not_assessed"]),
            "",
            *report["limitations"],
            "",
        ]
    )
    (output / "report.md").write_text("\n".join(lines))
    return report
