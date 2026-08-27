"""The salvage exit's runtime: reachability, once-only, and the ledger row.

The pure rule is tested in ``test_salvage.py``. What is tested here is the part
that has repeatedly gone wrong in this project: a correct rule wired somewhere
it never runs. Five features have shipped unreachable; three were found by their
own funnel counters, two by losing real money.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from core.strategy_brain.strategies.orderbook import analyse
from execution.kalshi_client import OrderResult
from execution.position_book import PositionBook
from execution.salvage_exit import SalvageExit
from execution.trade_ledger import TradeLedger


D = Decimal
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class Settings:
    environment = "prod"
    salvage_enabled = True
    salvage_window_seconds = 30
    salvage_max_bid = D("0.15")
    salvage_max_spread = D("0.02")
    salvage_longshot_haircut = D("0.40")
    salvage_min_gain_per_contract = D("0.01")


class Decisions:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def record_event(self, record_type: str, **fields) -> None:
        self.events.append((record_type, fields))

    def types(self) -> list[str]:
        return [name for name, _ in self.events]

    def of(self, name: str) -> list[dict]:
        return [fields for kind, fields in self.events if kind == name]


class Client:
    """A fake venue. Async methods are real coroutine functions, as the bot awaits."""

    def __init__(self, book=None, fill: OrderResult | None = None) -> None:
        self.book = book
        self.fill = fill
        self.exit_orders: list = []
        self.book_reads = 0

    async def get_book_analysis(self, ticker: str, depth: int = 20):
        self.book_reads += 1
        return self.book

    async def place_exit_order(self, plan):
        self.exit_orders.append(plan)
        return self.fill or OrderResult(
            order_id="x",
            client_order_id=plan.client_order_id,
            fill_count=plan.contracts,
            remaining_count=D("0"),
            average_fill_price=plan.outcome_limit_price,
        )


def book(yes_bid: str, yes_ask: str, yes_size="1000", no_size="1000"):
    """A depth snapshot. Kalshi publishes resting bids on both sides."""
    return analyse(
        [(D(yes_bid), D(yes_size))],
        [(D("1") - D(yes_ask), D(no_size))],
    )


@pytest.fixture
def ledger(tmp_path: Path) -> TradeLedger:
    return TradeLedger(tmp_path / "ledger.db", D("100"))


def open_trade(ledger: TradeLedger, *, outcome="no", entry="0.68", contracts="10",
               closes_in=20, base=None):
    """Open a position closing ``closes_in`` seconds after ``base``.

    ``base`` exists for the reachability tests, which drive bot.py's own clock
    rather than the frozen NOW: _salvage_pass re-reads the row from the ledger,
    so a record stamped on a different clock than the one the bot reads is never
    due and the test would pass for the wrong reason.
    """
    at = base or NOW
    return ledger.record_open_trade(
        mode="paper",
        environment="prod",
        strategy="predictor",
        asset="BTC",
        ticker="KXBTC15M-T",
        event_ticker="KXBTC15M-E",
        order_id="o1",
        client_order_id="c1",
        outcome=outcome,
        placed_at=at - timedelta(minutes=10),
        market_open_time=at - timedelta(minutes=14),
        market_close_time=at + timedelta(seconds=closes_in),
        status_check_time=at + timedelta(seconds=closes_in - 5),
        contracts=D(contracts),
        entry_price=D(entry),
        entry_cost=D(contracts) * D(entry),
        fees_paid=D("0.15"),
        total_debit=D(contracts) * D(entry) + D("0.15"),
        target_price=D("100000"),
        model_probability=D("0.70"),
        model_edge=D("0.05"),
        metadata={"fee_multiplier": "1"},
    )


def salvage(ledger, client, decisions=None, positions=None, live=False) -> SalvageExit:
    return SalvageExit(
        settings=Settings(),
        client=client,
        ledger=ledger,
        decisions=decisions or Decisions(),
        positions=positions or PositionBook(max_concurrent={"predictor": 1}),
        mode="paper",
        live=live,
        now=lambda: NOW,
    )


# ------------------------------------------------------------- the window --


def test_a_position_outside_its_window_is_not_evaluated(ledger):
    exit_ = salvage(ledger, Client(book("0.86", "0.88")))
    trade = open_trade(ledger, closes_in=120)
    assert not exit_.is_due(trade, NOW)
    assert exit_.seconds_until_due(trade, NOW) == pytest.approx(90.0)


def test_a_position_inside_its_window_is_due(ledger):
    exit_ = salvage(ledger, Client(book("0.86", "0.88")))
    trade = open_trade(ledger, closes_in=20)
    assert exit_.is_due(trade, NOW)
    assert exit_.seconds_until_due(trade, NOW) == 0.0


def test_a_closed_market_is_never_evaluated(ledger):
    """After close there is nothing left to sell, and a stale book would price it."""
    exit_ = salvage(ledger, Client(book("0.86", "0.88")))
    trade = open_trade(ledger, closes_in=-5)
    assert not exit_.is_due(trade, NOW)


async def test_each_position_is_put_to_the_question_exactly_once(ledger):
    """The window is a moment, not a monitoring mode.

    A rule re-asked every poll eventually says yes to noise - which is the shape
    of the failure that got exit-on-reversal removed.
    """
    client = Client(book("0.86", "0.88"))     # NO at 0.04/0.06: a clear sell
    exit_ = salvage(ledger, client)
    trade = open_trade(ledger)

    assert await exit_.run([trade]) == 1
    assert not exit_.is_due(trade, NOW)
    assert await exit_.run([trade]) == 0      # the row is EXITED now anyway
    assert client.book_reads == 1
    assert exit_.evaluations == 1


async def test_a_refusal_is_also_final(ledger):
    """Held is an answer. Re-asking it is how a hold becomes a sale."""
    client = Client(book("0.60", "0.62"))     # NO at 0.38: nowhere near the band
    exit_ = salvage(ledger, client)
    trade = open_trade(ledger)

    assert await exit_.run([trade]) == 0
    assert await exit_.run([trade]) == 0
    assert client.book_reads == 1
    assert exit_.blocks == {"band": 1}


# --------------------------------------------------------- what it records --


async def test_a_sale_writes_an_exited_row_and_frees_the_reservation(ledger):
    positions = PositionBook(max_concurrent={"predictor": 1})
    decisions = Decisions()
    client = Client(book("0.86", "0.88"))
    exit_ = salvage(ledger, client, decisions, positions)
    trade = open_trade(ledger)
    reservation = await positions.reserve(
        strategy="predictor", ticker=trade.ticker, amount=trade.total_debit
    )
    await positions.confirm(reservation, trade_id=trade.trade_id,
                            actual_amount=trade.total_debit)
    assert positions.open_count("predictor") == 1

    assert await exit_.run([trade]) == 1

    row = ledger.get_trade(trade.trade_id)
    assert row.status == "EXITED"
    assert row.exchange_status == "salvage"
    assert row.exit_price == D("0.12")        # sold at the bid, never the mid
    assert row.exit_contracts == D("10")
    assert row.pnl is not None and row.pnl < 0    # it is a loss, just a smaller one
    # The salvage recovered something rather than nothing: holding to a losing
    # settlement returns the whole debit.
    assert row.pnl > -row.total_debit
    assert positions.open_count("predictor") == 0
    assert "salvage_exit" in decisions.types()


async def test_every_evaluation_is_logged_whichever_way_it_went(ledger):
    """Both verdicts, always. A rule that fires rarely by design is
    indistinguishable from a dead one unless the refusals are on the record.
    """
    decisions = Decisions()
    exit_ = salvage(ledger, Client(book("0.60", "0.62")), decisions)
    await exit_.run([open_trade(ledger)])

    records = decisions.of("salvage_evaluated")
    assert len(records) == 1
    record = records[0]
    assert record["should_exit"] is False
    assert record["gate"] == "band"
    assert record["bid"] == "0.38"
    assert record["reason"]


def test_the_funnel_survives_a_session_with_no_exits(ledger):
    decisions = Decisions()
    exit_ = salvage(ledger, Client(None), decisions)
    exit_.blocks = {"band": 4, "spread": 1}
    exit_.record_funnel("session_end")
    funnel = decisions.of("salvage_funnel")[0]
    assert funnel["blocks"] == {"band": 4, "spread": 1}
    assert funnel["exits"] == 0


# --------------------------------------------------------------- the order --


async def test_the_live_order_sells_the_side_already_held(ledger):
    """Holding NO, the order is a BUY on the YES book - which is what selling
    NO is. reduce_only on the wire is what stops it opening a position instead.
    """
    client = Client(book("0.86", "0.88"))
    exit_ = salvage(ledger, client, live=True)
    await exit_.run([open_trade(ledger, outcome="no")])

    plan = client.exit_orders[0]
    assert plan.sdk_book_side == "bid"
    assert plan.sdk_yes_leg_price == D("0.8800")
    assert plan.outcome_limit_price == D("0.1200")
    assert plan.contracts == D("10")


async def test_holding_yes_sells_into_the_yes_bids(ledger):
    client = Client(book("0.12", "0.14"))
    exit_ = salvage(ledger, client, live=True)
    await exit_.run([open_trade(ledger, outcome="yes", entry="0.62")])

    plan = client.exit_orders[0]
    assert plan.sdk_book_side == "ask"
    assert plan.sdk_yes_leg_price == D("0.1200")


async def test_an_unfilled_live_order_leaves_the_position_open(ledger):
    """IOC can still miss after the depth check. The position must survive that
    intact and be settled normally, not left half-recorded.
    """
    decisions = Decisions()
    client = Client(
        book("0.86", "0.88"),
        fill=OrderResult(
            order_id="",
            client_order_id="c",
            fill_count=D("0"),
            remaining_count=D("10"),
            average_fill_price=None,
            error_code="insufficient_resting_volume",
        ),
    )
    exit_ = salvage(ledger, client, decisions, live=True)
    trade = open_trade(ledger)

    assert await exit_.run([trade]) == 0
    assert ledger.get_trade(trade.trade_id).status == "OPEN"
    assert "salvage_unfilled" in decisions.types()
    assert exit_.blocks == {"unfilled": 1}


async def test_a_live_no_fill_is_mapped_from_the_yes_leg_price(
    ledger,
):
    client = Client(
        book("0.86", "0.88"),
        fill=OrderResult(
            order_id="o",
            client_order_id="c",
            fill_count=D("10"),
            remaining_count=D("0"),
            # SDK reports the YES-leg price. Buying YES at 0.97 reduces a NO
            # position at an economic NO sale price of 0.03.
            average_fill_price=D("0.97"),
        ),
    )
    exit_ = salvage(ledger, client, live=True)
    trade = open_trade(ledger)
    await exit_.run([trade])

    row = ledger.get_trade(trade.trade_id)
    assert row.exit_contracts == D("10")
    assert row.exit_price == D("0.03")


# ----------------------------------------------------------- containment --


async def test_one_unreadable_book_does_not_strand_the_other_positions(ledger):
    class Exploding(Client):
        async def get_book_analysis(self, ticker: str, depth: int = 20):
            if ticker.endswith("BAD"):
                raise RuntimeError("venue said no")
            return self.book

    ledger_trade = open_trade(ledger)
    bad = ledger.record_open_trade(
        mode="paper", environment="prod", strategy="predictor", asset="ETH",
        ticker="KXETH15M-BAD", event_ticker="E", order_id="o2",
        client_order_id="c2", outcome="no", placed_at=NOW,
        market_open_time=NOW, market_close_time=NOW + timedelta(seconds=20),
        status_check_time=NOW, contracts=D("5"), entry_price=D("0.70"),
        entry_cost=D("3.50"), fees_paid=D("0.05"), total_debit=D("3.55"),
        target_price=D("4000"), model_probability=D("0.7"), model_edge=D("0.05"),
        metadata={"fee_multiplier": "1"},
    )
    exit_ = salvage(ledger, Exploding(book("0.86", "0.88")))

    assert await exit_.run([bad, ledger_trade]) == 1
    assert ledger.get_trade(bad.trade_id).status == "OPEN"
    assert ledger.get_trade(ledger_trade.trade_id).status == "EXITED"


async def test_a_very_cheap_bid_with_the_same_spread_is_refused_on_margin(ledger):
    """An emergent lower bound, and a correct one.

    The gain scales with the price but the spread does not, so at a 4c bid a 2c
    spread is half the contract's value and the edge is gone - which is what the
    sensitivity work showed before this was built. The absolute $0.01/contract
    floor therefore imposes a price floor as well as the explicit ceiling, and
    nothing needs to state it separately.
    """
    decisions = Decisions()
    exit_ = salvage(ledger, Client(book("0.94", "0.96")), decisions)
    assert await exit_.run([open_trade(ledger)]) == 0
    assert exit_.blocks == {"margin": 1}
    record = decisions.of("salvage_evaluated")[0]
    assert record["bid"] == "0.04"


# ------------------------------------------------------------ reachability --
#
# The rest of this file tests the rule. These test that the rule RUNS. Five
# features in this project have shipped correct and unreachable - the taker
# fallback, the regime-confidence floor, the predictor exit, and two live sizing
# faults - so the wiring gets its own coverage rather than being assumed.


def _bot(ledger, salvage_exit, trade, tmp_path):
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


async def test_the_predictor_only_loop_wakes_for_the_window(ledger, tmp_path):
    """Without this the feature is unreachable in the mode that ran live.

    ``_monitor_open_trade`` sleeps to close-5s, straight past the close-30s
    window. It must wake at whichever comes first.
    """
    exit_ = salvage(ledger, Client(book("0.86", "0.88")))
    exit_._now = lambda: datetime.now(timezone.utc)
    trade = open_trade(ledger, closes_in=120, base=datetime.now(timezone.utc))
    instance = _bot(ledger, exit_, trade, tmp_path)

    await instance._monitor_open_trade(trade, once=False)

    # ~90s to the window, not ~115s to the status check.
    assert instance.waits and 85 < instance.waits[0] < 92
    assert instance._countdown_label == "salvage check"


async def test_a_position_already_inside_the_window_is_evaluated_not_slept_on(
    ledger, tmp_path
):
    exit_ = salvage(ledger, Client(book("0.86", "0.88")))
    exit_._now = lambda: datetime.now(timezone.utc)
    trade = open_trade(ledger, closes_in=20, base=datetime.now(timezone.utc))
    instance = _bot(ledger, exit_, trade, tmp_path)

    await instance._monitor_open_trade(trade, once=False)

    # Sold, so there is nothing left to monitor and nothing was slept on.
    assert ledger.get_trade(trade.trade_id).status == "EXITED"
    assert instance.waits == []


async def test_the_monitor_is_unchanged_when_salvage_is_off(ledger, tmp_path):
    """Off by default means the existing path must be bit-for-bit as it was."""
    trade = open_trade(ledger, closes_in=120, base=datetime.now(timezone.utc))
    instance = _bot(ledger, None, trade, tmp_path)

    await instance._monitor_open_trade(trade, once=False)

    assert instance.waits and 110 < instance.waits[0] < 117
    assert instance._countdown_label == "status check"


async def test_the_supervised_pass_reads_the_ledger_for_itself(ledger):
    """It takes no argument on purpose: it is called from both position paths
    and the OPEN set is the only thing both agree on.
    """
    from types import SimpleNamespace

    import bot as bot_module

    exit_ = salvage(ledger, Client(book("0.86", "0.88")))
    instance = object.__new__(bot_module.KalshiCryptoBot)
    instance.mode = "paper"
    instance.ledger = ledger
    instance.salvage = exit_
    instance.settings = SimpleNamespace(environment="prod")
    trade = open_trade(ledger)

    assert await instance._salvage_pass() == 1
    assert ledger.get_trade(trade.trade_id).status == "EXITED"


async def test_the_supervised_pass_is_a_no_op_when_disabled(ledger):
    from types import SimpleNamespace

    import bot as bot_module

    instance = object.__new__(bot_module.KalshiCryptoBot)
    instance.mode = "paper"
    instance.ledger = ledger
    instance.settings = SimpleNamespace(environment="prod")
    trade = open_trade(ledger)

    assert await instance._salvage_pass() == 0
    assert ledger.get_trade(trade.trade_id).status == "OPEN"


def test_the_feature_is_off_unless_it_is_switched_on(monkeypatch):
    """It touches money on positions that are already losing, so the default
    matters. It proves itself in paper before it runs live.
    """
    from config import KalshiSettings

    monkeypatch.delenv("SALVAGE_ENABLED", raising=False)
    assert KalshiSettings.from_env().salvage_enabled is False
