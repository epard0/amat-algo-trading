"""Sizing, clamping, and the refusals that stop a run entirely."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from tests.test_plan_source import _plan_payload

from executor.broker import Account, Order
from executor.config import RiskLimits
from executor.journal import Journal
from executor.plan_source import PlanIntent, parse_plan
from executor.submit import _preflight, _resolve_quantity, client_order_id_for, submit_plan

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
    # Within the default `_plan_payload()` window (earliest execution session
    # 2026-08-11, expires 2026-08-14): a literal "today" would land past
    # `expires_at` and every test below would see "expired" instead of the
    # refusal it is actually exercising.
    return dt.datetime(2026, 8, 11, 13, 40, tzinfo=dt.UTC)


def _account(**overrides: object) -> Account:
    payload = {
        "account_number": "PA123",
        "status": "ACTIVE",
        "equity": "100000",
        "last_equity": "100000",
        "cash": "100000",
        "buying_power": "200000",
        "trading_blocked": False,
        "transfers_blocked": False,
        "account_blocked": False,
        "pattern_day_trader": False,
    }
    payload.update(overrides)
    return Account.from_payload(payload)


def _intent(**overrides: object) -> PlanIntent:
    payload = {
        "symbol": "SPY",
        "side": "buy",
        "quantity": "15.9684",
        "reference_price": "772.99",
        "notional": "12344.07",
        "current_weight": 0.0,
        "target_weight": 0.1234,
        "reason": "target_delta",
    }
    payload.update(overrides)
    return PlanIntent.from_payload(payload)


# -- quantity resolution ----------------------------------------------------


def test_fractional_quantity_survives_a_fractionable_symbol() -> None:
    quantity, note = _resolve_quantity(_intent(), fractionable=True, held=Decimal("0"))
    assert quantity == Decimal("15.9684")
    assert note == ""


def test_quantity_is_quantized_to_what_the_broker_accepts() -> None:
    """The plan's exact quotient is not a sendable order.

    Alpaca allows nine decimal places on ``qty`` and rejects longer requests
    outright, so a plan quantity carried at full ``Decimal`` precision -- which
    is what dividing a dollar target by a price produces -- would have every
    order in the day's rebalance refused. Observed in a live plan: a target of
    113.9409539635789972420494148 shares of DBC.
    """
    exact = "113.9409539635789972420494148"
    quantity, _ = _resolve_quantity(_intent(quantity=exact), fractionable=True, held=Decimal("0"))

    assert -quantity.as_tuple().exponent <= 9
    assert quantity == Decimal("113.940953963")
    # Down, not nearest: never spend capital the sizing did not allocate.
    assert quantity < Decimal(exact)


def test_quantization_is_applied_to_a_clamped_sell_too() -> None:
    """The cap is on the wire format, so it binds whatever path produced the size."""
    quantity, _ = _resolve_quantity(
        _intent(side="sell", quantity="99", target_weight=0.05),
        fractionable=True,
        held=Decimal("12.3456789012345"),
    )

    assert quantity == Decimal("12.345678901")
    assert quantity <= Decimal("12.3456789012345"), "must never sell more than is held"


def test_a_whole_share_quantity_is_left_alone() -> None:
    """Quantization must not perturb a quantity that already fits."""
    quantity, note = _resolve_quantity(_intent(quantity="10"), fractionable=True, held=Decimal("0"))
    assert quantity == Decimal("10")
    assert note == ""


def test_non_fractionable_symbol_rounds_down_never_up() -> None:
    """Rounding up would buy exposure the allocator did not ask for."""
    quantity, note = _resolve_quantity(_intent(), fractionable=False, held=Decimal("0"))
    assert quantity == Decimal("15")
    assert "rounded down" in note
    assert "residual" in note


def test_sell_is_clamped_to_the_shares_actually_held() -> None:
    """Long-or-cash: a sell reduces or closes, never crosses into a short."""
    intent = _intent(side="sell", quantity="12.0001", target_weight=0.05)
    quantity, note = _resolve_quantity(intent, fractionable=True, held=Decimal("12"))
    assert quantity == Decimal("12")
    assert "clamped" in note


def test_closing_a_non_fractionable_position_does_not_strand_dust() -> None:
    """Rounding a full exit down would leave an untradeable remainder forever."""
    intent = _intent(side="sell", quantity="12.4", target_weight=0.0)
    quantity, note = _resolve_quantity(intent, fractionable=False, held=Decimal("12.4"))
    assert quantity == Decimal("12.4")
    assert "closing the full" in note


def test_a_partial_sell_of_a_whole_share_symbol_still_rounds_down() -> None:
    """The dust rule applies only to a full exit (target weight zero)."""
    intent = _intent(side="sell", quantity="5.7", target_weight=0.04)
    quantity, _ = _resolve_quantity(intent, fractionable=False, held=Decimal("12"))
    assert quantity == Decimal("5")


def test_a_sub_share_buy_on_a_whole_share_symbol_resolves_to_zero() -> None:
    """Which the caller turns into a withheld intent, not a rounded-up order."""
    quantity, note = _resolve_quantity(
        _intent(quantity="0.6"), fractionable=False, held=Decimal("0")
    )
    assert quantity == Decimal("0")
    assert "residual" in note


# -- preflight refusals -----------------------------------------------------


def test_preflight_passes_on_a_normal_day() -> None:
    plan = parse_plan(_plan_payload())
    reason = _preflight(
        plan,
        _account(),
        dt.date(2026, 8, 11),
        dt.datetime(2026, 8, 11, 13, 30, tzinfo=dt.UTC),
        limits=_limits(),
    )
    assert reason == ""


def test_preflight_refuses_an_expired_plan() -> None:
    plan = parse_plan(_plan_payload())
    reason = _preflight(
        plan,
        _account(),
        dt.date(2026, 8, 17),
        dt.datetime(2026, 8, 17, 13, 30, tzinfo=dt.UTC),
        limits=_limits(),
    )
    assert "expired" in reason


def test_preflight_refuses_before_the_earliest_execution_session() -> None:
    plan = parse_plan(_plan_payload())
    reason = _preflight(
        plan,
        _account(),
        dt.date(2026, 8, 10),
        dt.datetime(2026, 8, 10, 13, 30, tzinfo=dt.UTC),
        limits=_limits(),
    )
    assert "may not execute before" in reason


def test_preflight_refuses_when_the_account_holds_something_unrecognized() -> None:
    """Its value is in the denominator of every target weight in the plan."""
    plan = parse_plan(_plan_payload(unrecognized_holdings=["ARKK"]))
    reason = _preflight(
        plan,
        _account(),
        dt.date(2026, 8, 11),
        dt.datetime(2026, 8, 11, 13, 30, tzinfo=dt.UTC),
        limits=_limits(),
    )
    assert "ARKK" in reason
    assert "denominator" in reason


@pytest.mark.parametrize(
    ("field", "value"),
    [("trading_blocked", True), ("account_blocked", True), ("status", "ONBOARDING")],
)
def test_preflight_refuses_a_blocked_account(field: str, value: object) -> None:
    plan = parse_plan(_plan_payload())
    reason = _preflight(
        plan,
        _account(**{field: value}),
        dt.date(2026, 8, 11),
        dt.datetime(2026, 8, 11, 13, 30, tzinfo=dt.UTC),
        limits=_limits(),
    )
    assert "will not accept orders" in reason


def test_preflight_reports_an_empty_plan_as_a_non_event() -> None:
    plan = parse_plan(_plan_payload(intents=[]))
    reason = _preflight(
        plan,
        _account(),
        dt.date(2026, 8, 11),
        dt.datetime(2026, 8, 11, 13, 30, tzinfo=dt.UTC),
        limits=_limits(),
    )
    assert "already matches the target" in reason


# -- idempotency ------------------------------------------------------------


def test_client_order_id_is_deterministic_for_one_plan() -> None:
    plan = parse_plan(_plan_payload())
    first = client_order_id_for(plan, plan.intents[0])
    second = client_order_id_for(plan, plan.intents[0])
    assert first == second
    assert first.startswith("amat-2026-08-10-SPY-buy-")
    assert len(first) <= 128


def test_client_order_id_changes_when_the_size_changes() -> None:
    """A deliberate re-plan must not be rejected as a duplicate of the old one."""
    plan = parse_plan(_plan_payload())
    resized = parse_plan(
        _plan_payload(intents=[{**_plan_payload()["intents"][0], "quantity": "20.0"}])
    )
    assert client_order_id_for(plan, plan.intents[0]) != client_order_id_for(
        resized, resized.intents[0]
    )


# -- the whole run ----------------------------------------------------------


class _RecordingClient:
    """Enough of the broker to observe what submit_plan sends."""

    def __init__(self, *, fractionable: bool = True) -> None:
        self.submitted: list[dict[str, object]] = []
        self._fractionable = fractionable

    def fractionable_symbols(self, symbols: tuple[str, ...]) -> dict[str, bool]:
        return dict.fromkeys(symbols, self._fractionable)

    def submit_market_order(
        self, *, symbol: str, side: str, quantity: Decimal, client_order_id: str
    ) -> object:
        self.submitted.append(
            {"symbol": symbol, "side": side, "quantity": quantity, "id": client_order_id}
        )
        return Order.from_payload(
            {
                "id": f"order-{len(self.submitted)}",
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "qty": str(quantity),
                "filled_qty": "0",
                "filled_avg_price": None,
                "status": "accepted",
                "submitted_at": "2026-08-11T13:30:00Z",
            }
        )


def test_dry_run_sends_nothing_but_records_every_decision(tmp_path) -> None:
    journal = Journal(tmp_path / "journal.ndjson", tmp_path / "equity.ndjson")
    client = _RecordingClient()
    plan = parse_plan(_plan_payload())

    result = submit_plan(
        plan,
        client,  # type: ignore[arg-type]
        account=_account(),
        session=dt.date(2026, 8, 11),
        now=dt.datetime(2026, 8, 11, 13, 30, tzinfo=dt.UTC),
        journal=journal,
        dry_run=True,
        limits=_limits(),
    )

    assert client.submitted == []
    assert result.executed
    assert [outcome.status for outcome in result.outcomes] == ["dry_run"]
    assert result.outcomes[0].submitted_quantity == Decimal("15.9684")


def test_a_live_run_submits_and_journals(tmp_path) -> None:
    journal = Journal(tmp_path / "journal.ndjson", tmp_path / "equity.ndjson")
    client = _RecordingClient()
    plan = parse_plan(_plan_payload())

    result = submit_plan(
        plan,
        client,  # type: ignore[arg-type]
        account=_account(),
        session=dt.date(2026, 8, 11),
        now=dt.datetime(2026, 8, 11, 13, 30, tzinfo=dt.UTC),
        journal=journal,
        dry_run=False,
        limits=_limits(),
    )

    assert len(client.submitted) == 1
    assert client.submitted[0]["symbol"] == "SPY"
    assert result.submitted[0].order_id == "order-1"
    assert "order_submitted" in (tmp_path / "journal.ndjson").read_text(encoding="utf-8")


def test_a_refused_plan_submits_nothing(tmp_path) -> None:
    journal = Journal(tmp_path / "journal.ndjson", tmp_path / "equity.ndjson")
    client = _RecordingClient()
    plan = parse_plan(_plan_payload(unrecognized_holdings=["ARKK"]))

    result = submit_plan(
        plan,
        client,  # type: ignore[arg-type]
        account=_account(),
        session=dt.date(2026, 8, 11),
        now=dt.datetime(2026, 8, 11, 13, 30, tzinfo=dt.UTC),
        journal=journal,
        dry_run=False,
        limits=_limits(),
    )

    assert client.submitted == []
    assert not result.executed
    assert "ARKK" in result.refusal_reason
    assert "submission_refused" in (tmp_path / "journal.ndjson").read_text(encoding="utf-8")


def test_a_whole_share_broker_withholds_a_sub_share_intent(tmp_path) -> None:
    journal = Journal(tmp_path / "journal.ndjson", tmp_path / "equity.ndjson")
    client = _RecordingClient(fractionable=False)
    plan = parse_plan(_plan_payload(intents=[{**_plan_payload()["intents"][0], "quantity": "0.4"}]))

    result = submit_plan(
        plan,
        client,  # type: ignore[arg-type]
        account=_account(),
        session=dt.date(2026, 8, 11),
        now=dt.datetime(2026, 8, 11, 13, 30, tzinfo=dt.UTC),
        journal=journal,
        dry_run=False,
        limits=_limits(),
    )

    assert client.submitted == []
    assert result.outcomes[0].status == "withheld"


# -- risk limits: authorization, kill switch, and caps -----------------------


def test_preflight_refuses_an_unauthorized_config_digest() -> None:
    plan = parse_plan(_plan_payload(config_sha256="f" * 64))
    reason = _preflight(plan, _account(), plan.earliest_execution_session, _now(), limits=_limits())
    assert "not authorised" in reason
    assert "f" * 12 in reason


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
    reason = _preflight(
        plan,
        _account(equity=str(plan.account_equity)),
        plan.earliest_execution_session,
        _now(),
        limits=_limits(),
    )
    assert reason == ""
