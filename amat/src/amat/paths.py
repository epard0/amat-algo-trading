"""Repository layout and immutable-evidence guards.

The layout below is the single source of truth for where evidence lives. The
guard functions encode guardrail 4 ("immutable evidence") in code so that a
mistaken write path fails in the library rather than only in a Claude Code hook.

Immutable roots
---------------
``data/raw``          checksum-addressed vendor snapshots, append-only
``outputs/validated`` validated run artifacts referenced by reports

Both are *append-only by new versioned subdirectory*. An existing file under an
immutable root is never rewritten in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from amat.errors import ImmutableArtifactError

__all__ = [
    "IMMUTABLE_RELATIVE_ROOTS",
    "RepositoryLayout",
    "ensure_writable",
    "is_immutable_path",
    "reserve_immutable_directory",
    "resolve_within",
]

#: Paths, relative to the repository root, that may never be rewritten in place.
IMMUTABLE_RELATIVE_ROOTS: tuple[str, ...] = (
    "data/raw",
    "data/snapshots",
    "outputs/validated",
    "outputs/holdouts",
)


@dataclass(frozen=True, slots=True)
class RepositoryLayout:
    """Absolute locations of every evidence and configuration directory."""

    root: Path

    @classmethod
    def from_root(cls, root: Path) -> RepositoryLayout:
        """Build a layout anchored at ``root``.

        The root is resolved so that later containment checks compare real
        paths and cannot be defeated by ``..`` segments or symlinks.
        """
        return cls(root=Path(root).expanduser().resolve())

    # -- configuration ------------------------------------------------------
    @property
    def configs(self) -> Path:
        return self.root / "configs"

    @property
    def strategy_configs(self) -> Path:
        return self.configs / "strategy"

    @property
    def data_configs(self) -> Path:
        return self.configs / "data"

    @property
    def experiment_configs(self) -> Path:
        return self.configs / "experiments"

    # -- data ---------------------------------------------------------------
    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def data_raw(self) -> Path:
        """Immutable vendor snapshots, one versioned subdirectory per pull."""
        return self.data / "raw"

    @property
    def data_snapshots(self) -> Path:
        """Immutable ingested snapshots, one versioned subdirectory per pull.

        Serves the role ``data_raw`` describes. It exists as a separate root
        because ``data/raw`` is not writable in every environment this project
        runs in (under the agent sandbox it is masked by a character device, so
        a write there would be silently discarded rather than persisted). Both
        roots carry identical immutability guarantees.
        """
        return self.data / "snapshots"

    @property
    def data_interim(self) -> Path:
        """Reproducible intermediate artifacts; safe to delete and regenerate."""
        return self.data / "interim"

    @property
    def data_normalized(self) -> Path:
        """Typed, schema-validated tables produced from a named raw snapshot."""
        return self.data / "normalized"

    @property
    def data_manifests(self) -> Path:
        """Snapshot manifests: provider, feed, as-of time, coverage, checksums."""
        return self.data / "manifests"

    # -- outputs ------------------------------------------------------------
    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def outputs_runs(self) -> Path:
        """One directory per run id, containing its run manifest."""
        return self.outputs / "runs"

    @property
    def outputs_validated(self) -> Path:
        """Immutable results that passed the validation protocol."""
        return self.outputs / "validated"

    @property
    def outputs_holdouts(self) -> Path:
        """Immutable holdout declarations, one dated subdirectory each.

        Separate from ``outputs_validated`` for the same reason
        ``data_snapshots`` is separate from ``data_raw``: the older root is
        masked by a read-only placeholder in some environments this project
        runs in, and a declaration that silently failed to persist would be
        worse than none. Same immutability guarantees.
        """
        return self.outputs / "holdouts"

    @property
    def outputs_reports(self) -> Path:
        """Rendered reports; each must reference a run manifest."""
        return self.outputs / "reports"

    def all_directories(self) -> tuple[Path, ...]:
        """Every directory the project expects to exist, in creation order."""
        return (
            self.configs,
            self.strategy_configs,
            self.data_configs,
            self.experiment_configs,
            self.data,
            self.data_raw,
            self.data_snapshots,
            self.data_interim,
            self.data_normalized,
            self.data_manifests,
            self.outputs,
            self.outputs_runs,
            self.outputs_validated,
            self.outputs_holdouts,
            self.outputs_reports,
        )


def resolve_within(root: Path, candidate: Path | str) -> Path:
    """Resolve ``candidate`` and require that it stay inside ``root``.

    Rejects absolute escapes, ``..`` traversal, and symlinks that point outside
    the repository.

    Raises:
        ImmutableArtifactError: if the resolved path escapes ``root``.
    """
    resolved_root = Path(root).expanduser().resolve()
    target = Path(candidate).expanduser()
    if not target.is_absolute():
        target = resolved_root / target
    resolved_target = target.resolve()
    if resolved_target != resolved_root and resolved_root not in resolved_target.parents:
        message = f"path escapes the repository root: {candidate!s} -> {resolved_target!s}"
        raise ImmutableArtifactError(message)
    return resolved_target


def is_immutable_path(root: Path, candidate: Path | str) -> bool:
    """Return whether ``candidate`` lies under an immutable evidence root."""
    resolved_root = Path(root).expanduser().resolve()
    resolved_target = resolve_within(resolved_root, candidate)
    relative = resolved_target.relative_to(resolved_root).as_posix()
    return any(
        relative == immutable or relative.startswith(f"{immutable}/")
        for immutable in IMMUTABLE_RELATIVE_ROOTS
    )


def reserve_immutable_directory(root: Path, candidate: Path | str) -> Path:
    """Create a *new* versioned directory under an immutable evidence root.

    This is the one sanctioned way to add evidence. ``ensure_writable`` refuses
    every path under an immutable root, which is correct for rewrites but leaves
    ingestion with no legal target at all; the append-only-by-new-versioned-
    subdirectory rule this module documents needs an operation that can create
    one and only one.

    The reservation is exclusive: ``mkdir`` runs without ``exist_ok``, so two
    concurrent ingestions cannot both believe they own the same snapshot id, and
    re-running an ingestion cannot quietly overwrite the evidence it produced
    last time.

    Raises:
        ImmutableArtifactError: if the target escapes the repository root, does
            not lie under an immutable root, or already exists.
    """
    resolved_root = Path(root).expanduser().resolve()
    target = resolve_within(resolved_root, candidate)
    if not is_immutable_path(resolved_root, target):
        relative = target.relative_to(resolved_root).as_posix()
        message = (
            f"{relative} is not under an immutable evidence root; "
            "use an ordinary write path for mutable output"
        )
        raise ImmutableArtifactError(message)
    if target.exists():
        relative = target.relative_to(resolved_root).as_posix()
        message = (
            f"snapshot directory already exists: {relative}. "
            "Evidence is append-only; choose a new version identifier."
        )
        raise ImmutableArtifactError(message)
    target.mkdir(parents=True)
    return target


def ensure_writable(root: Path, candidate: Path | str) -> Path:
    """Return a resolved write target, or fail closed.

    Raises:
        ImmutableArtifactError: if the target escapes the repository root, lies
            under an immutable evidence root, or already exists there.
    """
    resolved_root = Path(root).expanduser().resolve()
    resolved_target = resolve_within(resolved_root, candidate)
    if is_immutable_path(resolved_root, resolved_target):
        relative = resolved_target.relative_to(resolved_root).as_posix()
        message = (
            f"refusing to write under an immutable evidence root: {relative}. "
            "Create a new versioned snapshot or run directory instead."
        )
        raise ImmutableArtifactError(message)
    return resolved_target
