from __future__ import annotations

import json

from research.backtest import load_markets


def _row(asset: str) -> dict:
    return {
        "ticker": f"KX{asset}15M-TEST",
        "asset": asset,
        "label_source": "venue",
        "label_settled_yes": True,
        "model_probability_yes": "0.8",
        "mkt_yes_bid": "0.70",
        "mkt_yes_ask": "0.71",
        "mkt_fee_multiplier": "1",
        "seconds_to_close": 60,
        "exec_limit_price": "0.72",
    }


def test_market_half_honors_the_cli_asset_scope(tmp_path) -> None:
    path = tmp_path / "samples.jsonl"
    path.write_text("\n".join(json.dumps(_row(a)) for a in ("BTC", "ETH")))

    markets = load_markets(path, assets=("BTC",))

    assert [market.asset for market in markets] == ["BTC"]
