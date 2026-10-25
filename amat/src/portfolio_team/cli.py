"""Evidence-grounded shadow reviews using a scripted or explicitly selected model provider."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from amat.errors import ConfigurationError
from llmrouter.policy import COMPLEX_MODEL
from portfolio_team.automation import run_cycle
from portfolio_team.coordinator import run_review
from portfolio_team.evaluation import evaluate, load_cases
from portfolio_team.evidence import EvidenceError, load_catalog, make_bundle
from portfolio_team.model_runtime import ModelSettings, RemoteProvider
from portfolio_team.openai_runtime import OpenAIProvider, OpenAISettings, ReviewTeam
from portfolio_team.preflight import preflight_review
from portfolio_team.providers import FakeProvider
from portfolio_team.reports import _write_once, export_review
from portfolio_team.research_queue import (
    ExperimentQueue,
    TaskApproval,
    approve_task,
    build_queue,
    dispatch_spec,
    execute_approved_task,
    load_queue,
    run_registered_task,
    validate_approval,
)
from portfolio_team.routing import RoutedProvider
from portfolio_team.store import Store


def _ingest(store: Store, root: Path, path: Path) -> int:
    catalog = load_catalog(path)
    failed = False
    for comparison in catalog.comparisons:
        try:
            bundle, blobs = make_bundle(catalog, comparison.id, root)
            digest = store.import_bundle(bundle, blobs)
            print(f"{comparison.id}: imported {digest}")
        except (ValueError, OSError, ConfigurationError) as exc:
            store.quarantine(comparison.id, str(exc))
            print(f"{comparison.id}: quarantined — {exc}")
            failed = True
    return int(failed)


def _provider(args: argparse.Namespace) -> FakeProvider | RemoteProvider:
    if args.provider == "auto":
        if args.model or args.chief_model:
            raise EvidenceError(
                "Use an explicit provider with model overrides; auto uses shared policy"
            )
        return RoutedProvider(
            ModelSettings(
                model="shared-policy",
                request_timeout_seconds=args.request_timeout,
                review_timeout_seconds=args.review_timeout,
                concurrent_requests=args.concurrent_requests,
            )
        )
    if args.provider == "fake":
        if args.chief_model:
            raise EvidenceError(
                "A model chief requires real first-pass reviewers, not scripted ones"
            )
        return FakeProvider()
    if args.provider == "openai" and not args.model:
        args.model = COMPLEX_MODEL
    credential = "OPENAI_API_KEY" if args.provider == "openai" else "GEMINI_API_KEY"
    if not args.model or not os.getenv(credential):
        raise EvidenceError(f"{args.provider} requires --model and {credential}")
    settings = {
        "model": args.model,
        "request_timeout_seconds": args.request_timeout,
        "review_timeout_seconds": args.review_timeout,
        "concurrent_requests": args.concurrent_requests,
    }
    provider: RemoteProvider
    if args.provider == "openai":
        provider = OpenAIProvider(
            OpenAISettings(**settings, reasoning_effort=args.reasoning_effort)
        )
    else:
        provider = RemoteProvider(ModelSettings(**settings))
    if args.chief_model:
        if not os.getenv("OPENAI_API_KEY"):
            raise EvidenceError("An OpenAI chief requires OPENAI_API_KEY")
        chief = OpenAIProvider(
            OpenAISettings(
                **{**settings, "model": args.chief_model},
                reasoning_effort=args.chief_reasoning,
            )
        )
        return ReviewTeam(provider, chief)
    return provider


def _model_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--provider", choices=("auto", "fake", "gemini", "openai"), default="auto")
    command.add_argument("--model")
    command.add_argument(
        "--reasoning-effort", choices=("low", "medium", "high", "xhigh"), default="high"
    )
    command.add_argument("--chief-model", help="Route chief_pm only to this explicit OpenAI model")
    command.add_argument(
        "--chief-reasoning", choices=("low", "medium", "high", "xhigh"), default="high"
    )
    command.add_argument("--request-timeout", type=float, default=90)
    command.add_argument("--review-timeout", type=float, default=480)
    command.add_argument("--concurrent-requests", type=int, choices=(1, 2, 3), default=1)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="AMAT project root")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("import-catalog")
    ingest.add_argument("catalog", type=Path)
    review = commands.add_parser("review")
    review.add_argument("comparison")
    _model_arguments(review)
    review.add_argument("--single-reviewer", action="store_true")
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("catalog", type=Path)
    _model_arguments(evaluation)
    cycle = commands.add_parser("cycle", help="Review a bounded set of declared comparisons once")
    cycle.add_argument("catalog", type=Path)
    cycle.add_argument("--max-reviews", type=int, default=1)
    _model_arguments(cycle)
    commands.add_parser("status")
    preflight = commands.add_parser("preflight", help="Check next dispatch without API calls")
    preflight.add_argument("comparison")
    preflight.add_argument("--single-reviewer", action="store_true")
    _model_arguments(preflight)
    plan = commands.add_parser(
        "plan", help="Export operator-gated follow-up experiments for a review"
    )
    plan.add_argument("review_id")
    approve = commands.add_parser(
        "approve-task", help="Record operator approval for one proposed task"
    )
    approve.add_argument("review_id")
    approve.add_argument("task_id")
    approve.add_argument("--by", required=True, dest="approved_by")
    dispatch = commands.add_parser(
        "dispatch-task", help="Validate an approved task and emit its fixed spec"
    )
    dispatch.add_argument("review_id")
    dispatch.add_argument("task_id")
    execute = commands.add_parser(
        "execute-task", help="Run an approved task through its registered worker"
    )
    execute.add_argument("review_id")
    execute.add_argument("task_id")
    execute.add_argument(
        "--run",
        action="store_true",
        help="Request execution; blocked until an adapter is registered",
    )
    for name in ("show", "replay"):
        command = commands.add_parser(name)
        command.add_argument("review_id")
        if name == "replay":
            command.add_argument("--offline", action="store_true", default=True)
    return parser


def _inspect(store: Store, args: argparse.Namespace) -> int:
    if args.command == "status":
        print(json.dumps(store.status(), indent=2))
        return 0
    result = preflight_review(
        store,
        store.resolve(args.comparison),
        _provider(args),
        single_reviewer=args.single_reviewer,
    )
    print(json.dumps(result, indent=2))
    return int(not result["ready"])


def _queue_path(target: Path) -> Path:
    versioned = target / "research_queue.v2.json"
    return versioned if versioned.is_file() else target / "research_queue.json"


def _review_queue(target: Path, review_id: str, evidence_sha256: str) -> ExperimentQueue:
    queue = load_queue(_queue_path(target))
    if queue.source_review_id != review_id or queue.evidence_sha256 != evidence_sha256:
        raise EvidenceError("queue does not match review evidence")
    return queue


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911, PLR0912, PLR0915
    parser = _parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("--root must identify the AMAT project")
    try:
        # The registered research adapter owns its own short registry accesses;
        # release the coordinator lock before acquiring the research ledger.
        if args.command == "execute-task" and args.run and (root / "program.json").is_file():
            with Store(root / "data/governance/pmteam") as store:
                decision = store.decision(args.review_id)
                if decision is None:
                    raise EvidenceError("no completed decision for that review ID")
                target = root / "outputs/governance" / decision.review_id
                queue = _review_queue(target, decision.review_id, decision.evidence_sha256)
            approval = TaskApproval.model_validate_json(
                (target / "approvals" / f"{args.task_id}.json").read_text()
            )
            receipt = run_registered_task(root, queue, args.task_id, approval)
            _write_once(
                target / "executions" / f"{args.task_id}.matched.json",
                (receipt.canonical() + "\n").encode(),
            )
            print(receipt.model_dump_json(indent=2))
            return int(receipt.status != "completed")
        with Store(root / "data/governance/pmteam") as store:
            if args.command in ("preflight", "status"):
                return _inspect(store, args)
            if args.command == "cycle":
                path = args.catalog if args.catalog.is_absolute() else root / args.catalog
                result = run_cycle(store, root, path, _provider(args), max_reviews=args.max_reviews)
                print(json.dumps(result, indent=2))
                return int(result["incomplete"])
            if args.command == "evaluate":
                path = args.catalog if args.catalog.is_absolute() else root / args.catalog
                target = evaluate(
                    store,
                    load_cases(path),
                    _provider(args),
                    root / "outputs/governance/evaluations",
                )
                print(target / "report.md")
                results = json.loads((target / "results.json").read_text())
                incomplete = any(row["covered"] is None for row in results["rows"])
                if incomplete:
                    print("Evaluation incomplete: deferred/failed reviews are not scored as zero.")
                return int(incomplete)
            if args.command == "import-catalog":
                path = args.catalog if args.catalog.is_absolute() else root / args.catalog
                return _ingest(store, root, path)
            if args.command == "review":
                provider = _provider(args)
                decision = run_review(
                    store,
                    store.resolve(args.comparison),
                    provider,
                    single_reviewer=args.single_reviewer,
                )
            else:
                saved = store.decision(args.review_id)
                if saved is None:
                    raise EvidenceError("no completed decision for that review ID")
                decision = saved
            if args.command == "show":
                print(decision.model_dump_json(indent=2))
            elif args.command == "plan":
                bundle = store.bundle(decision.evidence_sha256)
                queue = build_queue(bundle, decision)
                target = root / "outputs/governance" / decision.review_id
                path = target / "research_queue.v2.json"
                _write_once(path, (queue.canonical() + "\n").encode())
                print(path)
            elif args.command == "approve-task":
                target = root / "outputs/governance" / decision.review_id
                queue = _review_queue(target, decision.review_id, decision.evidence_sha256)
                approval_path = target / "approvals" / f"{args.task_id}.json"
                if approval_path.is_file():
                    approval = TaskApproval.model_validate_json(approval_path.read_text())
                    validate_approval(queue, args.task_id, approval)
                    if approval.approved_by != args.approved_by:
                        raise EvidenceError("task already approved by a different operator")
                else:
                    approval = approve_task(queue, args.task_id, args.approved_by)
                _write_once(
                    approval_path,
                    (approval.canonical() + "\n").encode(),
                )
                print(target / "approvals" / f"{args.task_id}.json")
            elif args.command == "dispatch-task":
                target = root / "outputs/governance" / decision.review_id
                queue = _review_queue(target, decision.review_id, decision.evidence_sha256)
                approval_path = target / "approvals" / f"{args.task_id}.json"
                if not approval_path.is_file():
                    raise EvidenceError("task is not operator-approved")
                approval = TaskApproval.model_validate_json(approval_path.read_text())
                task = validate_approval(queue, args.task_id, approval)
                print(json.dumps(dispatch_spec(task), indent=2))
            elif args.command == "execute-task":
                target = root / "outputs/governance" / decision.review_id
                queue = _review_queue(target, decision.review_id, decision.evidence_sha256)
                approval_path = target / "approvals" / f"{args.task_id}.json"
                if not approval_path.is_file():
                    raise EvidenceError("task is not operator-approved")
                approval = TaskApproval.model_validate_json(approval_path.read_text())
                if args.run:
                    dispatch_result = run_registered_task(root, queue, args.task_id, approval)
                else:
                    dispatch_result = execute_approved_task(queue, args.task_id, approval)
                path = target / "executions" / f"{args.task_id}.json"
                _write_once(path, (dispatch_result.canonical() + "\n").encode())
                print(dispatch_result.model_dump_json(indent=2))
                return int(dispatch_result.status != "completed")
            else:
                target = export_review(store, decision, root / "outputs/governance")
                print(
                    f"{decision.effective_disposition} "
                    f"({decision.provider_id}; no execution authority)"
                )
                print(target / "report.md")
    except (ValueError, OSError, ConfigurationError) as exc:
        parser.exit(1, f"pmteam: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
