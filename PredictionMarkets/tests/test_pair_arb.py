from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from core.strategy_brain.strategies.pair_arb import assess_pair_arb
from execution.pair_arb_engine import PairArbEngine
from execution.trade_ledger import TradeLedger


D = Decimal
NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)


def assess(yes=("0.60", "10"), no=("0.45", "10"), **overrides):
    values = {
        "yes_bid_levels": [(D(yes[0]), D(yes[1]))],
        "no_bid_levels": [(D(no[0]), D(no[1]))],
        "fixed_budget": D("2"),
        "fee_multiplier": D("1"),
        "minimum_net_edge": D("0.02"),
        "minimum_leg_price": D("0.02"),
        "minimum_contracts": D("1"),
    }
    values.update(overrides)
    return assess_pair_arb(**values)


def test_normal_kalshi_book_cannot_manufacture_a_pair_arb() -> None:
    result = assess(yes=("0.55", "10"), no=("0.42", "10"))
    assert result.yes_ask == D("0.58")
    assert result.no_ask == D("0.45")
    assert result.pair_cost == D("1.03")
    assert not result.approved
    assert result.gate == "net_edge"


def test_crossed_pair_requires_equal_depth_and_clears_both_taker_fees() -> None:
    result = assess(yes=("0.62", "10"), no=("0.45", "10"))
    assert result.guaranteed_payout == D("1")
    assert result.pair_cost == D("0.93")
    assert result.approved
    assert result.contracts == D("2")
    assert result.net_edge > D("0.02")
    assert result.modeled_profit == result.contracts * result.net_edge


def test_one_shallow_leg_refuses_the_entire_pair() -> None:
    result = assess(yes=("0.62", "0.5"), no=("0.45", "10"))
    assert not result.approved
    assert result.gate == "depth"
    assert result.contracts == D("0")


def test_pair_engine_records_shadow_performance_once_per_unchanged_state(tmp_path) -> None:
    ledger = TradeLedger(tmp_path / "arb.db", D("20"))
    run_id = ledger.start_data_run(
        mode="paper", environment="production", strategy="arb", assets=("BTC",)
    )
    settings = SimpleNamespace(
        environment="production", trade_budget_usd=D("2"),
        pair_arb_min_net_edge=D("0.02"), pair_arb_min_leg_price=D("0.02"),
        min_contracts=D("1"),
    )

    class Decisions:
        def record_event(self, *args, **kwargs):
            return None

    engine = PairArbEngine(settings=settings, ledger=ledger, decisions=Decisions())
    market = SimpleNamespace(
        asset="BTC", ticker="KXBTC15M-ARB", fee_multiplier=D("1")
    )
    snapshot = SimpleNamespace(
        observed_at=NOW,
        yes_levels=((D("0.62"), D("10")),),
        no_levels=((D("0.45"), D("10")),),
    )
    engine.scan(run_id=run_id, cycle_id="one", market=market, snapshot=snapshot)
    snapshot.observed_at = NOW + timedelta(seconds=5)
    engine.scan(run_id=run_id, cycle_id="two", market=market, snapshot=snapshot)

    rows = ledger.connection.execute(
        "SELECT approved, simulated, guaranteed_payout FROM pair_arb_observations "
        "ORDER BY observed_at"
    ).fetchall()
    assert [(row["approved"], row["simulated"]) for row in rows] == [(1, 1), (1, 0)]
    assert {row["guaranteed_payout"] for row in rows} == {"1"}
    assert engine.approved == 1
    ledger.close()
