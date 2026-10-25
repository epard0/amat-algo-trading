"""Causal intraday indicators; frozen signal-time ATR, no fitted models."""
from functools import lru_cache

import numpy as np
import pandas as pd

FAMILIES = ('ema_adx', 'macd', 'rsi_reversal', 'bollinger_reversion',
            'vwap_reversion', 'donchian_volume', 'kalman_trend')


def indicators(bars):
    c = pd.Series(bars[:, 3])
    h, low, volume = (pd.Series(bars[:, i]) for i in (1, 2, 4))
    tr = pd.concat([h-low, (h-c.shift()).abs(), (low-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100-100/(1+gain/loss.replace(0, np.nan))
    rsi = rsi.mask((loss == 0) & (gain > 0), 100).mask((loss == 0) & (gain == 0), 50)
    up, down = h.diff(), -low.diff()
    plus = up.where((up > down) & (up > 0), 0).ewm(alpha=1/14, adjust=False).mean()/atr
    minus = down.where((down > up) & (down > 0), 0).ewm(alpha=1/14, adjust=False).mean()/atr
    adx = (100*(plus-minus).abs()/(plus+minus).replace(0, np.nan)).ewm(alpha=1/14, adjust=False).mean()
    fast, slow = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = fast-slow
    hist = macd-macd.ewm(span=9, adjust=False).mean()
    mid, std = c.rolling(20).mean(), c.rolling(20).std(ddof=1)
    vwap = (((h+low+c)/3)*volume).cumsum()/volume.cumsum().replace(0, np.nan)
    return pd.DataFrame({'close': c, 'atr': atr, 'rsi': rsi, 'adx': adx, 'fast': fast,
                         'slow': slow, 'hist': hist, 'mid': mid, 'std': std, 'vwap': vwap,
                         'upper': h.rolling(20).max().shift(1), 'lower': low.rolling(20).min().shift(1),
                         'volume_ratio': volume/volume.rolling(20).mean().shift(1)})


@lru_cache(maxsize=512)
def _rsi_values(payload):
    bars = np.frombuffer(payload, dtype=np.float64).reshape(-1, 5)
    values = indicators(bars)['rsi'].to_numpy(copy=True)
    values.setflags(write=False)
    return values


def rsi_values(bars):
    return _rsi_values(np.asarray(bars, dtype=np.float64).tobytes())


def causal_kalman_state(close, process_variance=.01, observation_variance=4.):
    # Fixed local-linear model in price units. Forward filtering only; no smoother,
    # full-sample variance estimate, fitted parameters, or backward fill.
    state = np.array([float(close[0]), 0.])
    covariance = np.eye(2)*100
    transition = np.array([[1., 1.], [0., 1.]])
    output, variance = [], []
    for price in close:
        state = transition@state
        covariance = transition@covariance@transition.T + np.diag([0., process_variance])
        gain = covariance[:, 0]/(covariance[0, 0]+observation_variance)
        state += gain*(price-state[0])
        covariance -= np.outer(gain, covariance[0].copy())
        output.append(state[1])
        variance.append(max(0., covariance[1, 1]))
    return np.array(output), np.array(variance)


def causal_kalman_slope(close, process_variance=.01, observation_variance=4.):
    return causal_kalman_state(close, process_variance, observation_variance)[0]


def decelerated_signals(long, short, slope, wait=10, start=40):
    """Wait at least two completed bars after a recross; opposing slope weakens twice."""
    result_long = pd.Series(False, index=long.index)
    result_short = pd.Series(False, index=short.index)
    pending = None
    for minute in range(start, len(long)):
        if long.iloc[minute] or short.iloc[minute]:
            pending = (minute, 1 if long.iloc[minute] else -1)
        if pending is None:
            continue
        origin, direction = pending
        if minute-origin > wait:
            pending = None
        elif minute-origin >= 2 and direction*slope[minute] > direction*slope[minute-1] > direction*slope[minute-2]:
            (result_long if direction > 0 else result_short).iloc[minute] = True
            pending = None
    return result_long, result_short


def kalman_gate(direction, normalized_slope, mode, threshold):
    if mode == 'aligned':
        return direction*normalized_slope >= threshold
    if mode == 'countertrend':
        return direction*normalized_slope <= -threshold
    if mode == 'calm':
        return np.abs(normalized_slope) <= threshold
    if mode == 'no_strong_opposition':
        return direction*normalized_slope >= -threshold
    raise ValueError(f'Unknown Kalman gate: {mode}')


@lru_cache(maxsize=256)
def _plan(payload, family, multiplier, cutoff, gate='aligned', threshold=.05, process_variance=.01,
          min_tstat=0., deceleration=False, direction='both', entry_start=40):
    bars = np.frombuffer(payload, dtype=np.float64).reshape(-1, 5)
    d = indicators(bars)
    c, prev = d['close'], d.shift(1)
    if family == 'ema_adx':
        long = (d.fast > d.slow) & (prev.fast <= prev.slow) & (d.adx >= 20)
        short = (d.fast < d.slow) & (prev.fast >= prev.slow) & (d.adx >= 20)
    elif family == 'macd':
        long = (d['hist'] > 0) & (prev['hist'] <= 0) & (c > d.slow)
        short = (d['hist'] < 0) & (prev['hist'] >= 0) & (c < d.slow)
    elif family in ('rsi_reversal', 'rsi_kalman'):
        long = (d.rsi > 30) & (prev.rsi <= 30)
        short = (d.rsi < 70) & (prev.rsi >= 70)
        if family == 'rsi_kalman':
            slope, variance = causal_kalman_state(c.to_numpy(), process_variance)
            normalized = pd.Series(slope)/d.atr.replace(0, np.nan)
            if deceleration:
                long, short = decelerated_signals(long, short, slope, start=entry_start)
            long &= kalman_gate(1, normalized, gate, threshold)
            short &= kalman_gate(-1, normalized, gate, threshold)
            if min_tstat:
                confident = np.abs(slope)/np.sqrt(np.maximum(variance, 1e-12)) >= min_tstat
                long &= confident
                short &= confident
    elif family == 'bollinger_reversion':
        long = (prev.close < prev.mid-2*prev['std']) & (c >= d.mid-2*d['std'])
        short = (prev.close > prev.mid+2*prev['std']) & (c <= d.mid+2*d['std'])
    elif family == 'vwap_reversion':
        long = (prev.close < prev.vwap-prev.atr) & (c >= d.vwap-d.atr)
        short = (prev.close > prev.vwap+prev.atr) & (c <= d.vwap+d.atr)
    elif family == 'donchian_volume':
        long = (c > d.upper) & (prev.close <= prev.upper) & (d.volume_ratio >= 1.2)
        short = (c < d.lower) & (prev.close >= prev.lower) & (d.volume_ratio >= 1.2)
    elif family == 'kalman_trend':
        slope = pd.Series(causal_kalman_slope(c.to_numpy()))
        long = (slope > .05*d.atr) & (slope.shift(1) <= .05*prev.atr) & (c > d.vwap)
        short = (slope < -.05*d.atr) & (slope.shift(1) >= -.05*prev.atr) & (c < d.vwap)
    else:
        raise ValueError(f'Unknown indicator strategy: {family}')
    if direction == 'long':
        short[:] = False
    elif direction == 'short':
        long[:] = False
    for minute in range(entry_start, min(cutoff, len(bars)-2)):
        if long.iloc[minute] or short.iloc[minute]:
            return minute, (1 if long.iloc[minute] else -1), max(.25, float(d.atr.iloc[minute])*multiplier)
    return None


def signal_plan(bars, candidate):
    return _plan(np.asarray(bars, dtype=np.float64).tobytes(), candidate['strategy'],
                 candidate['atr_multiplier'], candidate.get('entry_cutoff_minute', 180),
                 candidate.get('kalman_gate', 'aligned'), candidate.get('kalman_threshold', .05),
                 candidate.get('kalman_process_variance', .01), candidate.get('kalman_min_tstat', 0.),
                 candidate.get('deceleration', False), candidate.get('direction', 'both'),
                 candidate.get('entry_start_minute', 40))
