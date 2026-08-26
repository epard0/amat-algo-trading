"""Persisted run records: every measured result traceable to a manifest.

Promotion gate: "Every report traces to a run manifest and immutable inputs."
Until this module existed, study summaries and falsification batteries lived
only on stdout -- deterministic, but unpersisted, so a registry entry could
cite numbers no artifact carried. A run record closes that gap with two files
under ``outputs/runs/<run_id>/``:

* ``manifest.json`` -- the :class:`~amat.provenance.RunManifest`: strategy,
  configuration digest, code revision, dataset references, seeds, timing;
* ``results.json`` -- the measured sections (study summary, falsification
  battery rows and verdicts, walk-forward folds, Monte Carlo percentiles),
  plus the manifest digest they belong to.

``outputs/runs`` is deliberately *not* one of the immutable evidence roots --
that status is earned by passing the validation protocol, which moves nothing
automatically -- but records are still append-only in practice: the run
directory is created exclusively and an existing one is never rewritten.
Deleting or editing a record by hand is the same offence as editing
``data/raw``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path

from amat.data.snapshot import SnapshotManifest
from amat.errors import ImmutableArtifactError
from amat.paths import is_immutable_path, resolve_within
from amat.provenance import (
    DatasetReference,
    RunManifest,
    current_python_version,
    detect_code_revision,
)
from amat.validation.falsification import FalsificationResult, UniverseFalsification

__all__ = [
    "blend_battery_payload",
    "conditioning_battery_payload",
    "dataset_reference",
    "new_run_manifest",
    "persist_run_record",
    "universe_battery_payload",
]

#: How the panel's price bases are used, recorded on every dataset reference:
#: adjusted closes feed features, raw closes mark the ledger, raw window VWAPs
#: price execution. One string, because the split is a property of the panel
#: contract rather than of any single run.
_ADJUSTMENT_MODE = "adjusted_features/raw_marks/raw_window_vwaps"

#: Everything outside this set is folded to ``-`` in a run identifier, so the
#: id stays a safe path component whatever the strategy was named.
_RUN_ID_SAFE = re.compile(r"[^a-z0-9._-]+")


def _slug(value: str) -> str:
    return _RUN_ID_SAFE.sub("-", value.strip().lower()).strip("-") or "unnamed"


def dataset_reference(manifest: SnapshotManifest) -> DatasetReference:
    """The immutable-dataset reference recorded for one snapshot.

    The snapshot manifest carries one checksum per partition; the run manifest
    schema records one per dataset. The single digest here is the SHA-256 of
    the sorted ``name:checksum`` lines, so any partition change -- or a
    partition appearing or disappearing -- changes the recorded value.
    """
    combined = hashlib.sha256(
        "\n".join(
            f"{name}:{checksum}" for name, checksum in sorted(manifest.partition_sha256.items())
        ).encode("utf-8")
    ).hexdigest()
    return DatasetReference(
        snapshot_id=manifest.snapshot_id,
        provider=manifest.provider,
        feed=manifest.feed,
        adjustment_mode=_ADJUSTMENT_MODE,
        as_of=manifest.as_of,
        partition_sha256=combined,
        first_session=manifest.first_session,
        last_session=manifest.last_session,
        session_count=manifest.session_count,
    )


def new_run_manifest(
    *,
    command: str,
    strategy_name: str,
    strategy_version: str,
    config_sha256: str,
    config_source: str | None,
    datasets: Sequence[DatasetReference],
    execution_model_version: str,
    cost_model_version: str,
    random_seeds: Sequence[int] = (),
    started_at: dt.datetime,
    ended_at: dt.datetime | None = None,
    repository_root: Path | None = None,
    holdout_accessed: bool = False,
    holdout_access_reason: str | None = None,
) -> RunManifest:
    """Assemble the manifest for one finished command invocation.

    The run id embeds the command, the strategy, and the UTC start instant to
    microsecond precision; :func:`persist_run_record`'s exclusive directory
    creation turns any residual collision into a loud failure instead of a
    silent overwrite.
    """
    run_id = (
        f"{_slug(command)}-{_slug(strategy_name)}-{started_at.astimezone(dt.UTC):%Y%m%dT%H%M%S.%f}Z"
    )
    return RunManifest(
        run_id=run_id,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        config_sha256=config_sha256,
        config_source=config_source,
        code_revision=detect_code_revision(repository_root or Path.cwd()),
        python_version=current_python_version(),
        datasets=tuple(datasets),
        execution_model_version=execution_model_version,
        cost_model_version=cost_model_version,
        random_seeds=tuple(random_seeds),
        started_at=started_at,
        ended_at=ended_at,
        holdout_accessed=holdout_accessed,
        holdout_access_reason=holdout_access_reason,
    )


def _jsonify(value: object) -> object:
    """Serialization fallback for the result payload's domain values.

    ``Decimal`` becomes a string rather than a float: the ledger's exact cash
    arithmetic must not be re-rounded by the act of recording it.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    message = f"run-record payload contains an unserializable {type(value).__name__}"
    raise TypeError(message)


def _owning_repository_root(path: Path) -> Path | None:
    """The nearest ancestor of ``path`` that is a repository root, if any.

    ``IMMUTABLE_RELATIVE_ROOTS`` are relative to a *repository* root, so an
    immutability check handed anything else compares apples to nothing and
    silently passes. Outside any repository (a test's tmp directory), there is
    no immutable layout to defend and the caller may skip the check.
    """
    for candidate in (path, *path.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def persist_run_record(
    run_root: Path,
    manifest: RunManifest,
    results: Mapping[str, object],
) -> Path:
    """Write ``manifest.json`` and ``results.json`` under a new run directory.

    Raises:
        ImmutableArtifactError: if ``manifest.run_id`` resolves outside
            ``run_root`` -- the id is derived from configuration-controlled
            names, and a crafted name must not become a path traversal -- or
            if ``run_root`` itself sits under one of the owning repository's
            immutable evidence roots (``--run-root outputs/validated`` must
            refuse, not comply).
        FileExistsError: if the run directory already exists. Records are
            append-only; a collision is a defect to surface, not to absorb.
    """
    root = Path(run_root).expanduser().resolve()
    repository = _owning_repository_root(root)
    if repository is not None and is_immutable_path(repository, root):
        message = f"run records may not be written under an immutable evidence root: {root}"
        raise ImmutableArtifactError(message)
    root.mkdir(parents=True, exist_ok=True)
    target = resolve_within(root, manifest.run_id)
    target.mkdir(parents=False, exist_ok=False)

    (target / "manifest.json").write_text(manifest.to_json() + "\n", encoding="utf-8")
    payload = {
        "run_id": manifest.run_id,
        "manifest_sha256": manifest.digest(),
        "results": dict(results),
    }
    (target / "results.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_jsonify) + "\n",
        encoding="utf-8",
    )
    return target


def universe_battery_payload(battery: UniverseFalsification) -> dict[str, object]:
    """The universe falsification battery as a plain recordable mapping."""
    return {
        "kind": "universe_falsification",
        "verdict": battery.verdict,
        "beats_baseline_on_sharpe": battery.beats_baseline_on_sharpe,
        "dominated_by": list(battery.dominated_by),
        "carried_by": list(battery.carried_by),
        "rows": [
            dataclasses.asdict(row)
            for row in (battery.baseline, battery.candidate, *battery.leave_one_out)
        ],
    }


def blend_battery_payload(battery: FalsificationResult) -> dict[str, object]:
    """The v4 stance-blend battery as a plain recordable mapping."""
    return {
        "kind": "blend_falsification",
        "verdict": battery.verdict,
        "beats_baseline_on_sharpe": battery.beats_baseline_on_sharpe,
        "beats_every_control_on_sharpe": battery.beats_every_control_on_sharpe,
        "rows": [
            dataclasses.asdict(row)
            for row in (battery.baseline, battery.candidate, *battery.controls)
        ],
    }


def conditioning_battery_payload(battery: FalsificationResult) -> dict[str, object]:
    """The scalar-conditioning battery as a plain recordable mapping.

    Identical shape to :func:`blend_battery_payload` apart from its ``kind``:
    the two batteries share :class:`FalsificationResult`, and a record that
    did not say which harness produced it would leave the reader to guess.
    """
    payload = blend_battery_payload(battery)
    payload["kind"] = "conditioning_falsification"
    return payload
