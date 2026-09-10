"""Run manifests: the reproducibility record for every result.

A report that cannot name its manifest is not evidence. The fields below are the
list in ``.claude/references/architecture.md``; each one is required, and each
absence is recorded explicitly rather than defaulted away.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import subprocess
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "CodeRevision",
    "DatasetReference",
    "RunManifest",
    "current_python_version",
    "detect_code_revision",
]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class CodeRevision(BaseModel):
    """The code state a run executed against.

    ``detected`` is false when the revision could not be determined. In that
    case ``reason`` explains why and ``commit`` stays ``None``. The run is still
    recordable, but a run without a detected clean revision must not be promoted.
    """

    model_config = _MODEL_CONFIG

    detected: bool
    commit: str | None = None
    dirty: bool | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _require_consistent_detection(self) -> Self:
        if self.detected:
            if not self.commit:
                message = "a detected revision must carry a commit identifier"
                raise ValueError(message)
            if self.dirty is None:
                message = "a detected revision must record whether the tree was dirty"
                raise ValueError(message)
        elif not self.reason:
            message = "an undetected revision must explain why detection failed"
            raise ValueError(message)
        return self

    @property
    def is_promotable(self) -> bool:
        """Whether this revision satisfies the reproducibility promotion gate."""
        return self.detected and self.dirty is False


class DatasetReference(BaseModel):
    """One immutable dataset a run consumed."""

    model_config = _MODEL_CONFIG

    snapshot_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    feed: str = Field(min_length=1)
    adjustment_mode: str = Field(min_length=1)
    as_of: dt.datetime
    partition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    first_session: dt.date
    last_session: dt.date
    session_count: int = Field(gt=0)

    @model_validator(mode="after")
    def _require_utc_and_ordered_coverage(self) -> Self:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() != dt.timedelta(0):
            message = "as_of must be a timezone-aware UTC datetime"
            raise ValueError(message)
        if self.last_session < self.first_session:
            message = "last_session must not precede first_session"
            raise ValueError(message)
        return self


class RunManifest(BaseModel):
    """Everything needed to reproduce and audit one run.

    ``validation_status`` starts at ``"pending"``. A run is ``"passed"`` only
    when ``failed_gates`` is empty, which prevents a report from claiming
    success while listing failures.
    """

    model_config = _MODEL_CONFIG

    run_id: str = Field(min_length=1)
    strategy_name: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_source: str | None = None
    code_revision: CodeRevision
    dependency_lock_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    python_version: str = Field(min_length=1)
    datasets: tuple[DatasetReference, ...] = ()
    execution_model_version: str = Field(min_length=1)
    cost_model_version: str = Field(min_length=1)
    random_seeds: tuple[int, ...] = ()
    started_at: dt.datetime
    ended_at: dt.datetime | None = None
    validation_status: Literal["pending", "passed", "failed"] = "pending"
    failed_gates: tuple[str, ...] = ()
    holdout_accessed: bool = False
    holdout_access_reason: str | None = None

    @model_validator(mode="after")
    def _require_utc_timestamps(self) -> Self:
        for name in ("started_at", "ended_at"):
            value = getattr(self, name)
            if value is None:
                continue
            if value.tzinfo is None or value.utcoffset() != dt.timedelta(0):
                message = f"{name} must be a timezone-aware UTC datetime"
                raise ValueError(message)
        if self.ended_at is not None and self.ended_at < self.started_at:
            message = "ended_at must not precede started_at"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _require_consistent_status(self) -> Self:
        if self.validation_status == "passed" and self.failed_gates:
            message = (
                "validation_status cannot be 'passed' while failed_gates is non-empty: "
                f"{', '.join(self.failed_gates)}"
            )
            raise ValueError(message)
        if self.validation_status == "failed" and not self.failed_gates:
            message = "a failed run must name at least one failed gate"
            raise ValueError(message)
        if self.holdout_accessed and not self.holdout_access_reason:
            message = "holdout access must record when and why the holdout was read"
            raise ValueError(message)
        return self

    def to_json(self) -> str:
        """Serialize deterministically: sorted keys, no incidental whitespace."""
        payload = self.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        """SHA-256 of the deterministic serialization."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


def detect_code_revision(root: Path | str) -> CodeRevision:
    """Determine the git revision of ``root`` without failing the caller.

    Absence of git, or absence of a repository, is recorded as an undetected
    revision with a reason. It is never silently reported as clean.
    """
    workdir = Path(root).expanduser().resolve()
    try:
        # Fixed argument vector, no shell, no interpolated user input.
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CodeRevision(detected=False, reason=f"git invocation failed: {exc}")

    if head.returncode != 0:
        detail = head.stderr.strip() or "git rev-parse HEAD failed"
        return CodeRevision(detected=False, reason=detail)

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S607
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CodeRevision(detected=False, reason=f"git invocation failed: {exc}")
    if status.returncode != 0:
        detail = status.stderr.strip() or "git status --porcelain failed"
        return CodeRevision(detected=False, reason=detail)

    return CodeRevision(
        detected=True,
        commit=head.stdout.strip(),
        dirty=bool(status.stdout.strip()),
    )


def current_python_version() -> str:
    """Interpreter identifier recorded in the manifest."""
    return f"{platform.python_implementation()} {platform.python_version()}"
