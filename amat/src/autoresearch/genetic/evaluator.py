"""Causal candle inference; caller supplies a complete, aligned regular bar grid."""

from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray

from autoresearch.genetic.models import OHLC, CandleGenome, PatternDefinition

PANEL_DIMENSIONS = 2


def evaluate_candles(
    pattern: PatternDefinition, fields: Mapping[str, NDArray[np.float64]]
) -> NDArray[np.float64]:
    """Return (time, asset) 0/1/NaN, strictly propagating referenced invalid bars.

    All OHLC fields must share shape and adjustment basis. Every referenced bar
    must have finite positive prices and valid nonstrict OHLC ordering. Leading
    lag windows are unavailable. Missing bars must be NaN rows, never compressed
    or filled; timestamps/calendar and completed-bar availability are the caller's
    responsibility. Binding is metadata, not applied here. No labels are read.
    """
    genome = pattern.genome
    if not isinstance(genome, CandleGenome):
        raise ValueError("symbolic_indicator evaluation is not implemented")
    if any(field not in fields for field in OHLC):
        raise ValueError("all four OHLC fields are required")
    arrays = {field: np.asarray(fields[field], dtype=np.float64) for field in OHLC}
    shape = arrays["close"].shape
    if len(shape) != PANEL_DIMENSIONS or any(array.shape != shape for array in arrays.values()):
        raise ValueError("OHLC fields must share a two-dimensional (time, asset) shape")
    valid = np.ones(shape, dtype=bool)
    for array in arrays.values():
        valid &= np.isfinite(array) & (array > 0)
    for field in ("open", "close"):
        valid &= (arrays["low"] <= arrays[field]) & (arrays[field] <= arrays["high"])
    available = np.ones(shape, dtype=bool)
    matches = np.ones(shape, dtype=bool)
    for rule in genome.rules:
        operands = []
        for operand in (rule.left, rule.right):
            lag = operand.lag
            shifted = np.full(shape, np.nan)
            lag_valid = np.zeros(shape, dtype=bool)
            if lag == 0:
                shifted[:] = arrays[operand.field]
                lag_valid[:] = valid
            elif lag < shape[0]:
                shifted[lag:] = arrays[operand.field][:-lag]
                lag_valid[lag:] = valid[:-lag]
            available &= lag_valid
            operands.append(shifted)
        left, right = operands
        matches &= left > right if rule.op == "gt" else left < right
    return np.where(available, matches.astype(np.float64), np.nan)
