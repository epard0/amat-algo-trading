"""Explicit training-only pattern validation and discovery commands."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from amat.errors import AmatError
from autoresearch.genetic.data import digest_json, load_crypto, load_daily
from autoresearch.genetic.models import canonical_genome_digest, parse_pattern
from autoresearch.genetic.search import SearchProtocol
from autoresearch.genetic.study import ScreenConfig, run_study


def configure_parser(parser: argparse.ArgumentParser) -> None:  # noqa: PLR0915 - explicit CLI options
    commands = parser.add_subparsers(dest="genetic_command", required=True)
    nested = commands.add_parser(
        "validate-search", help="Frozen nested symbolic search and whole-search nulls"
    )
    nested.add_argument("--protocol", type=Path, required=True)
    nested.add_argument("--incumbent", type=Path, required=True)
    nested.add_argument("--snapshot", type=Path, required=True)
    nested.add_argument("--output", type=Path, required=True)
    nested.add_argument("--resume", action="store_true")
    freeze = commands.add_parser(
        "shadow-freeze", help="Freeze an observed P4 pattern for diagnostics"
    )
    freeze.add_argument("--validation-study", type=Path, required=True)
    freeze.add_argument("--snapshot", type=Path, required=True)
    freeze.add_argument("--fold", required=True)
    freeze.add_argument("--method", choices=("genetic", "random"), required=True)
    freeze.add_argument("--rank", type=int, default=0)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument(
        "--allow-registry-rebind",
        action="store_true",
        help="Explicit diagnostic reuse after registry change; no validation transfer",
    )
    shadow = commands.add_parser(
        "shadow-observe", help="Report a frozen pattern on a snapshot session"
    )
    shadow.add_argument("--artifact", type=Path, required=True)
    shadow.add_argument("--snapshot", type=Path, required=True)
    shadow.add_argument("--as-of", type=dt.date.fromisoformat, required=True)
    shadow.add_argument("--ledger", type=Path, required=True)
    shadow.add_argument("--output", type=Path, required=True)
    refresh = commands.add_parser(
        "shadow-refresh", help="Reconcile prices and freeze new calibration"
    )
    refresh.add_argument("--previous-artifact", type=Path, required=True)
    refresh.add_argument("--snapshot", type=Path, required=True)
    refresh.add_argument("--receipts", type=Path, required=True)
    refresh.add_argument("--root", type=Path, required=True)
    refresh.add_argument("--snapshot-id", required=True)
    refresh.add_argument("--baseline-end", type=dt.date.fromisoformat, required=True)
    refresh.add_argument("--output", type=Path, required=True)
    spot = commands.add_parser("shadow-spot-intraday", help="Report frozen 15-minute/hourly views")
    spot.add_argument("--artifact", type=Path, required=True)
    spot.add_argument("--intraday-data", type=Path, required=True)
    spot.add_argument("--intraday-calibration-end", type=dt.date.fromisoformat, required=True)
    spot.add_argument("--as-of", type=dt.datetime.fromisoformat, required=True)
    spot.add_argument("--output", type=Path, required=True)
    audit = commands.add_parser(
        "shadow-audit-intraday", help="Explain 15-minute/hourly input coverage gaps"
    )
    audit.add_argument("--intraday-data", type=Path, required=True)
    audit.add_argument("--intraday-calibration-end", type=dt.date.fromisoformat, required=True)
    audit.add_argument("--as-of", type=dt.datetime.fromisoformat, required=True)
    audit.add_argument("--output", type=Path, required=True)
    minute = commands.add_parser(
        "shadow-fetch-intraday", help="Download immutable SIP minute inputs"
    )
    minute.add_argument("--symbols", nargs="+", required=True)
    minute.add_argument("--start", type=dt.date.fromisoformat, required=True)
    minute.add_argument("--end", type=dt.date.fromisoformat, required=True)
    minute.add_argument("--output", type=Path, required=True)
    native = commands.add_parser(
        "shadow-fetch-native", help="Download separate native aggregate evidence"
    )
    native.add_argument("--symbols", nargs="+", required=True)
    native.add_argument("--start", type=dt.date.fromisoformat, required=True)
    native.add_argument("--end", type=dt.date.fromisoformat, required=True)
    native.add_argument("--timeframe", choices=("15m", "1h"), required=True)
    native.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser(
        "shadow-compare-native", help="Audit native aggregates against retained minute inputs"
    )
    compare.add_argument("--native-data", type=Path, required=True)
    compare.add_argument("--intraday-data", type=Path, required=True)
    compare.add_argument("--intraday-calibration-end", type=dt.date.fromisoformat, required=True)
    compare.add_argument("--as-of", type=dt.datetime.fromisoformat, required=True)
    compare.add_argument("--output", type=Path, required=True)
    search = commands.add_parser(
        "search-parameters", help="Resumable MTF and exit-parameter portfolio research"
    )
    search.add_argument("--artifact", type=Path, required=True)
    search.add_argument("--snapshot", type=Path, required=True)
    search.add_argument("--native-data", type=Path, required=True)
    search.add_argument("--measured-start", type=dt.date.fromisoformat, required=True)
    search.add_argument("--train-end", type=dt.date.fromisoformat, required=True)
    search.add_argument("--validation-end", type=dt.date.fromisoformat, required=True)
    search.add_argument("--global-trials", type=int, default=2048)
    search.add_argument("--local-trials", type=int, default=256)
    search.add_argument("--workers", type=int, default=6)
    search.add_argument("--seed", type=int, default=20260929)
    search.add_argument("--output", type=Path, required=True)
    walk = commands.add_parser(
        "walkforward-parameters", help="Diversified rolling MTF portfolio parameter research"
    )
    walk.add_argument("--artifact", type=Path, required=True)
    walk.add_argument("--snapshot", type=Path, required=True)
    walk.add_argument("--native-data", type=Path, required=True)
    walk.add_argument("--folds", type=Path, required=True, help="JSON list of chronological folds")
    walk.add_argument("--global-trials", type=int, default=640)
    walk.add_argument("--local-trials", type=int, default=128)
    walk.add_argument("--workers", type=int, default=6)
    walk.add_argument("--seed", type=int, default=20260929)
    walk.add_argument("--output", type=Path, required=True)
    components = commands.add_parser(
        "compare-components", help="Matched MTF/exit removal and execution-cost stress research"
    )
    components.add_argument("--artifact", type=Path, required=True)
    components.add_argument("--snapshot", type=Path, required=True)
    components.add_argument("--native-data", type=Path, required=True)
    components.add_argument("--p11-study", type=Path, required=True)
    components.add_argument("--workers", type=int, default=6)
    components.add_argument("--costs", type=float, nargs="+", default=[2, 5, 10, 20])
    components.add_argument("--output", type=Path, required=True)
    register = commands.add_parser("shadow-register", help="Register a frozen shadow campaign")
    register.add_argument("--artifact", type=Path, required=True)
    register.add_argument("--snapshot", type=Path, required=True)
    register.add_argument("--output", type=Path, required=True)
    register.add_argument("--intraday-data", type=Path)
    register.add_argument("--intraday-calibration-end", type=dt.date.fromisoformat)
    poll = commands.add_parser("shadow-poll", help="Ingest completed bars and report once")
    poll.add_argument("--campaign", type=Path, required=True)
    poll.add_argument("--through", type=dt.date.fromisoformat, required=True)
    poll.add_argument("--ledger", type=Path, required=True)
    poll.add_argument("--output", type=Path, required=True)
    poll.add_argument("--intraday-data", type=Path)
    validate = commands.add_parser(
        "validate", help="Validate inert pattern JSON and canonical hash"
    )
    validate.add_argument("pattern", type=Path)
    symbolic = commands.add_parser("discover-symbolic", help="Typed numerical GP training screen")
    symbolic.add_argument("--snapshot", type=Path, required=True)
    symbolic.add_argument("--symbols", nargs="+", required=True)
    symbolic.add_argument("--train-start", type=dt.date.fromisoformat, required=True)
    symbolic.add_argument("--train-end", type=dt.date.fromisoformat, required=True)
    symbolic.add_argument("--output", type=Path, required=True)
    symbolic.add_argument("--method", choices=("genetic", "random"), default="genetic")
    symbolic.add_argument("--population", type=int, default=32)
    symbolic.add_argument("--generations", type=int, default=3)
    symbolic.add_argument("--seed", type=int, default=11)
    symbolic.add_argument("--max-depth", type=int, default=3)
    symbolic.add_argument("--max-nodes", type=int, default=15)
    symbolic.add_argument(
        "--features",
        nargs="+",
        default=["efficiency", "volume_ratio", "volatility_shock", "body_atr"],
    )
    symbolic.add_argument("--feature-lookback", type=int, default=21)
    symbolic.add_argument("--feature-lag", type=int, default=0)
    symbolic.add_argument("--feature-source", default="self")
    symbolic.add_argument(
        "--feature-transform",
        choices=("time_series_rank", "rolling_zscore"),
        default="rolling_zscore",
    )
    symbolic.add_argument("--one-way-bps", type=float, default=2.0)
    symbolic.add_argument("--min-effect-sessions", type=int, default=30)
    symbolic.add_argument("--max-slots", type=int)
    symbolic.add_argument("--resume", action="store_true")
    replay = commands.add_parser("replay", help="Matched historical portfolio replay; no promotion")
    replay.add_argument(
        "--incumbent", type=Path, required=True, help="Strategy TOML or Candidate JSON"
    )
    replay.add_argument("--snapshot", type=Path, required=True)
    replay.add_argument("--genetic-study", type=Path, required=True)
    replay.add_argument("--random-study", type=Path, required=True)
    replay.add_argument("--start", type=dt.date.fromisoformat, required=True)
    replay.add_argument("--train-end", type=dt.date.fromisoformat, required=True)
    replay.add_argument("--evaluation-end", type=dt.date.fromisoformat, required=True)
    replay.add_argument("--output", type=Path, required=True)
    discover = commands.add_parser("discover", help="Training-only candle search; no promotion")
    source = discover.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path, help="Verified daily ETF snapshot directory")
    source.add_argument(
        "--crypto-data", type=Path, help="Downloaded hourly crypto manifest directory"
    )
    discover.add_argument("--symbols", nargs="+", required=True)
    discover.add_argument("--train-start", type=dt.date.fromisoformat, required=True)
    discover.add_argument("--train-end", type=dt.date.fromisoformat, required=True)
    discover.add_argument("--output", type=Path, required=True)
    discover.add_argument("--method", choices=("genetic", "random"), default="genetic")
    discover.add_argument("--population", type=int, default=32)
    discover.add_argument(
        "--generations", type=int, default=3, help="Generations after the initial population"
    )
    discover.add_argument("--seed", type=int, default=11)
    discover.add_argument("--max-rules", type=int, default=3)
    discover.add_argument("--max-lag", type=int, default=2)
    discover.add_argument("--one-way-bps", type=float, default=2.0)
    discover.add_argument("--min-matched-sessions", type=int, default=30)
    discover.add_argument("--max-slots", type=int, help="Pause after this many additional slots")
    discover.add_argument("--resume", action="store_true")


def dispatch(args: argparse.Namespace) -> int:  # noqa: PLR0911 - explicit command routing
    if args.genetic_command in {
        "shadow-register",
        "shadow-refresh",
        "shadow-poll",
        "shadow-fetch-intraday",
        "shadow-spot-intraday",
        "shadow-audit-intraday",
        "shadow-fetch-native",
        "shadow-compare-native",
        "search-parameters",
        "walkforward-parameters",
        "compare-components",
    }:
        try:
            return _campaign(args)
        except AmatError as exc:
            raise ValueError(str(exc)) from exc
    if args.genetic_command in {"shadow-freeze", "shadow-observe"}:
        return _shadow(args)
    if args.genetic_command == "validate-search":
        return _validate_search(args)
    if args.genetic_command == "validate":
        pattern = parse_pattern(args.pattern.read_text())
        print(  # noqa: T201 - explicit CLI output
            json.dumps(
                {
                    "valid": True,
                    "genome_sha256": canonical_genome_digest(pattern),
                    "kind": pattern.genome.kind,
                    "execution_authority": "none",
                    "evaluator_supported": pattern.genome.kind == "candle_conjunction"
                    or (
                        pattern.timeframe == "1d"
                        and pattern.price_basis == "corporate_action_adjusted"
                    ),
                },
                indent=2,
            )
        )
        return 0
    if args.genetic_command == "replay":
        from autoresearch.genetic.replay import load_incumbent, run_replay  # noqa: PLC0415

        report = run_replay(
            load_incumbent(args.incumbent),
            args.snapshot.resolve(),
            args.genetic_study.resolve(),
            args.random_study.resolve(),
            start=args.start,
            train_end=args.train_end,
            evaluation_end=args.evaluation_end,
            output=args.output.resolve(),
        )
        print(  # noqa: T201 - explicit CLI output
            json.dumps(
                {
                    "status": report["status"],
                    "execution_authority": "none",
                    "report": str(args.output.resolve() / "report.md"),
                },
                indent=2,
            )
        )
        return 0 if report["status"] == "completed" else 1
    if args.genetic_command == "discover-symbolic":
        return _discover_symbolic(args)
    # Validate the compute budget before reading potentially large input files.
    protocol = SearchProtocol(
        population_size=args.population,
        generations=args.generations,
        seed=args.seed,
        max_rules=args.max_rules,
        max_lag=args.max_lag,
        elite_count=1,
        tournament_size=3,
        crossover_probability=0.8,
        mutation_probability=0.1,
        fresh_probability=0.02,
        method=args.method,
    )
    screen = ScreenConfig(
        one_way_bps=args.one_way_bps, min_matched_sessions=args.min_matched_sessions
    )
    loader = load_daily if args.snapshot else load_crypto
    dataset = loader(
        (args.snapshot or args.crypto_data).resolve(),
        tuple(args.symbols),
        start=args.train_start,
        end=args.train_end,
    )
    report = run_study(
        dataset,
        protocol,
        screen,
        args.output.resolve(),
        resume=args.resume,
        max_slots=args.max_slots,
    )
    print(  # noqa: T201 - explicit CLI output
        json.dumps(
            {
                "status": report["status"],
                "recorded_slots": report["recorded_slots"],
                "unique_genomes": report["unique_genomes"],
                "failed_slots": report["failed_slots"],
                "evidence_level": report["evidence_level"],
                "execution_authority": "none",
                "report": str(args.output.resolve() / "report.md"),
            },
            indent=2,
        )
    )
    return 0


def _discover_symbolic(args: argparse.Namespace) -> int:
    from amat.data.snapshot import load_panel  # noqa: PLC0415
    from autoresearch.genetic.symbolic_search import (  # noqa: PLC0415
        SymbolicSearchProtocol,
        SymbolicTerminal,
    )
    from autoresearch.genetic.symbolic_study import (  # noqa: PLC0415
        SymbolicScreenConfig,
        run_symbolic_study,
    )

    protocol = SymbolicSearchProtocol(
        population_size=args.population,
        generations=args.generations,
        seed=args.seed,
        max_depth=args.max_depth,
        max_nodes=args.max_nodes,
        method=args.method,
        elite_count=1,
        tournament_size=3,
        crossover_probability=0.8,
        mutation_probability=0.1,
        fresh_probability=0.02,
        feature_terminals=tuple(
            SymbolicTerminal(
                name=name,
                lookback=args.feature_lookback,
                lag=args.feature_lag,
                transform=args.feature_transform,
                source=args.feature_source,
            )
            for name in args.features
        ),
    )
    screen = SymbolicScreenConfig(
        one_way_bps=args.one_way_bps, min_effect_sessions=args.min_effect_sessions
    )
    snapshot, symbols = args.snapshot.resolve(), tuple(args.symbols)
    dataset = load_daily(snapshot, symbols, start=args.train_start, end=args.train_end)
    panel, _ = load_panel(
        snapshot, symbols, start=args.train_start, end=args.train_end, with_context=True
    )
    report = run_symbolic_study(
        dataset,
        panel,
        protocol,
        screen,
        args.output.resolve(),
        resume=args.resume,
        max_slots=args.max_slots,
    )
    print(  # noqa: T201 - explicit CLI output
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "recorded_slots",
                    "unique_genomes",
                    "failed_slots",
                    "evidence_level",
                )
            }
            | {"execution_authority": "none", "report": str(args.output.resolve() / "report.md")},
            indent=2,
        )
    )
    return 0


def _campaign(args: argparse.Namespace) -> int:
    from autoresearch.genetic.shadow_campaign import (  # noqa: PLC0415
        poll_campaign,
        register_campaign,
    )

    if args.genetic_command == "shadow-refresh":
        from amat.data.alpaca import AlpacaMarketData  # noqa: PLC0415
        from autoresearch.genetic.shadow_refresh import refresh_shadow  # noqa: PLC0415

        result = refresh_shadow(
            args.previous_artifact,
            args.snapshot,
            args.receipts,
            root=args.root,
            client=AlpacaMarketData(feed="sip"),
            snapshot_id=args.snapshot_id,
            baseline_end=args.baseline_end,
            output=args.output,
        )
    elif args.genetic_command == "shadow-spot-intraday":
        from autoresearch.genetic.multitimeframe import spot_intraday  # noqa: PLC0415

        result = spot_intraday(
            args.artifact,
            args.intraday_data,
            args.intraday_calibration_end,
            as_of=args.as_of,
            output=args.output,
        )
    elif args.genetic_command == "shadow-audit-intraday":
        from autoresearch.genetic.intraday_quality import audit_intraday  # noqa: PLC0415

        result = audit_intraday(
            args.intraday_data,
            args.intraday_calibration_end,
            as_of=args.as_of,
            output=args.output,
        )
    elif args.genetic_command == "shadow-fetch-intraday":
        from autoresearch.genetic.shadow_intraday import fetch_intraday  # noqa: PLC0415

        result = fetch_intraday(args.output, tuple(args.symbols), args.start, args.end)
        result = {"output": str(args.output.resolve()), "payload_sha256": digest_json(result)}
    elif args.genetic_command == "shadow-fetch-native":
        from autoresearch.genetic.native_intraday import fetch_native_intraday  # noqa: PLC0415

        result = fetch_native_intraday(
            args.output, tuple(args.symbols), args.start, args.end, timeframe=args.timeframe
        )
        result = {"output": str(args.output.resolve()), "payload_sha256": digest_json(result)}
    elif args.genetic_command == "shadow-compare-native":
        from autoresearch.genetic.native_comparison import audit_native_intraday  # noqa: PLC0415

        result = audit_native_intraday(
            args.native_data,
            args.intraday_data,
            args.intraday_calibration_end,
            as_of=args.as_of,
            output=args.output,
        )
    elif args.genetic_command == "search-parameters":
        from autoresearch.genetic.parameter_study import run_study  # noqa: PLC0415

        result = run_study(
            args.artifact,
            args.snapshot,
            args.native_data,
            output=args.output,
            measured_start=args.measured_start,
            train_end=args.train_end,
            validation_end=args.validation_end,
            global_trials=args.global_trials,
            local_trials=args.local_trials,
            workers=args.workers,
            seed=args.seed,
        )
        result = {
            "output": str(args.output.resolve()),
            "train_unique_evaluated": result.get("train_unique_evaluated"),
            "selected_recipe_sha256": result.get("selected_recipe_sha256"),
            "summary": str((args.output / "search" / "summary.json").resolve()),
        }
    elif args.genetic_command == "walkforward-parameters":
        from autoresearch.genetic.walkforward_study import run_walkforward_study  # noqa: PLC0415

        result = run_walkforward_study(
            args.artifact,
            args.snapshot,
            args.native_data,
            output=args.output,
            folds=json.loads(args.folds.read_text()),
            global_trials=args.global_trials,
            local_trials=args.local_trials,
            workers=args.workers,
            seed=args.seed,
        )
        result = {"output": str(args.output.resolve()), "status": "completed"}
    elif args.genetic_command == "compare-components":
        from autoresearch.genetic.component_study import run_component_study  # noqa: PLC0415

        run_component_study(
            args.artifact,
            args.snapshot,
            args.native_data,
            p11_study=args.p11_study,
            output=args.output,
            workers=args.workers,
            costs=tuple(args.costs),
        )
        result = {"output": str(args.output.resolve()), "status": "completed"}
    elif args.genetic_command == "shadow-register":
        result = register_campaign(
            args.artifact,
            args.snapshot,
            output=args.output,
            intraday_data=args.intraday_data,
            intraday_calibration_end=args.intraday_calibration_end,
        )
    else:
        result = poll_campaign(
            args.campaign,
            through=args.through,
            ledger=args.ledger,
            output=args.output,
            intraday_data=args.intraday_data,
        )
    print(json.dumps(result, indent=2, allow_nan=False))  # noqa: T201
    return 0


def _shadow(args: argparse.Namespace) -> int:
    from autoresearch.genetic.shadow import freeze_from_validation, observe_shadow  # noqa: PLC0415

    if args.genetic_command == "shadow-freeze":
        artifact = freeze_from_validation(
            args.validation_study.resolve(),
            args.snapshot.resolve(),
            allow_registry_rebind=args.allow_registry_rebind,
            fold=args.fold,
            method=args.method,
            rank=args.rank,
            output=args.output.resolve(),
        )
        result = {"artifact_sha256": artifact["payload_sha256"], "diagnostic_only": True}
    else:
        observation = observe_shadow(
            args.artifact.resolve(),
            args.snapshot.resolve(),
            as_of=args.as_of,
            ledger=args.ledger.resolve(),
            output=args.output.resolve(),
        )
        result = {
            "sequence": observation["sequence"],
            "monitor_state": observation["effective_monitor_state"],
        }
    print(  # noqa: T201 - explicit CLI output
        json.dumps(
            {
                **result,
                "execution_authority": "none",
                "promotion_authority": "none",
                "output": str(args.output.resolve()),
            },
            indent=2,
        )
    )
    return 0


def _validate_search(args: argparse.Namespace) -> int:
    from autoresearch.genetic.nested import run_validation  # noqa: PLC0415
    from autoresearch.genetic.replay import load_incumbent  # noqa: PLC0415
    from autoresearch.genetic.symbolic_search import SymbolicSearchProtocol  # noqa: PLC0415
    from autoresearch.genetic.symbolic_study import SymbolicScreenConfig  # noqa: PLC0415
    from autoresearch.genetic.validation import ValidationProtocol  # noqa: PLC0415

    payload = json.loads(args.protocol.read_text())
    if set(payload) != {"start", "validation", "search", "screen"}:
        raise ValueError("validation plan requires exactly start, validation, search and screen")
    validation = ValidationProtocol.from_dict(payload["validation"])
    search = SymbolicSearchProtocol.from_dict(payload["search"])
    screen = SymbolicScreenConfig(**payload["screen"])
    report = run_validation(
        load_incumbent(args.incumbent),
        args.snapshot.resolve(),
        start=dt.date.fromisoformat(payload["start"]),
        validation=validation,
        search=search,
        screen=screen,
        output=args.output.resolve(),
        resume=args.resume,
    )
    print(  # noqa: T201 - explicit CLI output
        json.dumps(
            {
                "status": report["status"],
                "execution_authority": "none",
                "completed_null_replicates": report["completed_null_replicates"],
                "report": str(args.output.resolve() / "report.md"),
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    configure_parser(parser)
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"pattern research: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
