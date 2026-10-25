"""Bounded autonomous strategy and feature development in an isolated workspace."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

from autoresearch.contracts import Program, Proposal
from autoresearch.failures import collect_failures
from autoresearch.ledger import Ledger
from autoresearch.loop import MAX_BATCH, cached_measurement, checkpoint, report, run_loop
from autoresearch.presentation import batch_results, write_html_report
from autoresearch.promotion import active_promotion, promote
from autoresearch.registry import catalog
from autoresearch.summaries import summarize_experiment
from autoresearch.telemetry import analysis_lock, export_analysis, monitoring, read_telemetry
from autoresearch.workspace import digest_file, initialize, load_program, write_json
from llmrouter.chain import Chain, Router


def print_summary(item: dict[str, Any]) -> None:
    print(f"\nExperiment {item['experiment']} — {item['decision']}", flush=True)
    if item["status"] != "complete":
        print(f"LLM summary unavailable: {item['reason']}", flush=True)
        return
    summary = item["summary"]
    print(f"{summary['verdict'].upper()}: {summary['headline']}\n{summary['explanation']}")
    for text in summary["strengths"]:
        print(f"  Strength: {text}")
    for text in summary["weaknesses"]:
        print(f"  Weakness: {text}")
    print(f"Next experiment: {summary['next_experiment']}")
    print(f"Evidence limits: {summary['evidence_limits']}")
    print(f"LLM: {item['provider']} / {item['model']}\n", flush=True)


def argument_parser() -> argparse.ArgumentParser:  # noqa: PLR0915 - declarative CLI options
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    from autoresearch.genetic.cli import configure_parser  # noqa: PLC0415 - isolated research tool

    configure_parser(
        commands.add_parser("patterns", help="Validate and discover causal candle patterns")
    )
    trades = commands.add_parser("trades", help="Inspect backtest and paper trade-monitor JSON")
    trades.add_argument("inputs", nargs="+", type=Path)
    trades.add_argument("--output", type=Path, default=Path("outputs/trade-monitor"))
    followup = commands.add_parser("follow-up", help="Execute an approved matched research task")
    followup.add_argument("workspace", type=Path)
    followup.add_argument("review_id")
    followup.add_argument("task_id")
    for name in ("related", "charts"):
        tool = commands.add_parser(name, help="Inspect verified research evidence")
        tool.add_argument("workspace", type=Path)
        tool.add_argument("--candidate")
        if name == "related":
            tool.add_argument("--query", default="")
    start = commands.add_parser("start", help="Propose, backtest and compare N distinct strategies")
    start.add_argument("iterations", type=int, nargs="?", default=5)
    start.add_argument("--source", type=Path, default=Path.cwd())
    start.add_argument("--snapshot", type=Path, help="Daily snapshot override for a new session")
    start.add_argument("--validation-end", type=dt.date.fromisoformat, default=dt.date(2026, 8, 10))
    start.add_argument(
        "--intraday",
        action="store_true",
        help="Research 1-60 minute strategies using actual minute bars",
    )
    start.add_argument(
        "--minute-data",
        type=Path,
        help="Hash-verified minute dataset; downloaded automatically if omitted",
    )
    start.add_argument(
        "--resume",
        action="store_true",
        help="Resume the selected workspace (Controller: both modes; low-level start: intraday)",
    )
    start.add_argument("--workspace", type=Path)
    start.add_argument("--chain", type=Path)
    start.add_argument(
        "--brief",
        default="",
        help="Research preferences for LLM proposals, frozen with the session",
    )
    start.add_argument("--program", choices=("standard", "training-2025"), default="standard")
    start.add_argument("--built-in", action="store_true", help="Use built-in strategy proposals")
    start.add_argument("--no-open", action="store_true")
    start.add_argument("--robustness", choices=("historical", "extended"), default="extended")
    start.add_argument("--scenario-synthetic-paths", type=int, default=1)
    start.add_argument("--max-model-tokens-per-day", type=int, default=300000)
    controller = commands.add_parser(
        "control",
        parents=[start],
        add_help=False,
        help="Controller plans, launches and oversees research",
    )
    controller.add_argument("--max-batches", type=int, default=3)
    controller.add_argument("--controller-model-tokens", type=int, default=100000)
    controller.add_argument("--ledger", type=Path, help="Read-only research ledger workspace")
    controller.add_argument("--plan-only", action="store_true")
    promotion = commands.add_parser(
        "promote", help="Select a measured strategy for new research sessions"
    )
    promotion.add_argument("workspace", type=Path)
    promotion.add_argument("--candidate", required=True)
    promotion.add_argument("--label", required=True)
    promotion.add_argument("--reason", required=True)
    promotion.add_argument("--source", type=Path, default=Path.cwd())
    commands.add_parser("active", help="Show the operator-selected research strategy").add_argument(
        "--source", type=Path, default=Path.cwd()
    )
    view = commands.add_parser("view", help="Open the latest strategy comparison report")
    view.add_argument("workspace", type=Path, nargs="?")
    view.add_argument("--source", type=Path, default=Path.cwd())
    view.add_argument("--no-open", action="store_true")
    analyze = commands.add_parser("analyze", help="Export recorded results for deeper analysis")
    analyze.add_argument("workspace", type=Path, nargs="?")
    analyze.add_argument("--source", type=Path, default=Path.cwd())
    failures = commands.add_parser("failures", help="View unsuccessful strategy analyses")
    failures.add_argument("workspace", type=Path, nargs="?")
    failures.add_argument("--source", type=Path, default=Path.cwd())
    failures.add_argument(
        "--all",
        action="store_true",
        help="Collect all exported sessions under the research directory",
    )
    failures.add_argument("--no-open", action="store_true")
    commands.add_parser(
        "features", help="List registered feature formulas and availability contracts"
    )
    init = commands.add_parser("init")
    init.add_argument("workspace", type=Path)
    init.add_argument("--source", type=Path, default=Path.cwd())
    init.add_argument(
        "--config", type=Path, default=Path("configs/strategy/amat_group_selection_volatility.toml")
    )
    init.add_argument("--snapshot", type=Path, default=Path("data/snapshots/alpaca-sip-2026-08-11"))
    init.add_argument("--name", default="v6-feature-development")
    init.add_argument("--train-end", type=dt.date.fromisoformat, default=dt.date(2022, 12, 30))
    init.add_argument("--validation-end", type=dt.date.fromisoformat, default=dt.date(2026, 8, 10))
    init.add_argument("--max-experiments", type=int, default=50)
    init.add_argument("--stall-limit", type=int, default=12)
    init.add_argument("--timeout-seconds", type=int)
    init.add_argument("--max-model-tokens-per-day", type=int, default=100000)
    init.add_argument("--robustness", choices=("historical", "extended"), default="historical")
    init.add_argument("--scenario-seed", type=int, default=20260911)
    init.add_argument("--scenario-bootstrap-paths", type=int, default=2)
    init.add_argument("--scenario-synthetic-paths", type=int, default=1)
    init.add_argument("--summary-chain", type=Path)
    for name in ("loop", "run-one", "status", "checkpoint", "diagnostics", "summarize"):
        command = commands.add_parser(name)
        command.add_argument("workspace", type=Path, nargs="?" if name == "status" else None)
        if name == "status":
            command.add_argument("--source", type=Path, default=Path.cwd())
            command.add_argument("--human", action="store_true")
            command.add_argument(
                "--watch", action="store_true", help="refresh status every 2 seconds"
            )
        if name == "loop":
            command.add_argument("--iterations", type=int, default=1)
            command.add_argument(
                "--chain",
                type=Path,
                help="Model proposer chain; default: built-in feature search",
            )
        if name == "run-one":
            command.add_argument("proposal", type=Path)
        if name == "diagnostics":
            command.add_argument("--candidate", help="Candidate digest; defaults to incumbent")
        if name in ("loop", "run-one", "summarize"):
            command.add_argument("--summary-chain", type=Path)
        if name == "summarize":
            command.add_argument("--experiment", type=int, required=True)
    return parser


def latest_path(source: Path) -> Path:
    return source.resolve().parent.parent / "amat-research" / "latest-session.json"


def open_report(root: Path, *, no_open: bool) -> None:
    path = root / "report.html"
    if not path.is_file():
        raise ValueError(f"comparison report is not ready yet: {path}")
    print(f"\nResults: {path}", flush=True)
    if not no_open and not webbrowser.open(path.as_uri()):
        print("Open the results file in your browser to view the comparison.", flush=True)


def brief_review(item: dict[str, Any]) -> None:
    if item.get("status") == "complete":
        review = item["summary"]
        print(f"  {review['verdict'].upper()}: {review['headline']}", flush=True)
    else:
        print(f"  LLM review unavailable: {item['reason']}", flush=True)


def research_active(root: Path) -> bool:
    if monitoring(read_telemetry(root), root)["writer_active"]:
        return True
    audit = root.with_name(root.name + "-controller")
    return (audit / "research.sqlite3").is_file() and bool(
        monitoring(read_telemetry(audit), audit)["writer_active"]
    )


def inspect_session(args: argparse.Namespace, root: Path) -> None:
    telemetry = read_telemetry(root)
    monitor = monitoring(telemetry, root)
    batch = batch_results(telemetry["experiments"], telemetry["state"].get("batch", {}))
    engineering = telemetry["state"].get("engineering")
    controller_path = root.with_name(root.name + "-controller") / "controller.json"
    controller = json.loads(controller_path.read_text()) if controller_path.is_file() else None
    if args.command == "status":
        if args.human:
            print(f"{batch['completed']}/{batch['requested']} strategies · {monitor['status']}")
            print(f"Phase: {monitor['phase']} · experiment: {monitor['experiment']}")
            if controller:
                print(
                    f"Controller: {controller['status']} · audit: {controller['audit_directory']}"
                )
            if monitor["phase_elapsed_seconds"] is not None:
                print(f"Phase elapsed: {monitor['phase_elapsed_seconds']:.1f}s")
            print(
                f"Provider calls: {monitor['provider_calls']} "
                f"· errors: {monitor['provider_errors']}"
            )
            print(
                f"Budget charged tokens: {monitor['budget_charged_tokens']} "
                f"· calls with unknown usage: {monitor['unknown_usage_calls']}"
            )
            for model in monitor["models"]:
                outcomes = ", ".join(f"{key}={value}" for key, value in model["outcomes"].items())
                print(f"  {model['provider']}/{model['model']}: {outcomes}")
            if engineering:
                print(f"Engineering: {engineering['status']} · {engineering['directory']}")
        else:
            print(
                json.dumps(
                    {
                        **monitor,
                        "batch": batch,
                        "engineering": engineering,
                        "controller": controller,
                    },
                    indent=2,
                )
            )
        return
    with analysis_lock(root):
        telemetry = read_telemetry(root)
        if digest_file(root / "program.json") != (root / "program.sha256").read_text():
            raise ValueError("recorded program changed")
        program = Program.model_validate_json((root / "program.json").read_text())
        result = json.loads((root / "report.json").read_text())
        if result["program_sha256"] != program.digest():
            raise ValueError("report does not match its frozen program")
        result["experiments"] = telemetry["experiments"]
        result["status"] = monitoring(telemetry, root)["status"]
        result["batch"] = batch_results(
            telemetry["experiments"], telemetry["state"].get("batch", {})
        )
        result["incumbent"] = telemetry["state"].get("incumbent")
        result["analysis"] = export_analysis(root, result, telemetry)
        write_json(root / "report.json", result)
        write_html_report(root, result)
        print(f"Analysis exports: {root / 'analysis'}")
        print(f"Comparison report: {root / 'report.html'}")


def frozen_start_batch(
    root: Path, iterations: int, built_in: bool, guidance: str = ""
) -> dict[str, Any]:
    """Executed by the child interpreter against the newly frozen controller."""
    program = load_program(root)
    return run_loop(
        root,
        iterations=iterations,
        chain=None if built_in else program.summary_chain,
        fallback_search=True,
        controller_guidance=guidance,
        on_summary=brief_review,
        on_progress=lambda message: print(message, flush=True),
    )


def run_frozen_start(
    root: Path, iterations: int, built_in: bool, guidance: str = ""
) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "runtime")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Preserve the historical three-argument protocol unless guidance is actually supplied.
    extra = ", sys.argv[4]" if guidance else ""
    code = (
        "import sys; from pathlib import Path; "
        "from autoresearch.cli import frozen_start_batch; "
        f"frozen_start_batch(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3] == '1'{extra})"
    )
    completed = subprocess.run(  # noqa: S603 - fixed code and argv, no shell
        [sys.executable, "-c", code, str(root), str(iterations), str(int(built_in))]
        + ([guidance] if guidance else []),
        cwd=root,
        env=env,
        check=False,
    )
    if completed.returncode:
        raise ValueError(f"frozen research process exited with code {completed.returncode}")
    return dict(json.loads((root / "report.json").read_text()))


def start_session(args: argparse.Namespace, *, initial_iterations: int | None = None) -> int:
    if not 1 <= args.iterations <= MAX_BATCH:
        raise ValueError("strategy count must be within 1..100")
    source = args.source.resolve()
    if args.program == "training-2025" and args.intraday:
        raise ValueError("training-2025 is a separate daily program")
    if args.intraday:
        if args.snapshot or args.validation_end != dt.date(2026, 8, 10):
            raise ValueError("--snapshot and --validation-end are daily-mode options")
        return start_intraday(args, initial_iterations=initial_iterations)
    if args.resume or args.minute_data:
        raise ValueError("--resume and --minute-data require --intraday")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S-%f")
    root = (args.workspace or latest_path(source).parent / f"session-{stamp}").resolve()
    chain_path = args.chain or Path(
        os.getenv("AMAT_MODEL_CHAIN", str(source / "configs/autoresearch/llm-chain.example.toml"))
    )
    chain = None if args.built_in else Chain.load(chain_path)
    # The user's current research incumbent is daily group selection. Old
    # promotions remain audit history and cannot replace this chosen reference.
    selected = None
    initialize(
        root,
        source,
        source / "configs/strategy/amat_group_selection_volatility.toml",
        (
            args.snapshot
            if args.snapshot and args.snapshot.is_absolute()
            else source / (args.snapshot or "data/snapshots/alpaca-sip-core-2026-09-14-verified")
        ),
        name=f"research-{stamp}",
        extensions_only=True,
        schema_version="strategy-development/9",
        brief=args.brief,
        promotion=selected,
        engineering_policy=json.loads(
            (source / "configs/autoresearch/engineering.json").read_text()
        ),
        independent_calendar=True,
        train_end=dt.date(2025, 12, 31)
        if args.program == "training-2025"
        else dt.date(2022, 12, 30),
        validation_end=args.validation_end,
        max_experiments=args.iterations * 3,
        stall_limit=args.iterations * 3,
        timeout_seconds=1800 if args.robustness == "extended" else 900,
        max_model_tokens_per_day=args.max_model_tokens_per_day,
        evaluation={
            "robustness": args.robustness,
            "scenario_synthetic_paths": args.scenario_synthetic_paths,
        },
        summary_chain=chain,
    )
    write_json(latest_path(source), {"workspace": str(root)})
    print(
        f"\nResearch session: {root}\nTesting {args.iterations} strategies plus the baseline.",
        flush=True,
    )
    print(
        "Baseline: daily group selection on core ETFs; lower-return candidates are discarded.",
        flush=True,
    )
    print("Measuring the baseline…", flush=True)
    result = run_frozen_start(root, initial_iterations or args.iterations, args.built_in)
    batch = result["batch"]
    if result.get("engineering"):
        print(
            f"Engineering: {result['engineering']['status']} · {result['engineering']['directory']}"
        )
    print(
        f"\nCompleted {batch['completed']}/{batch['requested']} strategies; "
        f"{batch['escalated']} escalated. Status: {result['status']}.",
        flush=True,
    )
    open_report(root, no_open=args.no_open)
    return int(batch["completed"] != batch["requested"])


def run_frozen_intraday(
    root: Path, iterations: int, built_in: bool = False, guidance: str = ""
) -> dict[str, Any]:
    if built_in:
        raise ValueError("intraday requires model proposals")
    env = dict(os.environ)
    env.update(PYTHONPATH=str(root / "runtime"), PYTHONDONTWRITEBYTECODE="1")
    code = (
        "import sys; from pathlib import Path; from autoresearch.intraday.research import run; "
        "run(Path(sys.argv[1]), max_iterations=int(sys.argv[2]), guidance=sys.argv[3], "
        "progress=lambda message: print(message, flush=True))"
    )
    completed = subprocess.run(  # noqa: S603 - fixed code, argv only
        [sys.executable, "-c", code, str(root), str(iterations), guidance],
        cwd=root,
        env=env,
        check=False,
    )
    if completed.returncode:
        raise ValueError(f"frozen intraday process exited with code {completed.returncode}")
    return dict(json.loads((root / "report.json").read_text()))


def start_intraday(args: argparse.Namespace, *, initial_iterations: int | None = None) -> int:
    from autoresearch.intraday.data import fetch  # noqa: PLC0415 - optional research mode
    from autoresearch.intraday.research import freeze  # noqa: PLC0415 - optional mode

    if not 1 <= args.iterations <= MAX_BATCH:
        raise ValueError("iterations must be within 1..100")
    if args.built_in:
        raise ValueError("intraday uses full-recipe model proposals; --built-in is daily-only")
    source = args.source.resolve()
    directory = latest_path(source).parent
    root = args.workspace or directory / (
        "intraday-" + dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    )
    root = root.resolve()
    if args.resume:
        if args.workspace is None:
            raise ValueError("--resume requires the original --workspace")
    else:
        data = args.minute_data or directory / "intraday-data-sip-2022-2025"
        if not (data / "manifest.json").is_file():
            print("Downloading actual one-minute SIP bars for intraday research…", flush=True)
            fetch(data, dt.date(2022, 1, 1), dt.date(2025, 12, 31))
        chain = Chain.load(
            args.chain
            or Path(
                os.getenv(
                    "AMAT_MODEL_CHAIN", str(source / "configs/autoresearch/llm-chain.example.toml")
                )
            )
        )
        freeze(
            root,
            source,
            data,
            args.iterations,
            chain,
            args.max_model_tokens_per_day,
            brief=args.brief,
        )
    write_json(latest_path(source), {"workspace": str(root)})
    print(f"Intraday research session: {root}", flush=True)
    state = run_frozen_intraday(root, initial_iterations or args.iterations, False, args.brief)
    open_report(root, no_open=args.no_open)
    return int(state["status"] not in {"completed", "batch_completed"})


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911, PLR0912, PLR0915 - CLI dispatch
    parser = argument_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "patterns":
            from autoresearch.genetic.cli import dispatch  # noqa: PLC0415 - isolated research tool

            return dispatch(args)
        if args.command == "follow-up":
            from portfolio_team.cli import main as team_main  # noqa: PLC0415 - optional worker

            load_program(args.workspace.resolve())
            return team_main(
                [
                    "--root",
                    str(args.workspace.resolve()),
                    "execute-task",
                    args.review_id,
                    args.task_id,
                    "--run",
                ]
            )
        if args.command == "trades":
            from amat.reporting.trade_monitor import combine  # noqa: PLC0415

            print(combine(args.inputs, args.output).resolve())
            return 0
        if args.command in ("related", "charts"):
            from autoresearch.tools import inspect_tool  # noqa: PLC0415 - read-only tools

            print(
                json.dumps(
                    inspect_tool(
                        args.workspace.resolve(),
                        args.command,
                        args.candidate,
                        getattr(args, "query", ""),
                    ),
                    indent=2,
                )
            )
            return 0
        if args.command == "active":
            print(json.dumps(active_promotion(latest_path(args.source).parent), indent=2))
            return 0
        if args.command == "promote":
            record = promote(
                args.workspace,
                latest_path(args.source).parent,
                args.candidate,
                label=args.label,
                reason=args.reason,
            )
            print(json.dumps(record, indent=2))
            return 0
        if args.command == "control":
            from autoresearch.controller import run_controller  # noqa: PLC0415

            outcome = run_controller(
                args,
                start=start_session,
                continue_batch=run_frozen_intraday if args.intraday else run_frozen_start,
                session_directory=latest_path(args.source.resolve()).parent,
                progress=lambda message: print(message, flush=True),
            )
            print(json.dumps(outcome, indent=2))
            if (Path(outcome["workspace"]) / "report.html").is_file():
                open_report(Path(outcome["workspace"]), no_open=args.no_open)
            return int(outcome["status"] not in {"stopped", "completed_limits", "planned"})
        if args.command == "start":
            return start_session(args)
        if args.command == "failures" and args.all:
            library = collect_failures(latest_path(args.source).parent)
            print(json.dumps(library, indent=2))
            if not args.no_open:
                webbrowser.open(Path(library["path"]).as_uri())
            return 0
        if args.command in ("view", "status", "analyze", "failures"):
            root = args.workspace
            if root is None:
                pointer = latest_path(args.source)
                if not pointer.is_file():
                    raise ValueError("no research session yet; run ./research 5 first")
                root = Path(json.loads(pointer.read_text())["workspace"])
            program_path = root / "program.json"
            if (
                program_path.is_file()
                and json.loads(program_path.read_text()).get("schema") == "intraday-research/1"
            ):
                state = json.loads((root / "report.json").read_text())
                if args.command == "status":
                    print(
                        f"Intraday: {state['status']}; "
                        f"{len(state['experiments']) - 1}/{state['requested']} completed; "
                        f"best {state['incumbent']}"
                    )
                elif args.command == "analyze":
                    from autoresearch.intraday.report import render  # noqa: PLC0415
                    from autoresearch.intraday.research import verify  # noqa: PLC0415

                    verify(root)
                    with Ledger(root, 256):
                        render(root, state)
                    print(f"Intraday metrics: {root / 'metrics.csv'}")
                else:
                    open_report(root.resolve(), no_open=args.no_open)
                return 0
            if args.command == "failures":
                path = root.resolve() / "analysis/failures.html"
                if not path.is_file():
                    raise ValueError(
                        "failure library is not ready; run ./research analyze for this session"
                    )
                print(f"Failure library: {path}")
                if not args.no_open:
                    webbrowser.open(path.as_uri())
            elif args.command == "view":
                open_report(root.resolve(), no_open=args.no_open)
            else:
                inspect_session(args, root.resolve())
                if args.command == "status" and args.watch:
                    try:
                        while research_active(root):
                            time.sleep(2)
                            inspect_session(args, root.resolve())
                    except KeyboardInterrupt:
                        return 0
        elif args.command == "features":
            print(json.dumps(catalog(), indent=2))
        elif args.command == "init":
            source = args.source.resolve()
            config = args.config if args.config.is_absolute() else source / args.config
            snapshot = args.snapshot if args.snapshot.is_absolute() else source / args.snapshot
            summary_path = args.summary_chain or Path(
                os.getenv(
                    "AMAT_MODEL_CHAIN", str(source / "configs/autoresearch/llm-chain.example.toml")
                )
            )
            if args.summary_chain and not summary_path.is_file():
                raise ValueError(f"summary chain does not exist: {summary_path}")
            program = initialize(
                args.workspace,
                source,
                config,
                snapshot,
                name=args.name,
                train_end=args.train_end,
                validation_end=args.validation_end,
                max_experiments=args.max_experiments,
                stall_limit=args.stall_limit,
                timeout_seconds=args.timeout_seconds
                if args.timeout_seconds is not None
                else (1800 if args.robustness == "extended" else 240),
                max_model_tokens_per_day=args.max_model_tokens_per_day,
                evaluation={
                    "robustness": args.robustness,
                    "scenario_seed": args.scenario_seed,
                    "scenario_bootstrap_paths": args.scenario_bootstrap_paths,
                    "scenario_synthetic_paths": args.scenario_synthetic_paths,
                },
                summary_chain=Chain.load(summary_path) if summary_path.is_file() else None,
            )
            print(
                json.dumps(
                    {"workspace": str(args.workspace.resolve()), "program_sha256": program.digest()}
                )
            )
        elif args.command in ("loop", "run-one"):
            result = run_loop(
                args.workspace,
                iterations=args.iterations if args.command == "loop" else 1,
                chain=Chain.load(args.chain) if args.command == "loop" and args.chain else None,
                proposal=Proposal.model_validate_json(args.proposal.read_text())
                if args.command == "run-one"
                else None,
                summary_chain=Chain.load(args.summary_chain) if args.summary_chain else None,
                on_summary=print_summary,
            )
            print(
                json.dumps(
                    {
                        key: value
                        for key, value in result.items()
                        if key not in ("experiments", "llm_summaries")
                    },
                    indent=2,
                )
            )
            return int(
                result["status"]
                in (
                    "paused_budget",
                    "paused_no_provider",
                    "invalid_model_proposal",
                    "batch_incomplete",
                )
            )
        elif args.command == "checkpoint":
            print(json.dumps(checkpoint(args.workspace), indent=2))
        else:
            root = args.workspace.resolve()
            program = load_program(root)
            with Ledger(root, program.max_model_tokens_per_day) as ledger:
                if args.command == "summarize":
                    chain = (
                        Chain.load(args.summary_chain)
                        if args.summary_chain
                        else program.summary_chain
                    )
                    print(
                        json.dumps(
                            summarize_experiment(
                                root,
                                ledger,
                                args.experiment,
                                Router(chain) if chain else None,
                                retry=True,
                            ),
                            indent=2,
                        )
                    )
                    report(root, program, ledger)
                elif args.command == "diagnostics":
                    diagnostic_result = cached_measurement(
                        root, ledger, args.candidate or ledger.get("incumbent")
                    )
                    if diagnostic_result is None:
                        raise ValueError("no completed measurements for that candidate")
                    print(
                        json.dumps(
                            {
                                key: diagnostic_result.get(key)
                                for key in (
                                    "diagnostics",
                                    "control_summary",
                                    "robustness",
                                    "failed_gates",
                                )
                            },
                            indent=2,
                        )
                    )
                else:
                    print(json.dumps(report(root, program, ledger), indent=2))
    except (ValueError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"autoresearch: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
