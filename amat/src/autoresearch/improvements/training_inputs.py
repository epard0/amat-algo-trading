"""Reconstruct declared training input identities without fitting or using future sessions."""

from __future__ import annotations

from typing import Any

from amat.backtest.panel import MarketPanel
from autoresearch.genetic.data import CandleDataset
from autoresearch.genetic.nested_replay import panel_prefix
from autoresearch.labels import adjusted_window_prices
from autoresearch.market_scenarios import panel_digest


def training_identity(panel: MarketPanel, cutoff: str) -> dict[str, Any]:
    stop = sum(str(day) <= cutoff for day in panel.sessions)
    if stop < 1:
        raise ValueError("Training cutoff has no observed sessions")
    training = panel_prefix(panel, stop)
    identity = panel_digest(training)
    fields = dict(zip(("open", "high", "low", "close"), training.candle_inputs(), strict=True))
    dataset = CandleDataset(
        tuple(str(x) for x in training.sessions),
        tuple(str(x) for x in training.symbols),
        fields,
        adjusted_window_prices(training),
        "1d",
        "corporate_action_adjusted",
        {"panel_sha256": identity},
    )
    return {
        "fit_panel_sha256": identity,
        "dataset_sha256": dataset.digest,
        "training_sessions": stop,
        "training_start": str(training.sessions[0]),
        "training_end": str(training.sessions[-1]),
        "cutoff": cutoff,
        "fitting_performed": False,
        "campaign_comparable": False,
    }
