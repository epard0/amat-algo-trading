from dataclasses import replace
from decimal import Decimal

import pytest

from kalshi_perps.domain import Order, dec
from kalshi_perps.paper import PaperBroker

T = "KXBTCPERP"


def broker(tmp_path, btc, **kwargs):
    return PaperBroker(tmp_path / "p.sqlite", {T: btc}, **kwargs)


def order(key, count=100, price="8.01", side="bid", **kw):
    return Order(key, T, side, dec(count), dec(price), 1, **kw)


def test_contract_multiplier_not_applied_twice(tmp_path, btc, quote_btc):
    b = broker(tmp_path, btc, fee_rate="0")
    b.snapshot("start", 0, {T: quote_btc})
    b.submit(order("buy"), timestamp_ms=0)
    b.snapshot("move", 1, {T: dict(quote_btc, mark="8.11")})
    assert dec(b.report()["pnl"]) == 10
    assert dec(b.report()["gross_notional"]) == 811
    assert btc.api_price("80000") == 8


def test_partial_fill_and_depth_not_reused(tmp_path, btc, quote_btc):
    b = broker(tmp_path, btc)
    b.snapshot("start", 0, {T: dict(quote_btc, asks=[["8.01", "3"]])})
    r = b.submit(order("buy", 10), timestamp_ms=0)
    assert r["fill_count"] == "3"
    assert r["canceled_count"] == "7"
    assert b.submit(order("next", 10), timestamp_ms=0)["fill_count"] == "0"


def test_fok_all_or_nothing(tmp_path, btc, quote_btc):
    b = broker(tmp_path, btc)
    b.snapshot("s", 0, {T: dict(quote_btc, asks=[["8.01", "3"]])})
    assert (
        b.submit(order("fok", 10, time_in_force="fill_or_kill"), timestamp_ms=0)["fill_count"]
        == "0"
    )
    assert b.submit(order("ioc", 3), timestamp_ms=0)["fill_count"] == "3"


def test_persistence_and_order_identity(tmp_path, btc, quote_btc):
    b = broker(tmp_path, btc)
    b.snapshot("s", 0, {T: quote_btc})
    first = b.submit(order("buy"), timestamp_ms=0)
    expected = b.report()
    b.store.close()
    b = broker(tmp_path, btc)
    assert b.submit(order("buy"), timestamp_ms=0) == first
    assert b.report() == expected
    with pytest.raises(ValueError, match="identity"):
        b.submit(order("buy", 101), timestamp_ms=0)


def test_funding_before_close_and_settlement_equity(tmp_path, btc, quote_btc):
    b = broker(tmp_path, btc, fee_rate="0")
    b.snapshot("s", 0, {T: quote_btc})
    b.submit(order("buy"), timestamp_ms=0)
    funding = [dict(event_id="f1", ticker=T, timestamp_ms=1, rate=".01", mark="8.11")]
    q = dict(quote_btc, mark="8.11")
    b.snapshot("fund", 1, {T: q}, funding, [T])
    assert dec(b.report()["equity"]) == Decimal("10001.89")
    assert dec(b.report()["funding"]) == Decimal("-8.11")
    assert dec(b.report()["realized_pnl"]) == 10
    assert b.report()["positions"][T]["entry"] == "8.11"
    b.snapshot("fund", 1, {T: q}, funding, [T])
    assert dec(b.report()["funding"]) == Decimal("-8.11")


def test_reduce_only_caps_and_no_reversal(tmp_path, btc, quote_btc):
    b = broker(tmp_path, btc)
    b.snapshot("s", 0, {T: quote_btc})
    b.submit(order("buy", 10), timestamp_ms=0)
    result = b.submit(order("close", 100, "7.99", "ask", reduce_only=True), timestamp_ms=0)
    assert result["fill_count"] == "10"
    assert dec(b.report()["positions"][T]["count"]) == 0
    with pytest.raises(ValueError, match="reduce-only"):
        b.submit(order("close-again", 10, "7.99", "ask", reduce_only=True), timestamp_ms=0)


def test_margin_rejection_rolls_back(tmp_path, btc, quote_btc):
    b = broker(tmp_path, btc, collateral="10")
    b.snapshot("s", 0, {T: quote_btc})
    expected = b.report()
    with pytest.raises(ValueError, match="initial margin"):
        b.submit(order("huge"), timestamp_ms=0)
    assert b.report() == expected
    assert b.store.state()["books"][T]["asks"][0][1] == "10000"


def test_gap_liquidation_keeps_negative_equity(tmp_path, btc, quote_btc):
    b = broker(tmp_path, btc, collateral="1000", fee_rate="0")
    b.snapshot("s", 0, {T: quote_btc})
    b.submit(order("short", 200, "7.99", "ask"), timestamp_ms=0)
    q = {"bids": [["19.99", "10000"]], "asks": [["20.01", "10000"]], "mark": "20"}
    b.snapshot("gap", 1, {T: q})
    assert b.report()["halted"] is True
    assert dec(b.report()["equity"]) < 0


def test_identity_and_metadata_binding(tmp_path, btc):
    b = broker(tmp_path, btc)
    b.store.close()
    with pytest.raises(ValueError, match="identity mismatch"):
        broker(tmp_path, replace(btc, version=2))


def test_stale_and_conflicting_snapshots(tmp_path, btc, quote_btc):
    b = broker(tmp_path, btc)
    b.snapshot("s", 10, {T: quote_btc})
    with pytest.raises(ValueError, match="increase"):
        b.snapshot("old", 9, {T: quote_btc})
    with pytest.raises(ValueError, match="identity"):
        b.snapshot("s", 11, {T: quote_btc})


@pytest.mark.parametrize("bad", ["NaN", "Infinity", True, "wrong"])
def test_invalid_decimals(bad):
    with pytest.raises((ValueError, TypeError)):
        dec(bad)


def test_all_instrument_economics(instruments):
    assert len(instruments) == 20
    for spec in instruments.values():
        if spec.status != "active" or spec.underlying_multiplier != 1:
            with pytest.raises(ValueError):
                spec.api_price(100)
        else:
            assert spec.api_price(100) == spec.contract_size * 100


def test_late_native_funding_uses_historical_holding(tmp_path, btc, quote_btc):
    b = broker(tmp_path, btc, fee_rate="0")
    b.snapshot("open", 0, {T: quote_btc})
    b.submit(order("buy", 100), timestamp_ms=0)
    b.snapshot("after-funding", 20, {T: quote_btc})
    b.submit(order("close", 100, "7.99", "ask", reduce_only=True), timestamp_ms=20)
    event = dict(event_id="published-late", ticker=T, timestamp_ms=10, rate=".001", mark="8")
    result = b.apply_historical_funding(event)
    assert dec(result["held_contracts"]) == 100
    assert dec(result["payment"]) == dec("-.8")
    before = b.report()
    assert b.apply_historical_funding(event) == result
    assert b.report() == before
