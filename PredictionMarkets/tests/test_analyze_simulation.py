from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from core.strategy_brain.strategies.pair_arb import PairArbAssessment
from execution.trade_ledger import TradeLedger
from research.analyze_simulation import analyze, best_opportunity


def test_hindsight_opportunity_rounds_contracts_down_to_order_precision():
    rows = [
        {
            "ticker": "KXBTC15M-TEST",
            "observed_at": "2026-08-23T00:00:00+00:00",
            "seconds_to_close": "600",
            "yes_bid": "0.32",
            "yes_ask": "0.33",
            "yes_bid_size": "100",
            "yes_ask_size": "100",
            "fee_multiplier": "1",
        },
        {
            "ticker": "KXBTC15M-TEST",
            "observed_at": "2026-08-23T00:01:00+00:00",
            "seconds_to_close": "540",
            "yes_bid": "0.50",
            "yes_ask": "0.51",
            "yes_bid_size": "100",
            "yes_ask_size": "100",
            "fee_multiplier": "1",
        },
    ]

    result = best_opportunity(
        rows,
        "yes",
        Decimal("2"),
        Decimal("0.01"),
        Decimal("0.03"),
    )

    assert result is not None
    assert result.contracts_at_fixed_budget == 5.78


def test_pair_extrema_exclude_incomplete_books(tmp_path):
    path = tmp_path / "analysis.db"
    ledger = TradeLedger(path, Decimal("20"))
    run_id = ledger.start_data_run(
        mode="paper", environment="production", strategy="arb", assets=("BTC",)
    )
    market = SimpleNamespace(asset="BTC", ticker="KXBTC15M-TEST")
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    ledger.record_pair_arb_observation(
        run_id=run_id,
        cycle_id="missing",
        market=market,
        observed_at=now,
        assessment=PairArbAssessment(False, "book", "both bid books are required"),
        simulated=False,
    )
    ledger.record_pair_arb_observation(
        run_id=run_id,
        cycle_id="complete",
        market=market,
        observed_at=now,
        assessment=PairArbAssessment(
            False,
            "net_edge",
            "no edge",
            yes_ask=Decimal("0.55"),
            no_ask=Decimal("0.46"),
            pair_cost=Decimal("1.01"),
            gross_edge=Decimal("-0.01"),
            net_edge=Decimal("-0.04"),
        ),
        simulated=False,
    )
    ledger.record_pair_arb_observation(
        run_id=run_id,
        cycle_id="stale",
        market=market,
        observed_at=now,
        assessment=PairArbAssessment(
            False,
            "stale_leg",
            "one leg is suspiciously close to zero",
            yes_ask=Decimal("0.999"),
            no_ask=Decimal("0.001"),
            pair_cost=Decimal("1"),
            net_edge=Decimal("-0.0002"),
        ),
        simulated=False,
    )
    ledger.finish_data_run(run_id, completed=True)
    ledger.close()

    report = analyze(
        path,
        run_id=run_id,
        fixed_budget=Decimal("2"),
        minimum_gain=Decimal("0.05"),
        maximum_spread=Decimal("0.03"),
    )

    assert report["pair_arb_shadow"]["incomplete_books"] == 1
    assert report["pair_arb_shadow"]["crossed_books"] == 0
    assert report["pair_arb_shadow"]["positive_after_fees"] == 0
    assert report["pair_arb_shadow"]["gate_counts"] == {
        "book": 1,
        "net_edge": 1,
        "stale_leg": 1,
    }
    assert report["pair_arb_shadow"]["minimum_pair_cost"] == 1.0
    assert report["pair_arb_shadow"]["maximum_net_edge"] == -0.0002
    assert report["pair_arb_shadow"]["minimum_guarded_pair_cost"] == 1.01
    assert report["pair_arb_shadow"]["maximum_guarded_net_edge"] == -0.04
