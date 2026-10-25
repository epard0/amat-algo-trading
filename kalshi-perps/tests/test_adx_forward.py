import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from kalshi_perps.adx_forward import account_metrics, execute_targets
from kalshi_perps.config import Config
from kalshi_perps.domain import dec, registry
from kalshi_perps.paper import PaperBroker

ROOT = Path(__file__).resolve().parents[1]
T = "KXSOLPERP"


def setup(tmp_path):
    inst = registry(json.loads((ROOT / "configs/instruments.json").read_text()))[T]
    broker = PaperBroker(
        tmp_path / "paper.sqlite",
        {T: inst},
        policy_hash="frozen-test",
        leverage_mode="venue_max",
        max_spread_bps="50",
    )
    state = broker.store.state()
    state["forward_start_ms"] = 0
    broker.store.save(state)
    config = Config.load(ROOT / "configs/paper.toml")
    runner = SimpleNamespace(
        config=config,
        broker=broker,
        risk=config.raw["risk"],
        symbols=(T,),
        strategy=SimpleNamespace(
            identity="strategy", profile=SimpleNamespace(legs=[dict(ticker=T, allocation="1")])
        ),
    )
    return runner


def event(now, size="100000"):
    return dict(
        event_id=str(now),
        timestamp_ms=now,
        quotes={
            T: dict(
                bids=[["9.99", size]],
                asks=[["10.01", size]],
                mark="10",
                leverage=dict(
                    long={"1000000": "4"},
                    short={"1000000": "4"},
                    initial_margin_multiplier="1.3",
                    liquidation_threshold="1",
                ),
            )
        },
    )


def step(runner, now, level, entry=100, reason="hold", size="100000"):
    e = event(now, size)
    runner.broker.snapshot(e["event_id"], now, e["quotes"])
    d = dict(
        as_of_ms=now,
        exit_policy={
            T: dict(level=level, entry_ms=entry if dec(level) > 0 else None, reason=reason)
        },
    )
    return execute_targets(runner, e, d)


def count(runner):
    return dec(runner.broker.store.state()["positions"][T]["count"])


def test_partial_ioc_target_survives_restart_and_never_rebuys(tmp_path):
    r = setup(tmp_path)
    step(r, 100, ".98", reason="entry")
    initial = count(r)
    step(r, 200, ".735", reason="partial_tp", size="10")
    assert count(r) == initial - 10
    frozen = r.broker.store.state()["adx_execution"][T]["pending"]["target"]
    # Reload the same durable account through a new broker instance.
    r.broker.store.close()
    inst = registry(json.loads((ROOT / "configs/instruments.json").read_text()))[T]
    r.broker = PaperBroker(
        tmp_path / "paper.sqlite",
        {T: inst},
        policy_hash="frozen-test",
        leverage_mode="venue_max",
        max_spread_bps="50",
    )
    step(r, 300, ".735", reason="partial_tp")
    assert count(r) == dec(frozen)
    remaining = count(r)
    for now in (400, 500, 600):
        step(r, now, ".735", reason="partial_tp")
    assert count(r) <= remaining
    stats = account_metrics(r.broker.store, 0, 600)
    assert stats["take_profit_actions"] == 1
    assert stats["partial_take_profit_actions"] == 1
    assert stats["take_profit_actions_completed"] == 1


def test_second_ladder_stage_during_pending_sale_uses_original_residual(tmp_path):
    r = setup(tmp_path)
    step(r, 100, ".98")
    initial = count(r)
    step(r, 200, ".735", size="10", reason="partial_tp")
    first_target = dec(r.broker.store.state()["adx_execution"][T]["pending"]["target"])
    step(r, 300, ".49", reason="partial_tp")
    expected = (first_target * dec(".49") / dec(".735")).to_integral_value(rounding="ROUND_DOWN")
    assert count(r) == expected
    assert count(r) >= initial / 2 - 2


def test_carried_startup_signal_skipped_until_fresh_episode(tmp_path):
    r = setup(tmp_path)
    s = r.broker.store.state()
    s["forward_start_ms"] = 150
    r.broker.store.save(s)
    result = step(r, 200, ".98", entry=100)
    assert result["ignored_carried_signals"][T]
    assert count(r) == 0
    step(r, 300, "0", reason="time_exit")
    step(r, 400, ".98", entry=400)
    assert count(r) > 0


def test_depth_cost_limit_and_funding_affect_actual_pnl(tmp_path):
    r = setup(tmp_path)
    step(r, 100, ".98")
    held = count(r)
    # Funding publishes late after the next observation; charge historical holdings once.
    step(r, 200, ".98")
    funding = dict(event_id="native", ticker=T, timestamp_ms=150, rate=".001", mark="10")
    r.broker.apply_historical_funding(funding)
    r.broker.apply_historical_funding(funding)
    assert dec(r.broker.report()["funding"]) == -held * 10 * dec(".001")
    step(r, 300, "0", reason="take_profit")
    stats = account_metrics(r.broker.store, 0, 300)
    assert stats["take_profit_actions"] == 1
    assert stats["net_pnl"] == r.broker.report()["pnl"]
    assert abs(dec(stats["closed_episode_details"][0]["net_pnl"]) - dec(stats["net_pnl"])) < dec(
        ".00000001"
    )


def test_simultaneous_asset_profit_actions_remain_separate(tmp_path):
    r = setup(tmp_path)
    instruments = registry(json.loads((ROOT / "configs/instruments.json").read_text()))
    r.broker.store.close()
    r.broker = PaperBroker(
        tmp_path / "both.sqlite",
        {t: instruments[t] for t in ("KXDOGEPERP", T)},
        policy_hash="both",
        leverage_mode="venue_max",
        max_spread_bps="50",
    )
    state = r.broker.store.state()
    state["forward_start_ms"] = 0
    r.broker.store.save(state)
    r.symbols = ("KXDOGEPERP", T)
    r.strategy.profile.legs = [
        dict(ticker=t, allocation=str(a)) for t, a in [("KXDOGEPERP", 0.25), (T, 0.75)]
    ]
    for now, level, reason in [(100, ".98", "entry"), (200, ".735", "partial_tp")]:
        e = event(now)
        e["quotes"]["KXDOGEPERP"] = deepcopy(e["quotes"][T])
        r.broker.snapshot(e["event_id"], now, e["quotes"])
        execute_targets(
            r,
            e,
            dict(
                as_of_ms=now,
                exit_policy={t: dict(level=level, entry_ms=100, reason=reason) for t in r.symbols},
            ),
        )
    stats = account_metrics(r.broker.store, 0, 200)
    assert stats["take_profit_actions"] == 2
    assert len(stats["take_profit_action_details"]) == 2
