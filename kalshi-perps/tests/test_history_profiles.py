from pathlib import Path

import httpx
import pytest

from kalshi_perps.config import Config
from kalshi_perps.domain import HOUR, dec
from kalshi_perps.history import KrakenPerpetualProvider, SignalHistory, validate_rows
from kalshi_perps.profiles import StrategyProfile
from kalshi_perps.strategy import HistoryUnavailable, ProfileStrategy

ROOT = Path(__file__).resolve().parents[1]
AMAT = ROOT.parent / "amat"


def candle(t):
    return dict(time=t, open="100", high="101", low="99", close="100.5", volume="10")


def test_provider_pages_and_drops_open_candle():
    start, stop = 0, 600 * HOUR
    calls = []

    def handler(request):
        first = int(request.url.params["from"]) * 1000
        calls.append(first)
        end = min(first + 500 * HOUR, stop + HOUR)
        return httpx.Response(
            200,
            json={
                "candles": [candle(t) for t in range(first, end, HOUR)],
                "more_candles": end < stop,
            },
        )

    p = KrakenPerpetualProvider(
        httpx.Client(transport=httpx.MockTransport(handler)), sleeper=lambda _: None
    )
    rows, receipts = p.candles("PF_DOGEUSD", start, stop)
    assert len(rows) == 600 and rows[-1]["timestamp_ms"] == stop - HOUR
    assert len(calls) == len(receipts) == 2


def test_provider_rejects_missing_hours_and_invalid_ohlc():
    def handler(request):
        return httpx.Response(
            200, json={"candles": [candle(0), candle(2 * HOUR)], "more_candles": False}
        )

    p = KrakenPerpetualProvider(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(HistoryUnavailable, match="missing"):
        p.candles("PF_DOGEUSD", 0, 3 * HOUR)
    with pytest.raises(ValueError, match="OHLC"):
        validate_rows(
            [dict(timestamp_ms=0, open="1", high="0", low="1", close="1", tradable=1)], 0, HOUR
        )


def test_cache_incremental_preserves_anchor_and_normalizes_once(tmp_path, btc):
    class Provider:
        name = "fixture/1"
        calls = []

        def candles(self, symbol, start, stop):
            self.calls.append((start, stop))
            rows = [
                dict(
                    timestamp_ms=t,
                    open="80000",
                    high="81000",
                    low="79000",
                    close="80000",
                    tradable=1,
                )
                for t in range(start, stop, HOUR)
            ]
            return rows, [dict(symbol=symbol, start=start, stop=stop)]

    p = Provider()
    boundary = 1000 * HOUR
    cache = SignalHistory(tmp_path / "history.sqlite", {btc.ticker: btc}, days=32, provider=p)
    first, report = cache.load(boundary)
    assert (
        first[btc.ticker][0]["close"] == "8.0000000000" or dec(first[btc.ticker][0]["close"]) == 8
    )
    assert report[btc.ticker]["rows"] == 32 * 24
    assert cache.load(boundary)[0] == first
    assert len(p.calls) == 1
    extended, report = cache.load(boundary + HOUR)
    assert len(extended[btc.ticker]) == len(first[btc.ticker]) + 1
    assert extended[btc.ticker][0] == first[btc.ticker][0]
    assert len(p.calls) == 2
    cache.close()


def test_cache_rejects_revised_history(tmp_path, btc):
    class Provider:
        name = "fixture/1"
        revised = False

        def candles(self, symbol, start, stop):
            return [
                dict(
                    timestamp_ms=t,
                    open="80",
                    high="82",
                    low="78",
                    close="81" if self.revised else "80",
                    tradable=1,
                )
                for t in range(start, stop, HOUR)
            ], []

    p = Provider()
    c = SignalHistory(tmp_path / "h.sqlite", {btc.ticker: btc}, days=32, provider=p)
    c.load(1000 * HOUR)
    p.revised = True
    with pytest.raises(ValueError, match="changed"):
        c.load(1001 * HOUR)
    c.close()


def test_all_frozen_profiles_match_selected_artifacts():
    expected = {
        "doge-sol-adx14",
        "doge-sol-trend-shadow",
        "doge-sol-balanced",
        "doge-sol-lower-drawdown",
        "doge-sol-max-pnl",
        "doge-pnl",
        "sol-profit",
        "btc-eth-reference",
        "btc-long-pnl",
        "near-pnl",
        "uni-pnl",
    }
    paths = list((ROOT / "configs/strategies").glob("*.toml"))
    assert {p.stem for p in paths} == expected
    for path in paths:
        profile = StrategyProfile.load(path)
        assert profile.name == path.stem
        assert sum(dec(leg["allocation"]) for leg in profile.legs) == 1
    balanced = StrategyProfile.load(ROOT / "configs/strategies/doge-sol-balanced.toml")
    assert [dec(leg["allocation"]) for leg in balanced.legs] == [dec(".25"), dec(".75")]
    assert [dec(leg["tp_atr"]) for leg in balanced.legs] == [dec(".5"), dec("2")]


@pytest.mark.skipif(
    not (AMAT / "outputs").is_dir(),
    reason="Requires original private selection evidence; profile/source checks still run",
)
def test_frozen_profile_selection_evidence_matches():
    for path in (ROOT / "configs/strategies").glob("*.toml"):
        profile = StrategyProfile.load(path)
        if profile.evidence:
            artifact = ROOT.parent / profile.evidence["selection"]
            import hashlib

            assert (
                hashlib.sha256(artifact.read_bytes()).hexdigest()
                == profile.evidence["selection_sha256"]
            )


def test_profile_configuration_isolation():
    c = Config.load(ROOT / "configs/paper.toml")
    p = c.select_strategy("doge-pnl")
    s = c.select_strategy("sol-profit")
    assert p.database != s.database != c.database
    assert p.raw["account"] != s.raw["account"]
    with pytest.raises(ValueError):
        c.select_strategy("../escape")


def test_invalid_profile_allocations():
    raw = StrategyProfile.load(ROOT / "configs/strategies/doge-sol-balanced.toml").payload()
    raw["legs"][0] = dict(raw["legs"][0], allocation="0.5")
    with pytest.raises(ValueError, match="sum to one"):
        StrategyProfile.parse(raw)


def test_modular_profiles_call_actual_engines():
    import runpy

    helper = runpy.run_path(str(ROOT / "tests/test_strategy_runtime.py"))
    frames, boundary = helper["history"](1000)
    profile = StrategyProfile.load(ROOT / "configs/strategies/btc-eth-reference.toml")
    s = ProfileStrategy(profile, AMAT, AMAT / ".venv/bin/python")
    reference = helper["strategy"]().decide(frames, boundary)
    assert s.decide(frames, boundary)["weights"] == reference["weights"]
    for name in [
        "doge-sol-balanced",
        "doge-sol-max-pnl",
        "doge-pnl",
        "sol-profit",
        "btc-long-pnl",
        "near-pnl",
        "uni-pnl",
    ]:
        p = StrategyProfile.load(ROOT / f"configs/strategies/{name}.toml")
        mapped = {leg["ticker"]: frames["KXBTCPERP"] for leg in p.legs}
        decision = ProfileStrategy(p, AMAT, AMAT / ".venv/bin/python").decide(mapped, boundary)
        assert decision["ready"]
        assert set(decision["weights"]) == set(p.symbols)
        assert all(dec(w) >= 0 for w in decision["weights"].values())


def test_public_strategy_api_has_no_broker_dependency():
    from kalshi_perps.api import StrategyService

    service = StrategyService.from_config(ROOT / "configs/paper.toml")
    description = service.describe()
    assert description["name"] == "doge-sol-adx14"
    assert service.symbols == ("KXDOGEPERP", "KXSOLPERP")
    assert len(service.available_profiles()) == 11
    assert service.config.select_strategy("doge-sol-adx14") is service.config


def test_profile_selection_does_not_mutate_other_configuration():
    config = Config.load(ROOT / "configs/paper.toml")
    original = config.identity
    selected = config.select_strategy("sol-profit")
    assert config.identity == original
    assert selected.identity != original


@pytest.mark.parametrize(
    "engine,parameter",
    [("profit", "sizing"), ("profit", "direction"), ("directional", "max_hold_hours")],
)
def test_engine_parameters_cannot_be_silently_ignored(engine, parameter):
    profile = StrategyProfile.load(ROOT / "configs/strategies/doge-sol-balanced.toml")
    raw = profile.payload()
    raw["legs"][0] = dict(raw["legs"][0], **{parameter: "ignored"})
    if engine == "directional":
        raw = StrategyProfile.load(ROOT / "configs/strategies/doge-pnl.toml").payload()
        raw["legs"][0] = dict(raw["legs"][0], max_hold_hours=24)
    with pytest.raises(ValueError, match="unsupported"):
        StrategyProfile.parse(raw)


def test_profile_is_immutable_and_payload_is_an_independent_copy():
    profile = StrategyProfile.load(ROOT / "configs/strategies/doge-sol-balanced.toml")
    before = profile.identity
    with pytest.raises(TypeError):
        profile.legs[0]["allocation"] = "0.9"
    payload = profile.payload()
    payload["legs"][0]["allocation"] = "0.9"
    assert profile.identity == before
