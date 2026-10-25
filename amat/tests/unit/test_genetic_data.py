"""Dataset integrity and causal masks for the isolated discovery workflow."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from autoresearch.cli import main
from autoresearch.genetic.data import HOUR_MS, load_crypto, training_labels


@pytest.fixture
def crypto(tmp_path):
    start = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    first = int(start.timestamp() * 1000)
    rows = []
    for index in range(48):
        if index == 10:
            continue
        stamp = first + index * HOUR_MS + (1 if index == 25 else 0)
        rows.append(
            {
                "open_time_ms": stamp,
                "close_time_ms": stamp + HOUR_MS - (10 if index == 12 else 1),
                "open": 100.0 + index,
                "high": 102.0 + index,
                "low": 99.0 + index,
                "close": 101.0 + index,
                "volume": 0.0 if index == 35 else 100.0,
            }
        )
    path = tmp_path / "BTCUSDT.csv"
    pl.DataFrame(rows).write_csv(path)
    manifest = {
        "interval": "1h",
        "timestamp_unit": "milliseconds",
        "timezone": "UTC",
        "start_inclusive": "2020-01-01",
        "end_exclusive": "2020-01-03",
        "datasets": [
            {
                "symbol": "BTCUSDT",
                "file": path.name,
                "rows": len(rows),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def load(directory):
    return load_crypto(directory, ("BTCUSDT",), start=dt.date(2020, 1, 1), end=dt.date(2020, 1, 2))


def test_crypto_masks_real_grid_without_compressing_or_imputing(crypto):
    data = load(crypto)
    assert len(data.timestamps) == 48
    assert data.timestamps[10] == "2020-01-01T10:00:00+00:00"
    assert np.isnan(data.fields["open"][[10, 12, 25, 35], 0]).all()
    assert data.fields["open"][11, 0] == 111.0
    assert data.manifest["invalid_candle_cells"] == 4
    labels, support = training_labels(data, max_lag=2, one_way_bps=2.0)
    for gap in (10, 12, 25, 35):
        assert not support[max(0, gap - 2) : min(48, gap + 3)].any()
    assert not support[-2:].any()
    assert np.isnan(labels[-2:]).all()


def test_dataset_identity_binds_arrays_timestamps_and_symbols(crypto):
    data = load(crypto)
    assert data.digest == load(crypto).digest
    altered = data.execution_prices.copy()
    altered[0, 0] += 1
    assert data.digest != replace(data, execution_prices=altered).digest
    assert data.digest != replace(data, symbols=("ETHUSDT",)).digest
    assert data.digest != replace(data, timeframe="1d").digest


def test_crypto_checksum_failure_is_not_a_missing_bar(crypto):
    path = crypto / "BTCUSDT.csv"
    path.write_text(path.read_text().replace("100.0", "999.0", 1))
    with pytest.raises(ValueError, match="checksum"):
        load(crypto)


def test_crypto_refuses_outside_range_or_unlisted_symbol(crypto):
    with pytest.raises(ValueError, match="outside"):
        load_crypto(crypto, ("BTCUSDT",), start=dt.date(2019, 1, 1), end=dt.date(2020, 1, 2))
    with pytest.raises(ValueError, match="absent"):
        load_crypto(crypto, ("UNKNOWN",), start=dt.date(2020, 1, 1), end=dt.date(2020, 1, 2))


def test_crypto_manifest_cannot_reference_parent_path(crypto):
    path = crypto / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["datasets"][0]["file"] = "../BTCUSDT.csv"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="local filename"):
        load(crypto)


def test_existing_cli_validates_both_schema_examples(capsys):
    root = Path(__file__).resolve().parents[2] / "docs/genetic-patterns"
    for name in ("candle", "symbolic"):
        assert main(["patterns", "validate", str(root / f"{name}.example.json")]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["valid"] is True
        assert result["execution_authority"] == "none"
        assert result["evaluator_supported"] is True


def test_existing_cli_writes_training_only_report(crypto, tmp_path, capsys):
    output = tmp_path / "study"
    assert (
        main(
            [
                "patterns",
                "discover",
                "--crypto-data",
                str(crypto),
                "--symbols",
                "BTCUSDT",
                "--train-start",
                "2020-01-01",
                "--train-end",
                "2020-01-02",
                "--output",
                str(output),
                "--population",
                "4",
                "--generations",
                "0",
                "--min-matched-sessions",
                "2",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["recorded_slots"] == 4
    report = json.loads((output / "report.json").read_text())
    assert report["evidence_level"] == "adaptive_training_screen_only"
    assert report["execution_authority"] == "none"
