# Autoresearch Phase 0: Prerequisites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the workspace safe for an autonomous research writer and make LLM calls survive any single provider's outage, before any autoresearch loop is built.

**Architecture:** Three independent tracks. (1) The executor gains a config-digest allowlist, a global disable switch and notional/order caps checked in preflight, plus retry-within-window and honest small-sample reporting. (2) A new sibling package `llmrouter` provides an OpenAI-compatible transport (OpenAI, OpenRouter, NVIDIA NIM), a Gemini transport, per-provider health tracking and an ordered fallback chain; `portfolio_team` routes through it and stops leaking token budget on failures. (3) AMAT records an explicit warning when a learned-regime layer is inert during warm-up.

**Tech Stack:** Python 3.12, uv, pydantic 2, pytest, mypy strict, ruff; `openai>=2,<3` SDK (Responses and Chat Completions), `google-genai>=2.17,<3`; SQLite (stdlib).

**Spec:** `docs/research/autoresearch-audit-and-plan-2026-09-10.md` (sections 2.1, 2.2, 2.3, 4.7, Phase 0).

## Global Constraints

- Executor stays paper-only; `PAPER_TRADING_HOST` literal unchanged; `executor/tests/test_boundary.py` must keep passing.
- `portfolio_team` and `llmrouter` must never import `executor`, `subprocess`, `requests`, `httpx`, or `alpaca` (existing test `tests/unit/test_portfolio_team.py::test_package_has_no_broker_or_process_execution_imports`; add the same for `llmrouter`).
- No credentials in source, tests, fixtures, logs, or stored artifacts. Keys are read from the environment at call time only: `OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`.
- No SDK auto-retries (`max_retries=0` / `HttpRetryOptions(attempts=0)`); failover is the router's job.
- Every quality gate must pass before a task is done: `uv run --locked pytest -q`, `uv run --locked mypy src`, `uv run --locked ruff check src tests`, `uv run --locked ruff format --check src tests` in the affected project (`amat/` or `executor/`).
- Git commits require operator approval (workspace rule). Each task ends with a proposed commit; the executing agent stages the files and asks once per phase unless the operator has already approved commits for the phase.
- Defaults are restrictive: an unset limit refuses, it does not permit.
- Line length 100, `from __future__ import annotations`, frozen dataclasses in the executor, pydantic `Record` (extra forbidden) in `portfolio_team`/`llmrouter`.

## File Structure

Executor (`executor/src/executor/`):
- Modify `config.py`: add `RiskLimits` frozen dataclass and env parsing; `ExecutorSettings.limits`.
- Modify `submit.py`: `_preflight` takes `limits` and `live_positions`; new refusal reasons; `submit_plan(limits=...)`.
- Modify `daemon.py`: pass limits; startup digest check; retry-within-window with attempt ledger.
- Modify `report/metrics.py`, `report/daily.py`: gate annualized/tail stats on sample size.
- Tests: `tests/test_config.py`, `tests/test_submit.py`, `tests/test_daemon.py`, `tests/test_report.py`.
- Docs: `docs/executor/README.md` (new env vars), `executor/.env.example` if present.

AMAT (`amat/src/`):
- Create `llmrouter/__init__.py`, `llmrouter/contracts.py` (request/response/outcome types, error classification), `llmrouter/health.py` (sliding-window provider health), `llmrouter/transports.py` (OpenAI-compatible + Gemini transports), `llmrouter/chain.py` (chain config, TOML loader, `Router`), `llmrouter/py.typed`.
- Create `configs/governance/llm-chain.toml` (default chain).
- Modify `portfolio_team/store.py` (budget settle rules, provider/model columns, breakdown query, release), `portfolio_team/model_runtime.py` (`RoutedTransport`, response provider/model capture), `portfolio_team/openai_runtime.py` (chief chain), `portfolio_team/cli.py` (`--chain`, `budget` subcommand), `portfolio_team/evidence.py` + `automation.py` (traceback capture).
- Modify `amat/backtest/runner.py` (learned-regime warm-up warning), `amat/cli/main.py` (`--start` help text, validate-config warning).
- Modify `pyproject.toml` (add `src/llmrouter` to wheel packages).
- Tests: `tests/unit/test_llmrouter_health.py`, `tests/unit/test_llmrouter_transports.py`, `tests/unit/test_llmrouter_chain.py`, `tests/unit/test_portfolio_team_budget.py`, existing `test_portfolio_team*.py`, `tests/unit/test_learned_regime_warmup.py`.

---

### Task 1: Executor risk limits in settings

**Files:**
- Modify: `executor/src/executor/config.py` (after `_positive_int`, and `ExecutorSettings`)
- Test: `executor/tests/test_config.py`

**Interfaces:**
- Produces: `RiskLimits` frozen dataclass with fields `authorized_config_sha256: frozenset[str]`, `disabled: bool`, `max_session_notional: Decimal`, `max_orders_per_session: int`, `max_symbol_notional: Decimal`, `equity_tolerance: Decimal`; classmethod `RiskLimits.from_environment()`; `ExecutorSettings.limits: RiskLimits`.
- Env vars: `AUTHORIZED_CONFIG_SHA256` (comma-separated 64-hex digests, required), `EXECUTOR_DISABLED` (`true`/`false`, default `false`), `MAX_SESSION_NOTIONAL` (Decimal USD, required), `MAX_ORDERS_PER_SESSION` (positive int, required), `MAX_SYMBOL_NOTIONAL` (Decimal USD, required), `EQUITY_TOLERANCE` (fraction, default `0.01`).

- [ ] **Step 1: Write the failing tests**

Append to `executor/tests/test_config.py`:

```python
def _limits_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHORIZED_CONFIG_SHA256", "a" * 64 + ", " + "b" * 64)
    monkeypatch.setenv("MAX_SESSION_NOTIONAL", "60000")
    monkeypatch.setenv("MAX_ORDERS_PER_SESSION", "12")
    monkeypatch.setenv("MAX_SYMBOL_NOTIONAL", "25000")


def test_risk_limits_resolve_from_environment(base_env: pytest.MonkeyPatch) -> None:
    _limits_env(base_env)
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
    ["AUTHORIZED_CONFIG_SHA256", "MAX_SESSION_NOTIONAL", "MAX_ORDERS_PER_SESSION", "MAX_SYMBOL_NOTIONAL"],
)
def test_risk_limits_fail_closed_when_unset(base_env: pytest.MonkeyPatch, missing: str) -> None:
    _limits_env(base_env)
    base_env.delenv(missing)
    with pytest.raises(ConfigurationError, match=missing):
        ExecutorSettings.from_environment()


@pytest.mark.parametrize("bad", ["not-hex", "abc", "A" * 64 + "x"])
def test_a_malformed_digest_is_rejected(base_env: pytest.MonkeyPatch, bad: str) -> None:
    _limits_env(base_env)
    base_env.setenv("AUTHORIZED_CONFIG_SHA256", bad)
    with pytest.raises(ConfigurationError, match="AUTHORIZED_CONFIG_SHA256"):
        ExecutorSettings.from_environment()


def test_disabled_switch_and_tolerance_parse(base_env: pytest.MonkeyPatch) -> None:
    _limits_env(base_env)
    base_env.setenv("EXECUTOR_DISABLED", "TRUE")
    base_env.setenv("EQUITY_TOLERANCE", "0.02")
    limits = ExecutorSettings.from_environment().limits
    assert limits.disabled is True
    assert limits.equity_tolerance == Decimal("0.02")


@pytest.mark.parametrize("value", ["0", "-5", "abc"])
def test_non_positive_notional_is_rejected(base_env: pytest.MonkeyPatch, value: str) -> None:
    _limits_env(base_env)
    base_env.setenv("MAX_SESSION_NOTIONAL", value)
    with pytest.raises(ConfigurationError, match="MAX_SESSION_NOTIONAL"):
        ExecutorSettings.from_environment()
```

Add `from decimal import Decimal` to the test imports. The existing `base_env` fixture must also `delenv` the six new names in its cleanup list, and every existing `from_environment` test needs the limits present: add `_limits_env(base_env)` at the top of the `base_env` fixture body after the existing `setenv` calls (so defaults tests keep passing), and have the fail-closed test delete them explicitly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd executor && uv run --locked pytest tests/test_config.py -q`
Expected: FAIL with `AttributeError: 'ExecutorSettings' object has no attribute 'limits'` and `ConfigurationError` not raised.

- [ ] **Step 3: Implement `RiskLimits`**

In `executor/src/executor/config.py`, add `import re` and `from decimal import Decimal, InvalidOperation` at the top, then after `_positive_int`:

```python
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
            max_orders_per_session=_positive_int("MAX_ORDERS_PER_SESSION", 0)
            if _optional("MAX_ORDERS_PER_SESSION")
            else _missing("MAX_ORDERS_PER_SESSION"),
            max_symbol_notional=_positive_decimal("MAX_SYMBOL_NOTIONAL"),
            equity_tolerance=_fraction("EQUITY_TOLERANCE", "0.01"),
        )


def _missing(name: str) -> int:
    _require(name)  # raises ConfigurationError with the standard message
    raise AssertionError("unreachable")
```

Add `limits: RiskLimits` to `ExecutorSettings` immediately after `smtp: SmtpSettings | None` and before `news_assets` (keep `news_assets` last because it has a default). In `from_environment`, pass `limits=RiskLimits.from_environment(),`. Extend `__repr__` with `f"limits={self.limits!r}, "`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd executor && uv run --locked pytest tests/test_config.py -q`
Expected: PASS. Then `uv run --locked pytest -q`: the daemon tests will now FAIL because `_settings` in `tests/test_daemon.py` lacks `limits`. Fix in Step 5.

- [ ] **Step 5: Update the daemon test fixture**

In `executor/tests/test_daemon.py::_settings`, add after `smtp=None,`:

```python
        limits=RiskLimits(
            authorized_config_sha256=frozenset({"2712f694227321e4" + "0" * 48}),
            disabled=False,
            max_session_notional=Decimal("1000000"),
            max_orders_per_session=100,
            max_symbol_notional=Decimal("1000000"),
            equity_tolerance=Decimal("1"),
        ),
```

with imports `from decimal import Decimal` and `from executor.config import ExecutorSettings, RiskLimits`. The digest matches `tests/test_plan_source.py::_plan_payload`.

- [ ] **Step 6: Run the full executor gate**

Run: `cd executor && uv run --locked pytest -q && uv run --locked mypy src && uv run --locked ruff check src tests && uv run --locked ruff format --check src tests`
Expected: all PASS.

- [ ] **Step 7: Commit (with approval)**

```bash
git add executor/src/executor/config.py executor/tests/test_config.py executor/tests/test_daemon.py
git commit -m "executor: require authorized config digests and risk limits in settings"
```

---

### Task 2: Preflight authorization, kill switch, and caps

**Files:**
- Modify: `executor/src/executor/submit.py:142-173` (`_preflight`), `submit_plan` signature
- Modify: `executor/src/executor/daemon.py:294-375` (`run_submit`), `Daemon.__init__` startup check
- Test: `executor/tests/test_submit.py`, `executor/tests/test_daemon.py`
- Docs: `docs/executor/README.md`

**Interfaces:**
- Consumes: `RiskLimits` from Task 1.
- Produces: `_preflight(plan, account, session, now, *, limits: RiskLimits) -> str`; `submit_plan(..., limits: RiskLimits)` keyword required; `Daemon._verify_authorized_config()` raising `ConfigurationError`; journal event `authorization_check` at startup.

- [ ] **Step 1: Write the failing tests**

In `executor/tests/test_submit.py`, add a helper and tests after the existing `_intent` helper:

```python
from executor.config import RiskLimits

_DIGEST = "2712f694227321e4" + "0" * 48


def _limits(**overrides: object) -> RiskLimits:
    payload: dict[str, object] = {
        "authorized_config_sha256": frozenset({_DIGEST}),
        "disabled": False,
        "max_session_notional": Decimal("100000"),
        "max_orders_per_session": 10,
        "max_symbol_notional": Decimal("50000"),
        "equity_tolerance": Decimal("0.01"),
    }
    payload.update(overrides)
    return RiskLimits(**payload)  # type: ignore[arg-type]


def _now() -> dt.datetime:
    return dt.datetime(2026, 9, 10, 13, 40, tzinfo=dt.UTC)


def test_preflight_refuses_an_unauthorized_config_digest() -> None:
    plan = parse_plan(_plan_payload(config_sha256="f" * 64))
    reason = _preflight(plan, _account(), plan.earliest_execution_session, _now(), limits=_limits())
    assert "not authorised" in reason and "f" * 12 in reason


def test_preflight_refuses_when_disabled() -> None:
    plan = parse_plan(_plan_payload())
    reason = _preflight(
        plan, _account(), plan.earliest_execution_session, _now(), limits=_limits(disabled=True)
    )
    assert reason.startswith("EXECUTOR_DISABLED")


def test_preflight_refuses_when_session_notional_exceeds_cap() -> None:
    plan = parse_plan(_plan_payload())
    total = sum(intent.notional for intent in plan.intents)
    reason = _preflight(
        plan,
        _account(),
        plan.earliest_execution_session,
        _now(),
        limits=_limits(max_session_notional=total - Decimal("1")),
    )
    assert "MAX_SESSION_NOTIONAL" in reason


def test_preflight_refuses_when_order_count_exceeds_cap() -> None:
    plan = parse_plan(_plan_payload())
    reason = _preflight(
        plan,
        _account(),
        plan.earliest_execution_session,
        _now(),
        limits=_limits(max_orders_per_session=max(len(plan.intents) - 1, 0) or 1),
    )
    assert "MAX_ORDERS_PER_SESSION" in reason if len(plan.intents) > 1 else reason == ""


def test_preflight_refuses_when_one_symbol_exceeds_cap() -> None:
    plan = parse_plan(_plan_payload())
    biggest = max(intent.notional for intent in plan.intents)
    reason = _preflight(
        plan,
        _account(),
        plan.earliest_execution_session,
        _now(),
        limits=_limits(max_symbol_notional=biggest - Decimal("1")),
    )
    assert "MAX_SYMBOL_NOTIONAL" in reason


def test_preflight_refuses_when_broker_equity_drifted_from_plan_equity() -> None:
    plan = parse_plan(_plan_payload())
    drifted = plan.account_equity * Decimal("1.05")
    reason = _preflight(
        plan,
        _account(equity=str(drifted)),
        plan.earliest_execution_session,
        _now(),
        limits=_limits(),
    )
    assert "EQUITY_TOLERANCE" in reason


def test_preflight_passes_within_all_limits() -> None:
    plan = parse_plan(_plan_payload())
    reason = _preflight(plan, _account(equity=str(plan.account_equity)), plan.earliest_execution_session, _now(), limits=_limits())
    assert reason == ""
```

Check `_plan_payload` in `tests/test_plan_source.py` for the intents and `account_equity` it produces; if `account_equity` differs from `100000`, the equity in `_account()` above must be set from `plan.account_equity` as shown in the last test. Every existing call to `_preflight(...)` and `submit_plan(...)` in the test file gets `limits=_limits()` appended.

In `executor/tests/test_daemon.py`, add:

```python
def test_daemon_refuses_to_start_with_an_unauthorized_strategy_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.strategy_config.parent.mkdir(parents=True, exist_ok=True)
    settings.strategy_config.write_text("[strategy]\nname='x'\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not in AUTHORIZED_CONFIG_SHA256"):
        Daemon(settings)
```

and make the existing `_daemon` helper write a strategy file whose sha256 is in the limits: compute it in `_settings` by writing the file first and setting `authorized_config_sha256=frozenset({hashlib.sha256(content).hexdigest()})`. Keep the plan-payload digest too (both in the set) so submit tests remain valid.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd executor && uv run --locked pytest tests/test_submit.py tests/test_daemon.py -q`
Expected: FAIL with `TypeError: _preflight() got an unexpected keyword argument 'limits'`.

- [ ] **Step 3: Implement preflight checks**

Replace `_preflight` in `executor/src/executor/submit.py`:

```python
def _preflight(
    plan: ExecutionPlan,
    account: Account,
    session: dt.date,
    now: dt.datetime,
    *,
    limits: RiskLimits,
) -> str:
    """Return a refusal reason, or the empty string when it is safe to trade.

    Order matters: the operator's own switches come first, then identity, then
    staleness, then account state, then size. Each refusal names the limit that
    produced it so the journal entry is actionable without reading this code.
    """
    if limits.disabled:
        return "EXECUTOR_DISABLED is true: every submission is refused by operator switch"
    digest = plan.config_sha256.lower()
    if digest not in limits.authorized_config_sha256:
        return (
            f"strategy configuration {digest[:12]}... is not authorised for this executor "
            f"(AUTHORIZED_CONFIG_SHA256 lists {len(limits.authorized_config_sha256)} digest(s)); "
            f"a plan from an unlisted configuration is never traded"
        )
    if not account.is_tradable:
        return (
            f"broker will not accept orders: status={account.status} "
            f"trading_blocked={account.trading_blocked} "
            f"account_blocked={account.account_blocked}"
        )
    if plan.is_expired(now=now):
        return (
            f"plan expired at {plan.expires_at.isoformat()} and must not be executed late; "
            f"a stale signal is not a licence to trade"
        )
    if not plan.is_executable_on(session):
        return (
            f"plan may not execute before {plan.earliest_execution_session.isoformat()}, "
            f"and today is {session.isoformat()}"
        )
    if plan.unrecognized_holdings:
        return (
            f"account holds {', '.join(plan.unrecognized_holdings)}, which the strategy "
            f"universe does not contain. Their value sits in the denominator of every "
            f"target weight in this plan, so no trade is safe until a human resolves it."
        )
    if plan.account_equity > 0:
        drift = abs(account.equity - plan.account_equity) / plan.account_equity
        if drift > limits.equity_tolerance:
            return (
                f"broker equity {account.equity} differs from the plan's sizing equity "
                f"{plan.account_equity} by {drift:.4%}, beyond EQUITY_TOLERANCE "
                f"{limits.equity_tolerance}; the plan was sized on a different account"
            )
    if not plan.intents:
        return "no trades: the account already matches the target within the rebalance band"
    if len(plan.intents) > limits.max_orders_per_session:
        return (
            f"plan carries {len(plan.intents)} intents, above MAX_ORDERS_PER_SESSION "
            f"{limits.max_orders_per_session}"
        )
    session_notional = sum((intent.notional for intent in plan.intents), Decimal("0"))
    if session_notional > limits.max_session_notional:
        return (
            f"plan reference notional {session_notional} exceeds MAX_SESSION_NOTIONAL "
            f"{limits.max_session_notional}"
        )
    oversized = [
        intent for intent in plan.intents if intent.notional > limits.max_symbol_notional
    ]
    if oversized:
        names = ", ".join(f"{item.symbol}={item.notional}" for item in oversized)
        return f"intent notional above MAX_SYMBOL_NOTIONAL {limits.max_symbol_notional}: {names}"
    return ""
```

Add `from executor.config import RiskLimits` under `TYPE_CHECKING` imports (it is only used for typing; keep runtime import light by importing at module level, `config.py` has no heavy deps, so a plain import is fine). Add `limits: RiskLimits` as a required keyword parameter of `submit_plan` (after `dry_run`), and pass it: `refusal = _preflight(plan, account, session, now, limits=limits)`.

- [ ] **Step 4: Wire the daemon and the startup check**

In `executor/src/executor/daemon.py`:

In `run_submit`, pass `limits=settings.limits` to `submit_plan`. In `Daemon.__init__`, after `self._journal = Journal(...)`, call `self._verify_authorized_config()`. Add:

```python
    def _verify_authorized_config(self) -> None:
        """Refuse to start if the pinned strategy file is not on the allowlist.

        The plan will carry the same digest and be refused again at submit time;
        checking here turns a misconfiguration into a loud startup failure
        instead of a quiet day of refused submissions.
        """
        digest = hashlib.sha256(self._settings.strategy_config.read_bytes()).hexdigest()
        authorized = self._settings.limits.authorized_config_sha256
        self._journal.record(
            "authorization_check",
            strategy_config=str(self._settings.strategy_config),
            config_sha256=digest,
            authorized=digest in authorized,
            authorized_count=len(authorized),
            disabled=self._settings.limits.disabled,
        )
        if digest not in authorized:
            message = (
                f"STRATEGY_CONFIG {self._settings.strategy_config} has sha256 {digest}, "
                f"which is not in AUTHORIZED_CONFIG_SHA256"
            )
            raise ConfigurationError(message)
```

with `import hashlib` and `from executor.errors import ConfigurationError` (check the class lives there; `config.py` imports it from `executor.errors`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd executor && uv run --locked pytest -q && uv run --locked mypy src && uv run --locked ruff check src tests && uv run --locked ruff format --check src tests`
Expected: all PASS.

- [ ] **Step 6: Document the new environment variables**

In `docs/executor/README.md`, in the configuration/environment section, add a table:

| Variable | Required | Meaning |
|---|---|---|
| `AUTHORIZED_CONFIG_SHA256` | yes | Comma-separated raw-file SHA-256 digests of strategy TOMLs this executor may trade. Compute with `sha256sum configs/strategy/amat_v6.toml` in the `amat` checkout. A plan from any other digest is refused, and the daemon refuses to start if `STRATEGY_CONFIG` is unlisted. |
| `EXECUTOR_DISABLED` | no (`false`) | `true` refuses every submission; reports keep running. |
| `MAX_SESSION_NOTIONAL` | yes | USD ceiling on the sum of intent reference notionals per session. |
| `MAX_ORDERS_PER_SESSION` | yes | Ceiling on intents per session. |
| `MAX_SYMBOL_NOTIONAL` | yes | USD ceiling per intent. |
| `EQUITY_TOLERANCE` | no (`0.01`) | Max relative gap between plan sizing equity and broker equity. |

If `executor/.env.example` exists, add the same names with placeholder values (never real digests of anything but v6).

- [ ] **Step 7: Commit (with approval)**

```bash
git add executor/src/executor/submit.py executor/src/executor/daemon.py executor/tests/test_submit.py executor/tests/test_daemon.py executor/README.md
git commit -m "executor: refuse unauthorised config digests, add kill switch and notional caps"
```

---

### Task 3: Retry a failed phase while its window is still open

**Files:**
- Modify: `executor/src/executor/daemon.py:589-644` (`tick`), constants near line 84-106
- Test: `executor/tests/test_daemon.py`

**Interfaces:**
- Produces: constants `_PHASE_RETRY_SECONDS = 300.0`, `_MAX_PHASE_ATTEMPTS = 3`; in-memory `Daemon._attempts: dict[str, int]`; journal event `phase_retry_scheduled`.

- [ ] **Step 1: Write the failing test**

Append to `executor/tests/test_daemon.py` (follow the existing fake-client/phase test pattern in that file; `_FakeClient` and `_at` exist):

```python
def test_a_transient_submit_failure_is_retried_within_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _FakeClient(sessions=[_session("2026-09-10")], now=_at("2026-09-10", 9, 25))
    daemon = _daemon(tmp_path, client)
    calls: list[str] = []

    def flaky(session: dt.date, *, minutes_late: int = 0) -> None:
        calls.append(session.isoformat())
        if len(calls) == 1:
            raise RuntimeError("broker hiccup")

    monkeypatch.setattr(daemon, "run_submit", flaky)

    sleep = daemon.tick()
    assert calls == ["2026-09-10"]
    assert sleep == pytest.approx(300.0)
    assert not daemon._already_done("submit", dt.date(2026, 9, 10))

    client.now = _at("2026-09-10", 9, 31)
    daemon.tick()
    assert calls == ["2026-09-10", "2026-09-10"]
    assert daemon._already_done("submit", dt.date(2026, 9, 10))
    kinds = [entry["kind"] for entry in daemon._journal.entries()]
    assert "phase_failed" in kinds and "phase_retry_scheduled" in kinds


def test_a_phase_gives_up_after_the_attempt_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _FakeClient(sessions=[_session("2026-09-10")], now=_at("2026-09-10", 9, 25))
    daemon = _daemon(tmp_path, client)
    monkeypatch.setattr(
        daemon, "run_submit", lambda session, *, minutes_late=0: (_ for _ in ()).throw(RuntimeError("x"))
    )
    for minute in (25, 31, 37):
        client.now = _at("2026-09-10", 9, minute)
        daemon.tick()
    assert daemon._already_done("submit", dt.date(2026, 9, 10))
```

Adapt `_FakeClient` construction and `_session` to the helpers that already exist in the file (read the top of `test_daemon.py`; the names may differ, use the existing ones). If the journal has no `entries()` accessor, read `settings.journal_path` lines and parse JSON.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd executor && uv run --locked pytest tests/test_daemon.py -q -k "transient or gives_up"`
Expected: FAIL (`_already_done` is True after the first failure; sleep is 1.0).

- [ ] **Step 3: Implement retry-within-window**

In `daemon.py` add constants next to `_LATE_SUBMIT_MINUTES`:

```python
#: A phase that raised is retried after this many seconds, while its window is
#: still open. Long enough for a broker blip or a stuck subprocess to clear,
#: short enough that a 09:25 failure still trades well before the cutoff.
_PHASE_RETRY_SECONDS = 300.0
#: Attempts per phase per session, including the first. After this the phase
#: is settled as failed so the loop does not hammer a broken dependency.
_MAX_PHASE_ATTEMPTS = 3
```

In `Daemon.__init__` add `self._attempts: dict[str, int] = {}`. Replace the `if phase.is_due(now):` block in `tick` with:

```python
            if phase.is_due(now):
                attempt = self._attempts.get(phase.key, 0) + 1
                self._attempts[phase.key] = attempt
                try:
                    if phase.name == "submit":
                        self.run_submit(phase.session, minutes_late=phase.minutes_late(now))
                    else:
                        self.run_report(phase.session)
                except Exception as exc:
                    LOGGER.exception("%s phase failed for %s", phase.name, phase.session)
                    self._journal.record(
                        "phase_failed",
                        session=phase.session,
                        phase=phase.name,
                        attempt=attempt,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    retry_at = now + dt.timedelta(seconds=_PHASE_RETRY_SECONDS)
                    if attempt < _MAX_PHASE_ATTEMPTS and retry_at <= phase.until:
                        self._journal.record(
                            "phase_retry_scheduled",
                            session=phase.session,
                            phase=phase.name,
                            attempt=attempt,
                            retry_at=retry_at.isoformat(),
                            window_closes_at=phase.until.isoformat(),
                        )
                        return _PHASE_RETRY_SECONDS
                self._mark_done(phase.name, phase.session)
                self._attempts.pop(phase.key, None)
                return 1.0  # re-evaluate immediately; another phase may also be due
```

Submit retries are safe because `client_order_id_for` is deterministic: an order that already reached the broker is rejected as a duplicate and journalled as `order_duplicate`.

- [ ] **Step 4: Run the gate**

Run: `cd executor && uv run --locked pytest -q && uv run --locked mypy src && uv run --locked ruff check src tests && uv run --locked ruff format --check src tests`
Expected: all PASS.

- [ ] **Step 5: Commit (with approval)**

```bash
git add executor/src/executor/daemon.py executor/tests/test_daemon.py
git commit -m "executor: retry a failed phase within its window before settling it"
```

---

### Task 4: Suppress annualized and tail statistics below the meaningful sample

**Files:**
- Modify: `executor/src/executor/report/metrics.py:238-263`
- Modify: `executor/src/executor/report/daily.py:139-173`
- Test: `executor/tests/test_report.py`

- [ ] **Step 1: Write the failing test**

```python
def test_annualized_and_tail_statistics_are_null_below_the_meaningful_sample() -> None:
    curve = [
        {"session": f"2026-09-{day:02d}", "equity": str(100000 + 1000 * index)}
        for index, day in enumerate((1, 2, 3, 4, 8, 9, 10, 11, 14))
    ]
    stats = compute_stats(curve)
    assert stats.sessions == 9
    assert stats.is_meaningful is False
    assert stats.annualized_return is None
    assert stats.sharpe_ratio is None
    assert stats.sortino_ratio is None
    assert stats.calmar_ratio is None
    assert stats.historical_var_95 is None
    assert stats.historical_cvar_95 is None
    assert stats.annualized_volatility is None
    assert stats.total_return == pytest.approx(0.08)
```

Use the actual function name in `metrics.py` (the audit found `compute_stats`; confirm with `grep -n "^def " metrics.py`).

- [ ] **Step 2: Run to verify it fails**

Run: `cd executor && uv run --locked pytest tests/test_report.py -q -k meaningful`
Expected: FAIL (annualized_return is a float).

- [ ] **Step 3: Implement**

In `metrics.py`, change the two guards:

```python
    supports_annualization = (
        len(returns) >= _MEANINGFUL_SAMPLE - 1 and not has_extended_gap
    )
    if returns and supports_annualization:
```

and

```python
    if len(returns) >= _MIN_RETURNS_FOR_DISPERSION and supports_annualization:
```

(`sessions = len(returns) + 1`, so `_MEANINGFUL_SAMPLE` sessions means `_MEANINGFUL_SAMPLE - 1` returns; keep `is_meaningful` unchanged.) Update the `PerformanceStats` docstring or field comments to say annualized/tail fields are `None` until the sample is meaningful. In `daily.py`, move the "Not a meaningful sample" callout above the table and reword: "Annualized, dispersion, and tail statistics are withheld until at least 20 sessions with no extended gap are observed; this report has N session(s)."

- [ ] **Step 4: Run the gate**

Run: `cd executor && uv run --locked pytest -q && uv run --locked mypy src && uv run --locked ruff check src tests && uv run --locked ruff format --check src tests`
Expected: PASS. Existing tests that asserted a Sharpe on tiny samples must be updated to use 20+ sessions or to assert `None`.

- [ ] **Step 5: Commit (with approval)**

```bash
git add executor/src/executor/report/metrics.py executor/src/executor/report/daily.py executor/tests/test_report.py
git commit -m "executor: withhold annualized and tail statistics below the meaningful sample"
```

---

### Task 5: `llmrouter` contracts and provider health tracking

**Files:**
- Create: `amat/src/llmrouter/__init__.py`, `amat/src/llmrouter/py.typed`, `amat/src/llmrouter/contracts.py`, `amat/src/llmrouter/health.py`
- Modify: `amat/pyproject.toml:66` (`packages = [..., "src/llmrouter"]`)
- Test: `amat/tests/unit/test_llmrouter_health.py`

**Interfaces (produced, used by Tasks 6-8, 10):**

```python
# llmrouter/contracts.py
ProviderName = Literal["openai", "gemini", "openrouter", "nvidia"]
ErrorClass = Literal["ok", "rate_limit", "quota", "auth", "timeout", "transport", "server", "malformed", "refusal", "no_credential"]
class Record(BaseModel): model_config = ConfigDict(extra="forbid", frozen=True); def canonical(self) -> str; def digest(self) -> str
class ChatRequest(Record): system: str; user: str; schema_json: str; schema_name: str = "structured_output"; max_output_tokens: int (256..65536); timeout_seconds: float (>0, <=600); reasoning_effort: str | None = None
class ChatResponse(Record): provider: str; model: str; text: str | None = None; raw_json: str = "{}"; input_tokens: int | None; output_tokens: int | None; error_code: str | None = None; error_class: ErrorClass = "ok"; retry_after_seconds: float | None = None; elapsed_seconds: float = 0.0
    @property def ok(self) -> bool: return self.error_class == "ok"
def classify_http(status: int, body: str) -> ErrorClass   # 401/403 -> auth; 404 model -> auth; 429 -> quota if "insufficient_quota" or "exceeded your current quota" in body.lower() else rate_limit; 408 -> timeout; 5xx -> server; else transport
class Transport(Protocol): async def generate(self, request: ChatRequest, *, model: str, api_key: str, base_url: str) -> ChatResponse

# llmrouter/health.py
class HealthPolicy(Record): window_requests: int = 10; unknown_error_threshold: int = 3; failure_ratio_threshold: float = 0.5; failure_ratio_window_seconds: float = 300; timeout_threshold: int = 2; timeout_window_requests: int = 5; cooldown_seconds: float = 600; max_cooldown_seconds: float = 3600; rate_limit_cooldown_seconds: float = 900
class ProviderHealth:  # mutable, one per provider
    def __init__(self, policy: HealthPolicy, clock: Callable[[], float] = time.monotonic)
    state: Literal["available", "cooling", "disabled"]; cooling_until: float | None; consecutive_cooldowns: int
    def should_skip(self) -> bool           # True while cooling (until expiry) or disabled
    def record(self, error_class: ErrorClass, *, retry_after_seconds: float | None = None) -> None
    def snapshot(self) -> dict[str, object]  # state, cooling_until, recent outcomes count, reasons
```

Rules in `record`: `ok` clears consecutive cooldowns only after a successful probe following a cooldown; `auth` and `no_credential` set `disabled`; `rate_limit`/`quota` set `cooling` for `retry_after_seconds` if given else `rate_limit_cooldown_seconds`; `timeout` counts within the last `timeout_window_requests` and trips at `timeout_threshold`; `transport`/`server`/`malformed` trip at `unknown_error_threshold` within `window_requests` **or** when failures over `failure_ratio_window_seconds` exceed `failure_ratio_threshold` with at least 4 observations; each trip cools for `min(cooldown_seconds * 2**consecutive_cooldowns, max_cooldown_seconds)` and increments `consecutive_cooldowns`.

- [ ] **Step 1: Write the failing tests**

`amat/tests/unit/test_llmrouter_health.py`:

```python
from __future__ import annotations

from llmrouter.contracts import classify_http
from llmrouter.health import HealthPolicy, ProviderHealth


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _health(**overrides: object) -> tuple[ProviderHealth, _Clock]:
    clock = _Clock()
    return ProviderHealth(HealthPolicy(**overrides), clock=clock), clock  # type: ignore[arg-type]


def test_rate_limit_cools_for_the_provider_supplied_window() -> None:
    health, clock = _health()
    health.record("rate_limit", retry_after_seconds=42)
    assert health.state == "cooling" and health.should_skip()
    clock.now += 41
    assert health.should_skip()
    clock.now += 2
    assert not health.should_skip()


def test_auth_error_disables_for_the_process_lifetime() -> None:
    health, clock = _health()
    health.record("auth")
    clock.now += 10**6
    assert health.state == "disabled" and health.should_skip()


def test_unknown_errors_trip_after_three_in_ten_with_backoff() -> None:
    health, clock = _health(cooldown_seconds=10, max_cooldown_seconds=25)
    for _ in range(7):
        health.record("ok")
    health.record("server")
    health.record("transport")
    assert not health.should_skip()
    health.record("malformed")
    assert health.should_skip()
    clock.now += 11
    assert not health.should_skip()
    for _ in range(3):
        health.record("server")
    assert health.cooling_until is not None and health.cooling_until - clock.now == 20
    clock.now += 21
    for _ in range(3):
        health.record("server")
    assert health.cooling_until is not None and health.cooling_until - clock.now == 25


def test_failure_ratio_over_five_minutes_trips() -> None:
    health, clock = _health(unknown_error_threshold=99, window_requests=99)
    health.record("ok")
    health.record("server")
    health.record("ok")
    health.record("server")
    assert not health.should_skip()
    health.record("server")
    assert health.should_skip()


def test_two_timeouts_in_five_requests_trip() -> None:
    health, _ = _health()
    health.record("ok")
    health.record("timeout")
    health.record("ok")
    assert not health.should_skip()
    health.record("timeout")
    assert health.should_skip()


def test_success_after_cooldown_resets_backoff() -> None:
    health, clock = _health(cooldown_seconds=10)
    for _ in range(3):
        health.record("server")
    clock.now += 11
    health.record("ok")
    assert health.consecutive_cooldowns == 0


def test_http_classification() -> None:
    assert classify_http(401, "") == "auth"
    assert classify_http(404, '{"error":{"message":"model not found"}}') == "auth"
    assert classify_http(429, '{"error":{"type":"insufficient_quota"}}') == "quota"
    assert classify_http(429, "slow down") == "rate_limit"
    assert classify_http(503, "") == "server"
    assert classify_http(408, "") == "timeout"
    assert classify_http(418, "") == "transport"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd amat && uv run --locked pytest tests/unit/test_llmrouter_health.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'llmrouter'`.

- [ ] **Step 3: Implement contracts and health**

`amat/src/llmrouter/__init__.py`:

```python
"""Provider-neutral LLM routing: ordered fallback chains with per-provider health.

Stdlib plus the pinned provider SDKs. Never imports executor, subprocess, or an
HTTP client of its own; every network call goes through an official SDK with
retries disabled so failover stays observable and bounded here.
"""
```

`amat/src/llmrouter/contracts.py` per the interface block above. `Record.canonical` returns `self.model_dump_json(exclude_none=False)` with sorted keys via `json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))`; `digest` is the sha256 hex of `canonical().encode()`.

`amat/src/llmrouter/health.py`:

```python
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Literal

from llmrouter.contracts import ErrorClass, Record

State = Literal["available", "cooling", "disabled"]
_TRIPPING: frozenset[ErrorClass] = frozenset({"transport", "server", "malformed"})


class HealthPolicy(Record):
    window_requests: int = 10
    unknown_error_threshold: int = 3
    failure_ratio_threshold: float = 0.5
    failure_ratio_window_seconds: float = 300
    failure_ratio_min_observations: int = 4
    timeout_threshold: int = 2
    timeout_window_requests: int = 5
    cooldown_seconds: float = 600
    max_cooldown_seconds: float = 3600
    rate_limit_cooldown_seconds: float = 900


class ProviderHealth:
    """Sliding-window outcome tracker for one provider. Not thread-safe; the
    router serialises access."""

    def __init__(self, policy: HealthPolicy, clock: Callable[[], float] = time.monotonic) -> None:
        self.policy = policy
        self._clock = clock
        self._recent: deque[tuple[float, ErrorClass]] = deque(maxlen=max(policy.window_requests, policy.timeout_window_requests))
        self.state: State = "available"
        self.cooling_until: float | None = None
        self.consecutive_cooldowns = 0
        self._probe_pending = False
        self.last_reason: str | None = None

    def should_skip(self) -> bool:
        if self.state == "disabled":
            return True
        if self.state == "cooling":
            assert self.cooling_until is not None
            if self._clock() < self.cooling_until:
                return True
            self.state = "available"
            self.cooling_until = None
            self._probe_pending = True
        return False

    def record(self, error_class: ErrorClass, *, retry_after_seconds: float | None = None) -> None:
        now = self._clock()
        self._recent.append((now, error_class))
        if error_class == "ok":
            if self._probe_pending:
                self.consecutive_cooldowns = 0
                self._probe_pending = False
            return
        if error_class in ("auth", "no_credential"):
            self.state = "disabled"
            self.last_reason = error_class
            return
        if error_class in ("rate_limit", "quota"):
            seconds = retry_after_seconds if retry_after_seconds else self.policy.rate_limit_cooldown_seconds
            self._cool(now, seconds, error_class)
            return
        if error_class == "timeout":
            window = list(self._recent)[-self.policy.timeout_window_requests :]
            if sum(1 for _, item in window if item == "timeout") >= self.policy.timeout_threshold:
                self._trip(now, "timeouts")
            return
        if error_class in _TRIPPING:
            window = list(self._recent)[-self.policy.window_requests :]
            unknown = sum(1 for _, item in window if item in _TRIPPING)
            if unknown >= self.policy.unknown_error_threshold:
                self._trip(now, "unknown_errors")
                return
            since = now - self.policy.failure_ratio_window_seconds
            recent = [item for at, item in self._recent if at >= since]
            failures = sum(1 for item in recent if item != "ok")
            if len(recent) >= self.policy.failure_ratio_min_observations and failures / len(recent) > self.policy.failure_ratio_threshold:
                self._trip(now, "failure_ratio")

    def _trip(self, now: float, reason: str) -> None:
        seconds = min(self.policy.cooldown_seconds * (2**self.consecutive_cooldowns), self.policy.max_cooldown_seconds)
        self.consecutive_cooldowns += 1
        self._cool(now, seconds, reason)
        self._recent.clear()

    def _cool(self, now: float, seconds: float, reason: str) -> None:
        self.state = "cooling"
        self.cooling_until = now + seconds
        self.last_reason = reason

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state,
            "cooling_until": self.cooling_until,
            "consecutive_cooldowns": self.consecutive_cooldowns,
            "last_reason": self.last_reason,
            "recent": [item for _, item in self._recent],
        }
```

Add `"src/llmrouter"` to `packages` in `amat/pyproject.toml`. Add to `tests/unit/test_portfolio_team.py` a sibling test `test_llmrouter_has_no_broker_or_process_execution_imports` scanning `src/llmrouter` with the same forbidden set.

- [ ] **Step 4: Run the gate**

Run: `cd amat && uv run --locked pytest tests/unit/test_llmrouter_health.py tests/unit/test_portfolio_team.py -q && uv run --locked mypy src/llmrouter && uv run --locked ruff check src/llmrouter tests/unit/test_llmrouter_health.py && uv run --locked ruff format --check src/llmrouter tests/unit/test_llmrouter_health.py`
Expected: PASS.

- [ ] **Step 5: Commit (with approval)**

```bash
git add amat/src/llmrouter amat/pyproject.toml amat/tests/unit/test_llmrouter_health.py amat/tests/unit/test_portfolio_team.py
git commit -m "llmrouter: contracts and sliding-window provider health"
```

---

### Task 6: OpenAI-compatible and Gemini transports

**Files:**
- Create: `amat/src/llmrouter/transports.py`
- Test: `amat/tests/unit/test_llmrouter_transports.py`

**Interfaces:**
- Produces: `OpenAICompatibleTransport(api_style: Literal["responses", "chat"])` and `GeminiTransport()`, both satisfying `Transport.generate(request, *, model, api_key, base_url) -> ChatResponse`. Constant `BASE_URLS = {"openai": "https://api.openai.com/v1", "openrouter": "https://openrouter.ai/api/v1", "nvidia": "https://integrate.api.nvidia.com/v1", "gemini": "https://generativelanguage.googleapis.com"}`.

Behaviour:
- `responses` style: `client.responses.create(model, instructions, input, max_output_tokens, store=False, text={"format": {"type": "json_schema", "name": request.schema_name, "strict": True, "schema": ...}}, reasoning={"effort": ...} if reasoning_effort)`. Refusal → `error_class="refusal"`; `status != "completed"` → `error_class="malformed"`, `error_code="provider_incomplete_response"`.
- `chat` style (OpenRouter, NVIDIA): `client.chat.completions.create(model, messages=[{"role":"system",...},{"role":"user",...}], max_tokens, response_format={"type": "json_schema", "json_schema": {"name": ..., "strict": True, "schema": ...}})`. If the provider rejects `json_schema` with HTTP 400 mentioning `response_format`, retry once with `{"type": "json_object"}` and the schema appended to the system prompt; record `error_code="schema_downgraded"` in `raw_json` but `error_class="ok"` when the text parses as JSON. `finish_reason == "length"` → `malformed`.
- Usage: `usage.input_tokens/output_tokens` (responses) or `usage.prompt_tokens/completion_tokens` (chat); `output_tokens += usage.completion_tokens_details.reasoning_tokens` only for `chat` style when present and the provider is NVIDIA (OpenRouter includes them; document the asymmetry in a comment).
- `APIStatusError` → `classify_http(status, body)`, `retry_after_seconds` from the `retry-after` header when present; `APIConnectionError`/`APITimeoutError` → `transport`/`timeout`. Any other exception → `error_class="transport"`, `error_code=f"transport_{type(exc).__name__}"`, no exception text stored.
- Gemini: port the body of `portfolio_team.model_runtime.GeminiTransport.generate` unchanged except it takes `model`/`api_key`/`base_url` as arguments and maps `code` through `classify_http`. Key scrubbing: replace `api_key` in any stored message with `[REDACTED]`.

- [ ] **Step 1: Write the failing tests**

`amat/tests/unit/test_llmrouter_transports.py` (mock the SDK clients exactly as `tests/unit/test_portfolio_team_openai.py` does for `AsyncOpenAI`; copy its `_FakeAsyncOpenAI` pattern):

```python
import json
import pytest
from llmrouter.contracts import ChatRequest
from llmrouter.transports import BASE_URLS, OpenAICompatibleTransport


def _request(**overrides):
    payload = dict(system="rules", user="{}", schema_json=json.dumps({"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"], "additionalProperties": False}), max_output_tokens=512, timeout_seconds=30)
    payload.update(overrides)
    return ChatRequest(**payload)


@pytest.mark.asyncio
async def test_chat_style_sends_json_schema_and_reads_usage(monkeypatch):
    captured = {}
    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _completion('{"x": 1}', prompt=10, completion=5, finish="stop")
    monkeypatch.setattr("llmrouter.transports.AsyncOpenAI", _fake_client(chat=_Completions()))
    response = await OpenAICompatibleTransport("chat").generate(_request(), model="m", api_key="k", base_url=BASE_URLS["nvidia"])
    assert response.ok and response.text == '{"x": 1}'
    assert response.input_tokens == 10 and response.output_tokens == 5
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_chat_style_downgrades_to_json_object_when_schema_is_rejected(monkeypatch):
    calls = []
    class _Completions:
        async def create(self, **kwargs):
            calls.append(kwargs["response_format"]["type"])
            if kwargs["response_format"]["type"] == "json_schema":
                raise _status_error(400, '{"error":{"message":"response_format json_schema not supported"}}')
            return _completion('{"x": 2}', prompt=1, completion=1, finish="stop")
    monkeypatch.setattr("llmrouter.transports.AsyncOpenAI", _fake_client(chat=_Completions()))
    response = await OpenAICompatibleTransport("chat").generate(_request(), model="m", api_key="k", base_url=BASE_URLS["openrouter"])
    assert calls == ["json_schema", "json_object"] and response.ok
    assert json.loads(response.raw_json)["schema_downgraded"] is True


@pytest.mark.asyncio
async def test_status_errors_are_classified_and_scrubbed(monkeypatch):
    class _Completions:
        async def create(self, **kwargs):
            raise _status_error(429, '{"error":{"type":"insufficient_quota","message":"key k leaked"}}', retry_after="17")
    monkeypatch.setattr("llmrouter.transports.AsyncOpenAI", _fake_client(chat=_Completions()))
    response = await OpenAICompatibleTransport("chat").generate(_request(), model="m", api_key="k", base_url=BASE_URLS["openrouter"])
    assert response.error_class == "quota" and response.retry_after_seconds == 17
    assert "k leaked" not in response.raw_json and "[REDACTED]" in response.raw_json


@pytest.mark.asyncio
async def test_responses_style_refusal_and_incomplete(monkeypatch):
    ...  # refusal -> error_class refusal; status incomplete -> malformed
```

Write the helpers `_fake_client`, `_completion`, `_status_error` at the top of the test file, modelled on the fakes in `tests/unit/test_portfolio_team_openai.py` (construct `openai.APIStatusError(message, response=httpx.Response(...), body=...)` only if `httpx` is importable in the test environment as a transitive dependency of `openai`; otherwise build a minimal stub exposing `.status_code`, `.body`, `.response.headers`). Check `pytest-asyncio` availability: the existing OpenAI tests run coroutines with `asyncio.run`; follow that pattern instead of the marker if the plugin is absent.

- [ ] **Step 2: Run to verify they fail**

Run: `cd amat && uv run --locked pytest tests/unit/test_llmrouter_transports.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'llmrouter.transports'`.

- [ ] **Step 3: Implement transports** per the behaviour list. Every SDK client is created with `max_retries=0`, `timeout=request.timeout_seconds`, `base_url=base_url`, `api_key=api_key`, inside `async with`. Gemini uses `HttpOptions(base_url=..., timeout=int(ms), retry_options=HttpRetryOptions(attempts=0))`.

- [ ] **Step 4: Run the gate**

Run: `cd amat && uv run --locked pytest tests/unit/test_llmrouter_transports.py -q && uv run --locked mypy src/llmrouter && uv run --locked ruff check src/llmrouter tests/unit/test_llmrouter_transports.py`
Expected: PASS.

- [ ] **Step 5: Add an opt-in live smoke test**

`amat/tests/live/test_llmrouter_live.py`, skipped unless `LLMROUTER_LIVE=1`: for each provider with a credential set, list models through the SDK (`client.models.list()` for OpenAI-compatible; `client.models.list()` for Gemini) and assert the configured chain model names exist; then one tiny structured generation (`{"x": integer}`) with `max_output_tokens=64`. Register `tests/live` in `pyproject.toml` pytest config as excluded from the default `testpaths`. This is the only place that exercises the real SDK wire shapes (spec P4).

- [ ] **Step 6: Commit (with approval)**

```bash
git add amat/src/llmrouter/transports.py amat/tests/unit/test_llmrouter_transports.py amat/tests/live amat/pyproject.toml
git commit -m "llmrouter: OpenAI-compatible (OpenAI, OpenRouter, NVIDIA) and Gemini transports"
```

---

### Task 7: Chain configuration, router, and failover

**Files:**
- Create: `amat/src/llmrouter/chain.py`, `amat/configs/governance/llm-chain.toml`
- Test: `amat/tests/unit/test_llmrouter_chain.py`

**Interfaces:**

```python
class RouteTarget(Record): provider: ProviderName; model: str; api_style: Literal["responses","chat","gemini"]; credential_env: str; base_url: str; reasoning_effort: str | None = None; max_output_tokens: int | None = None
class RoleChain(Record): role: str; targets: tuple[RouteTarget, ...]
class ChainConfig(Record): schema_version: Literal["llm-chain/1"]; health: HealthPolicy; roles: tuple[RoleChain, ...]
    def for_role(self, role: str) -> RoleChain   # exact role, else "default"
def load_chain(path: Path) -> ChainConfig       # tomllib; fills base_url/credential_env/api_style defaults per provider
class BudgetHook(Protocol): def reserve(self, *, provider: str, model: str, role: str, input_tokens: int, output_tokens: int) -> object; def settle(self, token: object, response: ChatResponse) -> None
class RouteAttempt(Record): provider; model; error_class; error_code; skipped_reason: str | None; elapsed_seconds
class RouteResult(Record): response: ChatResponse; attempts: tuple[RouteAttempt, ...]; earliest_retry_at: float | None
class Router:
    def __init__(self, config: ChainConfig, transports: Mapping[str, Transport], *, budget: BudgetHook | None = None, clock=time.monotonic, env: Mapping[str, str] | None = None)
    async def complete(self, role: str, request: ChatRequest, *, deadline: float | None = None) -> RouteResult
    def health(self) -> dict[str, dict[str, object]]
```

`complete` walks `for_role(role).targets` in order: skip if health says skip (record `skipped_reason="cooling"|"disabled"`), skip if `env.get(credential_env)` is empty (record `no_credential`, mark health `no_credential` → disabled), reserve budget (a `ValueError`/`EvidenceError`-like exception from the hook → `skipped_reason="budget"`, continue), call transport with a timeout bounded by `deadline`, settle budget, `health.record(response.error_class, retry_after_seconds=...)`; return on `ok`. A `malformed`/`refusal` response is retried **once on the same target** with `request.user + "\n\nPrevious output was not valid JSON for the schema; return only the JSON object."`, then moves on. If all targets fail, return `RouteResult` whose response has `error_class` of the last failure and `error_code="all_providers_unavailable"`, `earliest_retry_at=min(cooling_until over cooling providers)`.

Default `configs/governance/llm-chain.toml` (model names verified against the live `/models` listings in Task 6 Step 5 before this file is committed; replace the placeholders with listed IDs):

```toml
schema_version = "llm-chain/1"

[health]
window_requests = 10
unknown_error_threshold = 3
failure_ratio_threshold = 0.5
failure_ratio_window_seconds = 300
timeout_threshold = 2
timeout_window_requests = 5
cooldown_seconds = 600
max_cooldown_seconds = 3600
rate_limit_cooldown_seconds = 900

[[roles]]
role = "chief_pm"
[[roles.targets]]
provider = "openai"
model = "gpt-6-astra"
reasoning_effort = "high"
max_output_tokens = 8192
[[roles.targets]]
provider = "gemini"
model = "<gemini pro-class id from live listing>"
[[roles.targets]]
provider = "openrouter"
model = "<frontier id from live listing>"
[[roles.targets]]
provider = "nvidia"
model = "<nim id from live listing>"

[[roles]]
role = "default"
[[roles.targets]]
provider = "openai"
model = "<cheap OpenAI id>"
reasoning_effort = "low"
[[roles.targets]]
provider = "gemini"
model = "gemini-3.6-flash"
[[roles.targets]]
provider = "openrouter"
model = "<cheap id>"
[[roles.targets]]
provider = "nvidia"
model = "<nim id>"
```

Provider defaults filled by `load_chain`: `openai` → responses, `OPENAI_API_KEY`; `gemini` → gemini, `GEMINI_API_KEY`; `openrouter` → chat, `OPENROUTER_API_KEY`; `nvidia` → chat, `NVIDIA_API_KEY`; base URLs from `BASE_URLS`.

- [ ] **Step 1: Write the failing tests** covering: (a) walks the chain in order and returns the first `ok`; (b) `no_credential` skips and disables that provider; (c) a `quota` response moves to the next provider and the first is skipped on the next call until `retry_after`; (d) three `server` responses trip cooling and the fourth call skips; (e) `malformed` retries once on the same target then falls through; (f) budget hook `reserve` raising skips the target and `settle` is called with the response for every attempted target; (g) all targets failing returns `all_providers_unavailable` with `earliest_retry_at`; (h) `load_chain` fills defaults and rejects an unknown provider and a missing `default` role. Use a scripted `Transport` fake that pops responses from a per-provider queue.

- [ ] **Step 2: Run to verify they fail** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `chain.py`** per the interface. Serialise `complete` with an `asyncio.Lock` per provider health object.

- [ ] **Step 4: Run the gate**

Run: `cd amat && uv run --locked pytest tests/unit/test_llmrouter_*.py -q && uv run --locked mypy src/llmrouter && uv run --locked ruff check src/llmrouter tests && uv run --locked ruff format --check src/llmrouter tests`
Expected: PASS.

- [ ] **Step 5: Commit (with approval)**

```bash
git add amat/src/llmrouter/chain.py amat/configs/governance/llm-chain.toml amat/tests/unit/test_llmrouter_chain.py
git commit -m "llmrouter: ordered fallback chain with health-driven failover"
```

---

### Task 8: pmteam budget: release on failure, provider columns, `pmteam budget`

**Files:**
- Modify: `amat/src/portfolio_team/store.py:38-49` (DDL), `:267-284` (`status`), `:286-366` (`reserve_model_call`, `finish_model_call`)
- Modify: `amat/src/portfolio_team/cli.py` (`budget` subcommand)
- Test: `amat/tests/unit/test_portfolio_team_budget.py`; update `tests/unit/test_portfolio_team_models.py:132-178` expectations

**Interfaces:**
- `Store.reserve_model_call(..., provider: str = "unknown", model: str = "unknown")` (new keyword args, stored in new columns `provider TEXT NOT NULL DEFAULT 'unknown'`, `model TEXT NOT NULL DEFAULT 'unknown'`, added idempotently with `PRAGMA table_info` check + `ALTER TABLE`).
- `finish_model_call(...)` settle rule: if `error is None`: `charged = COALESCE(actual, reserved)` (unchanged). If `error is not None`: `charged_input = COALESCE(actual_input, MIN(reserved_input, penalty))`, `charged_output = COALESCE(actual_output, MIN(reserved_output, penalty))` with `FAILURE_PENALTY_TOKENS = 256` module constant.
- `Store.budget_breakdown() -> list[dict]`: rows grouped by `day, provider, role, CASE WHEN error IS NULL THEN 'ok' ELSE 'failed' END` with `calls`, `charged_input`, `charged_output`, `actual_input`, `actual_output`.
- `Store.release_model_call(review_id, role, attempt, *, reason: str) -> None`: sets `charged_*` to `COALESCE(actual_*, penalty)` and writes an `events` row `budget_released`.
- CLI: `pmteam budget status` prints the breakdown as JSON; `pmteam budget release REVIEW_ID ROLE ATTEMPT --reason TEXT`.

- [ ] **Step 1: Write the failing tests**

```python
def test_failed_call_charges_only_the_penalty(tmp_path):
    store = Store(tmp_path)
    store.begin_attempt("r1", "research", 2)  # use the real signature from store.py
    store.reserve_model_call("r1", "research", 1, request_json="{}", input_tokens=5000, output_tokens=4096, limits=(10**6, 10**6, 10**6, 10**6), provider="gemini", model="m")
    store.finish_model_call("r1", "research", 1, response_json="{}", input_tokens=None, output_tokens=None, elapsed=1.0, error="provider_http_503")
    row = store.model_calls("r1")[0]
    assert row["charged_input"] == 256 and row["charged_output"] == 256
    assert row["provider"] == "gemini" and row["model"] == "m"


def test_successful_call_without_usage_keeps_the_reservation(tmp_path): ...  # charged == reserved


def test_thirteen_failures_do_not_exhaust_the_day(tmp_path):
    # reserve+fail 13 times with output reservation 8192 against a 100000 daily limit; the 14th reserve succeeds


def test_breakdown_and_release(tmp_path): ...  # breakdown rows keyed by provider/role/status; release lowers charged and writes event
```

Update `tests/unit/test_portfolio_team_models.py::test_deadline_cancels_transport_and_retains_usage` to assert `charged_output == 256` (the penalty) and rename it `..._charges_the_failure_penalty`; update `test_pre_dispatch_budget_and_durable_daily_limit` so the exhaustion scenario uses successful-without-usage calls (which still retain reservations) rather than failures.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement** the schema migration in `Store.__init__` after `executescript`:

```python
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(model_calls)")}
        for name in ("provider", "model"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE model_calls ADD COLUMN {name} TEXT NOT NULL DEFAULT 'unknown'")
```

and the settle rule in `finish_model_call` using two SQL branches selected in Python on `error is None`.

- [ ] **Step 4: Run the gate** for `tests/unit/test_portfolio_team*.py`, mypy, ruff.

- [ ] **Step 5: Commit (with approval)**

```bash
git add amat/src/portfolio_team/store.py amat/src/portfolio_team/cli.py amat/tests/unit/test_portfolio_team_budget.py amat/tests/unit/test_portfolio_team_models.py
git commit -m "pmteam: charge a fixed penalty for failed model calls and expose budget breakdown/release"
```

---

### Task 9: Route pmteam roles through the chain

**Files:**
- Modify: `amat/src/portfolio_team/model_runtime.py` (add `RoutedTransport`, keep `GeminiTransport` as a thin wrapper over `llmrouter.transports.GeminiTransport` for the legacy `--provider gemini` path)
- Modify: `amat/src/portfolio_team/remote_coordinator.py:44-70` (pass provider/model to `reserve_model_call`; use `response.raw_json` provider fields)
- Modify: `amat/src/portfolio_team/openai_runtime.py` (no behaviour change; `OpenAITransport` delegates to `llmrouter.transports.OpenAICompatibleTransport("responses")`)
- Modify: `amat/src/portfolio_team/cli.py:36-80` (`--provider chain`, `--chain PATH` default `configs/governance/llm-chain.toml`)
- Test: `amat/tests/unit/test_portfolio_team_models.py` (routed transport), `tests/unit/test_portfolio_team.py` (CLI wiring)

**Interfaces:**
- `class RoutedTransport(Transport)`: `__init__(self, router: Router, role: Role)`; `generate(request: ModelRequest) -> ModelResponse` converts to `ChatRequest(system=request.system_instruction, user=request.contents, schema_json=request.output_schema_json, schema_name="research_assessment", max_output_tokens=request.settings.max_output_tokens, timeout_seconds=request.settings.request_timeout_seconds, reasoning_effort=getattr(request.settings, "reasoning_effort", None))`, calls `router.complete(role, ...)`, and maps back: `ModelResponse(text, raw_json=json.dumps({"provider":..., "model":..., "attempts":[...], "response": json.loads(raw)}), input_tokens, output_tokens, error_code=(None if ok else error_code), retryable=False)`. Failover already happened, so `retryable` is False; `remote_phase` then raises `ModelRunError` for the exhausted chain, which the cycle records as `needs_operator_retry` (existing behaviour).
- `class ChainProvider(RemoteProvider)`: `id = f"chain/{chain_config.digest()}"`; `protocol()` includes the chain config so review identity captures provider/model routing (spec 4.7).
- Budget hook adapter `StoreBudget(store, review_id, role, attempt)` implementing `BudgetHook` by calling `reserve_model_call(..., provider=, model=)` and `finish_model_call` on settle. Because `remote_phase` already reserves before dispatch, the adapter's `reserve` records provider/model on the existing row (`UPDATE model_calls SET provider=?, model=? WHERE review_id=? AND role=? AND attempt=?`) instead of inserting a second reservation; document this in the adapter docstring.

- [ ] **Step 1: Write the failing tests**: routed transport returns the first ok provider's text and records provider/model on the `model_calls` row; a chain where OpenAI returns `quota` and Gemini returns ok yields a completed review with `provider == "gemini"` in the stored response artifact; CLI `--provider chain` builds a `ChainProvider` and `--chain missing.toml` errors clearly.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement** as specified. `ReviewTeam` stays for explicit `--chief-model` routing; when `--provider chain` is used, the chain's per-role targets replace it.

- [ ] **Step 4: Run the full AMAT gate**

Run: `cd amat && uv run --locked pytest -q && uv run --locked mypy src && uv run --locked ruff check src tests && uv run --locked ruff format --check src tests`
Expected: PASS (1,477+ tests).

- [ ] **Step 5: Update docs**: `docs/research/research-team-workflow.md` "Use it now" section gains the `--provider chain` form and a paragraph on failover behaviour and `pmteam budget`. `docs/portfolio-team-operations.md` budget section rewritten for the penalty rule.

- [ ] **Step 6: Commit (with approval)**

```bash
git add amat/src/portfolio_team amat/tests/unit/test_portfolio_team_models.py amat/tests/unit/test_portfolio_team.py amat/docs/research-team-workflow.md amat/docs/portfolio-team-operations.md
git commit -m "pmteam: route reviewer and chief calls through the llmrouter fallback chain"
```

---

### Task 10: Importer failures keep their traceback

**Files:**
- Modify: `amat/src/portfolio_team/evidence.py:158-163`, `amat/src/portfolio_team/automation.py:34-41`, `amat/src/portfolio_team/cli.py:22-34`
- Test: `amat/tests/unit/test_portfolio_team.py`

- [ ] **Step 1: Write the failing test**: monkeypatch `_import_run` to raise `KeyError("boom")`; `import_run` raises `EvidenceError` whose `.__cause__` is the `KeyError` and whose `.detail` (new attribute) contains `"KeyError: 'boom'"` and the innermost frame `file:line`; `automation.cycle` quarantine event payload includes `detail`.

- [ ] **Step 2: Implement**: `class EvidenceError(ValueError): def __init__(self, message: str, *, detail: str | None = None)`; in `import_run` wrap with `raise EvidenceError("unsupported or incomplete AMAT study envelope", detail=_summarise(exc)) from exc` where `_summarise` uses `traceback.extract_tb(exc.__traceback__)[-1]` for `file:line` plus `f"{type(exc).__name__}: {exc}"` truncated to 500 chars and with no environment values. Quarantine events and CLI stderr print `detail`.

- [ ] **Step 3: Run the pmteam tests, mypy, ruff.** Expected: PASS.

- [ ] **Step 4: Commit (with approval)**

```bash
git add amat/src/portfolio_team/evidence.py amat/src/portfolio_team/automation.py amat/src/portfolio_team/cli.py amat/tests/unit/test_portfolio_team.py
git commit -m "pmteam: keep importer failure detail so code regressions are not quarantined silently"
```

---

### Task 11: Learned-regime warm-up warning and `--start` help text

**Files:**
- Modify: `amat/src/amat/backtest/runner.py:741-765` (`_resolve_weight_scalars`) and the `warnings` list at `:882`
- Modify: `amat/src/amat/cli/main.py:1115-1138` (`_command_validate_config`) and the `--start` help strings on `study`, `walkforward`, `backtest` (grep `add_argument("--start"`)
- Test: `amat/tests/unit/test_learned_regime_warmup.py`

- [ ] **Step 1: Write the failing test**

```python
def test_learned_regime_shorter_warmup_records_an_explicit_warning(synthetic_panel_factory):
    # Use the repo's synthetic panel fixture (see tests/unit for `synthetic_panel` / `make_panel` helpers)
    strategy = load_strategy_config("configs/strategy/amat_v12_adaptive11.toml").strategy  # warmup 378 < fit_window 504
    result = run_backtest(strategy, panel, universe)
    assert any(w.startswith("learned_regime: inert for the first 126 measured sessions") for w in result.warnings)
```

and a CLI test asserting `amat validate-config configs/strategy/amat_v12_adaptive11.toml` prints a line starting with `Warning: learned_regime`. Locate the panel fixture used by existing runner tests (`grep -rn "def .*panel" tests/unit/conftest.py tests/unit/test_backtest*.py`) and reuse it; if the v12 config needs highs/lows/volume, use the fixture variant that supplies them or build the expectation with a minimal config that enables `learned_regime` with `fit_window_sessions=300` and `warmup.min_completed_sessions=252`.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**: in `run_backtest`, after `warnings: list[str] = []`, add

```python
    learned = strategy.learned_regime
    if learned.enabled and strategy.warmup.min_completed_sessions < learned.fit_window_sessions:
        inert = learned.fit_window_sessions - strategy.warmup.min_completed_sessions
        warnings.append(
            f"learned_regime: inert for the first {inert} measured sessions because "
            f"fit_window_sessions ({learned.fit_window_sessions}) exceeds "
            f"warmup.min_completed_sessions ({strategy.warmup.min_completed_sessions}); "
            f"the unknown_scalar {learned.unknown_scalar} applies until the first fit"
        )
```

Add a helper `learned_regime_warmup_warning(strategy) -> str | None` in `amat/config/models.py` next to `LearnedRegimeConfig` so the CLI and the runner share the text; `_command_validate_config` prints `Warning: <text>` when present. Change every `--start` help to: `"First session loaded (inclusive). Feature warm-up consumes the first warmup.min_completed_sessions loaded sessions, so measured performance begins later; read sample.first_session in the result for the measured start."`

- [ ] **Step 4: Run the full AMAT gate.** Expected: PASS.

- [ ] **Step 5: Commit (with approval)**

```bash
git add amat/src/amat/backtest/runner.py amat/src/amat/config/models.py amat/src/amat/cli/main.py amat/tests/unit/test_learned_regime_warmup.py
git commit -m "amat: warn when a learned regime layer is inert during warm-up; clarify --start"
```

---

### Task 12: Operator steps: authorize v6, rebuild the executor, verify

**Files:**
- Modify: `executor/.env` (operator-owned, untracked): add the six new variables.

- [ ] **Step 1: Compute the v6 digest**

Run: `cd amat && sha256sum configs/strategy/amat_v6.toml`
Expected: `2712f694227321e4...` (must match the `config_sha256` in the latest `executor` plan artifact; verify with `docker exec amat-executor grep -o '"config_sha256": "[a-f0-9]*' /var/lib/amat-executor/plans/$(docker exec amat-executor ls /var/lib/amat-executor/plans | tail -1)`).

- [ ] **Step 2: Add limits to `executor/.env`** (values proposed from the current account: equity about $101k, nine positions, 54% gross target):

```
AUTHORIZED_CONFIG_SHA256=<full v6 digest>
EXECUTOR_DISABLED=false
MAX_SESSION_NOTIONAL=80000
MAX_ORDERS_PER_SESSION=15
MAX_SYMBOL_NOTIONAL=30000
EQUITY_TOLERANCE=0.01
```

- [ ] **Step 3: Rebuild via the state-preserving path** described in `docs/executor/README.md` (snapshot the named volume, rebuild the image, recreate only the executor service), outside market hours. Then:

Run: `docker ps --filter name=amat-executor --format '{{.Status}}'` → healthy; `docker logs amat-executor --since 5m | grep -i "authorization_check\|ConfigurationError"` → one `authorization_check` with `authorized: true`; `docker exec amat-executor executor check` → plan built, nothing submitted.

- [ ] **Step 4: Negative rehearsal** (dry run only): temporarily set `EXECUTOR_DISABLED=true`, run `executor check`, confirm the journal shows `submission_refused` with the `EXECUTOR_DISABLED` reason, then restore `false` and recreate.

- [ ] **Step 5: Record the verification** in `docs/research/autoresearch-audit-and-plan-2026-09-10.md` under a new "Phase 0 verification (date)" heading: test counts, container status, digest authorized, negative rehearsal result.

---

## Self-review

**Spec coverage.** E1/E2/E3 → Tasks 1, 2. E4 → Task 3. E5 → Task 4. P1 → Task 8. P3 → Task 10. P4 → Task 6 Step 5 (live smoke). P5 → Task 6 (private import stays inside the Gemini transport only; noted, not eliminated, because the pinned SDK exposes no public equivalent). P2, P6, P7 → deferred to Phase 3 per the spec. A4/A5 → Task 11. Spec 4.7 fallback chain and triggers → Tasks 5, 6, 7, 9. Executor rebuild → Task 12. Git cleanup (Phase 0 item 1) → handled by the per-task commits plus a final operator-approved commit of the remaining Sep 7–10 work, listed in Task 12 Step 5's verification record.

**Placeholders.** Task 6 Step 1 has one elided test body (`...` for the responses-style refusal test) and Task 7/8/9 Step 1 describe tests in prose; the executing agent writes the full test code following the concrete examples in the same task. Chain model IDs are explicitly marked to be filled from the live listing in Task 6 Step 5 before Task 7 commits.

**Type consistency.** `RiskLimits` field names match between Tasks 1, 2 and the daemon fixture. `ChatRequest`/`ChatResponse`/`ErrorClass` names match across Tasks 5–9. `reserve_model_call(provider=, model=)` keywords match Tasks 8 and 9. `EvidenceError(detail=)` is defined in Task 10 and only used there.
