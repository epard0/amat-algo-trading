import runpy
from pathlib import Path

import pytest

from kalshi_perps.api import StrategyService
from kalshi_perps.config import Config
from kalshi_perps.runtime import build

ROOT = Path(__file__).resolve().parents[1]


def test_default_is_user_authorized_paper_only(tmp_path):
    c = Config.load(ROOT / "configs/paper.toml")
    service = StrategyService(c)
    assert service.describe()["name"] == "doge-sol-adx14"
    assert [leg["leverage"] for leg in service.engine.profile.legs] == ["0.5", "0.5"]
    assert c.mode == "paper" and c.raw["execution_enabled"] is False
    assert c.raw["paper"]["leverage_mode"] == "venue_max"
    assert c.raw["risk"]["max_gross"] == "100"
    assert c.raw["risk"]["max_order_notional"] == "1000000"
    assert service.engine.profile.evidence["execution_status"] == "paper_authorized"
    from copy import deepcopy

    raw = deepcopy(c.raw)
    raw["database"] = str(tmp_path / "paper.sqlite")
    isolated = Config(c.path, raw)
    strategy, broker = build(isolated)
    assert broker.report()["pnl"] == "0"
    broker.store.close()
    raw["mode"] = "live"
    with pytest.raises(ValueError, match="paper only"):
        build(Config(c.path, raw))


def test_adx_profile_evaluates_completed_candles_without_broker():
    helper = runpy.run_path(str(ROOT / "tests/test_strategy_runtime.py"))
    frames, boundary = helper["history"](1000)
    service = StrategyService.from_config(ROOT / "configs/paper.toml")
    candles = {ticker: frames["KXBTCPERP"] for ticker in service.symbols}
    decision = service.decide(candles, boundary)
    assert decision["ready"]
    assert set(decision["weights"]) == set(service.symbols)
    assert all(0 <= float(value) <= 0.49 for value in decision["weights"].values())


def test_explicit_legacy_profile_retains_its_identity():
    c = Config.load(ROOT / "configs/paper.toml").select_strategy("doge-sol-balanced")
    service = StrategyService(c)
    assert service.describe()["name"] == "doge-sol-balanced"
    assert "execution_status" not in service.engine.profile.evidence
