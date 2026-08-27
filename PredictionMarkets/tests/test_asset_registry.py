"""The asset registry, and every place that used to keep its own copy of it.

The supported 15-minute crypto and commodity series are listed in
`config.py` and, separately, in the model fitter, the dashboard's chart
selector, and a scalping concurrency default. Each copy was correct when it was
written, and each one silently stopped being correct the moment a series was
added: nothing raised, the new asset simply never appeared. These tests are the
drift guard.
"""

from __future__ import annotations

import re
import sys

import pytest
from pathlib import Path

from config import KRAKEN_PAIR_BY_ASSET, SERIES_BY_ASSET, KalshiSettings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_every_series_has_a_spot_pair_and_the_reverse() -> None:
    """A series with no Kraken pair cannot be priced; a pair with no series
    cannot be traded. Either half alone is a market the bot half-knows about.
    """
    assert set(SERIES_BY_ASSET) == set(KRAKEN_PAIR_BY_ASSET)


def test_all_supported_series_are_the_ones_probed_live() -> None:
    assert set(SERIES_BY_ASSET) == {
        "BTC",
        "ETH",
        "SOL",
        "HYPE",
        "DOGE",
        "XRP",
        "BNB",
        "NEAR",
        "ZEC",
        "GOLD",
        "SILVER",
        "WTI",
    }


def test_series_tickers_follow_the_15m_convention() -> None:
    for asset, series in SERIES_BY_ASSET.items():
        assert re.fullmatch(r"KX[A-Z]+15M", series), f"{asset} -> {series}"


def test_no_two_assets_share_a_series_or_a_pair() -> None:
    assert len(set(SERIES_BY_ASSET.values())) == len(SERIES_BY_ASSET)
    assert len(set(KRAKEN_PAIR_BY_ASSET.values())) == len(KRAKEN_PAIR_BY_ASSET)


def test_the_fitter_derives_its_asset_list_rather_than_repeating_it() -> None:
    """It was a hardcoded five-tuple, so --per-asset reported success having
    silently skipped every asset added after it was written.
    """
    pytest.importorskip("fit_regime_model")  # research/ does not ship
    from fit_regime_model import ASSETS

    assert tuple(ASSETS) == tuple(SERIES_BY_ASSET)


def test_history_maps_every_series_back_to_its_asset() -> None:
    from kalshi_history import asset_from_ticker

    for asset, series in SERIES_BY_ASSET.items():
        assert asset_from_ticker(f"{series}-26AUG192345-45") == asset


def test_the_backfill_cli_offers_every_asset() -> None:
    pytest.importorskip("backfill_candles")  # research/ does not ship
    from config import KRAKEN_PAIR_BY_ASSET as pairs

    # The CLI's --asset choices are sorted(KRAKEN_PAIR_BY_ASSET), so this is the
    # property that matters: no asset is unreachable from the command line.
    assert len(sorted(pairs)) == 12


def test_the_dashboard_takes_its_asset_list_from_the_server() -> None:
    """The chart selector hardcoded five assets in JavaScript. A page cannot
    import config.py, so the list has to be served - and the fallback literal
    must not be mistaken for the source of truth.
    """
    app_js = (PROJECT_ROOT / "monitoring" / "webui" / "app.js").read_text()
    assert "initAssets(config.assets)" in app_js

    server = (PROJECT_ROOT / "monitoring" / "server.py").read_text()
    assert '"assets": list(SERIES_BY_ASSET)' in server


def test_one_quote_slot_per_asset() -> None:
    """The documented scalping invariant. A literal 5 quietly became "the first
    five of nine" when the series list grew.
    """
    assert KalshiSettings.from_env().scalp_max_concurrent == len(SERIES_BY_ASSET)
    assert KalshiSettings.from_env().kalshi_request_min_interval_seconds == 0.35


def test_orderbook_capture_defaults_to_every_enabled_asset(monkeypatch) -> None:
    monkeypatch.delenv("ORDERBOOK_RECORDING_ASSETS", raising=False)
    monkeypatch.setenv("TRADING_ASSETS", "BTC,ETH,GOLD")
    settings = KalshiSettings.from_env()
    assert settings.orderbook_recording_assets == ("BTC", "ETH", "GOLD")
    assert settings.orderbook_rest_reconcile_seconds == 30.0
    assert settings.raw_book_event_recording_enabled is False
    assert settings.public_trade_recording_enabled is True


def test_the_documented_asset_list_matches_the_code(tmp_path) -> None:
    env_example = (PROJECT_ROOT / ".env.example").read_text()
    line = next(
        row for row in env_example.splitlines() if row.startswith("TRADING_ASSETS=")
    )
    documented = set(line.split("=", 1)[1].split(","))
    assert documented == set(SERIES_BY_ASSET)

    readme = (PROJECT_ROOT / "README.md").read_text()
    for series in SERIES_BY_ASSET.values():
        assert f"`{series}`" in readme, f"{series} is not in README.md"


def test_an_unknown_asset_is_still_refused(monkeypatch) -> None:
    """Widening the registry must not widen what TRADING_ASSETS accepts."""
    monkeypatch.setenv("TRADING_ASSETS", "BTC,LTC")
    try:
        KalshiSettings.from_env()
    except ValueError as exc:
        assert "LTC" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an unsupported asset was accepted")
