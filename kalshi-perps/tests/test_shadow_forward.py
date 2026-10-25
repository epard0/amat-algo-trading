from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_adx_forward import count, event, setup

from kalshi_perps.domain import dec
from kalshi_perps.shadow_forward import account_metrics, execute_targets


def shadow(tmp):
    r = setup(tmp)
    raw = deepcopy(r.config.raw)
    raw["risk"]["max_gross"] = "1"
    raw["execution_enabled"] = False
    r.config = SimpleNamespace(mode="paper", raw=raw)
    return r


def step(r, now, level, direction=-1, entry=100, reason="hold", size="100000"):
    e = event(now, size)
    r.broker.snapshot(e["event_id"], now, e["quotes"])
    d = dict(
        as_of_ms=now,
        signed_exit_policy={
            r.symbols[0]: dict(
                level=level,
                direction=direction if dec(level) else 0,
                entry_ms=entry if dec(level) else None,
                reason=reason,
            )
        },
    )
    return execute_targets(r, e, d)


@pytest.mark.parametrize("direction", [-1, 1])
def test_signed_ladder_retries_no_repurchase_and_accounting(tmp_path, direction):
    r = shadow(tmp_path)
    step(r, 100, ".98", direction, reason="entry")
    initial = count(r)
    assert initial * direction > 0 and abs(initial) * 10 <= 9800
    step(r, 200, ".735", direction, reason="partial_tp", size="10")
    assert abs(count(r)) == abs(initial) - 10
    frozen = dec(r.broker.store.state()["shadow_execution"][r.symbols[0]]["pending"]["target"])
    step(r, 300, ".735", direction, reason="partial_tp")
    assert count(r) == frozen
    for now in [400, 500]:
        step(r, now, ".735", direction, reason="partial_tp")
    assert count(r) == frozen
    f = dict(event_id="native", ticker=r.symbols[0], timestamp_ms=150, rate=".001", mark="10")
    r.broker.apply_historical_funding(f)
    r.broker.apply_historical_funding(f)
    assert dec(r.broker.report()["funding"]) == -initial * 10 * dec(".001")
    step(r, 600, "0", direction, reason="take_profit")
    assert count(r) == 0
    m = account_metrics(r.broker.store, 0, 600)
    assert m["take_profit_actions"] == 2
    assert abs(dec(m["closed_episode_details"][0]["net_pnl"]) - dec(m["net_pnl"])) < dec(
        ".00000001"
    )


def test_short_startup_skips_carried_signal(tmp_path):
    r = shadow(tmp_path)
    s = r.broker.store.state()
    s["forward_start_ms"] = 150
    r.broker.store.save(s)
    assert step(r, 200, ".98")["ignored_carried_signals"][r.symbols[0]]
    assert count(r) == 0
    step(r, 300, "0")
    step(r, 400, ".98", entry=400)
    assert count(r) < 0


def test_partial_entry_intent_persists(tmp_path):
    r = shadow(tmp_path)
    step(r, 100, ".98", size="10")
    assert count(r) == -10
    frozen = r.broker.store.state()["shadow_execution"][r.symbols[0]]["pending"]["target"]
    step(r, 200, ".98", size="10")
    assert count(r) == -20
    assert r.broker.store.state()["shadow_execution"][r.symbols[0]]["pending"]["target"] == frozen


def test_live_config_rejected(tmp_path):
    r = shadow(tmp_path)
    r.config.mode = "live"
    with pytest.raises(ValueError, match="paper only"):
        step(r, 100, ".98")


def test_frozen_signal_bridge_and_short_policies():
    from pathlib import Path

    from test_strategy_runtime import history

    from kalshi_perps.api import StrategyService
    from kalshi_perps.config import Config

    root = Path(__file__).resolve().parents[1]
    c = Config.load(root / "configs/paper.toml").select_strategy("doge-sol-trend-shadow")
    s = StrategyService(c)
    frames, boundary = history(1400)
    candles = {t: frames["KXETHPERP"] for t in s.symbols}
    d = s.decide(candles, boundary)
    assert d["ready"] and set(d["signed_exit_policy"]) == set(s.symbols)
    assert all(p["direction"] in (-1, 0) for p in d["signed_exit_policy"].values())
    assert any(p["direction"] == -1 for p in d["signed_exit_policy"].values())
    profile = s.engine.profile.payload()
    profile["legs"][0]["ema_bars"] = 50
    from kalshi_perps.profiles import StrategyProfile

    with pytest.raises(ValueError, match="frozen"):
        StrategyProfile.parse(profile)


def test_common_start_comparison_preserves_default_and_baseline(tmp_path):
    import json
    import time
    from datetime import UTC, datetime

    from kalshi_perps.shadow_forward import comparison

    source = tmp_path / "default.json"
    shadow_dir = tmp_path / "shadow"
    shadow_dir.mkdir()
    config = SimpleNamespace(
        raw={"shadow": {"default_report": str(source)}}, database=shadow_dir / "paper.sqlite"
    )
    now = int(time.time() * 1000)
    default = dict(
        updated_at=datetime.now(UTC).isoformat(),
        timestamp_ms=now,
        metrics=dict(equity="10000", funding="3", fees="2", take_profit_actions=2, positions={}),
    )
    source.write_text(json.dumps(default))
    original = source.read_bytes()
    report = dict(timestamp_ms=now, start_ms=now, metrics={})
    first = comparison(config, report)
    assert first["default"]["net_pnl"] == "0" and first["default"]["take_profit_actions"] == 0
    assert source.read_bytes() == original
    default["metrics"].update(equity="9500", funding="1", fees="4", take_profit_actions=3)
    source.write_text(json.dumps(default))
    report["timestamp_ms"] += 86400000
    second = comparison(config, report)
    assert second["default"]["net_pnl"] == "-500"
    assert second["default"]["max_drawdown"] == "-0.05"
    assert second["default"]["funding"] == "-2"
    assert second["default"]["take_profit_actions_per_30d"] == 30
    default["updated_at"] = "2020-01-01T00:00:00+00:00"
    source.write_text(json.dumps(default))
    assert comparison(config, report)["state"] == "default_comparison_unavailable"


def test_full_target_exit_is_counted_as_take_profit():
    import os
    import subprocess
    from pathlib import Path

    research = Path(__file__).resolve().parents[2] / "amat"
    code = """
import numpy as np
import pandas as pd
from unittest.mock import patch
from autoresearch import crypto_shadow_trend as m
frame=pd.DataFrame(index=pd.date_range('2026-01-01',periods=2,freq='h',tz='UTC'))
with patch.object(m,'context',return_value=({},None)), patch.object(m,'window_plan'), patch.object(m,'short_from_context'), patch.object(m,'combine',return_value=(np.array([.98,0]),np.array([1,7]))):
    _,_,p=m.decision_path(frame,dict(template='DOGE',adx_threshold=30,ema_bars=25))
    assert p['reason']=='take_profit' and p['level']==0
"""
    result = subprocess.run(
        [str(research / ".venv/bin/python"), "-c", code],
        env={**os.environ, "PYTHONPATH": str(research / "src")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
