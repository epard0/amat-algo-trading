"""Mechanics checks for a paired exit diagnostic; no strategy-efficacy evidence."""

import datetime as dt
import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.positions import Position
from amat.domain.types import parse_symbol

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/run_exit_pair_foundation.py"
DATES = tuple(dt.date(2020, 1, 1) + dt.timedelta(days=3 * i) for i in range(12))
CUTOFF_INDEX = 3
CUTOFF = DATES[CUTOFF_INDEX]


@pytest.fixture(scope="module")
def driver():
    spec = importlib.util.spec_from_file_location("exit_pair_foundation_tested", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def synthetic_ledger(session, quantities):
    """Negative synthetic entries test filtering, not native short support."""
    return SimpleNamespace(
        session_date=session,
        cash=Decimal("100.001"),
        equity=Decimal("200.001"),
        positions={
            symbol: SimpleNamespace(quantity=Decimal(str(quantity)), mark=Decimal("10.0"))
            for symbol, quantity in quantities.items()
        },
    )


def test_selection_uses_positive_settled_holdings_at_cutoff_only(driver):
    policy, observations = driver.embargo_policy(DATES, CUTOFF)
    targets = {"AAA": 0.1, "BBB": 0.2, "CCC": 0.3, "ZZZ": 0.4}
    before = synthetic_ledger(DATES[2], {"AAA": 100})
    assert policy(targets, before, DATES[2], None) == targets
    assert observations == []

    # AAA is lexically first but flat, BBB is negative; choose actual positive CCC.
    ledger = synthetic_ledger(CUTOFF, {"ZZZ": 1, "CCC": 2, "AAA": 0, "BBB": -1})
    result = policy(targets, ledger, CUTOFF, {"future": "must not select the asset"})
    assert result == {"AAA": 0.1, "BBB": 0.2, "CCC": 0.0, "ZZZ": 0.4}
    assert observations[0]["selected_symbol"] == "CCC"
    assert observations[0]["settled_state"]["positions"] == {
        "CCC": {"quantity": "2", "mark": "10.0"},
        "ZZZ": {"quantity": "1", "mark": "10.0"},
    }


def test_five_decision_embargo_then_baseline_restoration_and_terminal(driver):
    policy, observations = driver.embargo_policy(DATES, CUTOFF)
    for index, session in enumerate(DATES):
        targets = {"AAA": 0.11 + index / 100, "ZZZ": 0.2}
        result = policy(targets, synthetic_ledger(session, {"AAA": 1}), session, None)
        expected = dict(targets)
        if CUTOFF_INDEX <= index < CUTOFF_INDEX + 5:
            expected["AAA"] = 0.0
        assert result == expected

    assert [row["session"] for row in observations] == [str(session) for session in DATES[3:10]]
    assert [row["phase"] for row in observations] == ["zero_target"] * 5 + [
        "baseline_restored",
        "baseline_restored",
    ]
    assert observations[-1]["result_targets"]["AAA"] == 0.2


def test_selection_stays_fixed_after_holdings_change(driver):
    policy, observations = driver.embargo_policy(DATES, CUTOFF)
    targets = {"AAA": 0.3, "BBB": 0.4, "ZZZ": 0.1}
    policy(targets, synthetic_ledger(CUTOFF, {"BBB": 2, "ZZZ": 1}), CUTOFF, None)
    next_session = DATES[CUTOFF_INDEX + 1]
    result = policy(
        targets,
        synthetic_ledger(next_session, {"AAA": 10, "ZZZ": 3}),
        next_session,
        None,
    )
    assert result == {"AAA": 0.3, "BBB": 0.0, "ZZZ": 0.1}
    assert [row["selected_symbol"] for row in observations] == ["BBB", "BBB"]


def test_zero_baseline_target_is_a_valid_natural_exit(driver):
    policy, observations = driver.embargo_policy(DATES, CUTOFF)
    targets = {"AAA": 0.0, "ZZZ": 0.7}
    assert policy(targets, synthetic_ledger(CUTOFF, {"AAA": 5}), CUTOFF, None) == targets
    assert observations[0]["baseline_targets"]["AAA"] == 0.0
    assert observations[0]["selected_symbol"] == "AAA"


@pytest.mark.parametrize("holdings", [{}, {"AAA": 0}, {"AAA": 0, "BBB": -2}])
def test_empty_positive_holdings_fail_without_replacement_date(driver, holdings):
    policy, observations = driver.embargo_policy(DATES, CUTOFF)
    with pytest.raises(ValueError, match="no actual positive holding; no replacement date"):
        policy({"AAA": 0.5}, synthetic_ledger(CUTOFF, holdings), CUTOFF, None)
    later = DATES[CUTOFF_INDEX + 1]
    with pytest.raises(ValueError, match="Missing fixed held asset"):
        policy({"AAA": 0.5}, synthetic_ledger(later, {"AAA": 2}), later, None)
    assert observations == []


@pytest.mark.parametrize("offset", [0, 1, 4])
def test_missing_selected_target_fails_closed_during_embargo(driver, offset):
    policy, _ = driver.embargo_policy(DATES, CUTOFF)
    if offset:
        policy({"AAA": 0.5}, synthetic_ledger(CUTOFF, {"AAA": 1}), CUTOFF, None)
    session = DATES[CUTOFF_INDEX + offset]
    with pytest.raises(ValueError, match="Missing fixed held asset or policy target"):
        policy({"ZZZ": 0.5}, synthetic_ledger(session, {"AAA": 1}), session, None)


@pytest.mark.parametrize("remaining", [0, 1, 4, 5])
def test_terminal_requires_six_sessions_after_cutoff(driver, remaining):
    with pytest.raises(ValueError, match="Complete terminal settlement session required"):
        driver.embargo_policy(DATES[: CUTOFF_INDEX + remaining + 1], CUTOFF)


def test_exact_terminal_session_is_sufficient(driver):
    policy, _ = driver.embargo_policy(DATES[: CUTOFF_INDEX + 7], CUTOFF)
    assert callable(policy)


def test_targets_are_not_mutated_and_observations_copy_each_mapping(driver):
    policy, observations = driver.embargo_policy(DATES, CUTOFF)
    mutable = {"AAA": 0.35, "ZZZ": 0.15}
    targets = MappingProxyType(mutable)
    result = policy(targets, synthetic_ledger(CUTOFF, {"AAA": 1}), CUTOFF, None)
    assert mutable == {"AAA": 0.35, "ZZZ": 0.15}
    result["AAA"] = 0.9
    mutable["AAA"] = 0.8
    assert observations[0]["baseline_targets"] == {"AAA": 0.35, "ZZZ": 0.15}
    assert observations[0]["result_targets"] == {"AAA": 0.0, "ZZZ": 0.15}


@pytest.mark.parametrize("with_context", [False, True])
@pytest.mark.parametrize("with_opens", [False, True])
def test_prefix_preserves_all_history_and_optional_observed_fields(
    driver, with_context, with_opens
):
    names = ("adjusted_closes", "raw_closes", "window_vwaps", "window_volumes")
    arrays = {
        name: np.arange(24, dtype=float).reshape(12, 2) + (index + 1) * 100
        for index, name in enumerate(names)
    }
    optional = {
        name: np.full((12, 2), value, dtype=float) if supplied else None
        for name, value, supplied in (
            ("adjusted_highs", 999, with_context),
            ("adjusted_lows", 111, with_context),
            ("session_volumes", 12345, with_context),
            ("adjusted_opens", 555, with_opens),
        )
    }
    panel = MarketPanel(
        sessions=DATES,
        symbols=(parse_symbol("AAA"), parse_symbol("BBB")),
        source="synthetic-observed-fields",
        **arrays,
        **optional,
    )
    last = CUTOFF_INDEX + 6
    prefix = driver.prefix_panel(panel, last)
    assert prefix.sessions == DATES[: last + 1]
    assert prefix.sessions[0] == DATES[0]  # Keep seeding, rather than restarting at cutoff.
    assert prefix.symbols == panel.symbols
    assert prefix.source == panel.source
    assert panel.sessions == DATES
    for name in (*names, *optional):
        original = getattr(panel, name)
        actual = getattr(prefix, name)
        if original is None:
            assert actual is None
        else:
            np.testing.assert_array_equal(actual, original[: last + 1])
            assert actual.shape == (last + 1, 2)
            assert original.shape == (12, 2)


def test_ledger_serialization_keeps_decimal_precision_and_positive_positions(driver):
    aaa, zzz = parse_symbol("AAA"), parse_symbol("ZZZ")
    cash = Decimal("9007199254740993.000000000001")
    quantity = Decimal("3.000000000007")
    mark = Decimal("0.123456789123")
    ledger = LedgerSnapshot(
        session_date=CUTOFF,
        cash=cash,
        positions={
            zzz: Position(zzz, Decimal("0"), Decimal("9.1"), CUTOFF),
            aaa: Position(aaa, quantity, mark, CUTOFF),
        },
    )
    actual = driver.ledger_state(ledger)
    assert actual == {
        "session": str(CUTOFF),
        "cash": str(cash),
        "equity": str(cash + quantity * mark),
        "positions": {"AAA": {"quantity": str(quantity), "mark": str(mark)}},
    }
    assert Decimal(actual["cash"]) == cash
    assert Decimal(actual["equity"]) == ledger.equity
