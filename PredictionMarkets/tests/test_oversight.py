"""Confidence-scaled sizing and exposure-scaled oversight.

Two rules with one thing in common: both are new levers on real money, so what
is tested hardest here is not that they work but that they are **inert at their
defaults** and **bounded when switched on**. This project has shipped five
features that were unreachable and two that quietly did the wrong thing with a
position; the tests below are aimed squarely at both failure modes.

The separation between the two rules is itself an invariant and is asserted:
oversight may change *when* and *how often* the salvage rule is asked, never the
price at which it acts. That price is the exit-on-reversal boundary.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from config import KalshiSettings
from core.strategy_brain.strategies.orderbook import analyse
from core.strategy_brain.strategies.oversight import (
    exposure_fraction,
    plan_oversight,
)
from execution.kalshi_client import (
    AsyncKalshiTradingClient,
    BookQuote,
    KalshiMarket,
)
from execution.position_book import PositionBook
from execution.risk_gate import confidence_multiplier
from execution.salvage_exit import SalvageExit
from execution.trade_ledger import TradeLedger

D = Decimal
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------ pure: sizing --


def multiplier(probability: str, gain: str = "2.0") -> Decimal:
    return confidence_multiplier(
        probability=D(probability),
        floor=D("0.60"),
        full=D("0.90"),
        gain=D(gain),
    )


def test_a_gain_of_one_is_exactly_inert_at_every_probability():
    """The default must not perturb sizing at all, not merely approximately."""
    for probability in ("0.01", "0.50", "0.62", "0.85", "0.99", "1.0"):
        assert multiplier(probability, gain="1.0") == D("1")


def test_nothing_is_added_below_the_floor():
    assert multiplier("0.50") == D("1")
    assert multiplier("0.60") == D("1")


def test_the_full_gain_applies_at_and_above_the_ceiling():
    assert multiplier("0.90") == D("2.0")
    assert multiplier("0.99") == D("2.0")


def test_it_interpolates_and_never_decreases():
    """Monotone: a more confident read can never be funded less."""
    values = [multiplier(p) for p in ("0.60", "0.70", "0.75", "0.80", "0.90")]
    assert values == sorted(values)
    # The midpoint of the band earns exactly half the extra.
    assert multiplier("0.75") == D("1.5")


def test_a_degenerate_band_does_not_divide_by_zero():
    assert confidence_multiplier(
        probability=D("0.95"), floor=D("0.9"), full=D("0.9"), gain=D("2")
    ) == D("2")
    assert confidence_multiplier(
        probability=D("0.5"), floor=D("0.9"), full=D("0.9"), gain=D("2")
    ) == D("1")


# --------------------------------------------------------- pure: oversight --


def test_an_unknown_portfolio_reads_as_fully_exposed_not_as_safe():
    """The dangerous default is 'risking nothing'; refuse it deliberately."""
    assert exposure_fraction(position_value=D("5"), portfolio_value=D("0")) == D("1")
    assert exposure_fraction(position_value=D("5"), portfolio_value=D("-1")) == D("1")


def test_exposure_is_a_share_and_is_clamped_to_one():
    assert exposure_fraction(position_value=D("25"), portfolio_value=D("100")) == D(
        "0.25"
    )
    assert exposure_fraction(position_value=D("500"), portfolio_value=D("100")) == D(
        "1"
    )
    assert exposure_fraction(position_value=D("0"), portfolio_value=D("100")) == D("0")


def plan(position: str, portfolio: str = "100"):
    return plan_oversight(
        position_value=D(position),
        portfolio_value=D(portfolio),
        base_window_seconds=30,
        max_window_seconds=300,
        base_checks=1,
        max_checks=4,
        min_spacing_seconds=20,
        full_attention_exposure=D("0.25"),
        base_max_bid=D("0.15"),
        shadow_max_bid=D("0.35"),
    )


def test_zero_exposure_returns_the_base_settings_untouched():
    """What makes this safe to leave switched on."""
    p = plan("0")
    assert p.window_seconds == 30
    assert p.checks == 1
    assert p.shadow_max_bid == D("0.15")


def test_more_exposure_buys_a_wider_window_and_more_checks():
    small, large = plan("2"), plan("25")
    assert small.window_seconds < large.window_seconds
    assert small.checks < large.checks
    assert large.window_seconds == 300
    assert large.checks == 4


def test_attention_is_bounded_beyond_the_full_exposure_threshold():
    """An outsized position cannot demand unbounded supervision."""
    at_threshold, beyond = plan("25"), plan("90")
    assert beyond.window_seconds == at_threshold.window_seconds
    assert beyond.checks == at_threshold.checks
    assert beyond.shadow_max_bid == at_threshold.shadow_max_bid


def test_the_window_never_shrinks_below_the_base():
    p = plan_oversight(
        position_value=D("50"),
        portfolio_value=D("100"),
        base_window_seconds=120,
        max_window_seconds=30,  # nonsense: below the base
        base_checks=1,
        max_checks=1,
        min_spacing_seconds=0,
        full_attention_exposure=D("0.25"),
        base_max_bid=D("0.15"),
        shadow_max_bid=D("0.10"),  # nonsense: below the acted band
    )
    assert p.window_seconds == 120
    assert p.shadow_max_bid == D("0.15")


# ----------------------------------------------------- sizing: the predictor --


def order_settings(tmp_path: Path, **overrides) -> KalshiSettings:
    base = KalshiSettings(
        environment="demo",
        rest_url="https://example.invalid/trade-api/v2",
        websocket_url="wss://example.invalid/trade-api/ws/v2",
        api_key_id=None,
        private_key_path=tmp_path / "private.pem",
        public_key_path=tmp_path / "public.pem",
        subaccount=0,
        enable_live_trading=False,
        live_capital_limit_usd=D("10"),
        position_fraction=D("0.20"),
        paper_balance_usd=D("100"),
        poll_interval_seconds=5,
        entry_window_seconds=180,
        minimum_seconds_to_close=20,
        min_probability=D("0.62"),
        min_edge=D("0.05"),
        max_slippage=D("0.01"),
        fee_reserve_per_contract=D("0.02"),
        assets=("BTC",),
        ledger_path=tmp_path / "ledger.db",
        trade_log_path=tmp_path / "trades.jsonl",
        status_check_lead_seconds=5,
        settlement_poll_seconds=2,
        kelly_fraction=D("0.25"),
        min_contracts=D("0.01"),
    )
    return replace(base, **overrides) if overrides else base


def order_client(tmp_path: Path, **overrides) -> AsyncKalshiTradingClient:
    instance = object.__new__(AsyncKalshiTradingClient)
    instance.settings = order_settings(tmp_path, **overrides)
    instance.authenticated = True
    return instance


def order_market() -> KalshiMarket:
    return KalshiMarket(
        asset="BTC",
        ticker="KXBTC15M-TEST",
        event_ticker="KXBTC15M",
        title="BTC price up in next 15 mins?",
        target_price=D("100000"),
        open_time=NOW - timedelta(minutes=10),
        close_time=NOW + timedelta(minutes=5),
        yes_bid=D("0.69"),
        yes_ask=D("0.71"),
        yes_bid_size=D("5000"),
        yes_ask_size=D("5000"),
        fee_multiplier=D("1"),
    )


def sized(tmp_path: Path, probability: str, **overrides) -> Decimal:
    plan_ = order_client(tmp_path, **overrides).build_order_plan(
        market=order_market(),
        quote=BookQuote(
            outcome="yes",
            best_price=D("0.70"),
            limit_price=D("0.70"),
            depth_at_limit=D("100000"),
        ),
        available_balance=D("1000"),
        model_probability=D(probability),
    )
    return plan_.contracts


def test_predictor_sizing_is_unchanged_at_the_default_gain(tmp_path: Path):
    """Byte for byte, not merely close."""
    assert sized(tmp_path, "0.95") == sized(
        tmp_path, "0.95", sizing_confidence_gain=D("1.0")
    )


def test_a_more_confident_predictor_read_does_not_increase_risk(tmp_path: Path):
    modest = sized(tmp_path, "0.78", sizing_confidence_gain=D("2.0"))
    confident = sized(tmp_path, "0.95", sizing_confidence_gain=D("2.0"))
    assert confident == modest


def test_confidence_scaling_cannot_breach_the_hard_ceiling(tmp_path: Path):
    """The clamp is what makes the multiplier safe rather than merely bounded."""
    capped = sized(
        tmp_path,
        "0.99",
        sizing_confidence_gain=D("3.0"),
        kelly_fraction=D("0"),  # flat POSITION_FRACTION sizing
        position_fraction=D("0.20"),
        sizing_max_fraction=D("0.20"),
    )
    # 20% of $1000 is $200; a cent is reserved for rounding before contracts
    # are computed, so the ceiling binds at just under 200/0.72 contracts.
    assert capped <= (D("200") / (D("0.70") + D("0.02")))


# ------------------------------------------------------ sizing: the scalper --


class ScalperSettings:
    sizing_confidence_gain = D("2.0")
    sizing_confidence_floor = D("0.60")
    sizing_confidence_full = D("0.90")


class Quote:
    def __init__(self, probability) -> None:
        self.context = {} if probability is None else {"probability": probability}


def share(probability, gain="2.0") -> Decimal:
    engine = object.__new__(
        __import__("execution.scalp_engine", fromlist=["ScalpEngine"]).ScalpEngine
    )
    settings_ = ScalperSettings()
    settings_.sizing_confidence_gain = D(gain)
    engine.settings = settings_
    return engine._confidence_share(Quote(probability))


def test_scalper_share_is_exactly_one_when_scaling_is_off():
    assert share("0.62", gain="1.0") == D("1")
    assert share("0.95", gain="1.0") == D("1")


def test_scalper_confidence_never_changes_the_fixed_budget():
    assert share("0.95") == D("1")
    assert share("0.60") == D("1")


def test_an_unreadable_probability_still_cannot_change_size():
    assert share(None) == D("1")
    assert share("not-a-number") == D("1")


# ------------------------------------------------- runtime: the salvage exit --


class Settings:
    environment = "prod"
    salvage_enabled = True
    salvage_window_seconds = 30
    salvage_max_bid = D("0.15")
    salvage_max_spread = D("0.02")
    salvage_longshot_haircut = D("0.40")
    salvage_min_gain_per_contract = D("0.01")
    oversight_enabled = True
    oversight_full_exposure = D("0.25")
    oversight_max_window_seconds = 300
    oversight_max_checks = 4
    oversight_min_spacing_seconds = 20.0
    oversight_shadow_max_bid = D("0.35")
    salvage_min_actionable_seconds = 0.0
    oversight_extra_actionable_seconds = 0.0


class Decisions:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def record_event(self, record_type: str, **fields) -> None:
        self.events.append((record_type, fields))

    def of(self, name: str) -> list[dict]:
        return [fields for kind, fields in self.events if kind == name]


class Client:
    def __init__(self, book=None) -> None:
        self.book = book
        self.book_reads = 0

    async def get_book_analysis(self, ticker: str, depth: int = 20):
        self.book_reads += 1
        return self.book

    async def place_exit_order(self, plan):  # pragma: no cover - never reached
        raise AssertionError("these cases must not sell")


def book(yes_bid: str, yes_ask: str, size="1000"):
    return analyse(
        [(D(yes_bid), D(size))],
        [(D("1") - D(yes_ask), D(size))],
    )


@pytest.fixture
def ledger(tmp_path: Path) -> TradeLedger:
    return TradeLedger(tmp_path / "ledger.db", D("100"))


def open_trade(
    ledger: TradeLedger,
    *,
    debit="5.00",
    contracts="10",
    closes_in=200,
    ticker=None,
    base=None,
):
    at = base or NOW
    return ledger.record_open_trade(
        mode="paper",
        environment="prod",
        strategy="predictor",
        asset="BTC",
        ticker=ticker or f"KXBTC15M-{debit}-{closes_in}",
        event_ticker="KXBTC15M-E",
        order_id="o1",
        client_order_id="c1",
        outcome="no",
        placed_at=at - timedelta(minutes=10),
        market_open_time=at - timedelta(minutes=14),
        market_close_time=at + timedelta(seconds=closes_in),
        status_check_time=at + timedelta(seconds=closes_in - 5),
        contracts=D(contracts),
        entry_price=D("0.68"),
        entry_cost=D("6.80"),
        fees_paid=D("0.15"),
        total_debit=D(debit),
        target_price=D("100000"),
        model_probability=D("0.70"),
        model_edge=D("0.05"),
        metadata={"fee_multiplier": "1"},
    )


def exit_for(ledger, client, decisions=None, settings=None, now=None) -> SalvageExit:
    return SalvageExit(
        settings=settings or Settings(),
        client=client,
        ledger=ledger,
        decisions=decisions or Decisions(),
        positions=PositionBook(max_concurrent={"predictor": 1}),
        mode="paper",
        live=False,
        now=now or (lambda: NOW),
    )


def test_a_small_position_keeps_the_original_single_late_check(ledger):
    """Regression: the historical behaviour must survive the new machinery."""
    trade = open_trade(ledger, debit="1.00", closes_in=200)
    salvage = exit_for(ledger, Client())
    salvage.set_portfolio_value(D("100"))
    assert salvage.plan_for(trade).checks == 1
    # 200s out, a 1% position is nowhere near due on a ~37s window.
    assert not salvage.is_due(trade)


def test_a_large_position_is_examined_far_earlier(ledger):
    small = open_trade(ledger, debit="1.00", closes_in=200)
    salvage = exit_for(ledger, Client())
    salvage.set_portfolio_value(D("100"))
    assert not salvage.is_due(small)

    large = open_trade(ledger, debit="30.00", closes_in=200)
    assert salvage.is_due(large)
    assert salvage.plan_for(large).checks == 4


def test_oversight_off_reproduces_the_flat_window_exactly(ledger):
    class Off(Settings):
        oversight_enabled = False

    large = open_trade(ledger, debit="30.00", closes_in=200)
    salvage = exit_for(ledger, Client(), settings=Off())
    salvage.set_portfolio_value(D("100"))
    plan_ = salvage.plan_for(large)
    assert plan_.window_seconds == 30
    assert plan_.checks == 1
    assert plan_.shadow_max_bid == D("0.15")
    assert not salvage.is_due(large)


async def test_extra_checks_are_spaced_and_then_exhausted(ledger):
    """Bounded and spaced: supervision, not a poll loop."""
    clock = {"now": NOW}
    trade = open_trade(ledger, debit="30.00", closes_in=200)
    # A book that refuses on the band, so nothing sells and the checks run out.
    salvage = exit_for(ledger, Client(book("0.44", "0.45")), now=lambda: clock["now"])
    salvage.set_portfolio_value(D("100"))

    assert salvage.is_due(trade)
    await salvage.evaluate(trade)
    # Immediately afterwards the spacing rule refuses another look.
    assert not salvage.is_due(trade)

    seen = 1
    for _ in range(20):
        clock["now"] += timedelta(seconds=25)
        if (trade.market_close_time - clock["now"]).total_seconds() <= 0:
            break
        if salvage.is_due(trade):
            await salvage.evaluate(trade)
            seen += 1
    assert seen == 4, "a large position gets exactly its budget of checks"
    assert not salvage.is_due(trade)


async def test_a_shrinking_exposure_withdraws_the_checks_it_bought(ledger):
    """The budget is re-derived, not banked.

    A position earns extra checks from the share of the account it is risking.
    When the account grows - or the position is partly settled elsewhere - that
    share falls, and the supervision it bought falls with it. Without this the
    budget would be a high-water mark: whatever the position was worth at its
    most exposed moment, forever.

    This is the case that isolates the ``is_due`` budget from the terminal
    ``_evaluated`` guard, which only fires once the full budget is spent.
    """
    clock = {"now": NOW}
    # 65s out: inside the wide window a 30% position earns, and still inside
    # the narrow one it is left with. Otherwise the window guard refuses first
    # and this would pass without ever reaching the budget it means to test.
    trade = open_trade(ledger, debit="30.00", closes_in=65)
    salvage = exit_for(ledger, Client(book("0.44", "0.45")), now=lambda: clock["now"])

    salvage.set_portfolio_value(D("100"))  # 30% exposed: four checks
    assert salvage.plan_for(trade).checks == 4
    assert salvage.is_due(trade)
    await salvage.evaluate(trade)
    assert trade.trade_id not in salvage._evaluated, "budget not yet spent"

    # The account has grown; the same position is now a small part of it.
    clock["now"] += timedelta(seconds=30)
    salvage.set_portfolio_value(D("3000"))
    plan_ = salvage.plan_for(trade)
    assert plan_.checks == 1
    seconds_left = (trade.market_close_time - clock["now"]).total_seconds()
    assert 0 < seconds_left <= plan_.window_seconds, "still inside its window"
    assert not salvage.is_due(trade), (
        "one check was already taken and the position no longer earns more"
    )


async def test_the_acted_band_is_never_the_shadow_band(ledger):
    """The load-bearing separation. Widening attention must not widen the sell.

    A bid of 0.30 is inside the 0.35 shadow band and outside the 0.15 acted
    one. If this ever sells, the exit-on-reversal is back.
    """
    trade = open_trade(ledger, debit="30.00", closes_in=100)
    client = Client(book("0.69", "0.70"))  # NO bid 0.30, NO ask 0.31
    decisions = Decisions()
    salvage = exit_for(ledger, client, decisions=decisions)
    salvage.set_portfolio_value(D("100"))

    sold = await salvage.evaluate(trade)
    assert sold is False
    assert client.book_reads == 1

    shadow = decisions.of("salvage_shadow")
    assert len(shadow) == 1, "a band disagreement must be on the record"
    assert shadow[0]["shadow_would_exit"] is True
    assert shadow[0]["actually_exited"] is False
    assert shadow[0]["acted_max_bid"] == "0.15"


async def test_no_shadow_record_when_the_bands_agree(ledger):
    """A 38 GB log does not need a row saying nothing happened."""
    trade = open_trade(ledger, debit="30.00", closes_in=100)
    # NO bid 0.55 is outside both bands: both refuse, nothing to compare.
    salvage_decisions = Decisions()
    salvage = exit_for(
        ledger, Client(book("0.44", "0.45")), decisions=salvage_decisions
    )
    salvage.set_portfolio_value(D("100"))
    await salvage.evaluate(trade)
    assert salvage_decisions.of("salvage_shadow") == []
    assert len(salvage_decisions.of("salvage_evaluated")) == 1


async def test_an_unknown_portfolio_grants_more_supervision_not_less(ledger):
    """Failing safe means erring toward watching, never toward ignoring."""
    trade = open_trade(ledger, debit="1.00", closes_in=200)
    salvage = exit_for(ledger, Client())
    salvage.set_portfolio_value(None)
    assert salvage.plan_for(trade).checks == 4
    assert salvage.is_due(trade)


# ------------------------------------------------------- time to cut it in --


def timed(position: str, base: float, extra: float, portfolio: str = "100"):
    return plan_oversight(
        position_value=D(position),
        portfolio_value=D(portfolio),
        base_window_seconds=30,
        max_window_seconds=300,
        base_checks=1,
        max_checks=4,
        min_spacing_seconds=20,
        full_attention_exposure=D("0.25"),
        base_max_bid=D("0.15"),
        shadow_max_bid=D("0.35"),
        base_actionable_seconds=base,
        extra_actionable_seconds=extra,
    )


def test_the_actionable_floor_is_zero_unless_asked_for():
    assert timed("30", base=0.0, extra=0.0).min_actionable_seconds == 0.0


def test_a_bigger_position_needs_more_runway_to_be_worth_starting():
    """It consumes more of the book, so it is likelier to need a second go."""
    small = timed("1", base=8.0, extra=12.0)
    large = timed("25", base=8.0, extra=12.0)
    assert small.min_actionable_seconds == pytest.approx(8.48, abs=0.01)
    assert large.min_actionable_seconds == pytest.approx(20.0, abs=0.01)


async def test_no_check_is_scheduled_once_the_sale_could_not_complete(ledger):
    """A check with no time to act on its answer is not supervision."""

    class Timed(Settings):
        salvage_min_actionable_seconds = 25.0

    # 20s to close: inside the 30s window, but under the 25s floor.
    trade = open_trade(ledger, debit="1.00", closes_in=20)
    client = Client(book("0.44", "0.45"))
    salvage = exit_for(ledger, client, settings=Timed())
    salvage.set_portfolio_value(D("100"))

    assert not salvage.is_due(trade)
    assert salvage.blocks["too_late"] == 1
    assert client.book_reads == 0, "and it costs no book read either"


async def test_the_floor_still_leaves_the_exit_reachable(ledger):
    """The guard must narrow the window, never close it."""

    class Timed(Settings):
        salvage_min_actionable_seconds = 10.0

    trade = open_trade(ledger, debit="1.00", closes_in=20)
    salvage = exit_for(ledger, Client(book("0.44", "0.45")), settings=Timed())
    salvage.set_portfolio_value(D("100"))
    assert salvage.is_due(trade), "20s to close clears a 10s floor"


# ------------------------------------------- the bar rises with the stake --


def floor_at(exposure: str, uplift: str = "0.10") -> Decimal:
    from execution.risk_gate import required_probability

    return required_probability(
        base=D("0.62"),
        intended_exposure=D(exposure),
        full_exposure=D("0.25"),
        uplift=D(uplift),
    )


def test_a_zero_uplift_leaves_every_floor_where_it_was():
    for exposure in ("0.001", "0.05", "0.25", "0.90"):
        assert floor_at(exposure, uplift="0") == D("0.62")


def test_the_floor_rises_with_the_stake_and_then_stops():
    assert floor_at("0.05") < floor_at("0.15") < floor_at("0.25")
    assert floor_at("0.25") == floor_at("0.90"), "bounded past full exposure"


def test_the_floor_can_never_reach_certainty():
    """A floor of 1.0 is not a strict gate, it is a stopped strategy."""
    from execution.risk_gate import required_probability

    assert required_probability(
        base=D("0.95"),
        intended_exposure=D("1"),
        full_exposure=D("0.25"),
        uplift=D("0.30"),
    ) == D("0.99")


def test_the_gate_refuses_a_big_position_behind_a_thin_probability():
    from execution.risk_gate import check_size_scaled_probability

    assert not check_size_scaled_probability(D("0.65"), D("0.25"), D("0.72")).allowed
    assert check_size_scaled_probability(D("0.80"), D("0.25"), D("0.72")).allowed


def test_the_scalper_treats_an_unknown_portfolio_as_fully_exposed():
    """Otherwise the gate switches itself off when the balance cannot be read."""
    import execution.scalp_engine as se

    class S:
        size_probability_uplift = D("0.10")
        size_probability_full_exposure = D("0.25")
        scalp_band_low = D("0.60")

    engine = object.__new__(se.ScalpEngine)
    engine.settings = S()

    class Q:
        ticker = "KXBTC15M-T"
        context = {"probability": "0.65"}

    # 0.65 clears the flat 0.60 band but not the 0.70 a full-exposure position
    # demands, and an unreadable portfolio must be scored as exactly that.
    assert engine._size_probability_allows(Q(), D("1.00"), None) is False
    assert engine._size_probability_allows(Q(), D("1.00"), D("1000")) is True


# ------------------------------------- the budget must survive the wiring --
#
# The tests above drive SalvageExit directly, advancing a clock by hand. That
# is how the multi-check budget looked correct while being three-quarters dead:
# the predictor-only loop does not poll, so nothing was asking is_due again.
# These drive bot.py's own scheduler.


def monitor_bot(ledger, salvage_exit):
    from types import SimpleNamespace

    import bot as bot_module

    instance = object.__new__(bot_module.KalshiCryptoBot)
    instance.live = False
    instance.mode = "paper"
    instance.ledger = ledger
    instance.salvage = salvage_exit
    instance.settings = SimpleNamespace(
        environment="prod", status_check_lead_seconds=5, salvage_window_seconds=30
    )
    instance.waits = []

    async def interruptible_wait(seconds):
        instance.waits.append(float(seconds))

    instance._interruptible_wait = interruptible_wait
    return instance


async def test_a_refused_check_reschedules_instead_of_sleeping_past_the_budget(
    ledger,
):
    """The bug this exists for: one check fired, three silently discarded.

    A 30% position earns a 300s window and four checks 20s apart. The
    predictor-only loop sleeps to the status check in a single call, so before
    this the first refusal ended supervision for the position - it woke next at
    close-5s, by which point the remaining budget was unreachable and, under an
    actionable floor, refused as too late.
    """
    # Stamped on the live clock: _monitor_open_trade reads datetime.now, and a
    # row stamped on the frozen NOW is never due - it would pass for the wrong
    # reason, which is the failure mode this whole file exists to avoid.
    trade = open_trade(
        ledger, debit="30.00", closes_in=200, base=datetime.now(timezone.utc)
    )
    exit_ = exit_for(ledger, Client(book("0.44", "0.45")))  # NO bid 0.55: refused
    exit_._now = lambda: datetime.now(timezone.utc)
    instance = monitor_bot(ledger, exit_)

    await instance._monitor_open_trade(trade, once=False)

    assert exit_.evaluations == 1, "the first check ran"
    assert ledger.get_trade(trade.trade_id).status == "OPEN", "and refused to sell"
    assert instance.waits, "it must wait for something"
    # ~20s (the spacing before check two), NOT ~195s (the status check).
    assert instance.waits[0] < 60, (
        f"slept {instance.waits[0]:.0f}s, past the three remaining checks"
    )
    assert instance._countdown_label == "salvage check"


async def test_the_last_check_hands_the_wait_back_to_the_status_check(ledger):
    """Once the budget is spent there is nothing left to wake early for."""

    class OneCheck(Settings):
        oversight_max_checks = 1

    trade = open_trade(
        ledger, debit="30.00", closes_in=200, base=datetime.now(timezone.utc)
    )
    exit_ = exit_for(ledger, Client(book("0.44", "0.45")), settings=OneCheck())
    exit_._now = lambda: datetime.now(timezone.utc)
    instance = monitor_bot(ledger, exit_)

    await instance._monitor_open_trade(trade, once=False)

    assert exit_.evaluations == 1
    assert instance.waits and instance.waits[0] > 150, "back to the status check"
    assert instance._countdown_label == "status check"


# --------------------------- the wiring, not merely the arithmetic --------
#
# An audit of the tests above found that deleting the confidence multiplier
# from build_order_plan entirely left every predictor sizing test passing: one
# compared the default gain against an explicit 1.0 (the same call twice), one
# varied probability, where Kelly ALONE already produces the asserted ordering,
# and one pinned kelly_fraction to 0 so the pre-multiplier fraction already
# equalled the ceiling and the multiplier could not move it. All three verified
# arithmetic that was never reached.


def test_the_deprecated_gain_cannot_change_size(tmp_path: Path):
    off = sized(tmp_path, "0.80", sizing_confidence_gain=D("1.0"))
    on = sized(tmp_path, "0.80", sizing_confidence_gain=D("2.0"))
    assert on == off


def test_deprecated_multiplier_and_clamp_cannot_raise_the_fixed_budget(
    tmp_path: Path,
):
    """Kelly below the ceiling, multiplier above it: the clamp is load-bearing."""
    # p=0.95 at price 0.70: edge clamps to max_edge 0.18, full Kelly 0.60,
    # x0.25 = 0.15. Under the 0.20 ceiling. Gain 3.0 would take it to 0.45.
    unclamped = sized(tmp_path, "0.95", sizing_confidence_gain=D("1.0"))
    clamped = sized(
        tmp_path,
        "0.95",
        sizing_confidence_gain=D("3.0"),
        kelly_fraction=D("0.25"),
        sizing_max_fraction=D("0.20"),
    )
    assert clamped == unclamped
    ceiling = sized(
        tmp_path, "0.95", kelly_fraction=D("0"), sizing_max_fraction=D("0.20")
    )
    assert clamped == ceiling


def test_the_actionable_floor_is_inclusive_at_its_boundary():
    """`<` vs `<=` at the exact floor: no test distinguished them."""

    class AtFloor(Settings):
        salvage_min_actionable_seconds = 20.0
        oversight_extra_actionable_seconds = 0.0

    plan_ = plan_oversight(
        position_value=D("30"),
        portfolio_value=D("100"),
        base_window_seconds=30,
        max_window_seconds=300,
        base_checks=1,
        max_checks=4,
        min_spacing_seconds=20,
        full_attention_exposure=D("0.25"),
        base_max_bid=D("0.15"),
        shadow_max_bid=D("0.35"),
        base_actionable_seconds=20.0,
        extra_actionable_seconds=0.0,
    )
    assert plan_.min_actionable_seconds == 20.0
    assert AtFloor.salvage_min_actionable_seconds == 20.0


async def test_state_for_settled_positions_does_not_accumulate(ledger):
    """It is a supervisor for open positions, and it runs 24/7."""
    first = open_trade(ledger, debit="30.00", closes_in=100, ticker="KX-A")
    salvage = exit_for(ledger, Client(book("0.44", "0.45")))
    await salvage.run([first], portfolio_value=D("100"))
    assert salvage._checks, "the first position was recorded"

    # Next pass: the ledger reports a different open position, the first having
    # settled. Nothing about the settled one should still be held.
    second = open_trade(ledger, debit="30.00", closes_in=100, ticker="KX-B")
    await salvage.run([second], portfolio_value=D("100"))
    assert first.trade_id not in salvage._checks
    assert first.trade_id not in salvage._last_check
    assert first.trade_id not in salvage._evaluated

    await salvage.run([], portfolio_value=D("100"))
    assert not salvage._checks and not salvage._last_check


async def test_live_exposure_counts_the_predictors_locked_capital(ledger):
    """PositionBook is scalper-only, so it cannot see a predictor position.

    In live mode Kalshi's available balance is free cash with every open
    position already deducted. Using it as the exposure denominator understates
    the account by exactly the capital at risk, so every position looks like a
    larger share of it than it is. Paper hid this completely: the paper
    ledger's current_balance is already the total.
    """
    from types import SimpleNamespace

    import bot as bot_module

    open_trade(ledger, debit="40.00", closes_in=200, ticker="KX-LOCKED")

    instance = object.__new__(bot_module.KalshiCryptoBot)
    instance.mode = "paper"  # the ledger rows above are paper rows
    instance.ledger = ledger
    instance.settings = SimpleNamespace(environment="prod")
    instance.positions = PositionBook(max_concurrent={"predictor": 1})
    instance.live = True

    async def balance(exchange_index=None):
        return D("60")  # $60 free, $40 locked -> a $100 account

    instance.client = SimpleNamespace(get_available_balance=balance)

    assert instance._locked_capital() == D("40.00"), "the ledger sees it"
    assert instance.positions.committed() == D("0"), "PositionBook does not"
    assert await instance._portfolio_value() == D("100.00")


async def test_the_shadow_band_records_for_a_small_position_too(ledger):
    """Regression: the instrumentation collected nothing.

    Scaling the recorded band with exposure meant only a large position could
    ever produce a record - but the question it exists to answer ("should a
    large position be cut earlier?") is wanted BEFORE such a position is taken.
    Measured in the live simulation: five evaluations, zero records.
    """
    # 1% of the account. Its exposure-scaled band is barely above the acted
    # 0.15, but the configured ceiling is 0.35 and the bid is 0.30.
    trade = open_trade(ledger, debit="1.00", closes_in=100, ticker="KX-SMALL")
    decisions = Decisions()
    salvage = exit_for(ledger, Client(book("0.69", "0.70")), decisions=decisions)
    salvage.set_portfolio_value(D("100"))

    assert salvage.plan_for(trade).shadow_max_bid < D("0.20"), (
        "this position's scaled band would not have admitted a 0.30 bid"
    )
    sold = await salvage.evaluate(trade)

    assert sold is False, "and it still must not sell"
    shadow = decisions.of("salvage_shadow")
    assert len(shadow) == 1, "but the counterfactual is on the record"
    assert shadow[0]["shadow_max_bid"] == "0.35"
    assert shadow[0]["acted_max_bid"] == "0.15"
    assert shadow[0]["shadow_would_exit"] is True
    assert shadow[0]["actually_exited"] is False
