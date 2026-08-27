"""The account statement, exercised entirely against fakes.

Nothing here opens a socket or constructs an order. The interesting cases are
the four Kalshi conventions that a naive reading gets wrong: revenue in cents,
``outcome_side`` as the only trustworthy direction field, a cursor that
terminates on the empty string rather than ``None``, and a settlement row whose
two counts are gross traded volume rather than the position held at expiry.
"""

from __future__ import annotations

import ast
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from kalshi_python_async import ApiException

import kalshi_history as history


UTC = timezone.utc
ZERO_FEE = Decimal("0")
BASE = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


class FakeFill:
    def __init__(
        self,
        *,
        fill_id: str,
        ticker: str,
        outcome_side: str,
        count_fp: str,
        yes_price_dollars: str,
        no_price_dollars: str,
        fee_cost: str = "0.000000",
        created_time: datetime | None = BASE,
        ts: int | None = None,
        side: str | None = None,
    ) -> None:
        self.fill_id = fill_id
        self.trade_id = fill_id
        self.ticker = ticker
        self.market_ticker = ticker
        self.outcome_side = outcome_side
        # Deliberately contradicts outcome_side: the live account does exactly
        # this, and the reader must ignore it.
        self.side = side if side is not None else (
            "yes" if outcome_side == "no" else "no"
        )
        self.action = "sell"
        self.count_fp = count_fp
        self.yes_price_dollars = yes_price_dollars
        self.no_price_dollars = no_price_dollars
        self.fee_cost = fee_cost
        self.created_time = created_time
        self.ts = ts


class FakeSettlement:
    def __init__(
        self,
        *,
        ticker: str,
        market_result: str,
        yes_count_fp: str = "0.00",
        yes_total_cost_dollars: str = "0.000000",
        no_count_fp: str = "0.00",
        no_total_cost_dollars: str = "0.000000",
        revenue: int = 0,
        fee_cost: str = "0.000000",
        settled_time: datetime = BASE,
    ) -> None:
        self.ticker = ticker
        self.event_ticker = ticker.rsplit("-", 1)[0]
        self.market_result = market_result
        self.yes_count_fp = yes_count_fp
        self.yes_total_cost_dollars = yes_total_cost_dollars
        self.no_count_fp = no_count_fp
        self.no_total_cost_dollars = no_total_cost_dollars
        self.revenue = revenue
        self.fee_cost = fee_cost
        self.settled_time = settled_time
        self.value = 100 if market_result == "yes" else 0


class FakePage:
    def __init__(self, items: list, cursor: str, attribute: str = "fills") -> None:
        setattr(self, attribute, items)
        self.cursor = cursor


# ---------------------------------------------------------------- fills


def test_entry_price_follows_outcome_side_not_the_deprecated_side() -> None:
    leg = history.fill_leg(
        FakeFill(
            fill_id="a",
            ticker="KXDOGE15M-26AUG101830-30",
            outcome_side="no",
            count_fp="5.61",
            yes_price_dollars="0.3800",
            no_price_dollars="0.6200",
            fee_cost="0.092600",
        )
    )
    assert leg.outcome == "no"
    assert leg.price == Decimal("0.6200")
    assert leg.contracts == Decimal("5.61")
    assert leg.cost == Decimal("3.4782")
    assert leg.fee == Decimal("0.092600")


def test_fill_without_created_time_falls_back_to_the_unix_seconds() -> None:
    leg = history.fill_leg(
        FakeFill(
            fill_id="a",
            ticker="KXBTC15M-26AUG101830-T1",
            outcome_side="yes",
            count_fp="1.00",
            yes_price_dollars="0.5000",
            no_price_dollars="0.5000",
            created_time=None,
            ts=1786400821,
        )
    )
    assert leg.executed_at == datetime.fromtimestamp(1786400821, tz=UTC)


def test_unknown_outcome_side_is_refused() -> None:
    with pytest.raises(ValueError, match="outcome_side"):
        history.fill_leg(
            FakeFill(
                fill_id="a",
                ticker="KXBTC15M-1-T1",
                outcome_side="scalar",
                count_fp="1.00",
                yes_price_dollars="0.5",
                no_price_dollars="0.5",
            )
        )


def test_merge_dedupes_the_cutoff_overlap_and_sorts_newest_first() -> None:
    older = FakeFill(
        fill_id="old",
        ticker="KXBTC15M-a-T1",
        outcome_side="yes",
        count_fp="1.00",
        yes_price_dollars="0.5",
        no_price_dollars="0.5",
        created_time=BASE - timedelta(days=90),
    )
    newer = FakeFill(
        fill_id="new",
        ticker="KXBTC15M-b-T1",
        outcome_side="yes",
        count_fp="1.00",
        yes_price_dollars="0.5",
        no_price_dollars="0.5",
    )
    legs = history.merge_fills([newer, older], [older])

    assert [leg.fill_id for leg in legs] == ["new", "old"]


# ---------------------------------------------------------------- records


def test_revenue_is_cents_so_a_winner_pays_a_dollar_a_contract() -> None:
    settlement = FakeSettlement(
        ticker="KXBTC15M-26AUG101830-T1",
        market_result="yes",
        yes_count_fp="10.00",
        yes_total_cost_dollars="6.000000",
        revenue=1000,
        fee_cost="0.150000",
    )
    (record,) = history.build_records([], [settlement])

    assert record.payout == Decimal("10")
    assert record.paid == Decimal("6")
    assert record.pnl == Decimal("3.85")
    assert record.result == "WIN"
    assert record.side == "YES"


def test_a_loser_pays_nothing_and_keeps_the_fee() -> None:
    settlement = FakeSettlement(
        ticker="KXDOGE15M-26AUG101830-30",
        market_result="yes",
        no_count_fp="5.61",
        no_total_cost_dollars="3.478200",
        revenue=0,
        fee_cost="0.092600",
    )
    (record,) = history.build_records([], [settlement])

    assert record.side == "NO"
    assert record.payout == Decimal("0")
    assert record.pnl == Decimal("-3.570800")
    assert record.result == "LOSS"


def test_average_entry_is_size_weighted_across_fills() -> None:
    ticker = "KXBTC15M-26AUG101830-T1"
    fills = [
        FakeFill(
            fill_id="1",
            ticker=ticker,
            outcome_side="yes",
            count_fp="10.00",
            yes_price_dollars="0.6000",
            no_price_dollars="0.4000",
        ),
        FakeFill(
            fill_id="2",
            ticker=ticker,
            outcome_side="yes",
            count_fp="30.00",
            yes_price_dollars="0.7000",
            no_price_dollars="0.3000",
        ),
    ]
    settlement = FakeSettlement(
        ticker=ticker,
        market_result="yes",
        yes_count_fp="40.00",
        yes_total_cost_dollars="27.000000",
        revenue=4000,
    )
    (record,) = history.build_records(history.merge_fills(fills), [settlement])

    # The simple mean would be 0.65; forty contracts cost $27.00, not $26.00.
    assert record.average_entry == Decimal("0.675")
    assert record.fills == 2


def test_entry_falls_back_to_the_settlement_when_fills_are_incomplete() -> None:
    ticker = "KXBTC15M-26AUG101830-T1"
    fills = [
        FakeFill(
            fill_id="1",
            ticker=ticker,
            outcome_side="yes",
            count_fp="10.00",
            yes_price_dollars="0.6000",
            no_price_dollars="0.4000",
        )
    ]
    settlement = FakeSettlement(
        ticker=ticker,
        market_result="yes",
        yes_count_fp="40.00",
        yes_total_cost_dollars="27.000000",
        revenue=4000,
    )
    (record,) = history.build_records(history.merge_fills(fills), [settlement])

    assert record.average_entry == Decimal("0.675")
    assert record.contracts == Decimal("40.00")


def test_a_position_closed_before_expiry_is_not_a_total_loss() -> None:
    """Both counts non-zero means Kalshi already offset the matched pairs.

    Bought 17.65 YES at $0.84, sold at $0.99 - which the API books as
    acquiring 17.65 NO at $0.01. Revenue is zero because nothing survived to
    expiry, yet the round trip made money.
    """
    settlement = FakeSettlement(
        ticker="KXBTCD-26JUL1821-T64699.99",
        market_result="yes",
        yes_count_fp="17.65",
        yes_total_cost_dollars="14.826000",
        no_count_fp="17.65",
        no_total_cost_dollars="0.176500",
        revenue=0,
        fee_cost="0.166100",
    )
    (record,) = history.build_records([], [settlement])

    assert record.side == "YES+NO"
    assert record.settlement_payout == Decimal("0")
    assert record.offset_credit == Decimal("17.65")
    assert record.payout == Decimal("17.65")
    assert record.pnl == Decimal("2.481400")
    assert record.result == "WIN"


def test_a_partial_close_adds_the_offset_credit_to_the_remaining_revenue() -> None:
    settlement = FakeSettlement(
        ticker="KXBTC15M-26JUL281815-15",
        market_result="no",
        yes_count_fp="13.10",
        yes_total_cost_dollars="9.694000",
        no_count_fp="24.96",
        no_total_cost_dollars="19.968000",
        revenue=1186,
        fee_cost="0.456100",
    )
    (record,) = history.build_records([], [settlement])

    assert record.offset_credit == Decimal("13.10")
    assert record.payout == Decimal("24.96")
    assert record.pnl == Decimal("-5.158100")
    assert record.result == "LOSS"


def test_a_traded_ticker_with_no_settlement_is_open_not_dropped() -> None:
    fills = [
        FakeFill(
            fill_id="1",
            ticker="KXETH15M-26AUG101830-T1",
            outcome_side="no",
            count_fp="4.00",
            yes_price_dollars="0.3000",
            no_price_dollars="0.7000",
            fee_cost="0.050000",
        )
    ]
    (record,) = history.build_records(history.merge_fills(fills), [])

    assert record.result == "OPEN"
    assert record.payout is None
    assert record.pnl is None
    assert record.paid == Decimal("2.8")
    assert record.settled is False
    assert record.when == BASE


def test_an_open_market_held_both_ways_is_flagged_as_matched() -> None:
    """Two legs at $0.505 each is not $1.01 of directional risk."""
    ticker = "KXSAVEACT-27-JAN04"
    fills = [
        FakeFill(
            fill_id="1",
            ticker=ticker,
            outcome_side="yes",
            count_fp="1.00",
            yes_price_dollars="0.5100",
            no_price_dollars="0.4900",
            fee_cost="0.010000",
        ),
        FakeFill(
            fill_id="2",
            ticker=ticker,
            outcome_side="no",
            count_fp="1.00",
            yes_price_dollars="0.5000",
            no_price_dollars="0.5000",
        ),
    ]
    (record,) = history.build_records(history.merge_fills(fills), [])
    totals = history.summarise([record])

    assert record.side == "YES+NO"
    assert record.offset_contracts == Decimal("1.00")
    # Nothing has settled, so no credit is claimed - only the fact of a match.
    assert record.offset_credit == Decimal("0")
    assert record.pnl is None
    assert totals.matched_open_contracts == Decimal("1.00")
    assert "not directional" in history.render_totals(
        totals, None, colour=False, shown=1
    )


def test_records_are_newest_first() -> None:
    settlements = [
        FakeSettlement(
            ticker="KXBTC15M-old-T1",
            market_result="yes",
            yes_count_fp="1.00",
            yes_total_cost_dollars="0.500000",
            revenue=100,
            settled_time=BASE - timedelta(days=5),
        ),
        FakeSettlement(
            ticker="KXBTC15M-new-T1",
            market_result="yes",
            yes_count_fp="1.00",
            yes_total_cost_dollars="0.500000",
            revenue=100,
            settled_time=BASE,
        ),
    ]
    records = history.build_records([], settlements)

    assert [r.ticker for r in records] == ["KXBTC15M-new-T1", "KXBTC15M-old-T1"]


# ---------------------------------------------------------------- totals


def _settled(ticker: str, cost: str, revenue: int, fee: str = "0.000000"):
    return FakeSettlement(
        ticker=ticker,
        market_result="yes",
        yes_count_fp="10.00",
        yes_total_cost_dollars=cost,
        revenue=revenue,
        fee_cost=fee,
    )


def test_totals_reconcile_stake_payout_and_roi() -> None:
    records = history.build_records(
        [],
        [
            _settled("KXBTC15M-a-T1", "6.000000", 1000, "0.100000"),
            _settled("KXBTC15M-b-T1", "4.000000", 0, "0.100000"),
        ],
    )
    totals = history.summarise(records)

    assert totals.settled == 2
    assert totals.wins == 1 and totals.losses == 1
    assert totals.win_rate == Decimal("0.5")
    assert totals.staked == Decimal("10")
    assert totals.paid_out == Decimal("10")
    assert totals.fees == Decimal("0.2")
    assert totals.net_pnl == Decimal("-0.2")
    assert totals.roi == Decimal("-0.02")
    assert totals.revenue_winners == 1


def test_open_markets_are_exposure_not_stake() -> None:
    fills = [
        FakeFill(
            fill_id="1",
            ticker="KXSOL15M-open-T1",
            outcome_side="yes",
            count_fp="2.00",
            yes_price_dollars="0.5000",
            no_price_dollars="0.5000",
        )
    ]
    records = history.build_records(
        history.merge_fills(fills), [_settled("KXBTC15M-a-T1", "6.000000", 1000)]
    )
    totals = history.summarise(records)

    assert totals.settled == 1
    assert totals.open_markets == 1
    assert totals.staked == Decimal("6")
    assert totals.open_exposure == Decimal("1")
    # Staked, paid out and fees must still reconcile to the net on their own.
    assert totals.fees == ZERO_FEE
    assert totals.paid_out - totals.staked - totals.fees == totals.net_pnl


def test_win_rate_and_roi_are_none_rather_than_a_division_by_zero() -> None:
    totals = history.summarise([])

    assert totals.win_rate is None
    assert totals.roi is None


def test_period_balances_reverse_cash_events_and_total_transfers() -> None:
    events = [
        history.CashEvent(BASE - timedelta(hours=1), Decimal("10"), "deposit"),
        history.CashEvent(BASE, Decimal("4.25"), "settlement"),
        history.CashEvent(BASE + timedelta(hours=1), Decimal("-3"), "withdrawal"),
    ]

    period = history.period_balances(
        Decimal("11.25"),
        events,
        BASE - timedelta(minutes=30),
        BASE + timedelta(hours=2),
    )

    assert period.starting_balance == Decimal("10")
    assert period.ending_balance == Decimal("11.25")
    assert period.deposits == ZERO_FEE
    assert period.withdrawals == Decimal("3")
    assert period.net_transfers == Decimal("-3")
    assert period.balance_change == Decimal("1.25")


# ---------------------------------------------------------------- filtering


def _three_assets() -> list[history.MarketRecord]:
    return history.build_records(
        [],
        [
            FakeSettlement(
                ticker="KXBTC15M-a-T1",
                market_result="yes",
                yes_count_fp="1.00",
                yes_total_cost_dollars="0.500000",
                revenue=100,
                settled_time=BASE,
            ),
            FakeSettlement(
                ticker="KXETH15M-b-T1",
                market_result="yes",
                yes_count_fp="1.00",
                yes_total_cost_dollars="0.500000",
                revenue=100,
                settled_time=BASE - timedelta(days=30),
            ),
        ],
    )


def test_asset_filter_uses_the_series_prefix() -> None:
    selected = history.filter_records(_three_assets(), asset="btc")

    assert [r.ticker for r in selected] == ["KXBTC15M-a-T1"]


def test_since_filter_is_inclusive_of_the_day_given() -> None:
    selected = history.filter_records(
        _three_assets(), since=datetime(2026, 8, 10, tzinfo=UTC)
    )

    assert [r.ticker for r in selected] == ["KXBTC15M-a-T1"]


def test_until_filter_is_inclusive_and_crypto_filter_is_series_exact() -> None:
    records = _three_assets() + history.build_records(
        [],
        [
            FakeSettlement(
                ticker="KXBTCD-26AUG10-T1",
                market_result="yes",
                yes_count_fp="1.00",
                yes_total_cost_dollars="0.500000",
                revenue=100,
                settled_time=BASE,
            )
        ],
    )

    selected = history.filter_records(
        records,
        until=BASE,
        crypto_15m_only=True,
    )

    assert {record.ticker for record in selected} == {
        "KXBTC15M-a-T1",
        "KXETH15M-b-T1",
    }


def test_requested_market_exclusions_happen_before_all_other_filters() -> None:
    excluded_records = history.build_records(
        [],
        [
            FakeSettlement(
                ticker=ticker,
                market_result="yes",
                yes_count_fp="1.00",
                yes_total_cost_dollars="0.500000",
                revenue=100,
                settled_time=BASE,
            )
            for ticker in history.EXCLUDED_MARKET_TICKERS
        ],
    )
    included = _three_assets()[0]

    selected = history.filter_records(
        [included, *excluded_records],
        since=BASE - timedelta(days=1),
        until=BASE + timedelta(days=1),
        crypto_15m_only=True,
    )

    assert selected == [included]
    assert history.summarise(selected).markets == 1


def test_open_filter_drops_everything_settled() -> None:
    assert history.filter_records(_three_assets(), open_only=True) == []


def test_asset_labels_cover_the_series_the_bot_trades_and_the_rest() -> None:
    assert history.asset_from_ticker("KXBTC15M-26AUG101830-T1") == "BTC"
    assert history.asset_from_ticker("KXHYPE15M-26AUG101830-T1") == "HYPE"
    assert history.asset_from_ticker("KXBTCD-26JUL2417-T65999.99") == "BTCD"
    assert history.asset_from_ticker("KXNBAGAME-25NOV29DALLAC-DAL") == "NBAGAME"


# ---------------------------------------------------------------- rendering


def _rendered() -> str:
    records = history.build_records(
        [],
        [
            _settled("KXBTC15M-a-T1", "6.000000", 1000, "0.100000"),
            _settled("KXBTC15M-b-T1", "4.000000", 0, "0.100000"),
        ],
    )
    return history.render_table(records, colour=False)


def test_table_columns_line_up() -> None:
    lines = _rendered().splitlines()

    assert len({len(line.rstrip()) for line in lines[1:2]}) == 1
    header, rule, *rows = lines
    assert set(rule) == {"-"}
    starts = [header.index("Paid")]
    for row in rows:
        # Right-aligned money ends where the header ends, not where it starts.
        assert row.index("$") >= starts[0] - 12


def test_no_ansi_escapes_when_colour_is_off() -> None:
    assert "\033[" not in _rendered()


def test_colour_wraps_the_pnl_cell_only_and_after_padding() -> None:
    records = history.build_records(
        [], [_settled("KXBTC15M-a-T1", "6.000000", 1000, "0.100000")]
    )
    plain = history.render_table(records, colour=False).splitlines()[-1]
    tinted = history.render_table(records, colour=True).splitlines()[-1]

    assert tinted.count("\033[") == 2
    assert history._GREEN in tinted
    assert tinted.replace(history._GREEN, "").replace(history._RESET, "") == plain


def test_a_loss_is_red_and_a_flat_row_is_uncoloured() -> None:
    loss = history.build_records([], [_settled("KXBTC15M-b-T1", "4.000000", 0)])
    flat = history.build_records([], [_settled("KXBTC15M-c-T1", "10.000000", 1000)])

    assert history._RED in history.render_table(loss, colour=True)
    assert "\033[" not in history.render_table(flat, colour=True)
    assert flat[0].result == "FLAT"


def test_colour_is_refused_when_stdout_is_not_a_terminal(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(history.sys, "stdout", _NotATty())

    assert history.use_colour(False) is False


def test_no_color_env_beats_a_terminal(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "")
    monkeypatch.setattr(history.sys, "stdout", _IsATty())

    assert history.use_colour(False) is False
    monkeypatch.delenv("NO_COLOR")
    assert history.use_colour(False) is True
    assert history.use_colour(True) is False


class _NotATty:
    def isatty(self) -> bool:
        return False


class _IsATty:
    def isatty(self) -> bool:
        return True


def test_offset_markets_are_footnoted_rather_than_quietly_restated() -> None:
    records = history.build_records(
        [],
        [
            FakeSettlement(
                ticker="KXBTCD-26JUL1821-T64699.99",
                market_result="yes",
                yes_count_fp="17.65",
                yes_total_cost_dollars="14.826000",
                no_count_fp="17.65",
                no_total_cost_dollars="0.176500",
                revenue=0,
            )
        ],
    )
    table = history.render_table(records, colour=False)
    footer = history.render_totals(
        history.summarise(records), Decimal("61.12"), colour=False, shown=1
    )

    assert "WIN*" in table
    assert "closed or partly closed before expiry" in footer
    assert "revenue > 0: 0 of 1" in footer


def test_money_formats_a_negative_sign_outside_the_dollar() -> None:
    assert history.money(Decimal("-3.5")) == "-$3.50"
    assert history.money(Decimal("3.5"), signed=True) == "+$3.50"
    assert history.money(None) == "-"


# ---------------------------------------------------------------- json / csv


def test_json_serialises_money_as_strings() -> None:
    (record,) = history.build_records(
        [], [_settled("KXBTC15M-a-T1", "6.000000", 1000, "0.100000")]
    )
    payload = history.record_to_json(record)

    assert payload["paid"] == "6.000000"
    assert payload["payout"] == "10.00"
    assert isinstance(payload["pnl"], str)
    assert all(
        not isinstance(value, float) for value in payload.values()
    )


def test_totals_json_carries_the_balance_as_a_string() -> None:
    totals = history.summarise(
        history.build_records([], [_settled("KXBTC15M-a-T1", "6.000000", 1000)])
    )
    payload = history.totals_to_json(totals, Decimal("61.1208"))

    assert payload["account_balance"] == "61.1208"
    assert payload["settled"] == 1
    assert payload["win_rate"] == "1.00000000"
    assert payload["roi"] == "0.66666667"


def test_csv_round_trips_every_row(tmp_path: Path) -> None:
    records = history.build_records(
        [],
        [
            _settled("KXBTC15M-a-T1", "6.000000", 1000),
            _settled("KXBTC15M-b-T1", "4.000000", 0),
        ],
    )
    target = tmp_path / "history.csv"
    history.write_csv(target, records)

    with target.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 2
    assert rows[0]["ticker"] == "KXBTC15M-a-T1"
    assert rows[0]["payout"] == "10.00"
    assert set(rows[0]) == set(history.CSV_FIELDS)


# ---------------------------------------------------------------- paging


async def test_pagination_terminates_on_the_empty_string_cursor() -> None:
    pages = [
        FakePage([FakeFill(
            fill_id="1",
            ticker="KXBTC15M-a-T1",
            outcome_side="yes",
            count_fp="1.00",
            yes_price_dollars="0.5",
            no_price_dollars="0.5",
        )], cursor="next"),
        FakePage([FakeFill(
            fill_id="2",
            ticker="KXBTC15M-b-T1",
            outcome_side="yes",
            count_fp="1.00",
            yes_price_dollars="0.5",
            no_price_dollars="0.5",
        )], cursor=""),
    ]
    seen: list[str | None] = []

    async def fetch(cursor: str | None):
        seen.append(cursor)
        return pages[len(seen) - 1]

    collected = await history.collect_pages(fetch, "fills")

    assert len(collected) == 2
    assert seen == [None, "next"]


async def test_pagination_stops_on_an_empty_page() -> None:
    calls = 0

    async def fetch(cursor: str | None):
        nonlocal calls
        calls += 1
        return FakePage([], cursor="still-here")

    assert await history.collect_pages(fetch, "fills") == []
    assert calls == 1


async def test_a_repeating_cursor_cannot_loop_forever() -> None:
    calls = 0

    async def fetch(cursor: str | None):
        nonlocal calls
        calls += 1
        return FakePage([object()], cursor="same")

    collected = await history.collect_pages(fetch, "fills")

    assert calls == 2
    assert len(collected) == 2


async def test_throttling_is_retried_then_the_page_arrives(monkeypatch) -> None:
    slept: list[float] = []

    async def no_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(history.asyncio, "sleep", no_sleep)
    attempts = 0

    async def operation():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ApiException(status=429)
        return "ok"

    assert await history.with_backoff(operation) == "ok"
    assert slept == [0.5, 1.0]


async def test_a_non_throttling_error_is_not_retried() -> None:
    attempts = 0

    async def operation():
        nonlocal attempts
        attempts += 1
        raise ApiException(status=403)

    with pytest.raises(ApiException):
        await history.with_backoff(operation)
    assert attempts == 1


# ---------------------------------------------------------------- cli


def test_since_rejects_anything_that_is_not_a_date() -> None:
    assert history.parse_since("2026-07-01") == datetime(2026, 7, 1, tzinfo=UTC)
    with pytest.raises(Exception, match="YYYY-MM-DD"):
        history.parse_since("last tuesday")


def test_defaults_show_the_requested_crypto_window() -> None:
    args = history.parse_args([])

    assert args.limit == 0
    assert args.asset is None
    assert args.since == history.DEFAULT_START
    assert args.until == history.DEFAULT_END
    assert args.all_markets is False
    assert args.open is False
    assert args.json is False
    assert args.csv is None
    assert args.no_color is False


def _module_ast() -> ast.Module:
    return ast.parse(Path(history.__file__).read_text(encoding="utf-8"))


def test_the_module_imports_nothing_that_can_trade_or_write_bot_state() -> None:
    """An AST check, not a substring one: the docstring names both writers."""
    imported: set[str] = set()
    for node in ast.walk(_module_ast()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert "sqlite3" not in imported
    assert not any(name.startswith("execution") for name in imported)
    assert not any(name.startswith("monitoring") for name in imported)


def test_no_order_endpoint_is_named_anywhere_in_the_module() -> None:
    referenced: set[str] = set()
    for node in ast.walk(_module_ast()):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)

    assert "OrdersApi" not in referenced
    assert not any(name.startswith("create_order") for name in referenced)
    assert not any(name.startswith("batch_create") for name in referenced)
    # Every read this script performs is a GET on the portfolio or the archive.
    assert {"get_fills", "get_fills_historical", "get_settlements"} <= referenced


def test_the_only_thing_written_anywhere_is_the_requested_csv() -> None:
    writers = [
        node.name
        for node in ast.walk(_module_ast())
        if isinstance(node, ast.FunctionDef)
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in {"open", "write_text", "write_bytes"}
    ]

    assert writers == ["write_csv"]
