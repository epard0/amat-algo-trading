from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from kalshi_perps.comparison import prepare, stop_all
from kalshi_perps.comparison_metrics import metrics
from kalshi_perps.config import Config
from kalshi_perps.domain import HOUR, Order, dec
from kalshi_perps.leverage import maximum_notional, maximum_weights
from kalshi_perps.paper import PaperBroker
from kalshi_perps.runtime import Runner

ROOT = Path(__file__).resolve().parents[1]
T = "KXBTCPERP"


def leverage():
    return dict(
        long={"1000": "10", "10000": "10", "100000": "10", "1000000": "10"},
        short={"1000": "5", "10000": "5", "100000": "5", "1000000": "5"},
        initial_margin_multiplier="1.3",
    )


def test_maximum_leverage_respects_side_size_and_initial_margin():
    m = leverage()
    assert abs(maximum_notional(m, 1, dec("1300")) - dec("10000")) < dec(".000001")
    assert abs(maximum_notional(m, -1, dec("1300")) - dec("5000")) < dec(".000001")
    m["long"] = {"1000": "10", "10000": "5", "100000": "2", "1000000": "1"}
    target = maximum_notional(m, 1, dec("10000"))
    assert target < dec("20000")


def test_active_capital_uses_maximum_estimates_and_flat_stays_flat():
    weights, limits = maximum_weights(
        {T: ".1", "KXETHPERP": "0"},
        {T: ".25", "KXETHPERP": ".75"},
        {T: {"leverage": leverage()}},
        dec("10000"),
    )
    assert weights[T] > 7
    assert weights["KXETHPERP"] == 0
    assert dec(limits[T]["initial_leverage"]) == dec(10) / dec("1.3")


def test_dynamic_paper_admits_more_than_original_two_x_and_rebalances(tmp_path, btc, quote_btc):
    b = PaperBroker(tmp_path / "p.sqlite", {T: btc}, leverage_mode="venue_max")

    class Strategy:
        identity = "test"
        profile = SimpleNamespace(legs=[dict(ticker=T, allocation="1")])

        def decide(self, rows, boundary):
            return dict(
                ready=True, weights={T: ".98"}, input_hash=str(boundary), reasons={T: "hold"}
            )

    config = SimpleNamespace(
        mode="paper",
        raw=dict(
            paper={"leverage_mode": "venue_max"},
            risk=dict(
                max_gross="100",
                max_order_notional="1000000",
                max_spread_bps="100",
                max_loss_fraction=".35",
            ),
        ),
    )
    runner = Runner(config, Strategy(), b)
    quote = dict(quote_btc, leverage=leverage())
    event = dict(event_id="one", timestamp_ms=HOUR, quotes={T: quote})
    rows = {T: [dict(timestamp_ms=0, open="8", high="8.1", low="7.9", close="8", tradable=1)]}
    first = runner.process(event, rows)
    assert first["orders"]
    assert dec(first["report"]["gross_notional"]) / dec(first["report"]["equity"]) > 2
    second = runner.process(dict(event, event_id="two", timestamp_ms=HOUR + 1), rows)
    assert second["orders"]  # Same day/sign still resizes maximum-leverage exposure.


def test_take_profit_episode_net_includes_fees_and_late_funding(tmp_path, btc, quote_btc):
    b = PaperBroker(tmp_path / "p.sqlite", {T: btc}, fee_rate=".001")
    b.snapshot("s", 0, {T: quote_btc})
    b.submit(Order("buy", T, "bid", 100, "8.01", 1), timestamp_ms=0)
    q = dict(bids=[["8.21", "1000"]], asks=[["8.23", "1000"]], mark="8.22")
    b.snapshot("s2", 20, {T: q})
    b.apply_historical_funding(
        dict(event_id="fund", ticker=T, timestamp_ms=10, rate=".01", mark="8.1")
    )
    b.submit(Order("sell", T, "ask", 100, "8.21", 1, True), timestamp_ms=20, reason="take_profit")
    report = metrics(b.store, 0, 100)
    assert dec(report["net_pnl"]) == dec("10.278")
    assert report["take_profit_closes"] == report["profitable_take_profit_closes"] == 1
    assert dec(report["closed_episodes"][0]["net_pnl"]) == dec("10.278")
    assert report["take_profit_closes_per_30d"] is None


def test_campaign_accounts_are_fresh_isolated_and_never_live(tmp_path, monkeypatch):
    base = Config.load(ROOT / "configs/paper.toml")
    raw = deepcopy(base.raw)
    raw["database"] = str(tmp_path / "original.sqlite")
    config = Config(base.path, raw)
    manifest = prepare(config)
    assert len(manifest["accounts"]) == 9
    assert len({a["config"]["database"] for a in manifest["accounts"]}) == 9
    assert all(
        a["config"]["mode"] == "paper" and a["config"]["execution_enabled"] is False
        for a in manifest["accounts"]
    )
    assert all(a["config"]["paper"]["collateral"] == "10000" for a in manifest["accounts"])
    assert prepare(config)["id"] == manifest["id"]
    monkeypatch.setattr(
        "kalshi_perps.comparison.run_all", lambda *args, **kwargs: {"state": "stopped"}
    )
    assert stop_all(config)["state"] == "stopped"
    live = Config(base.path, dict(raw, mode="live"))
    with pytest.raises(ValueError, match="paper-only"):
        prepare(live)
