"""Frozen forward-only DOGE25/SOL50 trend variant; original exits unchanged."""
import numpy as np
from autoresearch.crypto_trend_short import context, short_from_context, combine
from autoresearch.crypto_breakout_windows import window_plan
from autoresearch.crypto_partial_exits import REASONS


def decision_path(frame, leg):
    asset=leg['template']
    ctx,prices=context(frame,asset)
    path,why=combine(window_plan(frame,20,asset), short_from_context(
        ctx,prices,asset,leg['adx_threshold'],leg['ema_bars']))
    value=float(path[-1]); entered=None;direction=0
    if np.isfinite(value) and value:
        direction=1 if value>0 else -1
        i=len(path)-1
        while i>0 and np.isfinite(path[i-1]) and path[i-1]*direction>0:i-=1
        entered=int(frame.index[i].timestamp()*1000)
    policy=dict(level=abs(value) if np.isfinite(value) else None,direction=direction,
                entry_ms=entered,reason='take_profit' if int(why[-1])==7 else REASONS.get(int(why[-1]),'hold'))
    return path,why,policy
