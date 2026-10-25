"""Native contract opt-in never aliases the strict minute input contract."""

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.backtest.panel import MarketPanel
from autoresearch.genetic.data import digest_json
from autoresearch.genetic.models import PatternDefinition
from autoresearch.genetic.native_views import SOURCE_PREFIX, native_view_contract
from autoresearch.genetic.symbolic import evaluate_symbolic


@pytest.mark.parametrize("timeframe", ["15m", "1h"])
def test_native_symbolic_requires_matching_contract_and_timeframe(timeframe):
    value = json.loads(
        (Path(__file__).parents[2] / "docs/genetic-patterns/symbolic.example.json").read_text()
    )
    value["timeframe"] = timeframe
    value["genome"] = {
        "kind": "symbolic_indicator",
        "outputTransform": "tanh",
        "root": {"node": "constant", "value": 1.0},
    }
    pattern = PatternDefinition.model_validate(value)
    array = np.ones((2, 1)) * 100
    source = f"{SOURCE_PREFIX}{timeframe}:contract={digest_json(native_view_contract())}"
    panel = MarketPanel(
        sessions=(
            dt.datetime(2025, 1, 2, 15, tzinfo=dt.UTC),
            dt.datetime(2025, 1, 2, 16, tzinfo=dt.UTC),
        ),
        symbols=("SPY",),
        adjusted_closes=array,
        raw_closes=array,
        window_vwaps=array,
        window_volumes=array,
        source=source,
    )
    np.testing.assert_allclose(evaluate_symbolic(pattern, panel), np.tanh(1))
    other = "1h" if timeframe == "15m" else "15m"
    for bad in (
        source.replace(digest_json(native_view_contract()), "0" * 64),
        f"{SOURCE_PREFIX}{other}:contract={digest_json(native_view_contract())}",
        "unmarked-native-data",
    ):
        with pytest.raises(ValueError, match="explicitly aggregated"):
            evaluate_symbolic(pattern, replace(panel, source=bad))
