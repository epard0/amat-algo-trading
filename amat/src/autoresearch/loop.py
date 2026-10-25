"""Propose, register, measure, retain/discard and feed training evidence back."""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from autoresearch.contracts import Candidate, Program, Proposal, apply_proposal, validate_candidate
from autoresearch.diagnostics import paired_uncertainty
from autoresearch.extensions import validate_daily_core, validate_daily_extension
from autoresearch.judge import judge_experiment, research_records
from autoresearch.ledger import BudgetPauseError, Ledger
from autoresearch.objective import score_description
from autoresearch.presentation import batch_results, write_html_report
from autoresearch.proposer import model_proposal, search_proposal
from autoresearch.research import research_parents, selected_parent
from autoresearch.search import choose_experiment, frontier
from autoresearch.summaries import render_summaries, summarize_experiment
from autoresearch.telemetry import daily_metrics, export_analysis, read_telemetry
from autoresearch.worker import ENGINE
from autoresearch.workspace import digest_file, load_program, write_json
from llmrouter.chain import Chain, Router

MAX_BATCH = 100
MAX_CONTROLLER_GUIDANCE = 8000


def incumbent_return_floor(candidate: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    """Reject lower/missing net returns at either matched development cost case."""
    cases = {}
    for case in ("train", "stress"):
        try:
            actual = float(candidate[case]["total_return"])
            reference = float(control[case]["total_return"])
            passes = math.isfinite(actual) and math.isfinite(reference) and actual >= reference
        except (KeyError, TypeError, ValueError):
            actual, reference, passes = None, None, False
        cases[case] = {
            "candidate": actual if actual is not None and math.isfinite(actual) else None,
            "incumbent": reference if reference is not None and math.isfinite(reference) else None,
            "passes": passes,
        }
    return {
        "passes": all(row["passes"] for row in cases.values()),
        "cases": cases,
        "scope": (
            "Matched development returns at primary and stress costs; no validation selection."
        ),
    }


def worker_environment(root: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(root / "runtime"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "AMAT_EXECUTION_MODE": "backtest",
    }


def execute(
    root: Path, program: Program, candidate: Candidate, target: Path, *, validation: bool = False
) -> dict[str, Any]:
    load_program(root)  # hash gate immediately before each process launch
    validate_candidate(program.baseline, candidate)
    if program.extensions_only:
        validate_daily_core(program.baseline, candidate)
    candidate_path = root / "candidates" / f"{candidate.digest()}.json"
    if candidate_path.exists():
        if Candidate.model_validate_json(candidate_path.read_text()) != candidate:
            raise ValueError("stored candidate was modified")
    else:
        write_json(candidate_path, candidate.model_dump(mode="json"))
    command = [
        sys.executable,
        "-m",
        "autoresearch.worker",
        str(root),
        str(candidate_path),
        str(target),
    ]
    if validation:
        command.append("--validation")
    completed = subprocess.run(  # noqa: S603 - fixed worker, argv list, stripped environment
        command,
        cwd=root,
        env=worker_environment(root),
        capture_output=True,
        text=True,
        timeout=program.timeout_seconds,
        check=False,
    )
    if completed.returncode:
        # Worker contains no credentials; preserve a bounded traceback for diagnosis.
        raise ValueError(f"worker exited {completed.returncode}: {completed.stderr[-3000:]}")
    load_program(root)
    return dict(
        json.loads((target / ("validation.json" if validation else "objective.json")).read_text())
    )


def cached_measurement(root: Path, ledger: Ledger, key: str) -> dict[str, Any] | None:
    cached = ledger.db.execute("SELECT result FROM cache WHERE candidate=?", (key,)).fetchone()
    if cached:
        result = json.loads(cached[0])
        for relative, expected in result["artifact_hashes"].items():
            if digest_file(root / relative) != expected:
                raise ValueError("cached experiment artifact changed")
        return dict(result)
    return None


def measure(root: Path, program: Program, ledger: Ledger, candidate: Candidate) -> dict[str, Any]:
    key = candidate.digest()
    cached = cached_measurement(root, ledger, key)
    if cached is not None:
        return cached
    target = root / "outputs/runs" / key
    result = execute(root, program, candidate, target)
    result["analysis_metrics"] = {
        variant: daily_metrics(json.loads((target / variant / "daily.json").read_text()))
        for variant in ("primary", "stress")
    }
    result["artifact_hashes"] = {
        str(path.relative_to(root)): digest_file(path)
        for path in target.rglob("*")
        if path.is_file()
    }
    with ledger.db:
        ledger.db.execute("INSERT INTO cache VALUES(?,?)", (key, json.dumps(result)))
    return result


def register(ledger: Ledger, candidate: str, parent: str, proposal: str) -> int:
    with ledger.db:
        row = ledger.db.execute(
            "INSERT INTO experiments(candidate,parent,proposal,status) VALUES(?,?,?,'running')",
            (candidate, parent, proposal),
        )
    return int(row.lastrowid or 0)


def finish(
    ledger: Ledger,
    index: int,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with ledger.db:
        ledger.db.execute(
            "UPDATE experiments SET status=?,result=?,error=? WHERE id=?",
            (status, json.dumps(result) if result is not None else None, error, index),
        )

    if result is not None and status in {"baseline", "keep", "discard"}:
        archive_result(ledger, index, status, result)


def archive_result(ledger: Ledger, index: int, status: str, result: dict[str, Any]) -> None:
    from autoresearch.best_strategies import record_best  # noqa: PLC0415

    try:
        row = ledger.db.execute("SELECT candidate FROM experiments WHERE id=?", (index,)).fetchone()
        record_best(
            ledger.root,
            program=json.loads((ledger.root / "program.json").read_text()),
            candidate=row[0],
            result=result,
            decision=status,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        # The experiment is already durable. Retry the secondary index on resume.
        ledger.event("best_archive_pending", experiment=index, error_type=type(exc).__name__)


def incumbent(root: Path, program: Program, ledger: Ledger) -> Candidate:
    key = ledger.get("incumbent")
    if key:
        candidate = Candidate.model_validate_json((root / "candidates" / f"{key}.json").read_text())
        if candidate.digest() != key:
            raise ValueError("incumbent content digest mismatch")
        return candidate
    index = register(ledger, program.baseline.digest(), "", "baseline")
    try:
        result = measure(root, program, ledger, program.baseline)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        finish(ledger, index, "crash", error=str(exc))
        raise
    finish(ledger, index, "baseline", result)
    ledger.put("incumbent", program.baseline.digest())
    return program.baseline


def publish_catalog(root: Path, ledger: Ledger) -> None:
    rows = [
        row
        for row in ledger.history()
        if row["result"] is not None and row["parent"] and row["candidate"] != row["parent"]
    ]
    ids = sorted({str(row[key]) for row in rows for key in ("candidate", "parent")})
    lines = [
        'schema_version = "portfolio-team-catalog/1"',
        'default_evaluation_use = "previously_inspected_history"',
    ]
    if not rows:
        lines.extend(["runs = []", "comparisons = []"])
    for key in ids:
        lines.extend(
            [
                "",
                "[[runs]]",
                f'id = "r_{key}"',
                f'path = "outputs/runs/{key}/primary"',
                f'config_path = "outputs/runs/{key}/primary/config.toml"',
                "study_index = 0",
                f'engine = "{ENGINE}"',
            ]
        )
    for row in rows:
        lines.extend(
            [
                "",
                "[[comparisons]]",
                f'id = "experiment_{row["id"]}"',
                f'candidate = "r_{row["candidate"]}"',
                f'control = "r_{row["parent"]}"',
                'purpose = "mechanism_test"',
            ]
        )
    path = root / "catalog.toml"
    temp = root / "catalog.toml.tmp"
    temp.write_text("\n".join(lines) + "\n")
    temp.replace(path)


def report(root: Path, program: Program, ledger: Ledger) -> dict[str, Any]:
    rows = ledger.history()
    summaries, summary_lines = render_summaries(ledger)
    summary = {
        "program": program.name,
        "program_sha256": program.digest(),
        "status": ledger.get("status", "ready"),
        "incumbent": ledger.get("incumbent"),
        "experiments": rows,
        "frontier": [candidate.digest() for candidate in frontier(root, program, rows)],
        "selection_evidence": "adaptive_chronological_development",
        "score_description": score_description(program),
        "starting_strategy": (
            json.loads((root / "runtime/research-promotion.json").read_text())
            if (root / "runtime/research-promotion.json").is_file()
            else None
        ),
        "model_calls": ledger.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0],
        "engineering": ledger.get("engineering"),
        "execution_authority": "none",
        "validation_used_for_selection": False,
        "llm_summaries": summaries,
        "research_judgments": research_records(ledger, "judgment"),
        "research_lessons": research_records(ledger, "lesson"),
        "batch": batch_results(rows, ledger.get("batch", {})),
    }
    summary["analysis"] = export_analysis(root, summary, read_telemetry(root))
    write_json(root / "report.json", summary)
    lines = [
        f"# Strategy development: {program.name}",
        "",
        f"Status: {summary['status']}",
        "Research results only; no deployment or trading authority.",
        "Validation is excluded from proposal context and keep/discard selection.",
        score_description(program),
        "",
        "| Trial | Status | Score | Train Sharpe | 6 bps Sharpe | Feature/config experiment |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        result = json.loads(row["result"]) if row["result"] else {}
        proposal = json.loads(row["proposal"]) if row["proposal"] != "baseline" else {}
        description = ", ".join(
            f"{feature['kind']}({feature['lookback']})" for feature in proposal.get("features", [])
        )
        description += " ".join(
            f" {patch['path']}={patch['value']}" for patch in proposal.get("patches", [])
        )
        signal = proposal.get("signal")
        if signal:
            description += (
                " "
                + signal["learner"]
                + ": "
                + ", ".join(
                    term["feature"]["kind"] + "(" + str(term["feature"]["lookback"]) + ")"
                    for term in signal["terms"]
                )
            )
        description = description or "baseline / feature ablation"
        description = proposal.get("stage", "baseline") + ": " + description
        train_sharpe = result.get("train", {}).get("sharpe", "—")
        stress_sharpe = result.get("stress", {}).get("sharpe", "—")
        lines.append(
            f"| {row['id']} | {row['status']} | {result.get('score', '—')} | "
            f"{train_sharpe} | {stress_sharpe} | {description} |"
        )
    (root / "report.md").write_text("\n".join(lines + summary_lines) + "\n")
    write_html_report(root, summary)
    publish_catalog(root, ledger)
    return summary


def next_proposal(
    router: Router | None,
    ledger: Ledger,
    program: Program,
    parent: Candidate,
    tried: set[str],
    *,
    parents: dict[str, Candidate] | None = None,
) -> Proposal | None:
    try:
        selected = (
            asyncio.run(model_proposal(router, ledger, program, parent, parents))
            if router
            else search_proposal(parent, tried)
        )
    except BudgetPauseError:
        ledger.put("status", "paused_budget")
        return None
    except ValueError:
        ledger.put("status", "invalid_model_proposal")
        return None
    finally:
        if router:
            ledger.put(f"provider_cooldowns:{router.chain.digest()}", router.cooldowns())
    if selected is None:
        ledger.put("status", "paused_no_provider" if router else "search_exhausted")
    return selected


def run_loop(root: Path, **options: Any) -> dict[str, Any]:
    from autoresearch.graph import run_graph  # noqa: PLC0415 - outer orchestration boundary

    def prepare() -> None:
        load_program(root.resolve())

    def verify(result: dict[str, Any]) -> None:
        program = load_program(root.resolve())
        if result.get("program_sha256") != program.digest():
            raise ValueError("graph result does not match its frozen program")

    return run_graph(prepare, lambda: _run_loop(root, **options), verify)


def _run_loop(  # noqa: PLR0912, PLR0915 - ordered durable experiment lifecycle
    root: Path,
    *,
    iterations: int = 1,
    chain: Chain | None = None,
    proposal: Proposal | None = None,
    summary_chain: Chain | None = None,
    on_summary: Callable[[dict[str, Any]], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
    fallback_search: bool = False,
    controller_guidance: str = "",
) -> dict[str, Any]:
    if not 1 <= iterations <= MAX_BATCH:
        raise ValueError("iterations must be within 1..100")
    if len(controller_guidance) > MAX_CONTROLLER_GUIDANCE:
        raise ValueError("Controller guidance exceeds 8000 characters")
    root = root.resolve()
    program = load_program(root)
    router = Router(chain) if chain is not None else None
    review_chain = summary_chain or program.summary_chain
    reviewer = Router(review_chain) if review_chain is not None else None
    with Ledger(root, program.max_model_tokens_per_day) as ledger:
        ledger.put("controller_guidance", controller_guidance)
        if controller_guidance:
            ledger.event("controller_guidance_received", characters=len(controller_guidance))
        ledger.event("batch_started", requested=iterations)
        ledger.put("status", "running")
        history = ledger.history()
        for previous in history:
            if previous["result"] is not None and previous["status"] in {
                "baseline",
                "keep",
                "discard",
            }:
                archive_result(
                    ledger, previous["id"], previous["status"], json.loads(previous["result"])
                )
        ledger.put(
            "batch", {"start_id": history[-1]["id"] if history else 0, "requested": iterations}
        )
        ledger.phase("baseline")
        report(root, program, ledger)
        if router:
            router.restore_cooldowns(ledger.get(f"provider_cooldowns:{router.chain.digest()}", {}))
        try:
            champion = incumbent(root, program, ledger)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            ledger.put("status", "baseline_failed")
            ledger.event("baseline_failed", error=str(exc))
            ledger.phase("finished")
            ledger.event(
                "batch_finished", completed=0, requested=iterations, status="baseline_failed"
            )
            report(root, program, ledger)
            raise
        ledger.phase("review", ledger.history()[-1]["id"])
        report(root, program, ledger)
        first_summary = summarize_experiment(root, ledger, ledger.history()[-1]["id"], reviewer)
        if on_summary:
            on_summary(first_summary)
        ledger.put("status", "running")
        ledger.put("batch", {"start_id": ledger.history()[-1]["id"], "requested": iterations})
        report(root, program, ledger)
        completed_count = 0
        # Replacements are bounded; failed or repeated recipes never fill a slot.
        for _ in range(1 if proposal is not None else iterations * 3):
            if completed_count == iterations:
                break
            ledger.phase("proposing")
            report(root, program, ledger)
            load_program(root)
            history = ledger.history()
            trials = [row for row in history if row["parent"]]
            if len(trials) >= program.max_experiments:
                ledger.put("status", "experiment_budget_reached")
                break
            if ledger.get("stall", 0) >= program.stall_limit:
                ledger.put("status", "stalled")
                break
            tried = {row["candidate"] for row in history}
            selected: Proposal | None
            parent = champion
            parents: dict[str, Candidate] = {}
            proposal_source = "manual" if proposal else ("llm" if router else "built-in")
            if proposal is None and router is None:
                choice = choose_experiment(root, program, champion, history)
                if choice is None:
                    ledger.put("status", "search_exhausted")
                    break
                parent, selected = choice
            else:
                parents = research_parents(root, program, champion, history)
                if on_progress and proposal is None:
                    on_progress(f"Proposing strategy {completed_count + 1}/{iterations}…")
                selected = proposal or next_proposal(
                    router, ledger, program, parent, tried, parents=parents
                )
                if selected is None and fallback_search and proposal is None:
                    ledger.event(
                        "proposal_fallback", reason=ledger.get("status"), completed=completed_count
                    )
                    if on_progress:
                        on_progress("Model proposals unavailable; continuing with built-in search.")
                    router = None
                    ledger.put("status", "running")
                    choice = choose_experiment(root, program, champion, history)
                    if choice is not None:
                        parent, selected = choice
                        proposal_source = "built-in"
                    else:
                        ledger.put("status", "search_exhausted")
                if selected is None:
                    break
                # Only the built-in, bounded seed list receives a screening
                # exemption. A model/operator stage label cannot waive stalls.
                if selected.stage == "screen":
                    selected = selected.model_copy(update={"stage": "manual"})
            # Persist the proposed experiment before validation or any backtest.
            index = register(ledger, selected.digest(), parent.digest(), selected.canonical())
            ledger.put(
                f"hypothesis:{index}",
                {
                    "experiment": index,
                    "proposal_sha256": selected.digest(),
                    "hypothesis": selected.hypothesis,
                    "research": selected.research.model_dump() if selected.research else None,
                },
            )
            ledger.event(
                "hypothesis_registered", experiment=index, proposal_sha256=selected.digest()
            )
            if proposal_source == "llm":
                ledger.put(f"proposal_model:{index}", ledger.get("last_proposal_model", {}))
            try:
                if selected.parent_candidate is not None:
                    parent = selected_parent(selected, champion, parents)
                    with ledger.db:
                        ledger.db.execute(
                            "UPDATE experiments SET parent=? WHERE id=?", (parent.digest(), index)
                        )
                candidate = apply_proposal(parent, selected)
                if program.extensions_only:
                    validate_daily_extension(parent, candidate)
                with ledger.db:
                    ledger.db.execute(
                        "UPDATE experiments SET candidate=? WHERE id=?", (candidate.digest(), index)
                    )
                # Explicit run-one replays retain their cached measurements.
                # Automatic batches must always select an untested recipe.
                if candidate.digest() in tried and proposal is None:
                    raise ValueError(
                        "duplicate strategy recipe; not counted as a completed strategy"
                    )
                if on_progress:
                    on_progress(
                        f"Strategy {completed_count + 1}/{iterations} ({proposal_source}): "
                        + selected.hypothesis
                    )
                ledger.phase("critique", index)
                report(root, program, ledger)
                judge_experiment(ledger, index, review_chain, stage="proposal")
                ledger.phase("backtesting", index)
                report(root, program, ledger)
                result = measure(root, program, ledger, candidate)
                ledger.phase("comparing", index)
                control = measure(root, program, ledger, parent)
                selection_control = measure(root, program, ledger, champion)
                if program.baseline.config.strategy.adaptive_rotation.group_selection:
                    floor = incumbent_return_floor(result, selection_control)
                    result = {**result, "return_floor": floor}
                    if not floor["passes"]:
                        result = {
                            **result,
                            "eligible": False,
                            "failed_gates": [
                                *result.get("failed_gates", []),
                                "incumbent_net_return",
                            ],
                        }
                improved = (
                    result["score"] is not None
                    and selection_control["score"] is not None
                    and result["score"] > selection_control["score"] + program.min_improvement
                )
                completed = {row["candidate"] for row in history if row["result"] is not None}
                uncertainty = (
                    selection_uncertainty(root, program, candidate, champion)
                    if result["eligible"] and improved
                    else {"passes": False, "reason": "eligibility_or_objective_margin"}
                )
                kept = (
                    result["eligible"]
                    and improved
                    and uncertainty["passes"]
                    and candidate.digest() not in completed
                )
                result = {
                    **result,
                    "control_candidate": parent.digest(),
                    "control_score": control["score"],
                    "selection_control": champion.digest(),
                    "selection_control_score": selection_control["score"],
                    "uncertainty": uncertainty,
                    "required_improvement": program.min_improvement,
                    "structural_audit_passed": True,
                    "proposal_source": proposal_source,
                }
                finish(ledger, index, "keep" if kept else "discard", result)
                ledger.event(
                    "decision",
                    experiment=index,
                    candidate=candidate.digest(),
                    decision="keep" if kept else "discard",
                    score=result["score"],
                    selection_control=result["selection_control"],
                    selection_control_score=result["selection_control_score"],
                )
                completed_count += 1
                if kept:
                    champion = candidate
                    ledger.put("incumbent", candidate.digest())
                ledger.put(
                    "stall", 0 if kept else ledger.get("stall", 0) + int(selected.stage != "screen")
                )
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                finish(ledger, index, "crash", error=str(exc))
                ledger.event("experiment_failed", experiment=index, error=str(exc))
                ledger.put("stall", ledger.get("stall", 0) + int(selected.stage != "screen"))
            ledger.phase("judging", index)
            report(root, program, ledger)
            judge_experiment(ledger, index, review_chain, stage="outcome")
            ledger.phase("review", index)
            report(root, program, ledger)
            explanation = summarize_experiment(root, ledger, index, reviewer)
            if on_summary:
                on_summary(explanation)
            report(root, program, ledger)
        if ledger.get("status") == "running":
            ledger.put(
                "status", "batch_completed" if completed_count == iterations else "batch_incomplete"
            )
        from autoresearch.engineering import automatic_handoff  # noqa: PLC0415 - optional lane

        automatic_handoff(root, program, ledger, champion, models_enabled=chain is not None)
        ledger.phase("finished")
        ledger.event(
            "batch_finished",
            completed=completed_count,
            requested=iterations,
            status=ledger.get("status"),
        )
        return report(root, program, ledger)


def selection_uncertainty(
    root: Path, program: Program, candidate: Candidate, control: Candidate
) -> dict[str, Any]:
    series = []
    dates = []
    for item in (candidate, control):
        daily = json.loads(
            (root / "outputs/runs" / item.digest() / "primary/daily.json").read_text()
        )
        dates.append([row["session"] for row in daily])
        series.append(np.array([row["return"] for row in daily[1:]], dtype=float))
    if dates[0] != dates[1]:
        raise ValueError("selection controls have different measured dates")
    outcome = paired_uncertainty(series[0], series[1], program)
    protocol_path = root / "runtime/statistical-protocol.json"
    if protocol_path.exists():
        from autoresearch.validation_contract import StatisticalProtocol  # noqa: PLC0415

        protocol = StatisticalProtocol(**json.loads(protocol_path.read_text()))
        with sqlite3.connect(root / "research.sqlite3") as connection:
            trial = connection.execute(
                "SELECT COUNT(*) FROM experiments WHERE parent!=''"
            ).fetchone()[0]
        diagnostic = protocol.compare(series[0].tolist(), series[1].tolist(), trial=trial)
        outcome["registered_statistical_diagnostic"] = diagnostic
    return outcome


def checkpoint(root: Path) -> dict[str, Any]:
    root = root.resolve()
    program = load_program(root)
    with Ledger(root, program.max_model_tokens_per_day) as ledger:
        parent = incumbent(root, program, ledger)
        results = {}
        for label, candidate in (("baseline", program.baseline), ("incumbent", parent)):
            target = root / "checkpoints" / candidate.digest()
            key = f"validation:{candidate.digest()}"
            result = cached_measurement(root, ledger, key)
            if result is None:
                result = execute(root, program, candidate, target, validation=True)
                result["artifact_hashes"] = {
                    str(path.relative_to(root)): digest_file(path)
                    for path in target.rglob("*")
                    if path.is_file()
                }
                with ledger.db:
                    ledger.db.execute("INSERT INTO cache VALUES(?,?)", (key, json.dumps(result)))
            results[label] = result
        write_json(root / "checkpoint.json", results)
        return results
