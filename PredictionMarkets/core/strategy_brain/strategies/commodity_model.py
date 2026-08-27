"""Pure-Python scorer for offline-fitted one-minute directional priors."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, stdev
from typing import Sequence

from core.strategy_brain.strategies.candles import PriceCandle


def _ema(values: list[float], span: int) -> list[float]:
    alpha = 2.0 / (span + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(alpha * value + (1 - alpha) * output[-1])
    return output


def directional_features(candles: Sequence[PriceCandle]) -> dict[str, float] | None:
    """Calculate the exact one-minute feature contract consumed by the model."""
    if len(candles) < 32:
        return None
    ordered = sorted(candles, key=lambda candle: candle.timestamp)[-60:]
    closes = [float(row.close) for row in ordered]
    highs = [float(row.high) for row in ordered]
    lows = [float(row.low) for row in ordered]
    opens = [float(row.open) for row in ordered]
    volumes = [math.log1p(float(row.volume)) for row in ordered]
    if min(closes) <= 0:
        return None
    ema12 = _ema(closes, 12)
    ema20 = _ema(closes, 20)
    ema26 = _ema(closes, 26)
    macd = [fast - slow for fast, slow in zip(ema12, ema26)]
    macd_signal = _ema(macd, 9)
    deltas = [right - left for left, right in zip(closes[-15:-1], closes[-14:])]
    average_gain = fmean(max(delta, 0.0) for delta in deltas)
    average_loss = fmean(max(-delta, 0.0) for delta in deltas)
    if average_gain == 0 and average_loss == 0:
        rsi = 50.0
    elif average_loss == 0:
        rsi = 100.0
    else:
        rsi = 100 - 100 / (1 + average_gain / average_loss)
    mean20 = fmean(closes[-20:])
    scale20 = stdev(closes[-20:])
    volume_window = volumes[-32:]
    volume_scale = stdev(volume_window)
    pressure = []
    for open_price, high, low, close in zip(
        opens[-5:], highs[-5:], lows[-5:], closes[-5:]
    ):
        pressure.append((close - open_price) / (high - low) if high > low else 0.0)
    last = closes[-1]
    ema_spread = (ema12[-1] - ema26[-1]) / last
    close_vs_ema20 = (last - ema20[-1]) / last
    macd_delta = (macd[-1] - macd_signal[-1]) / last
    rsi_centered = (rsi - 50) / 50
    hour = ordered[-1].timestamp.hour + ordered[-1].timestamp.minute / 60
    return {
        "return_1": math.log(last / closes[-2]),
        "return_3": math.log(last / closes[-4]) / 3,
        "return_8": math.log(last / closes[-9]) / 8,
        "return_15": math.log(last / closes[-16]) / 15,
        "range_5": fmean(
            (high - low) / close
            for high, low, close in zip(highs[-5:], lows[-5:], closes[-5:])
        ),
        "volume_z": (volumes[-1] - fmean(volume_window)) / volume_scale
        if volume_scale
        else 0.0,
        "ema_spread": ema_spread,
        "close_vs_ema20": close_vs_ema20,
        "macd_signal": macd_delta,
        "rsi_14": rsi_centered,
        "bollinger_z": (last - mean20) / scale20 if scale20 else 0.0,
        "candle_pressure_5": fmean(pressure),
        "indicator_score": fmean(
            (math.copysign(1, value) if value else 0.0)
            for value in (ema_spread, macd_delta, rsi_centered, close_vs_ema20)
        ),
        "hour_sin": math.sin(2 * math.pi * hour / 24),
        "hour_cos": math.cos(2 * math.pi * hour / 24),
    }


@dataclass(frozen=True)
class CommodityDirectionalModel:
    asset: str
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float
    blend_weight: float

    def predict(self, candles: Sequence[PriceCandle]) -> float | None:
        raw = directional_features(candles)
        if raw is None:
            return None
        score = self.intercept
        for name, mean, scale, coefficient in zip(
            self.feature_names, self.means, self.scales, self.coefficients
        ):
            score += coefficient * ((raw.get(name, mean) - mean) / max(scale, 1e-12))
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, score))))


def load_commodity_models(path: Path | None) -> dict[str, CommodityDirectionalModel]:
    if path is None or not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0)) != 2:
        raise ValueError("unsupported directional model schema")
    result = {}
    for asset, row in (payload.get("assets") or {}).items():
        model = CommodityDirectionalModel(
            asset=asset,
            feature_names=tuple(row["feature_names"]),
            means=tuple(float(value) for value in row["feature_means"]),
            scales=tuple(float(value) for value in row["feature_scales"]),
            coefficients=tuple(float(value) for value in row["coefficients"]),
            intercept=float(row["intercept"]),
            blend_weight=float(row.get("blend_weight", 0)),
        )
        if not 0 <= model.blend_weight <= 0.25:
            raise ValueError(f"{asset}: directional blend weight outside [0, .25]")
        result[asset] = model
    return result
