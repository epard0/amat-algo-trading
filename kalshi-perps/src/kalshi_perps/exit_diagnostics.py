"""Explain a completed engine exit without changing its target or execution."""


def exit_reason(frame, values, leg):
    import numpy as np
    import pandas as pd

    v = np.asarray(values).reshape(-1)
    if len(v) < 2 or not np.isfinite(v[-1]) or not np.isfinite(v[-2]):
        return "unknown"
    if v[-1]:
        return "hold" if v[-2] else "entry"
    if not v[-2]:
        return "flat"
    if leg["engine"] == "symmetric":
        return "channel_exit"
    prices = (
        frame[["open", "high", "low", "close"]]
        .where(frame.tradable > 0, axis=0)
        .resample("4h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    )
    exit_bar = frame.index[-1] - pd.Timedelta(hours=4)
    if exit_bar not in prices.index:
        return "unknown"
    # Locate the contiguous active episode in the engine's already-produced path.
    first = len(v) - 2
    while first > 0 and np.isfinite(v[first - 1]) and v[first - 1] * v[first] > 0:
        first -= 1
    entry_bar = frame.index[first] - pd.Timedelta(hours=4)
    if entry_bar not in prices.index:
        return "unknown"
    c, hi, lo = prices.close, prices.high, prices.low
    direction = 1 if v[-2] > 0 else -1
    channel = lo.rolling(60).min().shift(1) if direction > 0 else hi.rolling(60).max().shift(1)
    if (
        c.loc[exit_bar] < channel.loc[exit_bar]
        if direction > 0
        else c.loc[exit_bar] > channel.loc[exit_bar]
    ):
        return "channel_exit"
    tp = leg.get("tp_atr")
    if tp is not None:
        tr = pd.concat([hi - lo, (hi - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(
            axis=1
        )
        atr = tr.rolling(14).mean().loc[entry_bar]
        if direction * (c.loc[exit_bar] - c.loc[entry_bar]) >= float(tp) * atr:
            return "take_profit"
        hold = leg.get("max_hold_hours", 72)
        if (exit_bar - entry_bar).total_seconds() / 3600 >= hold:
            return "time_exit"
    return "unknown"
