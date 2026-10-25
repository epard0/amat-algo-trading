"""Causal minute signals and next-open execution with fixed notional sleeves.

Signals use completed raw minute bars. Positions never cross a session boundary.
Stops trigger on a completed close and fill at the next open, not at a perfect stop price.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from autoresearch.intraday.data import Panel
from autoresearch.intraday.specs import Recipe, Term

EPSILON = 1e-12
LAST_OPEN = 389


def rolling(x: np.ndarray, window: int, kind: str = "mean") -> np.ndarray:
    output = np.full_like(x, np.nan)
    windows = sliding_window_view(x, window, axis=1)
    if kind == "mean":
        values = np.mean(windows, axis=-1)
    elif kind == "std":
        values = np.std(windows, axis=-1)
    elif kind == "max":
        values = np.max(windows, axis=-1)
    elif kind == "min":
        values = np.min(windows, axis=-1)
    else:
        values = np.sum(windows, axis=-1)
    output[:, window - 1 :] = values
    return output


def shift(x: np.ndarray, amount: int) -> np.ndarray:
    if not amount:
        return x.copy()
    output = np.full_like(x, np.nan)
    output[:, amount:] = x[:, :-amount]
    return output


def feature(panel: Panel, term: Term) -> np.ndarray:  # noqa: PLR0912 - explicit feature formulas
    key = term.model_copy(update={"weight": 1, "center": 0, "scale": 1}).digest()
    if key in panel.cache:
        return panel.cache[key]
    # Invalid asset-days are fully masked after calculation. Neutral placeholders
    # avoid numerical warnings without making the data tradable.
    v = np.where(np.isfinite(panel.values), panel.values, 1.0)
    o, h, low, c, volume = (v[:, :, :, i] for i in range(5))
    w = term.lookback
    returns = c / shift(c, 1) - 1
    returns[:, 0] = 0
    vol = np.maximum(rolling(returns, w, "std"), 1e-6)
    momentum = (c / shift(c, w) - 1) / (vol * np.sqrt(w))
    top, bottom = rolling(h, w, "max"), rolling(low, w, "min")
    if term.feature == "momentum":
        value = momentum
    elif term.feature == "mean_reversion":
        value = -(c / rolling(c, w) - 1) / (vol * np.sqrt(w))
    elif term.feature == "channel_position":
        value = 2 * (c - bottom) / np.maximum(top - bottom, 1e-8) - 1
    elif term.feature == "breakout":
        value = (c / shift(top, 1) - 1) / vol
    elif term.feature == "efficiency":
        value = (c - shift(c, w)) / np.maximum(rolling(np.abs(c - shift(c, 1)), w, "sum"), 1e-8)
    elif term.feature == "volatility":
        value = vol * 1000
    elif term.feature == "volume_surprise":
        value = volume / np.maximum(rolling(volume, w), 1) - 1
    elif term.feature == "vwap_distance":
        typical = (h + low + c) / 3
        vwap = rolling(typical * volume, w, "sum") / np.maximum(rolling(volume, w, "sum"), 1)
        value = (c / vwap - 1) / (vol * np.sqrt(w))
    elif term.feature == "candle_body":
        value = rolling((c - o) / np.maximum(h - low, 1e-8), w)
    elif term.feature == "range_expansion":
        value = (h - low) / np.maximum(rolling(h - low, w), 1e-8) - 1
    elif term.feature == "trend_agreement":
        half = max(1, w // 2)
        value = (np.sign(c - shift(c, w)) + np.sign(c - shift(c, half))) / 2
    else:
        half = max(1, w // 2)
        value = ((c / shift(c, half) - 1) - (shift(c, half) / shift(c, w) - 1)) / (vol * np.sqrt(w))
    if term.transform == "absolute":
        value = np.abs(value)
    elif term.transform == "signed_square":
        value = np.sign(value) * np.square(np.clip(value, -10, 10))
    value = shift(rolling(value, term.smoothing), term.lag)
    value[~np.broadcast_to(panel.valid[:, None, :], value.shape)] = np.nan
    panel.cache[key] = value
    return value


def signal(panel: Panel, recipe: Recipe) -> np.ndarray:
    terms = [np.tanh((feature(panel, t) - t.center) / t.scale) for t in recipe.terms]
    columns = [value * t.weight for value, t in zip(terms, recipe.terms, strict=True) if t.weight]
    weights = [abs(t.weight) for t in recipe.terms if t.weight]
    for interaction in recipe.interactions:
        left, right = terms[interaction.left], terms[interaction.right]
        if interaction.operation == "product":
            value = left * right
        elif interaction.operation == "difference":
            value = left - right
        elif interaction.operation == "minimum":
            value = np.minimum(left, right)
        elif interaction.operation == "maximum":
            value = np.maximum(left, right)
        else:
            value = left * (right + 1) / 2
        if interaction.weight:
            columns.append(value * interaction.weight)
            weights.append(abs(interaction.weight))
    stack = np.array(columns)
    if recipe.combine == "weighted":
        result = stack.sum(axis=0) / sum(weights)
    elif recipe.combine == "minimum":
        result = stack.min(axis=0)
    elif recipe.combine == "maximum":
        result = stack.max(axis=0)
    else:
        result = np.prod(stack, axis=0)
    return np.clip(result, -5, 5)


def performance(returns: np.ndarray) -> dict[str, Any]:
    if not len(returns):
        return {"sessions": 0}
    equity = np.r_[1.0, np.cumprod(1 + returns)]
    drawdown = equity / np.maximum.accumulate(equity) - 1
    volatility = float(np.std(returns, ddof=1) * np.sqrt(252)) if len(returns) > 1 else 0.0
    ann = float(equity[-1] ** (252 / len(returns)) - 1) if equity[-1] > 0 else -1.0
    downside = float(np.sqrt(np.mean(np.square(np.minimum(returns, 0)))) * np.sqrt(252))
    arithmetic = float(returns.mean() * 252)
    q = float(np.quantile(returns, 0.05))
    tail = returns[returns <= q]
    durations, streak = [], 0
    for value in drawdown:
        streak = streak + 1 if value < -EPSILON else 0
        durations.append(streak)
    return {
        "sessions": len(returns),
        "total_return": float(equity[-1] - 1),
        "annualized_return": ann,
        "annualized_volatility": volatility,
        "sharpe": arithmetic / volatility if volatility > EPSILON else 0.0,
        "sortino": arithmetic / downside if downside > EPSILON else None,
        "calmar": ann / abs(float(drawdown.min())) if drawdown.min() < 0 else None,
        "max_drawdown": float(drawdown.min()),
        "max_drawdown_duration_sessions": max(durations),
        "daily_var_95": q,
        "daily_cvar_95": float(tail.mean()),
        "best_day": float(returns.max()),
        "worst_day": float(returns.min()),
        "positive_day_fraction": float(np.mean(returns > 0)),
    }


def simulate(  # noqa: PLR0912, PLR0915 - chronological fill/accounting pass
    panel: Panel,
    recipe: Recipe,
    *,
    cost_bps: float = 2.0,
    delay: int = 1,
    scores: np.ndarray | None = None,
) -> dict[str, Any]:
    if not set(recipe.symbols).issubset(panel.symbols):
        raise ValueError("recipe selects assets outside frozen minute dataset")
    scores = signal(panel, recipe) if scores is None else scores
    days = len(panel.dates)
    pnl = np.zeros((days, 390))
    exposure = np.zeros_like(pnl)
    trades: list[dict[str, Any]] = []
    rejected_liquidity = 0
    for a, symbol in enumerate(panel.symbols):
        if symbol not in recipe.symbols:
            continue
        for d, date in enumerate(panel.dates):
            if not panel.valid[d, a]:
                continue
            bars = panel.values[d, :, a]
            o, h, low, c, volume = (bars[:, i] for i in range(5))
            s = scores[d, :, a]
            signs = np.where(
                s >= recipe.entry_threshold, 1, np.where(s <= -recipe.entry_threshold, -1, 0)
            )
            if recipe.direction == "long_only":
                signs[signs < 0] = 0
            elif recipe.direction == "short_only":
                signs[signs > 0] = 0
            unconfirmed = signs.copy()
            for k in range(1, recipe.confirmation_bars):
                lag = k * recipe.decision_minutes
                prior = np.r_[np.zeros(lag), unconfirmed[:-lag]]
                signs = np.where(signs == prior, signs, 0)
            decisions = np.arange(recipe.start_minute, recipe.end_minute + 1)
            decisions = decisions[decisions % recipe.decision_minutes == 0]
            available, count, realized = 0, 0, 0.0
            for t in decisions:
                entry = int(t + delay)
                side = int(signs[t])
                if not side or entry < available or entry + recipe.max_hold_minutes > LAST_OPEN:
                    continue
                if (
                    count >= recipe.max_trades_per_asset_day
                    or realized <= -recipe.daily_loss_limit_bps / 1e4
                ):
                    break
                # Estimate participation only from completed bars, never future entry volume.
                weight = recipe.allocation_per_asset
                notional = 100000 * weight
                trailing_dollars = np.mean(
                    c[max(0, t - 19) : t + 1] * volume[max(0, t - 19) : t + 1]
                )
                if notional > 0.01 * trailing_dollars:
                    rejected_liquidity += 1
                    continue
                limit = entry + recipe.max_hold_minutes
                exit_at, reason = limit, "time_limit"
                excursion = side * (c[entry:limit] / o[entry] - 1)
                for k, r in enumerate(excursion):
                    minute = entry + k
                    trailing = float(excursion[: k + 1].max() - r) * 1e4
                    if r * 1e4 <= -recipe.stop_loss_bps:
                        exit_at, reason = minute + 1, "stop_close"
                        break
                    if r * 1e4 >= recipe.take_profit_bps:
                        exit_at, reason = minute + 1, "take_profit_close"
                        break
                    if (
                        recipe.trailing_stop_bps is not None
                        and trailing >= recipe.trailing_stop_bps
                    ):
                        exit_at, reason = minute + 1, "trailing_stop_close"
                        break
                    if (
                        minute + 1 - entry >= recipe.min_hold_minutes
                        and minute % recipe.decision_minutes == 0
                        and side * s[minute] <= recipe.exit_threshold
                    ):
                        exit_at, reason = minute + 1, "signal_exit"
                        break
                gross = side * (o[exit_at] / o[entry] - 1)
                # Cost on actual buy/sell notional; explicit borrow/locate proxy on shorts.
                entry_cost = cost_bps / 1e4
                exit_cost = cost_bps / 1e4 * o[exit_at] / o[entry]
                short_fee = 1.0 / 1e4 if side < 0 else 0.0
                net = gross - entry_cost - exit_cost - short_fee
                pnl[d, entry] -= weight * (entry_cost + short_fee)
                pnl[d, entry + 1 : exit_at + 1] += (
                    weight * side * np.diff(o[entry : exit_at + 1]) / o[entry]
                )
                pnl[d, exit_at] -= weight * exit_cost
                exposure[d, entry:exit_at] += weight * o[entry:exit_at] / o[entry]
                if side > 0:
                    mfe, mae = (
                        h[entry:exit_at].max() / o[entry] - 1,
                        low[entry:exit_at].min() / o[entry] - 1,
                    )
                else:
                    mfe, mae = (
                        1 - low[entry:exit_at].min() / o[entry],
                        1 - h[entry:exit_at].max() / o[entry],
                    )
                trades.append(
                    {
                        "date": date,
                        "symbol": symbol,
                        "decision_minute": int(t),
                        "entry_minute": entry,
                        "exit_minute": exit_at,
                        "hold_minutes": exit_at - entry,
                        "direction": side,
                        "entry_price": float(o[entry]),
                        "exit_price": float(o[exit_at]),
                        "weight": weight,
                        "gross_return": float(gross),
                        "net_return": float(net),
                        "portfolio_return": float(weight * net),
                        "cost_return": float(entry_cost + exit_cost + short_fee),
                        "mfe": float(mfe),
                        "mae": float(mae),
                        "exit_reason": reason,
                        "signal": float(s[t]),
                        "estimated_notional": notional,
                        "estimated_participation": float(notional / trailing_dollars),
                    }
                )
                available, count, realized = (
                    exit_at + recipe.cooldown_minutes,
                    count + 1,
                    realized + weight * net,
                )
    trades.sort(key=lambda trade: (trade["date"], trade["entry_minute"], trade["symbol"]))
    returns = pnl.sum(axis=1)
    if np.any(returns <= -1):
        raise ValueError("strategy bankrupt under fixed sleeve accounting")
    metrics = performance(returns)
    net = np.array([t["net_return"] for t in trades])
    winners, losers = net[net > 0], net[net < 0]
    gross = np.array([t["gross_return"] for t in trades])
    day_start = np.r_[1.0, np.cumprod(1 + returns)[:-1]]
    minute_equity = (day_start[:, None] * (1 + np.cumsum(pnl, axis=1))).reshape(-1)
    peaks = np.maximum.accumulate(np.r_[1.0, minute_equity])[1:]
    metrics.update(
        {
            "total_trades": len(trades),
            "total_fills": 2 * len(trades),
            "win_rate": float(np.mean(net > 0)) if len(net) else None,
            "profit_factor": float(winners.sum() / abs(losers.sum())) if len(losers) else None,
            "expectancy_bps": float(net.mean() * 1e4) if len(net) else None,
            "gross_expectancy_bps": float(gross.mean() * 1e4) if len(net) else None,
            "average_win_bps": float(winners.mean() * 1e4) if len(winners) else None,
            "average_loss_bps": float(losers.mean() * 1e4) if len(losers) else None,
            "average_hold_minutes": float(np.mean([t["hold_minutes"] for t in trades]))
            if trades
            else None,
            "average_mae_bps": float(np.mean([t["mae"] for t in trades]) * 1e4) if trades else None,
            "average_mfe_bps": float(np.mean([t["mfe"] for t in trades]) * 1e4) if trades else None,
            "average_gross_exposure": float(exposure.mean()),
            "peak_gross_exposure": float(exposure.max()),
            "intraday_max_drawdown": float(np.min(minute_equity / peaks - 1)),
            "turnover_per_day": sum(
                t["weight"] * (1 + t["exit_price"] / t["entry_price"]) for t in trades
            )
            / days,
            "cost_drag_annualized": sum(t["weight"] * t["cost_return"] for t in trades)
            * 252
            / days,
            "liquidity_rejections": rejected_liquidity,
            "cost_bps_one_way": cost_bps,
            "execution_delay_minutes": delay,
        }
    )
    daily = [
        {
            "session": date,
            "return": float(returns[d]),
            "equity": float(np.prod(1 + returns[: d + 1])),
            "gross_exposure": float(exposure[d].mean()),
        }
        for d, date in enumerate(panel.dates)
    ]
    return {"metrics": metrics, "daily": daily, "trades": trades}
