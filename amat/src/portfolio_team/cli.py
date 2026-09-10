"""Evidence-grounded shadow reviews using a scripted or explicitly selected model provider."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from amat.errors import ConfigurationError
from portfolio_team.automation import run_cycle
from portfolio_team.coordinator import run_review
from portfolio_team.evaluation import evaluate, load_cases
from portfolio_team.evidence import EvidenceError, load_catalog, make_bundle
from portfolio_team.model_runtime import ModelSettings, RemoteProvider
from portfolio_team.openai_runtime import OpenAIProvider, OpenAISettings, ReviewTeam
from portfolio_team.providers import FakeProvider
from portfolio_team.reports import export_review
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
    if args.provider == "fake":
        if args.chief_model:
            raise EvidenceError(
                "A model chief requires real first-pass reviewers, not scripted ones"
            )
        return FakeProvider()
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
    command.add_argument("--provider", choices=("fake", "gemini", "openai"), default="fake")
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
    for name in ("show", "replay"):
        command = commands.add_parser(name)
        command.add_argument("review_id")
        if name == "replay":
            command.add_argument("--offline", action="store_true", default=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("--root must identify the AMAT project")
    try:
        with Store(root / "data/governance/pmteam") as store:
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
                    print("Evaluation incomplete: failed reviews are recorded, not scored as zero.")
                return int(incomplete)
            if args.command == "import-catalog":
                path = args.catalog if args.catalog.is_absolute() else root / args.catalog
                return _ingest(store, root, path)
            if args.command == "status":
                print(json.dumps(store.status(), indent=2))
                return 0
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
