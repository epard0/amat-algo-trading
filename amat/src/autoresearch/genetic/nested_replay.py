"""Training-only portfolio selection followed by frozen outer-fold diagnostics."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import BacktestResult
from amat.metrics import summarize
from autoresearch.contracts import Candidate, Program
from autoresearch.experiments import Simulation, daily_returns, exposure_control
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.evaluator import evaluate_candles
from autoresearch.genetic.models import PatternDefinition, SymbolicGenome, parse_pattern
from autoresearch.genetic.replay import _simple_pattern, attach_pattern, exact_replay_equal
from autoresearch.genetic.study import write_json
from autoresearch.genetic.symbolic import evaluate_symbolic
from autoresearch.objective import apply_incremental_score, score_run
from autoresearch.signals import SignalTrace
from autoresearch.worker import complexity, persist_run, validation_slice

VERSION = "nested-genetic-portfolio/1"
_FIELDS = (
    "adjusted_closes",
    "raw_closes",
    "window_vwaps",
    "window_volumes",
    "adjusted_highs",
    "adjusted_lows",
    "session_volumes",
    "adjusted_opens",
)


def panel_prefix(panel: MarketPanel, stop: int) -> MarketPanel:
    """Copy every market series onto the same exclusive session boundary."""
    arrays: dict[str, Any] = {
        key: None if getattr(panel, key) is None else getattr(panel, key)[:stop].copy()
        for key in _FIELDS
    }
    return replace(panel, sessions=panel.sessions[:stop], **arrays)


class FoldSimulation(Simulation):
    """Offline null intervention on features, leaving market/accounting untouched."""

    def __init__(
        self,
        panel: MarketPanel,
        program: Program,
        snapshot: Path | None,
        mapping: np.ndarray | None,
        common_rows: np.ndarray | None,
    ) -> None:
        super().__init__(panel, program, snapshot)
        self.mapping, self.common_rows = mapping, common_rows

    def trace(self, candidate: Candidate) -> SignalTrace:
        if candidate.genetic_pattern is None or (self.mapping is None and self.common_rows is None):
            return super().trace(candidate)
        key = candidate.digest()
        if key not in self.traces:
            baseline = Candidate.model_validate(
                {**candidate.model_dump(mode="json"), "genetic_pattern": None}
            )
            trace = super().trace(baseline)
            pattern = candidate.genetic_pattern.pattern
            symbolic = isinstance(pattern.genome, SymbolicGenome)
            values = (
                evaluate_symbolic(pattern, self.panel)
                if symbolic
                else evaluate_candles(
                    pattern,
                    dict(
                        zip(
                            ("open", "high", "low", "close"),
                            self.panel.candle_inputs(),
                            strict=True,
                        )
                    ),
                )
            )
            if self.mapping is not None:
                values = values[self.mapping]
            values = values.copy()
            if self.common_rows is not None:
                values[~self.common_rows] = np.nan
            contribution = values if symbolic else 2 * values - 1
            scores = trace.scores.copy()
            active = (scores > 0) & np.isfinite(contribution)
            scores[active] = np.clip(
                scores[active] + pattern.binding.max_effect * contribution[active], 0, 1
            )
            self.traces[key] = replace(trace, scores=scores, genetic_scores=values)
        return self.traces[key]


def _inner(simulation: Simulation, candidate: Candidate) -> dict[str, Any]:
    primary, stress, delayed = (
        simulation.run(candidate),
        simulation.run(candidate, cost=6.0),
        simulation.run(candidate, delay=1),
    )
    score = score_run(primary, stress, simulation.program, complexity(candidate))
    match, matches = exposure_control(simulation, candidate)
    controls = {"exposure_match": match}
    apply_incremental_score(
        score,
        controls,
        simulation.program,
        complexity(candidate),
        complexity(simulation.program.baseline),
    )
    if not match["matched"]:
        score["failed_gates"].append("exposure_match_unresolved")
    elif match["sharpe_difference"] <= 0:
        score["failed_gates"].append("no_incremental_exposure_matched_information")
    if any(run.warnings for run in (delayed, *matches.values())):
        score["failed_gates"].append("control_runner_warning")
    if summarize(delayed).sharpe <= 0:
        score["failed_gates"].append("delay_1_sharpe")
    if score["score"] is None or score["score"] <= 0:
        score["failed_gates"].append("no_positive_incremental_score")
    score["eligible"] = not score["failed_gates"]
    return {"candidate_sha256": candidate.digest(), "objective": score, "controls": controls}


def _check_prefix(primary: BacktestResult, full: BacktestResult, end: dt.date) -> None:
    prefix = tuple(row for row in full.sessions if row.session <= end)
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
        raise ValueError("outer replay changed inner selection accounting")


def _outer(  # noqa: PLR0917 - explicit simulation and provenance context
    name: str,
    candidate: Candidate,
    inner: Simulation,
    outer: FoldSimulation,
    output: Path,
    contract_hash: str,
) -> dict[str, Any]:
    full = outer.run(candidate)
    _check_prefix(inner.run(candidate), full, inner.program.train_end)
    runs = {
        "outer": validation_slice(full, inner.program.train_end),
        "cost_6bps": validation_slice(outer.run(candidate, cost=6.0), inner.program.train_end),
        "delay_1": validation_slice(outer.run(candidate, delay=1), inner.program.train_end),
    }
    for role, result in runs.items():
        persist_run(
            output,
            output / "runs" / name / role,
            result,
            candidate,
            contract_hash,
            intervention={
                "study": VERSION,
                "role": role,
                "execution_authority": "none",
                "offline_feature_null": outer.mapping is not None,
                "feature_mapping_sha256": None
                if outer.mapping is None
                else digest_json(outer.mapping.tolist()),
            },
        )
    primary = runs["outer"]
    return {
        "candidate_sha256": candidate.digest(),
        "metrics": summarize(primary).as_dict(),
        "cost_6bps": summarize(runs["cost_6bps"]).as_dict(),
        "delay_1": summarize(runs["delay_1"]).as_dict(),
        "dates": [str(row.session) for row in primary.sessions[1:]],
        "returns": daily_returns(primary).tolist(),
        "warnings": {role: list(result.warnings) for role, result in runs.items()},
    }


def _diagnostic_patterns(pattern: PatternDefinition) -> dict[str, PatternDefinition]:
    result = {}
    for factor in (0.8, 1.2):
        payload = pattern.model_dump(mode="json", by_alias=True)
        payload["binding"]["maxEffect"] = min(0.25, pattern.binding.max_effect * factor)
        result[f"strength_{factor}"] = parse_pattern(payload)
    if isinstance(pattern.genome, SymbolicGenome):
        root = pattern.model_dump(mode="json", by_alias=True)["genome"]["root"]
        paths: list[tuple[str, ...]] = []

        def visit(node: dict[str, Any], path: tuple[str, ...]) -> None:
            if node["node"] == "feature":
                paths.append(path)
            for child in ("arg", "left", "right"):
                if child in node:
                    visit(node[child], (*path, child))

        visit(root, ())
        for index, path in enumerate(paths[:15]):
            payload = pattern.model_dump(mode="json", by_alias=True)
            target = payload["genome"]
            for part in ("root", *path[:-1]) if path else ():
                target = target[part]
            target[path[-1] if path else "root"] = {"node": "constant", "value": 0.0}
            result[f"remove_leaf_{index}"] = parse_pattern(payload)
    return result


def evaluate_fold(  # noqa: PLR0912, PLR0915 - freeze before outer transaction
    incumbent: Candidate,
    panel: MarketPanel,
    snapshot: Path | None,
    *,
    fit_end: dt.date,
    selection_end: dt.date,
    output: Path,
    contract_hash: str,
    contenders: dict[str, list[PatternDefinition]],
    feature_mapping: np.ndarray | None = None,
    common_rows: np.ndarray | None = None,
    diagnostics: bool = True,
) -> dict[str, Any]:
    """Select each method on inner dates; freeze choices before any outer run."""
    if incumbent.genetic_pattern is not None:
        raise ValueError("nested incumbent must not already contain a genetic pattern")
    if not panel.sessions[0] <= fit_end < selection_end < panel.sessions[-1]:
        raise ValueError("fold requires chronological fit, selection and outer windows")
    if fit_end not in panel.sessions or selection_end not in panel.sessions:
        raise ValueError("fold boundaries must be available sessions")
    if set(contenders) != {"genetic", "random"}:
        raise ValueError("contenders must provide genetic and random methods")
    fit_stop = panel.sessions.index(fit_end) + 1
    stop = panel.sessions.index(selection_end) + 1
    if feature_mapping is not None:
        mapping = np.asarray(feature_mapping)
        if (
            mapping.shape != (len(panel),)
            or mapping.dtype.kind not in "iu"
            or np.any(mapping < 0)
            or np.any(mapping >= len(panel))
            or not np.array_equal(np.sort(mapping), np.arange(len(panel)))
            or any(
                np.any(mapping[start:end] < start) or np.any(mapping[start:end] >= end)
                for start, end in ((0, fit_stop), (fit_stop, stop), (stop, len(panel)))
            )
        ):
            raise ValueError(
                "feature mapping must be a permutation preserving each fit/selection/outer boundary"
            )
    if common_rows is not None and (
        common_rows.shape != (len(panel),) or common_rows.dtype != bool
    ):
        raise ValueError("common rows must be a boolean session mask")
    if (
        feature_mapping is not None
        and common_rows is not None
        and not np.array_equal(common_rows[feature_mapping], common_rows)
    ):
        raise ValueError("feature mapping must preserve common-row availability")
    program = Program(
        schema_version="strategy-development/9",
        name="nested-symbolic-fold",
        load_start=panel.sessions[0],
        measured_start=fit_end,
        train_end=selection_end,
        validation_end=panel.sessions[-1],
        baseline=incumbent,
        frozen_files={},
        dependencies={},
    )
    inner = FoldSimulation(
        panel_prefix(panel, stop),
        program,
        snapshot,
        None if feature_mapping is None else feature_mapping[:stop],
        None if common_rows is None else common_rows[:stop],
    )
    chosen, selections = {}, {}
    for method, patterns in contenders.items():
        candidates = [attach_pattern(incumbent, pattern) for pattern in patterns]
        rows = [_inner(inner, candidate) for candidate in candidates]
        eligible = [
            (row["objective"]["score"], candidate.digest(), candidate)
            for candidate, row in zip(candidates, rows, strict=True)
            if row["objective"]["eligible"]
        ]
        selected = (
            sorted(eligible, key=lambda item: (-item[0], item[1]))[0][2] if eligible else incumbent
        )
        chosen[method] = selected
        selections[method] = {
            "selected_sha256": selected.digest(),
            "retained_incumbent": selected == incumbent,
            "selected_pattern": None
            if selected.genetic_pattern is None
            else selected.genetic_pattern.pattern.model_dump(mode="json", by_alias=True),
            "contenders": rows,
        }
    frozen = {
        "schema_version": VERSION,
        "contract_sha256": contract_hash,
        "fit_end": str(fit_end),
        "selection_end": str(selection_end),
        "selections": selections,
        "feature_mapping_sha256": None
        if feature_mapping is None
        else digest_json(feature_mapping.tolist()),
        "common_rows_sha256": None if common_rows is None else digest_json(common_rows.tolist()),
        "offline_null_meaning": (
            "joint feature-time remapping within registered segments; "
            "prices and execution unchanged"
        ),
        "selection_uses_outer": False,
        "execution_authority": "none",
    }
    if output.exists():
        saved = output / "selection.json"
        if not saved.exists() or digest_json(json.loads(saved.read_text())) != digest_json(frozen):
            raise ValueError("existing fold selection provenance mismatch")
    else:
        output.mkdir(parents=True)
        write_json(output / "selection.json", frozen)
    # Persist every shortlisted inner recipe, including rejected contenders.
    for method, patterns in contenders.items():
        for index, pattern in enumerate(patterns):
            candidate = attach_pattern(incumbent, pattern)
            for role, cost, delay in (
                ("selection", 2.0, 0),
                ("selection_cost_6bps", 6.0, 0),
                ("selection_delay_1", 2.0, 1),
            ):
                persist_run(
                    output,
                    output / "runs" / f"{method}_contender_{index}" / role,
                    inner.run(candidate, cost=cost, delay=delay),
                    candidate,
                    contract_hash,
                    intervention={
                        "study": VERSION,
                        "role": role,
                        "execution_authority": "none",
                        "feature_mapping_sha256": frozen["feature_mapping_sha256"],
                    },
                )
    outer = FoldSimulation(panel, program, snapshot, feature_mapping, common_rows)
    recipes = {"incumbent": incumbent, **chosen}
    sample = next((p for patterns in contenders.values() for p in patterns), None)
    if sample is not None and diagnostics:
        payload = sample.model_dump(mode="json", by_alias=True)
        payload["genome"] = {
            "kind": "symbolic_indicator",
            "outputTransform": "tanh",
            "root": {
                "node": "feature",
                "name": "body_atr",
                "lookback": 21,
                "lag": 0,
                "transform": "rolling_zscore",
                "source": "self",
            },
        }
        recipes["body_atr"] = attach_pattern(incumbent, parse_pattern(payload))
        recipes["bullish_candle"] = attach_pattern(incumbent, _simple_pattern(sample))
    results = {
        name: _outer(name, candidate, inner, outer, output, contract_hash)
        for name, candidate in recipes.items()
    }
    diagnostic_results: dict[str, Any] = {}
    for method, candidate in chosen.items():
        diagnostic_results[method] = {}
        if candidate.genetic_pattern is not None and diagnostics:
            for name, pattern in _diagnostic_patterns(candidate.genetic_pattern.pattern).items():
                diagnostic_results[method][name] = _outer(
                    f"{method}_{name}",
                    attach_pattern(incumbent, pattern),
                    inner,
                    outer,
                    output,
                    contract_hash,
                )
                if name.startswith("strength_"):
                    diagnostic_results[method][name]["perturbation"] = {
                        "requested_factor": float(name.removeprefix("strength_")),
                        "effective_max_effect": pattern.binding.max_effect,
                        "changed": pattern.binding.max_effect
                        != candidate.genetic_pattern.pattern.binding.max_effect,
                    }
        removed = Candidate.model_validate(
            {**candidate.model_dump(mode="json"), "genetic_pattern": None}
        )
        if not exact_replay_equal(outer.run(removed), outer.run(incumbent)):
            raise ValueError("removing the pattern changed incumbent accounting")
        diagnostic_results[method]["remove_pattern_exact_parity"] = True
    for row in results.values():
        row["incumbent_returns"] = results["incumbent"]["returns"]
    report = {
        **frozen,
        "outer": results,
        "diagnostics": diagnostic_results,
        "diagnostics_used_for_selection": False,
    }
    report["methods"] = {
        method: {**selections[method], "outer": results[method]["metrics"]} for method in chosen
    }
    report["paired_returns"] = {
        method: {
            "dates": results[method]["dates"],
            "candidate": results[method]["returns"],
            "incumbent": results["incumbent"]["returns"],
        }
        for method in chosen
    }
    write_json(output / "report.json", report)
    return report
