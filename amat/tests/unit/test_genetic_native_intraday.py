"""Native vendor receipts remain separate from session-aligned minute panels."""

from __future__ import annotations

import datetime as dt
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from amat.errors import DataIntegrityError
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.native_intraday import (
    fetch_native_intraday,
    load_native_intraday,
    validate_native_intraday,
)
from autoresearch.genetic.shadow_intraday import load_intraday

DAY = dt.date(2025, 11, 28)  # Early-close session, 18:00 UTC.
NOW = dt.datetime(2025, 11, 29, tzinfo=dt.UTC)


class Feed:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}
        self.bars = [{"t": "2025-11-28T14:00:00Z", "o": 10, "h": 12, "l": 9, "c": 11, "v": 40}]

    def _paginate(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert path == "/v2/stocks/bars"
        self.params = params
        return [{"bars": {"IEF": self.bars}}]


@pytest.fixture
def payload(tmp_path: Path) -> dict[str, Any]:
    return fetch_native_intraday(
        tmp_path / "native.json", ("IEF", "DBC"), DAY, DAY, timeframe="1h", client=Feed(), now=NOW
    )


@pytest.mark.parametrize("timeframe,provider", [("15m", "15Min"), ("1h", "1Hour")])
def test_request_contract_and_roundtrip(tmp_path: Path, timeframe: str, provider: str) -> None:
    feed = Feed()
    path = tmp_path / "native.json"
    payload = fetch_native_intraday(
        path, ("IEF", "DBC"), DAY, DAY, timeframe=timeframe, client=feed, now=NOW
    )
    assert load_native_intraday(path) == payload
    assert payload["symbols"] == ["IEF", "DBC"]
    assert payload["alignment"] == "unknown"
    assert payload["native_records"][0]["t"] == "2025-11-28T14:00:00Z"
    assert feed.params["timeframe"] == provider
    assert feed.params["feed"] == "sip"
    assert feed.params["adjustment"] == "all"
    # Missing DBC remains missing rather than becoming a synthetic bar.
    assert len(payload["native_records"]) == 1
    with pytest.raises(FileExistsError):
        fetch_native_intraday(path, ("IEF",), DAY, DAY, timeframe=timeframe, client=feed, now=NOW)
    with pytest.raises(DataIntegrityError):
        load_intraday(path)


def test_checksum_rejects_tampering(tmp_path: Path, payload: dict[str, Any]) -> None:
    assert payload["timeframe"] == "1h"
    path = tmp_path / "native.json"
    original = json.loads(path.read_text())
    original["payload"]["native_records"][0]["v"] += 1
    path.write_text(json.dumps(original))
    with pytest.raises(DataIntegrityError, match="checksum"):
        load_native_intraday(path)


@pytest.mark.parametrize(
    "mode",
    [
        "duplicate",
        "unknown_symbol",
        "outside",
        "naive",
        "second",
        "unclosed",
        "negative_volume",
        "nan",
        "zero",
        "bad_high",
        "bad_low",
        "symbols_duplicate",
        "symbols_set",
        "raw",
        "aligned_claim",
        "wrong_timeframe",
        "wrong_provider_timeframe",
        "empty",
        "missing_field",
        "collection_naive",
        "future_range",
    ],
)
def test_invalid_receipts_refused(payload: dict[str, Any], mode: str) -> None:  # noqa: PLR0912
    bar = payload["native_records"][0]
    if mode == "duplicate":
        duplicate = deepcopy(bar)
        duplicate["t"] = "2025-11-28T09:00:00-05:00"
        payload["native_records"].append(duplicate)
    elif mode == "unknown_symbol":
        bar["symbol"] = "SPY"
    elif mode == "outside":
        bar["t"] = "2025-11-27T14:00:00Z"
    elif mode == "naive":
        bar["t"] = "2025-11-28T14:00:00"
    elif mode == "second":
        bar["t"] = "2025-11-28T14:00:01Z"
    elif mode == "unclosed":
        bar["t"] = "2025-11-28T23:30:00Z"
    elif mode == "negative_volume":
        bar["v"] = -1
    elif mode == "nan":
        bar["v"] = float("nan")
    elif mode == "zero":
        bar["l"] = 0
    elif mode == "bad_high":
        bar["h"] = 8
    elif mode == "bad_low":
        bar["l"] = 11
    elif mode == "symbols_duplicate":
        payload["symbols"] = ["IEF", "IEF"]
    elif mode == "symbols_set":
        payload["symbols"] = {"IEF", "DBC"}
    elif mode == "raw":
        payload["adjustment"] = "raw"
    elif mode == "aligned_claim":
        payload["alignment"] = "session open"
    elif mode == "wrong_timeframe":
        payload["timeframe"] = "1d"
    elif mode == "wrong_provider_timeframe":
        payload["provider_timeframe"] = "1Min"
    elif mode == "empty":
        payload["native_records"] = []
    elif mode == "missing_field":
        del bar["h"]
    elif mode == "collection_naive":
        payload["collected_at"] = "2025-11-29T00:00:00"
    elif mode == "future_range":
        payload["end"] = "2025-12-01"
    with pytest.raises(DataIntegrityError):
        validate_native_intraday(payload)


@pytest.mark.parametrize(
    "mode",
    [
        "unknown_timeframe",
        "duplicate_universe",
        "naive_clock",
        "unclosed_session",
        "future_weekend",
        "weekend_only",
        "empty_provider",
        "unclosed_provider",
    ],
)
def test_fetch_refuses_before_persistence(tmp_path: Path, mode: str) -> None:
    feed = Feed()
    start, end, now, timeframe, symbols = DAY, DAY, NOW, "1h", ("IEF",)
    if mode == "unknown_timeframe":
        timeframe = "1d"
    elif mode == "duplicate_universe":
        symbols = ("IEF", "IEF")
    elif mode == "naive_clock":
        now = NOW.replace(tzinfo=None)
    elif mode == "unclosed_session":
        now = dt.datetime(2025, 11, 28, 17, 59, tzinfo=dt.UTC)
    elif mode == "future_weekend":
        end = dt.date(2025, 11, 30)
    elif mode == "weekend_only":
        start = end = dt.date(2025, 11, 29)
    elif mode == "empty_provider":
        feed.bars = []
    elif mode == "unclosed_provider":
        feed.bars[0]["t"] = "2025-11-28T23:30:00Z"
    path = tmp_path / "refused.json"
    with pytest.raises(DataIntegrityError):
        fetch_native_intraday(path, symbols, start, end, timeframe=timeframe, client=feed, now=now)
    assert not path.exists()


def test_checksum_valid_but_bad_contract_rejected(tmp_path: Path, payload: dict[str, Any]) -> None:
    payload["feed"] = "iex"
    path = tmp_path / "modified.json"
    path.write_text(json.dumps({"payload": payload, "payload_sha256": digest_json(payload)}))
    with pytest.raises(DataIntegrityError):
        load_native_intraday(path)
