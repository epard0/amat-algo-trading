import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from kalshi_perps.config import Config
from kalshi_perps.domain import HOUR, dec
from kalshi_perps.paper import PaperBroker
from kalshi_perps.runtime import Runner, lease, run
from kalshi_perps.strategy import SYMBOLS, CryptoStrategy

ROOT = Path(__file__).resolve().parents[1]
AMAT = ROOT.parent / "amat"


def history(hours=1000):
    start = 1767225600000
    candles = {}
    for ticker, base, direction in zip(SYMBOLS, (8, 2.4), (1, -1), strict=True):
        rows = []
        for i in range(hours):
            close = base * math.exp(direction * 0.0002 * i + 0.002 * math.sin(i / 17))
            rows.append(
                dict(
                    timestamp_ms=start + i * HOUR,
                    open=str(close),
                    high=str(close * 1.00005),
                    low=str(close * 0.99995),
                    close=str(close),
                    tradable=1,
                )
            )
        candles[ticker] = rows
    return candles, start + hours * HOUR


def strategy():
    if not (AMAT / ".venv/bin/python").exists():
        pytest.skip("integration requires the configured AMAT environment")
    source = AMAT / "src/autoresearch/crypto_perpetual_study.py"
    return CryptoStrategy(
        AMAT, AMAT / ".venv/bin/python", hashlib.sha256(source.read_bytes()).hexdigest()
    )


def test_actual_primary_strategy_and_causality():
    s = strategy()
    candles, boundary = history()
    decision = s.decide(candles, boundary)
    assert decision["ready"]
    assert dec(decision["weights"][SYMBOLS[0]]) > 0
    assert dec(decision["weights"][SYMBOLS[1]]) < 0
    assert max(abs(dec(w)) for w in decision["weights"].values()) <= 1
    shorter = {t: rows[:-4] for t, rows in candles.items()}
    earlier = s.decide(shorter, boundary - 4 * HOUR)
    altered = deepcopy(shorter)
    altered[SYMBOLS[0]][-1]["timestamp_ms"] = boundary
    with pytest.raises(ValueError, match="completed"):
        s.decide(altered, boundary - 4 * HOUR)
    assert earlier["ready"]


def test_source_pin_and_missing_history():
    with pytest.raises(ValueError, match="hash mismatch"):
        CryptoStrategy(AMAT, AMAT / ".venv/bin/python", "0" * 64)
    s = strategy()
    candles, boundary = history(20)
    assert not s.decide(candles, boundary)["ready"]
    del candles[SYMBOLS[0]][5]
    with pytest.raises(ValueError, match="contiguous"):
        s.decide(candles, boundary)


def test_paper_runner_atomic_replay_and_failure(tmp_path, instruments):
    candles, boundary = history(10)

    class Fake:
        identity = "test"

        def decide(self, history, boundary):
            return {
                "ready": True,
                "weights": dict(zip(SYMBOLS, [".1", "-.1"], strict=True)),
                "input_hash": str(boundary),
                "policy_hash": "test",
            }

    b = PaperBroker(tmp_path / "paper.sqlite", {t: instruments[t] for t in SYMBOLS})
    config = SimpleNamespace(
        mode="paper",
        raw={
            "risk": dict(
                max_gross="1",
                max_order_notional="1000",
                max_spread_bps="50",
                max_loss_fraction=".2",
            )
        },
    )
    r = Runner(config, Fake(), b)
    quotes = {t: dict(bids=[["7.99", "1000"]], asks=[["8.01", "1000"]], mark="8") for t in SYMBOLS}
    event = dict(event_id="one", timestamp_ms=boundary, quotes=quotes)
    first = r.process(event, candles)
    state = b.report()
    assert r.process(event, candles) == first
    assert b.report() == state
    assert len(first["orders"]) == 2

    def broken(history, boundary):
        raise RuntimeError("simulated process failure")

    r.strategy.decide = broken
    with pytest.raises(RuntimeError):
        r.process(dict(event, event_id="two", timestamp_ms=boundary + 1), candles)
    assert b.report() == state
    assert b.store.state()["timestamp_ms"] == boundary


def test_single_runner_lease(tmp_path):
    with lease(tmp_path / "x.sqlite"), pytest.raises(ValueError, match="already owns"):
        with lease(tmp_path / "x.sqlite"):
            pass


def test_config_live_limit(tmp_path):
    raw = (
        (ROOT / "configs/live.toml")
        .read_text()
        .replace("execution_enabled = false", "execution_enabled = true")
    )
    raw = raw.replace('max_gross = "1"', 'max_gross = "2"')
    path = tmp_path / "live.toml"
    path.write_text(raw)
    with pytest.raises(ValueError, match="1x"):
        Config.load(path)


def test_replay_end_to_end_restart(tmp_path):
    raw = (ROOT / "configs/reference-paper.toml").read_text()
    raw = raw.replace("../runtime/paper.sqlite", str(tmp_path / "account.sqlite"))
    raw = raw.replace('"instruments.json"', json.dumps(str(ROOT / "configs/instruments.json")))
    raw = raw.replace('"../../amat"', json.dumps(str(AMAT)))
    raw = raw.replace('"../../amat/.venv/bin/python"', json.dumps(str(AMAT / ".venv/bin/python")))
    config_path = tmp_path / "paper.toml"
    config_path.write_text(raw)
    candles, boundary = history()
    quotes = {}
    for t in SYMBOLS:
        price = dec(candles[t][-1]["close"])
        bid = price.quantize(dec(".0001")) - dec(".0001")
        ask = bid + dec(".0002")
        quotes[t] = dict(bids=[[str(bid), "100000"]], asks=[[str(ask), "100000"]], mark=str(price))
    recording = dict(
        schema="kalshi-perps-recording/1",
        price_unit="usd_per_contract",
        candles=candles,
        source="synthetic fixture",
        events=[dict(event_id="start", timestamp_ms=boundary, quotes=quotes)],
    )
    record_path = tmp_path / "recording.json"
    record_path.write_text(json.dumps(recording))
    first = run(Config.load(config_path), record_path)
    assert first["result"]["state"] == "running"
    assert len(first["result"]["orders"]) == 2
    assert run(Config.load(config_path), record_path) == first


def test_flatten_respects_each_order_cap_and_closes_available_depth(tmp_path, instruments):
    b = PaperBroker(tmp_path / "flat.sqlite", {t: instruments[t] for t in SYMBOLS}, fee_rate="0")
    q = {t: dict(bids=[["7.99", "1000"]], asks=[["8.01", "1000"]], mark="8") for t in SYMBOLS}
    b.snapshot("s", 0, q)
    from kalshi_perps.domain import Order

    b.submit(Order("open", SYMBOLS[0], "bid", 100, "8.01", 1), timestamp_ms=0)
    config = SimpleNamespace(mode="paper", raw={"risk": {"max_order_notional": "100"}})
    r = Runner(config, None, b)  # Closing does not require the strategy process.
    result = r.flatten("stop")
    assert dec(result["report"]["gross_notional"]) == 0
    assert len(result["results"]) > 1


def test_primary_interpreter_keeps_venv_symlink():
    s = strategy()
    assert str(s.python).endswith("/amat/.venv/bin/python")
