"""Settings resolution: what is required, what is optional, what is redacted."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from executor.config import PAPER_TRADING_HOST, ExecutorSettings, SmtpSettings
from executor.errors import ConfigurationError
from executor.plan_source import DEFAULT_PLAN_COMMAND


@pytest.fixture
def amat_root(tmp_path: Path) -> Path:
    """A directory shaped enough like the strategy checkout to be accepted."""
    root = tmp_path / "amat"
    (root / "configs" / "strategy").mkdir(parents=True)
    (root / "data" / "snapshots" / "snap-1").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='amat'\n", encoding="utf-8")
    (root / "configs" / "strategy" / "amat_v6.toml").write_text("name='x'\n", encoding="utf-8")
    return root


def _limits_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHORIZED_CONFIG_SHA256", "a" * 64 + ", " + "b" * 64)
    monkeypatch.setenv("MAX_SESSION_NOTIONAL", "60000")
    monkeypatch.setenv("MAX_ORDERS_PER_SESSION", "12")
    monkeypatch.setenv("MAX_SYMBOL_NOTIONAL", "25000")


@pytest.fixture
def base_env(
    monkeypatch: pytest.MonkeyPatch, amat_root: Path, tmp_path: Path
) -> pytest.MonkeyPatch:
    for name in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "REPORT_SENDER",
        "REPORT_RECIPIENTS",
        "AMAT_PLAN_COMMAND",
        "REPORT_RETENTION_DAYS",
        "DRY_RUN",
        "STRATEGY_CONFIG",
        "NEWS_ASSETS",
        "AUTHORIZED_CONFIG_SHA256",
        "EXECUTOR_DISABLED",
        "MAX_SESSION_NOTIONAL",
        "MAX_ORDERS_PER_SESSION",
        "MAX_SYMBOL_NOTIONAL",
        "EQUITY_TOLERANCE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "key-value")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "secret-value")
    monkeypatch.setenv("AMAT_ROOT", str(amat_root))
    monkeypatch.setenv("SNAPSHOT", str(amat_root / "data" / "snapshots" / "snap-1"))
    monkeypatch.setenv("STATE_ROOT", str(tmp_path / "state"))
    _limits_env(monkeypatch)
    return monkeypatch


def test_resolves_with_defaults(base_env: pytest.MonkeyPatch, amat_root: Path) -> None:
    settings = ExecutorSettings.from_environment()

    assert settings.amat_root == amat_root.resolve()
    assert settings.strategy_config.name == "amat_v6.toml"
    assert settings.report_retention_days == 7
    assert settings.plan_lead_minutes == 10
    assert settings.plan_command == DEFAULT_PLAN_COMMAND
    assert settings.dry_run is False
    assert settings.smtp is None


@pytest.mark.parametrize("missing", ["ALPACA_PAPER_API_KEY", "ALPACA_PAPER_API_SECRET", "SNAPSHOT"])
def test_required_settings_fail_closed(base_env: pytest.MonkeyPatch, missing: str) -> None:
    base_env.delenv(missing)
    with pytest.raises(ConfigurationError, match=missing):
        ExecutorSettings.from_environment()


def test_a_wrong_amat_root_is_caught_at_startup(
    base_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Better than discovering it when the first plan fails at 09:20."""
    base_env.setenv("AMAT_ROOT", str(tmp_path / "not-the-checkout"))
    with pytest.raises(ConfigurationError, match="strategy checkout"):
        ExecutorSettings.from_environment()


def test_a_missing_snapshot_is_caught_at_startup(
    base_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base_env.setenv("SNAPSHOT", str(tmp_path / "no-such-snapshot"))
    with pytest.raises(ConfigurationError, match="not a directory"):
        ExecutorSettings.from_environment()


@pytest.mark.parametrize("value", ["0", "-1", "banana"])
def test_a_nonsense_retention_window_is_rejected(base_env: pytest.MonkeyPatch, value: str) -> None:
    base_env.setenv("REPORT_RETENTION_DAYS", value)
    with pytest.raises(ConfigurationError, match="REPORT_RETENTION_DAYS"):
        ExecutorSettings.from_environment()


def test_dry_run_is_opt_in_and_exact(base_env: pytest.MonkeyPatch) -> None:
    """Anything other than a literal `true` means orders will be sent, so the
    parse must not be clever about it."""
    base_env.setenv("DRY_RUN", "true")
    assert ExecutorSettings.from_environment().dry_run is True

    base_env.setenv("DRY_RUN", "yes")
    assert ExecutorSettings.from_environment().dry_run is False


def test_plan_command_is_overridable(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv("AMAT_PLAN_COMMAND", "/opt/amat/.venv/bin/amat")
    assert ExecutorSettings.from_environment().plan_command == ("/opt/amat/.venv/bin/amat",)

    base_env.setenv("AMAT_PLAN_COMMAND", "uv run --project /opt/amat amat")
    assert ExecutorSettings.from_environment().plan_command == (
        "uv",
        "run",
        "--project",
        "/opt/amat",
        "amat",
    )


def test_news_assets_is_optional_and_validated(
    base_env: pytest.MonkeyPatch, amat_root: Path
) -> None:
    assert ExecutorSettings.from_environment().news_assets is None
    artifact = amat_root / "assets.json"
    artifact.write_text("{}", encoding="utf-8")
    base_env.setenv("NEWS_ASSETS", "assets.json")
    assert ExecutorSettings.from_environment().news_assets == artifact.resolve()

    base_env.setenv("NEWS_ASSETS", "missing.json")
    with pytest.raises(ConfigurationError, match="NEWS_ASSETS"):
        ExecutorSettings.from_environment()


def test_credentials_never_appear_in_a_repr(base_env: pytest.MonkeyPatch) -> None:
    """A settings object lands in tracebacks and log lines."""
    rendered = repr(ExecutorSettings.from_environment())
    assert "key-value" not in rendered
    assert "secret-value" not in rendered
    assert "<redacted>" in rendered


# -- SMTP -------------------------------------------------------------------


def test_mail_is_off_when_no_host_is_set(base_env: pytest.MonkeyPatch) -> None:
    assert SmtpSettings.from_environment() is None


def test_a_host_without_recipients_is_a_configuration_error(
    base_env: pytest.MonkeyPatch,
) -> None:
    base_env.setenv("SMTP_HOST", "smtp.example.com")
    with pytest.raises(ConfigurationError, match="REPORT_RECIPIENTS"):
        SmtpSettings.from_environment()


def test_recipients_are_split_and_trimmed(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv("SMTP_HOST", "smtp.example.com")
    base_env.setenv("SMTP_USERNAME", "bot@example.com")
    base_env.setenv("REPORT_RECIPIENTS", " a@example.com , b@example.com ,")

    smtp = SmtpSettings.from_environment()
    assert smtp is not None
    assert smtp.recipients == ("a@example.com", "b@example.com")
    assert smtp.sender == "bot@example.com"
    assert smtp.port == 587
    assert smtp.use_starttls is True


def test_smtp_password_never_appears_in_a_repr(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv("SMTP_HOST", "smtp.example.com")
    base_env.setenv("SMTP_USERNAME", "bot@example.com")
    base_env.setenv("SMTP_PASSWORD", "hunter2-not-real")
    base_env.setenv("REPORT_RECIPIENTS", "a@example.com")

    rendered = repr(SmtpSettings.from_environment())
    assert "hunter2-not-real" not in rendered
    assert "<redacted>" in rendered


def test_the_broker_host_is_the_paper_host() -> None:
    assert PAPER_TRADING_HOST.startswith("paper-")


# -- Risk limits --------------------------------------------------------------


def test_risk_limits_resolve_from_environment(base_env: pytest.MonkeyPatch) -> None:
    settings = ExecutorSettings.from_environment()
    limits = settings.limits
    assert limits.authorized_config_sha256 == frozenset({"a" * 64, "b" * 64})
    assert limits.disabled is False
    assert limits.max_session_notional == Decimal("60000")
    assert limits.max_orders_per_session == 12
    assert limits.max_symbol_notional == Decimal("25000")
    assert limits.equity_tolerance == Decimal("0.01")


@pytest.mark.parametrize(
    "missing",
    [
        "AUTHORIZED_CONFIG_SHA256",
        "MAX_SESSION_NOTIONAL",
        "MAX_ORDERS_PER_SESSION",
        "MAX_SYMBOL_NOTIONAL",
    ],
)
def test_risk_limits_fail_closed_when_unset(base_env: pytest.MonkeyPatch, missing: str) -> None:
    base_env.delenv(missing)
    with pytest.raises(ConfigurationError, match=missing):
        ExecutorSettings.from_environment()


@pytest.mark.parametrize("bad", ["not-hex", "abc", "A" * 64 + "x"])
def test_a_malformed_digest_is_rejected(base_env: pytest.MonkeyPatch, bad: str) -> None:
    base_env.setenv("AUTHORIZED_CONFIG_SHA256", bad)
    with pytest.raises(ConfigurationError, match="AUTHORIZED_CONFIG_SHA256"):
        ExecutorSettings.from_environment()


def test_disabled_switch_and_tolerance_parse(base_env: pytest.MonkeyPatch) -> None:
    base_env.setenv("EXECUTOR_DISABLED", "TRUE")
    base_env.setenv("EQUITY_TOLERANCE", "0.02")
    limits = ExecutorSettings.from_environment().limits
    assert limits.disabled is True
    assert limits.equity_tolerance == Decimal("0.02")


@pytest.mark.parametrize("value", ["0", "-5", "abc"])
def test_non_positive_notional_is_rejected(base_env: pytest.MonkeyPatch, value: str) -> None:
    base_env.setenv("MAX_SESSION_NOTIONAL", value)
    with pytest.raises(ConfigurationError, match="MAX_SESSION_NOTIONAL"):
        ExecutorSettings.from_environment()
