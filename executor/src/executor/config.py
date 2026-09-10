"""Executor settings, resolved from the environment.

Guardrail 10 of the strategy repository applies here too, and more sharply:
this process holds credentials that can *move money in an account*. Nothing in
this module accepts a secret from a file tracked by git, a CLI flag (which
would land in shell history and `ps` output), or a default. Secrets come from
the environment, and their values are never written to a report, a log line, or
a journal entry.

The paper endpoint is a constant, not a setting. Making the host configurable
is how a "paper" deployment quietly becomes a live one after an edit to a
compose file; if this executor is ever meant to reach the live endpoint, that
must be a reviewed code change, not an environment variable.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from executor.errors import ConfigurationError
from executor.plan_source import DEFAULT_PLAN_COMMAND

__all__ = [
    "PAPER_TRADING_HOST",
    "ExecutorSettings",
    "RiskLimits",
    "SmtpSettings",
]

#: The paper trading host. Deliberately not configurable -- see the module
#: docstring. Alpaca's *live* trading host differs from this one only by the
#: leading `paper-`, which is exactly why it is pinned here as a literal and
#: asserted per request rather than assembled from parts or read from an
#: environment variable. `tests/test_boundary.py` fails if the live hostname
#: appears anywhere in this package, including in a comment like this one.
PAPER_TRADING_HOST = "paper-api.alpaca.markets"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        message = (
            f"{name} is not set. Export it in the process environment "
            f"(compose `env_file`, systemd `EnvironmentFile`, or your shell). "
            f"This executor never reads credentials from tracked files."
        )
        raise ConfigurationError(message)
    return value


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _positive_int(name: str, default: int) -> int:
    raw = _optional(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        message = f"{name} must be an integer, got {raw!r}"
        raise ConfigurationError(message) from exc
    if value <= 0:
        message = f"{name} must be positive, got {value}"
        raise ConfigurationError(message)
    return value


def _required_positive_int(name: str) -> int:
    _require(name)  # raises ConfigurationError naming `name` when unset
    return _positive_int(name, 0)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _positive_decimal(name: str) -> Decimal:
    raw = _require(name)
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        message = f"{name} must be a decimal number, got {raw!r}"
        raise ConfigurationError(message) from exc
    if not value.is_finite() or value <= 0:
        message = f"{name} must be positive, got {raw!r}"
        raise ConfigurationError(message)
    return value


def _fraction(name: str, default: str) -> Decimal:
    raw = _optional(name, default)
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        message = f"{name} must be a decimal fraction, got {raw!r}"
        raise ConfigurationError(message) from exc
    if not value.is_finite() or value < 0 or value > 1:
        message = f"{name} must be within [0, 1], got {raw!r}"
        raise ConfigurationError(message)
    return value


def _digest_set(name: str) -> frozenset[str]:
    raw = _require(name)
    digests = {item.strip().lower() for item in raw.split(",") if item.strip()}
    bad = sorted(item for item in digests if not _SHA256.match(item))
    if bad or not digests:
        message = (
            f"{name} must be a comma-separated list of 64-character hex SHA-256 digests "
            f"(`sha256sum configs/strategy/<file>.toml` in the strategy checkout); "
            f"rejected {bad or ['<empty>']}"
        )
        raise ConfigurationError(message)
    return frozenset(digests)


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Limits the executor owns, independent of anything a plan asserts.

    Every field is required from the environment except the two with safe
    defaults. An unset limit is a configuration error, never permission.
    """

    #: Raw-file SHA-256 digests of the strategy configurations this executor
    #: may trade. A plan whose ``config_sha256`` is not listed is refused.
    authorized_config_sha256: frozenset[str]
    #: Global switch. True refuses every submission while leaving reports on.
    disabled: bool
    #: Ceiling on the summed reference notional of all intents in one session.
    max_session_notional: Decimal
    #: Ceiling on the number of intents that may be submitted in one session.
    max_orders_per_session: int
    #: Ceiling on any single intent's reference notional.
    max_symbol_notional: Decimal
    #: Maximum relative difference between the equity the plan was sized on and
    #: the broker's equity at submission time.
    equity_tolerance: Decimal

    @classmethod
    def from_environment(cls) -> RiskLimits:
        return cls(
            authorized_config_sha256=_digest_set("AUTHORIZED_CONFIG_SHA256"),
            disabled=_optional("EXECUTOR_DISABLED", "false").lower() == "true",
            max_session_notional=_positive_decimal("MAX_SESSION_NOTIONAL"),
            max_orders_per_session=_required_positive_int("MAX_ORDERS_PER_SESSION"),
            max_symbol_notional=_positive_decimal("MAX_SYMBOL_NOTIONAL"),
            equity_tolerance=_fraction("EQUITY_TOLERANCE", "0.01"),
        )


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    """Where the daily report is mailed.

    Optional as a whole: if ``SMTP_HOST`` is unset the executor still writes
    the report to disk and simply records that mail was not configured. A
    missing mail server must never stop the trading loop.
    """

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    sender: str
    recipients: tuple[str, ...]
    use_starttls: bool

    @classmethod
    def from_environment(cls) -> SmtpSettings | None:
        """Build settings, or ``None`` when mail is not configured."""
        host = _optional("SMTP_HOST")
        if not host:
            return None

        recipients_raw = _optional("REPORT_RECIPIENTS")
        if not recipients_raw:
            message = "SMTP_HOST is set but REPORT_RECIPIENTS is empty; nowhere to send the report"
            raise ConfigurationError(message)
        recipients = tuple(
            address.strip() for address in recipients_raw.split(",") if address.strip()
        )
        if not recipients:
            message = "REPORT_RECIPIENTS contained no addresses"
            raise ConfigurationError(message)

        sender = _optional("REPORT_SENDER") or _optional("SMTP_USERNAME")
        if not sender:
            message = "set REPORT_SENDER (or SMTP_USERNAME) to the From address"
            raise ConfigurationError(message)

        return cls(
            host=host,
            port=_positive_int("SMTP_PORT", 587),
            username=_optional("SMTP_USERNAME"),
            password=os.environ.get("SMTP_PASSWORD", ""),
            sender=sender,
            recipients=recipients,
            use_starttls=_optional("SMTP_STARTTLS", "true").lower() != "false",
        )

    def __repr__(self) -> str:
        return (
            f"SmtpSettings(host={self.host!r}, port={self.port}, "
            f"username={self.username!r}, password=<redacted>, "
            f"sender={self.sender!r}, recipients={self.recipients!r})"
        )


def _plan_command() -> tuple[str, ...]:
    """How to invoke the strategy CLI, before the ``plan`` subcommand.

    Defaults to ``uv run --locked amat``. ``--locked`` both asserts the
    strategy's lockfile is current and keeps ``uv`` from writing into the
    strategy checkout, which the container mounts read-only. Override with
    ``AMAT_PLAN_COMMAND`` (shell-style, e.g. ``/opt/amat/.venv/bin/amat``) when
    the strategy is installed differently.
    """
    raw = _optional("AMAT_PLAN_COMMAND")
    if not raw:
        return DEFAULT_PLAN_COMMAND
    parts = tuple(shlex.split(raw))
    if not parts:
        message = "AMAT_PLAN_COMMAND is set but parsed to an empty command"
        raise ConfigurationError(message)
    return parts


@dataclass(frozen=True, slots=True)
class ExecutorSettings:
    """Everything the daemon needs, with secrets held separately."""

    #: Alpaca paper trading credentials. Distinct from the market-data keys the
    #: strategy repository uses; a data key cannot place an order and a trading
    #: key should not be handed to a backtest.
    trading_key_id: str = field(repr=False)
    trading_secret_key: str = field(repr=False)

    #: Repository root of the strategy project (the `amat` checkout).
    amat_root: Path
    #: Strategy configuration to run.
    strategy_config: Path
    #: Immutable snapshot the plan extends from.
    snapshot: Path

    #: Executor state root: journal, plans, reports.
    state_root: Path

    #: Trading days of reports to retain on disk.
    report_retention_days: int

    #: Minutes before the exchange open to build the plan.
    plan_lead_minutes: int
    #: Minutes after the exchange close to write the daily report.
    report_delay_minutes: int
    #: The submit phase will not run within this many minutes of the exchange
    #: close. It bounds how late a delayed rebalance may still go in: the
    #: daemon may catch up on a morning it slept through, but never by putting
    #: the day's orders in at the bell, where a market order's fill is least
    #: predictable and there is no time left to notice a rejection.
    submit_cutoff_minutes: int

    #: How the strategy CLI is invoked, before the `plan` subcommand.
    plan_command: tuple[str, ...]

    #: When true, everything runs except the order submission itself.
    dry_run: bool

    smtp: SmtpSettings | None
    #: Risk limits the executor owns, independent of anything a plan asserts.
    limits: RiskLimits
    #: Optional short-lived artifact produced by ``newsdesk discover-assets``.
    news_assets: Path | None = None

    @property
    def journal_path(self) -> Path:
        return self.state_root / "journal.ndjson"

    @property
    def equity_curve_path(self) -> Path:
        """One line per trading day. Never pruned -- it is the whole record of
        performance, and at roughly 200 bytes a day a decade costs 500 KB."""
        return self.state_root / "equity_curve.ndjson"

    @property
    def plans_dir(self) -> Path:
        return self.state_root / "plans"

    @property
    def reports_dir(self) -> Path:
        return self.state_root / "reports"

    @classmethod
    def from_environment(cls) -> ExecutorSettings:
        """Resolve settings, failing closed on anything missing or unusable."""
        amat_root = Path(_optional("AMAT_ROOT", "/opt/amat")).expanduser().resolve()
        if not (amat_root / "pyproject.toml").is_file():
            message = (
                f"AMAT_ROOT={amat_root} does not look like the strategy checkout "
                f"(no pyproject.toml). The executor runs `amat plan` from there."
            )
            raise ConfigurationError(message)

        strategy_config = Path(
            _optional("STRATEGY_CONFIG", str(amat_root / "configs/strategy/amat_v6.toml"))
        ).expanduser()
        if not strategy_config.is_absolute():
            strategy_config = (amat_root / strategy_config).resolve()
        if not strategy_config.is_file():
            message = f"STRATEGY_CONFIG={strategy_config} does not exist"
            raise ConfigurationError(message)

        snapshot = Path(_require("SNAPSHOT")).expanduser()
        if not snapshot.is_absolute():
            snapshot = (amat_root / snapshot).resolve()
        if not snapshot.is_dir():
            message = f"SNAPSHOT={snapshot} is not a directory"
            raise ConfigurationError(message)

        news_assets_raw = _optional("NEWS_ASSETS")
        news_assets = Path(news_assets_raw).expanduser() if news_assets_raw else None
        if news_assets is not None and not news_assets.is_absolute():
            news_assets = (amat_root / news_assets).resolve()
        if news_assets is not None and not news_assets.is_file():
            message = f"NEWS_ASSETS={news_assets} does not exist"
            raise ConfigurationError(message)

        state_root = Path(_optional("STATE_ROOT", "/var/lib/amat-executor")).expanduser()

        return cls(
            trading_key_id=_require("ALPACA_PAPER_API_KEY"),
            trading_secret_key=_require("ALPACA_PAPER_API_SECRET"),
            amat_root=amat_root,
            strategy_config=strategy_config,
            snapshot=snapshot,
            state_root=state_root,
            report_retention_days=_positive_int("REPORT_RETENTION_DAYS", 7),
            plan_lead_minutes=_positive_int("PLAN_LEAD_MINUTES", 10),
            report_delay_minutes=_positive_int("REPORT_DELAY_MINUTES", 15),
            submit_cutoff_minutes=_positive_int("SUBMIT_CUTOFF_MINUTES", 30),
            plan_command=_plan_command(),
            dry_run=_optional("DRY_RUN", "false").lower() == "true",
            smtp=SmtpSettings.from_environment(),
            limits=RiskLimits.from_environment(),
            news_assets=news_assets,
        )

    def ensure_directories(self) -> None:
        for directory in (self.state_root, self.plans_dir, self.reports_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return (
            f"ExecutorSettings(trading_key_id=<redacted>, trading_secret_key=<redacted>, "
            f"amat_root={self.amat_root!r}, strategy_config={self.strategy_config!r}, "
            f"snapshot={self.snapshot!r}, state_root={self.state_root!r}, "
            f"report_retention_days={self.report_retention_days}, "
            f"plan_command={self.plan_command!r}, dry_run={self.dry_run}, "
            f"limits={self.limits!r})"
        )
