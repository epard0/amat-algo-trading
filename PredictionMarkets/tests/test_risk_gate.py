from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from execution.risk_gate import (
    check_candle_freshness,
    check_capital_breaker,
    check_executable_edge_ceiling,
    check_model_market_divergence,
    check_quote_sanity,
    check_settlement_margin,
    expected_value_per_contract,
    late_entry_thresholds,
    consecutive_losses,
    kelly_position_fraction,
    market_implied_probability,
)


def test_divergence_gate_blocks_the_live_eth_scenario() -> None:
    """The 2026-07-28 log: model said P(NO)=75%, the book priced NO at 13c.

    That is a data fault, not a 59-point edge, and it must not reach an order.
    """
    result = check_model_market_divergence(
        model_probability_yes=Decimal("0.25"),
        yes_bid=Decimal("0.86"),
        yes_ask=Decimal("0.88"),
        maximum_divergence=Decimal("0.25"),
    )

    assert result.allowed is False
    assert "disagrees with market" in result.reason


def test_divergence_gate_allows_a_plausible_edge() -> None:
    result = check_model_market_divergence(
        model_probability_yes=Decimal("0.70"),
        yes_bid=Decimal("0.58"),
        yes_ask=Decimal("0.62"),
        maximum_divergence=Decimal("0.25"),
    )

    assert result.allowed is True


def test_market_implied_probability_handles_one_sided_books() -> None:
    assert market_implied_probability(Decimal("0.40"), Decimal("0.60")) == Decimal("0.5")
    assert market_implied_probability(Decimal("0"), Decimal("0.60")) == Decimal("0.60")
    assert market_implied_probability(Decimal("0"), Decimal("0")) == Decimal("0.5")


def test_stale_candles_are_rejected() -> None:
    now = datetime(2026, 7, 28, 22, 42, tzinfo=timezone.utc)

    fresh = check_candle_freshness(now - timedelta(seconds=45), now, 120)
    stale = check_candle_freshness(now - timedelta(seconds=400), now, 120)

    assert fresh.allowed is True
    assert stale.allowed is False
    assert "400s old" in stale.reason


def test_kelly_scales_with_edge_and_respects_cap() -> None:
    cap = Decimal("0.20")
    small = kelly_position_fraction(Decimal("0.66"), Decimal("0.60"), Decimal("0.25"), cap)
    large = kelly_position_fraction(Decimal("0.90"), Decimal("0.60"), Decimal("0.25"), cap)
    negative = kelly_position_fraction(Decimal("0.50"), Decimal("0.60"), Decimal("0.25"), cap)

    assert small < large <= cap
    assert negative == Decimal("0")
    # A zero multiplier keeps the old flat-fraction behaviour.
    assert kelly_position_fraction(
        Decimal("0.70"), Decimal("0.60"), Decimal("0"), cap
    ) == cap


def test_consecutive_losses_stops_at_first_non_loss() -> None:
    assert consecutive_losses(["LOST", "LOST", "WON", "LOST"]) == 2
    assert consecutive_losses(["WON", "LOST"]) == 0
    assert consecutive_losses([]) == 0


def test_breaker_halts_on_losing_streak() -> None:
    result = check_capital_breaker(
        current_balance=Decimal("18"),
        peak_balance=Decimal("20"),
        settled_statuses=["LOST", "LOST", "LOST", "LOST"],
        maximum_consecutive_losses=4,
        maximum_drawdown_fraction=Decimal("0.30"),
    )

    assert result.allowed is False
    assert "consecutive losses" in result.reason


def test_breaker_halts_on_drawdown_from_peak() -> None:
    result = check_capital_breaker(
        current_balance=Decimal("13"),
        peak_balance=Decimal("20"),
        settled_statuses=["WON", "LOST"],
        maximum_consecutive_losses=4,
        maximum_drawdown_fraction=Decimal("0.30"),
    )

    assert result.allowed is False
    assert "drawdown" in result.reason


def test_breaker_allows_a_healthy_ledger() -> None:
    result = check_capital_breaker(
        current_balance=Decimal("22"),
        peak_balance=Decimal("22"),
        settled_statuses=["WON", "LOST", "WON"],
        maximum_consecutive_losses=4,
        maximum_drawdown_fraction=Decimal("0.30"),
    )

    assert result.allowed is True


# ---------------------------------------------------------------------------
# Execution-time plausibility guards.
#
# Every scenario below is taken from a real live trade. The four losers in the
# first 13 live trades all reached an order because nothing checked the
# pre-order book re-read against the snapshot that justified the candidate.
# ---------------------------------------------------------------------------


def test_edge_ceiling_blocks_the_sol_51_point_edge() -> None:
    """2026-08-02 SOL: snapshot NO at 59c, re-read came back 14c.

    A 51-point edge on a market that was fairly priced seconds earlier is a bad
    book read. It lost the full stake, and Kelly had sized it 4x normal.
    """
    result = check_executable_edge_ceiling(Decimal("0.509"), Decimal("0.15"))

    assert result.allowed is False
    assert "data fault" in result.reason


def test_edge_ceiling_admits_every_winning_live_edge() -> None:
    """The ten winners ranged 0.054-0.139; none may be blocked."""
    for edge in ("0.054", "0.055", "0.092", "0.098", "0.112",
                 "0.118", "0.120", "0.135", "0.139"):
        assert check_executable_edge_ceiling(Decimal(edge), Decimal("0.15")).allowed


def test_edge_ceiling_blocks_every_losing_live_edge() -> None:
    """The four losers ranged 0.193-0.509; none may be admitted."""
    for edge in ("0.193", "0.283", "0.284", "0.509"):
        assert not check_executable_edge_ceiling(Decimal(edge), Decimal("0.15")).allowed


def test_edge_ceiling_is_inclusive_at_the_boundary() -> None:
    assert check_executable_edge_ceiling(Decimal("0.15"), Decimal("0.15")).allowed


def test_edge_ceiling_disabled_by_zero() -> None:
    assert check_executable_edge_ceiling(Decimal("0.99"), Decimal("0")).allowed


def test_quote_sanity_blocks_implausible_improvement() -> None:
    """Snapshot 0.59, re-read 0.14: a 45c gift a thin book does not give."""
    result = check_quote_sanity(Decimal("0.59"), Decimal("0.14"), Decimal("0.10"))

    assert result.allowed is False
    assert "implausible" in result.reason


def test_quote_sanity_allows_adverse_moves() -> None:
    """A price that moved against us is normal and handled by the edge gate.

    Only improvement is bounded; bounding both would reject ordinary drift.
    """
    assert check_quote_sanity(Decimal("0.50"), Decimal("0.62"), Decimal("0.10")).allowed


def test_quote_sanity_allows_small_improvement() -> None:
    assert check_quote_sanity(Decimal("0.60"), Decimal("0.55"), Decimal("0.10")).allowed


def test_quote_sanity_boundary_is_inclusive() -> None:
    assert check_quote_sanity(Decimal("0.60"), Decimal("0.50"), Decimal("0.10")).allowed
    assert not check_quote_sanity(
        Decimal("0.60"), Decimal("0.49"), Decimal("0.10")
    ).allowed


def test_quote_sanity_disabled_by_zero() -> None:
    assert check_quote_sanity(Decimal("0.90"), Decimal("0.05"), Decimal("0")).allowed


def test_kelly_caps_the_edge_it_sizes_on() -> None:
    """The 13.58-contract SOL position must shrink to roughly a quarter.

    Kelly is optimal only when handed a correct probability; given an
    overstated edge it overbets in proportion to the error.
    """
    uncapped = kelly_position_fraction(
        probability=Decimal("0.679"), price=Decimal("0.15"),
        kelly_multiplier=Decimal("0.25"), cap=Decimal("0.20"),
    )
    capped = kelly_position_fraction(
        probability=Decimal("0.679"), price=Decimal("0.15"),
        kelly_multiplier=Decimal("0.25"), cap=Decimal("0.20"),
        maximum_edge=Decimal("0.15"),
    )

    assert capped < uncapped
    # (0.15 / 0.85) * 0.25
    assert capped == (Decimal("0.15") / Decimal("0.85")) * Decimal("0.25")


def test_kelly_cap_leaves_ordinary_edges_untouched() -> None:
    """A normal 10-point edge must size exactly as it did before."""
    args = dict(
        probability=Decimal("0.70"), price=Decimal("0.60"),
        kelly_multiplier=Decimal("0.25"), cap=Decimal("0.20"),
    )
    assert kelly_position_fraction(**args) == kelly_position_fraction(
        **args, maximum_edge=Decimal("0.15")
    )


def test_kelly_cap_never_increases_size() -> None:
    for probability, price in (("0.65", "0.55"), ("0.90", "0.20"), ("0.99", "0.05")):
        args = dict(
            probability=Decimal(probability), price=Decimal(price),
            kelly_multiplier=Decimal("0.25"), cap=Decimal("0.20"),
        )
        assert kelly_position_fraction(
            **args, maximum_edge=Decimal("0.15")
        ) <= kelly_position_fraction(**args)


def test_kelly_cap_still_declines_a_negative_edge() -> None:
    assert kelly_position_fraction(
        probability=Decimal("0.40"), price=Decimal("0.60"),
        kelly_multiplier=Decimal("0.25"), cap=Decimal("0.20"),
        maximum_edge=Decimal("0.15"),
    ) == Decimal("0")


# ---------------------------------------------------------------------------
# Late-market entry: take small, high-confidence edges near expiry, because the
# terminal distribution is tight and the fee at the price extremes is small.
# ---------------------------------------------------------------------------

LATE_ARGS = dict(
    late_window_seconds=60,
    min_probability=Decimal("0.62"),
    min_edge=Decimal("0.05"),
    late_min_probability=Decimal("0.85"),
    late_min_edge=Decimal("0.02"),
)


def test_outside_the_late_window_uses_standing_floors() -> None:
    probability, edge, is_late = late_entry_thresholds(120, **LATE_ARGS)

    assert is_late is False
    assert probability == Decimal("0.62")
    assert edge == Decimal("0.05")


def test_inside_the_late_window_trades_edge_for_probability() -> None:
    """The window is stricter on probability and looser on edge, not looser overall."""
    probability, edge, is_late = late_entry_thresholds(30, **LATE_ARGS)

    assert is_late is True
    assert probability == Decimal("0.85")   # raised
    assert edge == Decimal("0.02")          # lowered


def test_late_window_boundary_is_inclusive() -> None:
    assert late_entry_thresholds(60, **LATE_ARGS)[2] is True
    assert late_entry_thresholds(61, **LATE_ARGS)[2] is False


def test_late_window_never_lowers_the_probability_floor() -> None:
    """A late floor below the standing one must not weaken the standing rule."""
    args = {**LATE_ARGS, "late_min_probability": Decimal("0.50")}
    probability, _, _ = late_entry_thresholds(30, **args)

    assert probability == Decimal("0.62")


def test_late_window_never_raises_the_edge_floor() -> None:
    args = {**LATE_ARGS, "late_min_edge": Decimal("0.40")}
    _, edge, _ = late_entry_thresholds(30, **args)

    assert edge == Decimal("0.05")


def test_late_window_disabled_by_zero_seconds() -> None:
    args = {**LATE_ARGS, "late_window_seconds": 0}
    assert late_entry_thresholds(5, **args)[2] is False


def test_settlement_margin_rejects_a_coin_flip_near_the_strike() -> None:
    """A high probability a hair from the strike is decided by index choice."""
    result = check_settlement_margin(Decimal("0.4"), Decimal("1.50"))

    assert result.allowed is False
    assert "index disagreement" in result.reason


def test_settlement_margin_admits_a_comfortable_distance() -> None:
    assert check_settlement_margin(Decimal("2.8"), Decimal("1.50")).allowed


def test_settlement_margin_is_direction_agnostic() -> None:
    """A NO trade sits below the strike; its z is negative and equally safe."""
    assert check_settlement_margin(Decimal("-2.8"), Decimal("1.50")).allowed
    assert not check_settlement_margin(Decimal("-0.4"), Decimal("1.50")).allowed


def test_settlement_margin_boundary_is_inclusive() -> None:
    assert check_settlement_margin(Decimal("1.50"), Decimal("1.50")).allowed
    assert not check_settlement_margin(Decimal("1.49"), Decimal("1.50")).allowed


def test_settlement_margin_disabled_by_zero() -> None:
    assert check_settlement_margin(Decimal("0.01"), Decimal("0")).allowed


def test_fee_curve_makes_small_edges_viable_only_near_the_extremes() -> None:
    """Kalshi's taker fee is 0.07*C*P*(1-P) and peaks at a 50c price.

    A 1.5-point edge is profitable on a 90c contract and loss-making on a 50c
    one, purely because of where the fee curve sits. That asymmetry is the
    reason the late window can afford a lower edge floor: it pairs it with a
    high probability floor, which puts the price near the cheap end.
    """
    fee_at_90 = Decimal("0.07") * Decimal("0.90") * Decimal("0.10")   # 0.0063
    fee_at_50 = Decimal("0.07") * Decimal("0.50") * Decimal("0.50")   # 0.0175
    assert fee_at_50 > fee_at_90 * 2   # the curve really is that lopsided

    assert expected_value_per_contract(
        Decimal("0.915"), Decimal("0.90"), fee_at_90
    ) > 0
    assert expected_value_per_contract(
        Decimal("0.515"), Decimal("0.50"), fee_at_50
    ) < 0


def test_late_min_edge_default_is_ev_positive_even_at_the_worst_price() -> None:
    """The 2% default must clear the fee at its most expensive point.

    The edge the gate sees is already net of the fee reserve and slippage
    buffers, so this is a floor on top of those — but it should still stand up
    on its own at a 50c price, which is where the fee peaks.
    """
    worst_fee = Decimal("0.07") * Decimal("0.50") * Decimal("0.50")
    assert expected_value_per_contract(
        Decimal("0.52"), Decimal("0.50"), worst_fee
    ) > 0


def test_expected_value_is_negative_when_price_exceeds_probability() -> None:
    assert expected_value_per_contract(
        Decimal("0.60"), Decimal("0.65"), Decimal("0.01")
    ) < 0


# ---------------------------------------------------------------------------
# Config defaults must not drift between the two construction paths.
#
# KalshiSettings can be built directly (tests, ad-hoc callers) or through
# from_env (the live bot). Editing one default and not the other produces a
# silent split: every test asserts one threshold while real money runs another.
# This actually happened to both MAX_EXECUTABLE_EDGE and MAX_CONSECUTIVE_LOSSES.
# ---------------------------------------------------------------------------

# dataclass field -> environment variable it is read from in from_env.
SAFETY_DEFAULT_PAIRS = {
    "trade_budget_usd": "TRADE_BUDGET_USD",
    "strategy_lab_balance_usd": "STRATEGY_LAB_BALANCE_USD",
    "max_edge": "MAX_EXECUTABLE_EDGE",
    "max_quote_improvement": "MAX_QUOTE_IMPROVEMENT",
    "max_model_market_divergence": "MAX_MODEL_MARKET_DIVERGENCE",
    "kelly_fraction": "KELLY_FRACTION",
    "max_consecutive_losses": "MAX_CONSECUTIVE_LOSSES",
    "max_drawdown_fraction": "MAX_DRAWDOWN_FRACTION",
    "max_candle_age_seconds": "MAX_CANDLE_AGE_SECONDS",
    "order_failure_cooldown_seconds": "ORDER_FAILURE_COOLDOWN_SECONDS",
    "late_entry_seconds": "LATE_ENTRY_SECONDS",
    "late_min_probability": "LATE_MIN_PROBABILITY",
    "late_min_edge": "LATE_MIN_EDGE",
    "late_min_settlement_sigma": "LATE_MIN_SETTLEMENT_SIGMA",
}


def _env_default(source: str, variable: str) -> str | None:
    """The literal default from _decimal_env / os.getenv for one variable."""
    import re

    match = re.search(
        rf'(?:_decimal_env|os\.getenv)\(\s*"{variable}",\s*"([^"]+)"', source
    )
    return match.group(1) if match else None


@pytest.mark.parametrize("field, variable", sorted(SAFETY_DEFAULT_PAIRS.items()))
def test_dataclass_default_matches_env_default(field: str, variable: str) -> None:
    from pathlib import Path as _Path

    from config import KalshiSettings

    source = (_Path(__file__).resolve().parent.parent / "config.py").read_text()
    raw = _env_default(source, variable)
    if raw is None:
        pytest.skip(f"{variable} has no literal default in from_env")

    declared = KalshiSettings.__dataclass_fields__[field].default
    expected = type(declared)(raw) if not isinstance(declared, Decimal) else Decimal(raw)

    assert declared == expected, (
        f"{field}={declared} but {variable} defaults to {raw}. "
        "A caller that builds KalshiSettings directly would run a different "
        "threshold than the live bot."
    )


@pytest.mark.parametrize("field, variable", sorted(SAFETY_DEFAULT_PAIRS.items()))
def test_env_example_matches_the_code_default(field: str, variable: str) -> None:
    """.env.example is the file operators copy to .env.

    A stale value there silently overrides the code default on every new
    deployment — and for MAX_MODEL_MARKET_DIVERGENCE it had been shipping a
    value twice as loose as the running gate, which re-opens
    the exact failure that motivated the gate.
    """
    import re
    from pathlib import Path as _Path

    from config import KalshiSettings

    example = (_Path(__file__).resolve().parent.parent / ".env.example")
    if not example.is_file():
        pytest.skip("no .env.example")
    match = re.search(rf"^{variable}=(.+)$", example.read_text(), re.MULTILINE)
    if match is None:
        pytest.skip(f"{variable} is not set in .env.example")

    declared = KalshiSettings.__dataclass_fields__[field].default
    raw = match.group(1).strip()
    expected = Decimal(raw) if isinstance(declared, Decimal) else type(declared)(raw)

    assert declared == expected, (
        f".env.example sets {variable}={raw} but the code default is {declared}"
    )


def test_every_setting_the_code_reads_is_in_env_example() -> None:
    """An undocumented setting is one nobody knows to change.

    .env.example is the only place most operators will ever see the knobs, and
    a new setting added to config.py without a line here is invisible until
    someone reads the source.
    """
    import re
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    source = (root / "config.py").read_text()
    read_by_code = set()
    for pattern in (r'os\.getenv\("([A-Z0-9_]+)"',
                    r'_bool_env\("([A-Z0-9_]+)"',
                    r'_decimal_env\("([A-Z0-9_]+)"'):
        read_by_code |= set(re.findall(pattern, source))

    documented = set(re.findall(r"^([A-Z0-9_]+)=", (root / ".env.example").read_text(),
                                re.MULTILINE))

    assert not read_by_code - documented, (
        f"undocumented settings: {sorted(read_by_code - documented)}"
    )
