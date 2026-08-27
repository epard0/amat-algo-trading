"""Chart payload: candles, the bot's own indicators, and every bet placed.

The point of this view is to make a decision legible after the fact. A dot on a
price chart answers "what was the market doing when it took that trade?" far
faster than a row in the ledger does.

Two honesty constraints shape the implementation:

  * The indicators are computed by calling ``market_regime`` itself over a
    rolling 45-candle window — the same function and the same window length the
    live bot uses. Reimplementing the maths for display would let the chart and
    the trader disagree, and the chart would be the one believed.

  * Kraken returns at most 720 OHLC rows, so one-minute data reaches back only
    12 hours. Requests for longer ranges are served at a coarser interval and
    the payload says so in ``candle_interval_minutes`` and ``indicator_note``,
    because indicators computed on 15-minute bars are *not* what the bot saw.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from config import KRAKEN_PAIR_BY_ASSET
from core.strategy_brain.strategies.candles import PriceCandle
from core.strategy_brain.strategies.market_regime import classify
from data_sources.kraken.rest import KrakenRestSource
from monitoring.sources import LedgerTrade

# The live bot asks Kraken for 45 one-minute candles and classifies on all of
# them; matching that exactly is what makes the plotted regime trustworthy.
INDICATOR_WINDOW = 45

MAX_KRAKEN_ROWS = 720


def choose_interval(hours: int) -> int:
    """Smallest Kraken interval that can cover the window in 720 rows."""
    for interval in (1, 5, 15, 30, 60, 240):
        if hours * 60 / interval <= MAX_KRAKEN_ROWS:
            return interval
    return 1440


def rolling_indicators(
    candles: Sequence[PriceCandle],
    window: int = INDICATOR_WINDOW,
) -> list[dict[str, Any]]:
    """Classify the regime at every point, using only data available then.

    ``candles[i - window:i + 1]`` is a strict backward slice, so no value here
    depends on a candle that had not yet formed. A chart that leaked future
    bars would make every past decision look better informed than it was.
    """
    out: list[dict[str, Any]] = []
    for index in range(len(candles)):
        if index < 8:  # too few bars for drift or volatility to mean anything
            out.append({})
            continue
        history = candles[max(0, index - window + 1) : index + 1]
        try:
            assessment = classify(history)
        except Exception:  # noqa: BLE001 - a display path must never raise
            out.append({})
            continue
        features = assessment.features
        out.append(
            {
                "regime": assessment.regime,
                "confidence": round(assessment.confidence, 4),
                "drift_multiplier": round(assessment.drift_multiplier, 4),
                "volatility_multiplier": round(assessment.volatility_multiplier, 4),
                "extra_shrinkage": round(assessment.extra_shrinkage, 4),
                "momentum_half_life_minutes": round(
                    assessment.momentum_half_life_minutes, 3
                ),
                "realized_vol_fast": round(features.realized_vol_fast, 8),
                "realized_vol_slow": round(features.realized_vol_slow, 8),
                "vol_ratio": round(features.vol_ratio, 4),
                "efficiency_ratio": round(features.efficiency_ratio, 4),
                "return_autocorrelation": round(features.return_autocorrelation, 4),
                "range_compression": round(features.range_compression, 4),
                "impulse_sigma": round(features.impulse_sigma, 4),
                "impulse_direction": features.impulse_direction,
                "minutes_since_impulse": features.minutes_since_impulse,
                "close_location": round(features.close_location, 4),
                "travel_direction": features.travel_direction,
                "recent_travel_sigma": round(features.recent_travel_sigma, 4),
                "reasons": list(assessment.reasons),
            }
        )
    return out


def market_intervals(
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """The 15-minute market boundaries spanning the window.

    Each entry is one tradeable market: Kalshi's KXBTC15M series opens and
    settles on these exact quarter-hour edges, so shading them turns the chart
    into a row of discrete bets rather than a continuous price line.
    """
    anchor = start.replace(minute=(start.minute // 15) * 15, second=0, microsecond=0)
    intervals: list[dict[str, Any]] = []
    cursor = anchor
    while cursor < end:
        close = cursor + timedelta(minutes=15)
        intervals.append(
            {
                "open": cursor.isoformat(),
                "close": close.isoformat(),
                "label": close.strftime("%H:%M"),
            }
        )
        cursor = close
    return intervals


def trade_markers(
    trades: Sequence[LedgerTrade],
    asset: str,
    start: datetime,
) -> list[dict[str, Any]]:
    """Every bet on this asset in range, positioned for plotting.

    Placed at ``placed_at`` on the time axis and at ``target_price`` on the
    price axis: the target is the strike the market settles against, so a dot
    sitting above the price line is a YES that needed a rise, and one below is
    a NO that needed the level to hold.
    """
    markers: list[dict[str, Any]] = []
    for trade in trades:
        if trade.asset != asset or trade.placed_at is None:
            continue
        if trade.placed_at < start:
            continue
        markers.append(
            {
                "trade_id": trade.trade_id,
                "mode": trade.mode,
                "timestamp": trade.placed_at.isoformat(),
                "close_time": (
                    trade.market_close_time.isoformat()
                    if trade.market_close_time
                    else None
                ),
                "outcome": trade.outcome,
                "status": trade.status,
                "market_result": trade.market_result,
                "target_price": str(trade.target_price) if trade.target_price else None,
                "entry_price": str(trade.entry_price) if trade.entry_price else None,
                "contracts": str(trade.contracts) if trade.contracts else None,
                "pnl": str(trade.pnl) if trade.pnl is not None else None,
                "model_probability": (
                    str(trade.model_probability) if trade.model_probability else None
                ),
                "model_edge": str(trade.model_edge) if trade.model_edge else None,
                "regime": trade.regime,
                "ticker": trade.ticker,
            }
        )
    return markers


def evaluation_markers(
    decisions: Sequence[dict[str, Any]],
    asset: str,
    start: datetime,
    *,
    limit: int = 400,
) -> list[dict[str, Any]]:
    """Near-misses: evaluations that reached the model but did not trade.

    Only decisions blocked *after* the probability was computed are included.
    The 16,000 records rejected for being outside the entry window carry no
    model output and would bury the interesting ones.
    """
    interesting = {
        "min_probability",
        "min_edge",
        "model_market_divergence",
        "live_edge",
        "highest_edge_candidate",
        "live_liquidity",
        "sizeable",
    }
    out: list[dict[str, Any]] = []
    for record in decisions:
        if record.get("record_type") != "decision" or record.get("asset") != asset:
            continue
        blocked = record.get("blocked_by")
        if blocked not in interesting:
            continue
        stamp = record.get("timestamp")
        if not stamp:
            continue
        try:
            when = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < start:
            continue
        prediction = record.get("prediction") or {}
        candidate = record.get("candidate") or {}
        market = record.get("market") or {}
        out.append(
            {
                "timestamp": when.isoformat(),
                "blocked_by": blocked,
                "reason": record.get("decision_reason"),
                "probability": candidate.get("outcome_probability"),
                "edge": candidate.get("edge"),
                "outcome": candidate.get("outcome"),
                "target_price": market.get("target_price"),
                "regime": prediction.get("regime"),
            }
        )
    return out[-limit:]


async def build_chart_payload(
    asset: str,
    trades: Sequence[LedgerTrade],
    decisions: Sequence[dict[str, Any]],
    *,
    hours: int = 12,
) -> dict[str, Any]:
    """Assemble candles, indicators, market boundaries, and markers."""
    pair = KRAKEN_PAIR_BY_ASSET.get(asset.upper())
    if pair is None:
        return {
            "error": f"unknown asset {asset}",
            "supported": sorted(KRAKEN_PAIR_BY_ASSET),
        }

    interval = choose_interval(hours)
    wanted = min(MAX_KRAKEN_ROWS, max(60, int(hours * 60 / interval)))

    source = KrakenRestSource()
    try:
        candles = await source.get_ohlc(
            pair, interval_minutes=interval, limit=wanted
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"kraken fetch failed: {exc}", "asset": asset}
    finally:
        await source.close()

    if not candles:
        return {"error": "kraken returned no candles", "asset": asset}

    indicators = rolling_indicators(candles)
    start = candles[0].timestamp
    end = candles[-1].timestamp + timedelta(minutes=interval)

    note = (
        "Indicators match the live bot exactly (45 one-minute candles)."
        if interval == 1
        else (
            f"Indicators recomputed on {interval}-minute candles to cover "
            f"{hours}h; the live bot classifies on one-minute data, so these "
            "are indicative, not identical."
        )
    )

    return {
        "asset": asset.upper(),
        "pair": pair,
        "candle_interval_minutes": interval,
        "indicator_window": INDICATOR_WINDOW,
        "indicator_note": note,
        "range_hours": hours,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candles": [
            {
                "t": candle.timestamp.isoformat(),
                "o": str(candle.open),
                "h": str(candle.high),
                "l": str(candle.low),
                "c": str(candle.close),
                "v": str(candle.volume),
            }
            for candle in candles
        ],
        "indicators": indicators,
        "intervals": market_intervals(start, end) if interval <= 15 else [],
        "trades": trade_markers(trades, asset.upper(), start),
        "evaluations": evaluation_markers(decisions, asset.upper(), start),
    }
