"""Parsing the plan artifact, and refusing the ones we must not interpret."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from executor.errors import PlanError
from executor.plan_source import parse_plan


def _plan_payload(**overrides: Any) -> dict[str, Any]:
    """A plan shaped exactly like `amat plan --json` emits."""
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "generated_at": "2026-08-11T13:20:00+00:00",
        "engine": "v1-frozen",
        "strategy_name": "amat-etf-regime-quality",
        "strategy_version": "6.0.0",
        "config_sha256": "2712f694227321e4" + "0" * 48,
        "snapshot_id": "alpaca-sip-2026-08-11",
        "appended_sessions": 0,
        "code_revision": "abc123",
        "as_of_session": "2026-08-10",
        "earliest_execution_session": "2026-08-11",
        "window_start_local": "09:30:00",
        "window_end_local": "09:35:00",
        "exchange_timezone": "America/New_York",
        "expires_at": "2026-08-14T13:35:00+00:00",
        "account_equity": "100000.00",
        "gross_target_exposure": 0.6399,
        "target_weights": {"SPY": 0.1234, "UUP": 0.15},
        "current_positions": {"SPY": "10"},
        "intents": [
            {
                "symbol": "SPY",
                "side": "buy",
                "quantity": "15.9684",
                "reference_price": "772.99",
                "notional": "12344.07",
                "current_weight": 0.0,
                "target_weight": 0.1234,
                "reason": "target_delta",
            }
        ],
        "withheld": [],
        "unrecognized_holdings": [],
        "caveats": ["PLAN ONLY"],
    }
    payload.update(overrides)
    return payload


def test_parses_a_well_formed_plan() -> None:
    plan = parse_plan(_plan_payload())

    assert plan.strategy_version == "6.0.0"
    assert plan.as_of_session == dt.date(2026, 8, 10)
    assert plan.earliest_execution_session == dt.date(2026, 8, 11)
    assert plan.account_equity == Decimal("100000.00")
    assert len(plan.intents) == 1
    assert plan.intents[0].symbol == "SPY"
    assert plan.intents[0].quantity == Decimal("15.9684")
    assert plan.current_positions == {"SPY": Decimal("10")}


@pytest.mark.parametrize("version", ["2.0.0", "0.9.0", "", "not-a-version"])
def test_refuses_an_unsupported_schema_major(version: str) -> None:
    """A renamed field that silently reads as None becomes a wrong position."""
    with pytest.raises(PlanError, match="not supported"):
        parse_plan(_plan_payload(schema_version=version))


def test_accepts_a_later_minor_of_the_same_major() -> None:
    """Additive changes within a major are compatible by construction."""
    plan = parse_plan(_plan_payload(schema_version="1.7.2"))
    assert plan.schema_version == "1.7.2"


def test_expiry_is_evaluated_against_an_aware_instant() -> None:
    plan = parse_plan(_plan_payload())

    assert not plan.is_expired(now=dt.datetime(2026, 8, 11, 13, 30, tzinfo=dt.UTC))
    assert plan.is_expired(now=dt.datetime(2026, 8, 15, 0, 0, tzinfo=dt.UTC))

    with pytest.raises(PlanError, match="aware instant"):
        plan.is_expired(now=dt.datetime(2026, 8, 11, 13, 30))  # noqa: DTZ001


def test_executability_is_on_or_after_not_equal_to() -> None:
    """The strategy names the earliest date; the executor resolves the session.

    A plan formed Friday for Monday must still execute if Monday is a holiday
    and the next real session is Tuesday.
    """
    plan = parse_plan(_plan_payload())

    assert not plan.is_executable_on(dt.date(2026, 8, 10))
    assert plan.is_executable_on(dt.date(2026, 8, 11))
    assert plan.is_executable_on(dt.date(2026, 8, 12))


def test_malformed_intent_is_an_error_not_a_skip() -> None:
    payload = _plan_payload()
    payload["intents"][0]["quantity"] = "not-a-number"

    with pytest.raises(PlanError):
        parse_plan(payload)


def test_unrecognized_holdings_survive_parsing() -> None:
    plan = parse_plan(_plan_payload(unrecognized_holdings=["TLT", "arkk"]))
    assert plan.unrecognized_holdings == ("TLT", "ARKK")
