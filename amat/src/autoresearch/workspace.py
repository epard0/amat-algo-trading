"""Frozen source/data copies outside the executor-mounted checkout."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from amat.data.snapshot import load_manifest, load_panel
from autoresearch.baseline import load_research_config
from autoresearch.contracts import Candidate, Program, validate_candidate
from autoresearch.specs import EvaluationSpec
from autoresearch.validation_contract import StatisticalProtocol


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dependencies() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        **{
            name: importlib.metadata.version(name)
            for name in (
                "numpy",
                "polars",
                "scikit-learn",
                "pydantic",
                "scipy",
                "joblib",
                "threadpoolctl",
                "openai",
                "google-genai",
                "langgraph",
                "langchain-core",
                "langsmith",
                "exchange-calendars",
            )
        },
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


MAX_BRIEF_BYTES = 3000


def initialize(  # noqa: PLR0912, PLR0915 - freeze and verify one complete research contract
    root: Path, source: Path, config: Path, snapshot: Path, **options: Any
) -> Program:
    root, source, snapshot = root.resolve(), source.resolve(), snapshot.resolve()
    if root.is_relative_to(source.parent) or root == source.parent or source.is_relative_to(root):
        raise ValueError("research workspace must be outside the executor-mounted project checkout")
    if root.exists():
        raise ValueError("workspace already exists; resume it instead of overwriting")
    reference = Candidate(config=load_research_config(config))
    promotion = options.pop("promotion", None)
    engineering_policy = options.pop("engineering_policy", None)
    independent_calendar = options.pop("independent_calendar", False)
    baseline = Candidate.model_validate(promotion["candidate"]) if promotion else reference
    validate_candidate(reference, baseline)
    snapshot_manifest = load_manifest(snapshot)
    for name, expected in snapshot_manifest.partition_sha256.items():
        partition = snapshot / name
        if not partition.resolve().is_relative_to(snapshot) or digest_file(partition) != expected:
            raise ValueError("snapshot partition does not match its manifest")
    panel, _ = load_panel(
        snapshot,
        [str(s) for s in baseline.config.strategy.universe.symbols],
        end=options["train_end"],
    )
    warmup = baseline.config.strategy.warmup.min_completed_sessions
    evaluation = EvaluationSpec.model_validate(options.pop("evaluation", {}))
    anchor_index = warmup - 1 + evaluation.initial_training_sessions
    if len(panel) <= anchor_index + 252:
        raise ValueError("training window needs at least one evaluation year after initial fitting")
    if panel.sessions[-1] != options["train_end"]:
        raise ValueError("train-end must be an available exchange session in the snapshot")
    validation_panel, _ = load_panel(
        snapshot,
        [str(s) for s in baseline.config.strategy.universe.symbols],
        start=options["validation_end"],
        end=options["validation_end"],
    )
    if validation_panel.sessions[-1] != options["validation_end"]:
        raise ValueError("validation-end must be an available exchange session in the snapshot")
    calendar_evidence = None
    if independent_calendar:
        from autoresearch.calendar_audit import audit_sessions  # noqa: PLC0415

        full_panel, _ = load_panel(
            snapshot,
            [str(s) for s in baseline.config.strategy.universe.symbols],
            end=options["validation_end"],
        )
        calendar_evidence = audit_sessions(
            list(full_panel.sessions), start=panel.sessions[0], end=options["validation_end"]
        )
    brief = options.pop("brief", "")
    if not isinstance(brief, str) or len(brief.encode()) > MAX_BRIEF_BYTES:
        raise ValueError("research brief must be text within 3000 UTF-8 bytes")
    root.mkdir(parents=True)
    try:
        for package in ("amat", "autoresearch", "llmrouter", "portfolio_team"):
            shutil.copytree(
                source / "src" / package,
                root / "runtime" / package,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        if baseline.config.strategy.version.startswith("13."):
            reference_path = source / "configs/autoresearch/baseline-reference.json"
            reference_record = json.loads(reference_path.read_text())
            if reference_record["config_sha256"] != digest_file(config):
                raise ValueError("v13 baseline reference changed")
            study = source / reference_record["study"]
            if not study.resolve().is_relative_to(source):
                raise ValueError("v13 reference escapes source")
            for relative, expected in reference_record["artifacts"].items():
                artifact = study / relative
                if (
                    not artifact.resolve().is_relative_to(study)
                    or digest_file(artifact) != expected
                ):
                    raise ValueError("v13 reference evidence changed")
                destination = root / "runtime/baseline-reference" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(artifact, destination)
            write_json(root / "runtime/baseline-reference.json", reference_record)
        if promotion:
            write_json(root / "runtime/research-promotion.json", promotion)
        if calendar_evidence is not None:
            write_json(root / "runtime/independent-calendar.json", calendar_evidence)
        if engineering_policy is not None:
            from autoresearch.engineering import EngineeringPolicy  # noqa: PLC0415 - cycle

            policy = EngineeringPolicy.model_validate(engineering_policy)
            write_json(root / "runtime/engineering-policy.json", policy.model_dump(mode="json"))
        if brief:
            (root / "runtime/research-brief.txt").write_text(brief)
        shutil.copytree(snapshot, root / "snapshot")
        shutil.copy2(config, root / "base.toml")
        if load_research_config(root / "base.toml") != reference.config:
            raise ValueError("baseline configuration changed during initialization")
        shutil.copy2(source / "uv.lock", root / "uv.lock")
        # Freeze a read-only snapshot of compatible earlier development evidence.
        from autoresearch.memory import MEMORY_PATH, freeze_memory  # noqa: PLC0415 - cycle

        provisional = Program(
            baseline=baseline,
            load_start=panel.sessions[0],
            measured_start=panel.sessions[anchor_index],
            evaluation=evaluation,
            frozen_files={},
            dependencies=dependencies(),
            **options,
        )
        write_json(root / MEMORY_PATH, freeze_memory(root.parent, root, provisional))
        StatisticalProtocol(max_trials=options.get("max_experiments", 50)).freeze(
            root / "runtime/statistical-protocol.json"
        )
        write_json(
            root / "runtime/development-calendar.json",
            {
                "sessions": [str(day) for day in panel.sessions[anchor_index:]],
                "source": "hash-verified snapshot; external calendar audit separate",
                "training_end": str(options["train_end"]),
                "later_history": "previously_observed_not_pristine",
            },
        )
        files = [root / "base.toml", root / "uv.lock"]
        files.extend(
            p
            for directory in (root / "runtime", root / "snapshot")
            for p in directory.rglob("*")
            if p.is_file()
        )
        program = Program(
            baseline=baseline,
            load_start=panel.sessions[0],
            measured_start=panel.sessions[anchor_index],
            evaluation=evaluation,
            frozen_files={str(p.relative_to(root)): digest_file(p) for p in sorted(files)},
            dependencies=dependencies(),
            **options,
        )
        write_json(root / "program.json", program.model_dump(mode="json"))
        (root / "program.sha256").write_text(digest_file(root / "program.json"))
        (root / "pyproject.toml").write_text(
            '[project]\nname="amat-research-artifacts"\nversion="0.0.0"\n'
        )
        launcher = root / "research.sh"
        launcher.write_text(
            "#!/bin/sh\nset -eu\n"
            f"export PYTHONPATH={shlex.quote(str(root / 'runtime'))}\n"
            "export PYTHONDONTWRITEBYTECODE=1\n"
            'command=${1:-status}\nif [ "$#" -gt 0 ]; then shift; fi\n'
            f'if [ "$command" = features ]; then exec {shlex.quote(sys.executable)} '
            '-m autoresearch.cli features "$@"; fi\n'
            f"exec {shlex.quote(sys.executable)} -m autoresearch.cli "
            f'"$command" {shlex.quote(str(root))} "$@"\n'
        )
        launcher.chmod(0o700)
        return program
    except Exception:
        shutil.rmtree(root)
        raise


def load_program(root: Path) -> Program:
    if digest_file(root / "program.json") != (root / "program.sha256").read_text():
        raise ValueError("program changed: initialize a new program instead")
    program = Program.model_validate_json((root / "program.json").read_text())
    for relative, expected in program.frozen_files.items():
        path = root / relative
        if (
            not path.resolve().is_relative_to(root.resolve())
            or path.is_symlink()
            or digest_file(path) != expected
        ):
            raise ValueError(f"frozen input changed: {relative}")
    actual = {
        str(p.relative_to(root))
        for directory in (root / "runtime", root / "snapshot")
        for p in directory.rglob("*")
        if p.is_file()
    }
    if actual != set(program.frozen_files) - {"base.toml", "uv.lock"}:
        raise ValueError("frozen source/data file set changed")
    # The coordinator's selection code must be the recorded code too. A new
    # installed release cannot silently change how an old program keeps trials.
    installed = Path(__file__).resolve().parent.parent
    for package in ("amat", "autoresearch", "llmrouter", "portfolio_team"):
        for path in (installed / package).rglob("*.py"):
            relative = f"runtime/{path.relative_to(installed)}"
            if digest_file(path) != program.frozen_files.get(relative):
                raise ValueError("controller changed; resume using this workspace's frozen runtime")
    if dependencies() != program.dependencies:
        raise ValueError("runtime dependencies changed; restore the recorded environment")
    return program
