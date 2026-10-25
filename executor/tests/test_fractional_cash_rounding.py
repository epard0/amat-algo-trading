"""Regression from the October 21 broker fill/cash evidence, with IDs removed."""

import json
from decimal import ROUND_UP, Decimal, localcontext
from pathlib import Path

import pytest

from executor.portfolio_monitor import _effect, reconcile


def test_recorded_fractional_fills_reconcile_without_rebasing():
    evidence = json.loads(
        (Path(__file__).parent / "fixtures" / "fractional_cash_20261021.json").read_text()
    )
    baseline = {
        "snapshot": {
            "account_hash": "paper-account",
            "cash": evidence["baseline_cash"],
            "positions": evidence["baseline_positions"],
        },
        "activities": {},
    }
    before = json.dumps(baseline, sort_keys=True)
    observed = {
        "account_hash": "paper-account",
        "cash": evidence["observed_cash"],
        "positions": evidence["observed_positions"],
    }
    rows = {row["id"]: row for row in evidence["activities"]}
    result = reconcile(baseline, observed, rows)
    assert result["status"] == "reconciled"
    assert result["exact_match"] is True
    assert result["expected_cash"] == "20764.65"
    assert Decimal(result["fill_cash_rounding_adjustment"]) == Decimal("-0.01644815129")
    assert json.dumps(baseline, sort_keys=True) == before
    assert reconcile(baseline, {**observed, "cash": "20764.63"}, rows)["status"] == "discrepancy"


@pytest.mark.parametrize(
    ("side", "price", "expected"),
    [
        ("buy", "2.025", "-2.02"),
        ("buy", "3.035", "-3.04"),
        ("sell", "2.025", "2.02"),
        ("sell", "3.035", "3.04"),
    ],
)
def test_fill_cash_rounding_uses_explicit_ties_to_even(side, price, expected):
    row = {"activity_type": "FILL", "symbol": "SPY", "side": side, "qty": "1", "price": price}
    with localcontext() as context:
        context.rounding = ROUND_UP
        assert _effect(row)[2] == Decimal(expected)


def test_independent_fills_round_independently_and_posted_fees_are_unchanged():
    row = {"activity_type": "FILL", "symbol": "SPY", "side": "buy", "qty": "0.006", "price": "1"}
    assert sum(_effect(row)[2] for _ in range(2)) == Decimal("-0.02")
    assert _effect({"activity_type": "FEE", "net_amount": "-0.01"})[2] == Decimal("-0.01")
