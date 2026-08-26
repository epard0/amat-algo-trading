"""Obtain today's execution plan from the strategy repository.

The strategy is invoked as a **subprocess**, not imported. That is deliberate:

* it keeps the dependency arrow one-way and enforceable -- this package has no
  import of ``amat`` anywhere, so the executor cannot reach around the plan
  artifact and call a signal function directly with different arguments;
* the plan JSON is the whole contract, so the executor is testable against a
  fixture file with no strategy checkout present;
* ``amat`` and ``executor`` can hold different, even conflicting, dependency
  pins without either constraining the other.

Account equity and positions flow *in* as arguments, matching the strategy
repository's rule that a plan is built from supplied state and never fetches an
account. The executor is what fetches; the strategy is what decides.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from executor.errors import PlanError

__all__ = [
    "SUPPORTED_PLAN_MAJOR",
    "ExecutionPlan",
    "PlanIntent",
    "build_plan",
    "parse_plan",
]

#: Plan schema major version this executor was written against. A plan carrying
#: a different major is refused rather than best-effort parsed: guardrail 9 of
#: the strategy repository, applied from the other side of the boundary. A
#: renamed field that silently reads as ``None`` becomes a wrong-sized position.
SUPPORTED_PLAN_MAJOR = 1

#: How long `amat plan` may take. It extends the snapshot from the market-data
#: API, which on a long weekend can be several pages per symbol.
_PLAN_TIMEOUT_SECONDS = 900.0

#: How the strategy CLI is invoked, before the `plan` subcommand and its
#: arguments. ``--locked`` asserts the strategy's lockfile is current instead of
#: silently resolving a different dependency set, and it means `uv` does not
#: need to write into the strategy checkout -- which matters because the
#: container mounts that checkout read-only. Override with
#: ``AMAT_PLAN_COMMAND`` when the strategy is already installed on PATH.
DEFAULT_PLAN_COMMAND = ("uv", "run", "--locked", "amat")


@dataclass(frozen=True, slots=True)
class PlanIntent:
    """One instruction, as the strategy expressed it."""

    symbol: str
    side: str
    quantity: Decimal
    reference_price: Decimal
    notional: Decimal
    current_weight: float
    target_weight: float
    reason: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PlanIntent:
        try:
            return cls(
                symbol=str(payload["symbol"]).upper(),
                side=str(payload["side"]),
                quantity=Decimal(str(payload["quantity"])),
                reference_price=Decimal(str(payload["reference_price"])),
                notional=Decimal(str(payload["notional"])),
                current_weight=float(payload["current_weight"]),
                target_weight=float(payload["target_weight"]),
                reason=str(payload.get("reason", "")),
            )
        except (KeyError, ArithmeticError, TypeError, ValueError) as exc:
            message = f"malformed intent in plan: {payload!r}"
            raise PlanError(message) from exc


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """The subset of the plan artifact this executor acts on, plus provenance.

    Unparsed fields are kept in :attr:`raw` so the daily report and the journal
    can quote the artifact exactly as the strategy emitted it.
    """

    schema_version: str
    generated_at: str
    engine: str
    strategy_name: str
    strategy_version: str
    config_sha256: str
    snapshot_id: str
    appended_sessions: int
    code_revision: str

    as_of_session: dt.date
    earliest_execution_session: dt.date
    expires_at: dt.datetime
    exchange_timezone: str
    window_start_local: dt.time
    window_end_local: dt.time

    account_equity: Decimal
    gross_target_exposure: float
    target_weights: dict[str, float]
    current_positions: dict[str, Decimal]
    intents: tuple[PlanIntent, ...]
    withheld: tuple[dict[str, Any], ...]
    unrecognized_holdings: tuple[str, ...]
    caveats: tuple[str, ...]
    raw: dict[str, Any]

    def is_expired(self, *, now: dt.datetime) -> bool:
        """Whether the plan's staleness backstop has passed."""
        if now.tzinfo is None:
            message = "plan expiry must be evaluated against an aware instant"
            raise PlanError(message)
        return now > self.expires_at

    def is_executable_on(self, session: dt.date) -> bool:
        """Whether ``session`` is on or after the earliest permitted session.

        The strategy names the earliest date it may execute; resolving that to a
        real exchange session is the executor's job, which is why this is a
        comparison and not an equality.
        """
        return session >= self.earliest_execution_session


def parse_plan(payload: dict[str, Any]) -> ExecutionPlan:
    """Parse a plan artifact, refusing an unrecognised schema major version."""
    schema_version = str(payload.get("schema_version", ""))
    major = schema_version.split(".", 1)[0]
    if not major.isdigit() or int(major) != SUPPORTED_PLAN_MAJOR:
        message = (
            f"plan schema version {schema_version!r} is not supported; this "
            f"executor was written against major {SUPPORTED_PLAN_MAJOR}. "
            f"Refusing to interpret an unknown plan shape."
        )
        raise PlanError(message)

    try:
        return ExecutionPlan(
            schema_version=schema_version,
            generated_at=str(payload["generated_at"]),
            engine=str(payload["engine"]),
            strategy_name=str(payload["strategy_name"]),
            strategy_version=str(payload["strategy_version"]),
            config_sha256=str(payload["config_sha256"]),
            snapshot_id=str(payload["snapshot_id"]),
            appended_sessions=int(payload.get("appended_sessions", 0)),
            code_revision=str(payload.get("code_revision", "")),
            as_of_session=dt.date.fromisoformat(str(payload["as_of_session"])),
            earliest_execution_session=dt.date.fromisoformat(
                str(payload["earliest_execution_session"])
            ),
            expires_at=_aware(str(payload["expires_at"])),
            exchange_timezone=str(payload["exchange_timezone"]),
            window_start_local=dt.time.fromisoformat(str(payload["window_start_local"])),
            window_end_local=dt.time.fromisoformat(str(payload["window_end_local"])),
            account_equity=Decimal(str(payload["account_equity"])),
            gross_target_exposure=float(payload.get("gross_target_exposure", 0.0)),
            target_weights={
                str(key).upper(): float(value)
                for key, value in dict(payload.get("target_weights", {})).items()
            },
            current_positions={
                str(key).upper(): Decimal(str(value))
                for key, value in dict(payload.get("current_positions", {})).items()
            },
            intents=tuple(PlanIntent.from_payload(item) for item in payload.get("intents", [])),
            withheld=tuple(dict(item) for item in payload.get("withheld", [])),
            unrecognized_holdings=tuple(
                str(item).upper() for item in payload.get("unrecognized_holdings", [])
            ),
            caveats=tuple(str(item) for item in payload.get("caveats", [])),
            raw=payload,
        )
    except (KeyError, ArithmeticError, TypeError, ValueError) as exc:
        message = f"could not parse the plan artifact: {exc}"
        raise PlanError(message) from exc


def _aware(text: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        message = f"plan carried a naive timestamp {text!r}"
        raise PlanError(message)
    return parsed


def build_plan(
    *,
    amat_root: Path,
    strategy_config: Path,
    snapshot: Path,
    equity: Decimal,
    positions: dict[str, Decimal],
    offline: bool = False,
    news_assets: Path | None = None,
    out_path: Path | None = None,
    plan_command: tuple[str, ...] = DEFAULT_PLAN_COMMAND,
) -> ExecutionPlan:
    """Run ``amat plan`` and return the parsed artifact.

    Args:
        amat_root: Strategy checkout; the subprocess runs with this as its cwd
            so ``amat``'s own code-revision detection sees the right repository.
        strategy_config: TOML configuration (v1-v6).
        snapshot: Immutable snapshot directory to extend from.
        equity: Account equity, fetched from the broker by the caller.
        positions: Share counts held, fetched from the broker by the caller.
        offline: Use the snapshot as-is instead of appending live sessions.
            Diagnostic only -- a real trading day must not run offline, because
            the plan would be formed from stale closes.
        news_assets: Optional short-lived artifact from ``newsdesk
            discover-assets``. Its path is passed to the isolated strategy
            subprocess; the executor never interprets headline content.
        out_path: Where to also archive the raw artifact.
        plan_command: How to invoke the strategy CLI, before ``plan``.

    Raises:
        PlanError: if the strategy exits non-zero, times out, or emits
            something this executor will not interpret.
    """
    positions_payload = {symbol: str(quantity) for symbol, quantity in sorted(positions.items())}

    with tempfile.TemporaryDirectory(prefix="amat-executor-") as scratch:
        positions_file = Path(scratch) / "positions.json"
        positions_file.write_text(json.dumps(positions_payload), encoding="utf-8")

        command = [
            *plan_command,
            "plan",
            str(strategy_config),
            "--snapshot",
            str(snapshot),
            "--equity",
            format(equity, "f"),
            "--positions",
            str(positions_file),
            "--json",
        ]
        if offline:
            command.append("--offline")
        if news_assets is not None:
            command.extend(("--news-assets", str(news_assets)))

        try:
            completed = subprocess.run(  # noqa: S603 - argv list, no shell
                command,
                cwd=amat_root,
                capture_output=True,
                text=True,
                timeout=_PLAN_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            message = (
                f"{plan_command[0]!r} is not on PATH; cannot run the strategy in "
                f"{amat_root}. Set AMAT_PLAN_COMMAND if the strategy CLI is "
                f"invoked differently in this deployment."
            )
            raise PlanError(message) from exc
        except subprocess.TimeoutExpired as exc:
            message = f"`amat plan` did not finish within {_PLAN_TIMEOUT_SECONDS:.0f}s"
            raise PlanError(message) from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        message = f"`amat plan` exited {completed.returncode}: {detail}"
        raise PlanError(message)

    payload = _extract_json_object(completed.stdout)
    plan = parse_plan(payload)

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return plan


def _extract_json_object(stdout: str) -> dict[str, Any]:
    """Read the plan object out of the subprocess's stdout.

    ``uv run`` may print environment-sync progress before the command's own
    output, so the first ``{`` is located rather than assuming stdout is pure
    JSON. Anything that still fails to parse is an error, never a partial read.
    """
    start = stdout.find("{")
    if start < 0:
        message = f"`amat plan` produced no JSON object. Output was: {stdout.strip()[:2000]}"
        raise PlanError(message)
    try:
        payload = json.loads(stdout[start:])
    except json.JSONDecodeError as exc:
        message = f"`amat plan` produced unparseable JSON: {exc}"
        raise PlanError(message) from exc
    if not isinstance(payload, dict):
        message = "`amat plan` produced JSON that is not an object"
        raise PlanError(message)
    return payload
