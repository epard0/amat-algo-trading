"""Causal feature recipes applied through AMAT's existing per-asset overlay."""

from __future__ import annotations

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import DecisionOverlay
from autoresearch.contracts import Feature


def feature_values(panel: MarketPanel, feature: Feature) -> np.ndarray:
    closes = panel.adjusted_closes
    values = np.full(closes.shape, np.nan)
    window = feature.lookback
    for index in range(window, len(panel)):
        history = closes[index - window : index + 1]
        returns = history[1:] / history[:-1] - 1
        if feature.kind == "momentum":
            row = history[-1] / history[0] - 1
        elif feature.kind == "volatility":
            row = returns.std(axis=0, ddof=1) * np.sqrt(252)
        elif feature.kind == "drawdown":
            row = history[-1] / history.max(axis=0) - 1
        elif feature.kind == "efficiency":
            distance = np.abs(np.diff(history, axis=0)).sum(axis=0)
            row = np.divide(
                np.abs(history[-1] - history[0]),
                distance,
                out=np.zeros_like(distance),
                where=distance > 0,
            )
        else:
            if panel.session_volumes is None:
                raise ValueError("volume_ratio requires full-session volume")
            volume = panel.session_volumes[index - window : index + 1]
            average = volume[:-1].mean(axis=0)
            row = np.divide(
                volume[-1], average, out=np.full_like(average, np.nan), where=average > 0
            )
        values[index] = row
    if feature.smoothing > 1:
        smoothed = np.full_like(values, np.nan)
        for index in range(window + feature.smoothing - 1, len(panel)):
            smoothed[index] = values[index - feature.smoothing + 1 : index + 1].mean(axis=0)
        values = smoothed
    if feature.cross_sectional_rank:
        # Average ranks preserve symmetry for ties; no future rows participate.
        ranked = np.full_like(values, np.nan)
        for index, row in enumerate(values):
            if np.isfinite(row).all():
                ranked[index] = np.array(
                    [(np.sum(row < value) + 0.5 * np.sum(row == value)) / len(row) for value in row]
                )
        values = ranked
    return values


def feature_overlay(panel: MarketPanel, features: tuple[Feature, ...]) -> DecisionOverlay:
    multipliers = np.ones(panel.adjusted_closes.shape)
    for feature in features:
        values = feature_values(panel, feature)
        favorable = (
            values >= feature.threshold
            if feature.direction == "above"
            else values <= feature.threshold
        )
        # Unavailable features reduce risk; they never authorize additional capital.
        favorable &= np.isfinite(values)
        multipliers *= np.where(favorable, 1.0, 1.0 - feature.attenuation)
    return DecisionOverlay(multipliers, np.ones(multipliers.shape, dtype=bool))
