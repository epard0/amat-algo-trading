"""Registered DOGE/SOL short-window search; no holdout-based tuning."""
from __future__ import annotations

import argparse
import ast
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import time

import numpy as np
import pandas as pd
import polars as pl

from autoresearch.crypto_fast_breakout import prepare, replay_events, short_weights, combined_metrics
from autoresearch.crypto_doge_sol import profit_weights
from autoresearch.crypto_universe_study import load_asset

TRAIN = ('2024-01-01', '2025-06-30')
PERIODS = dict(validation=('2025-07-01', '2025-12-31'), test=('2026-01-01', '2026-09-30'),
               post_training=('2025-07-01', '2026-09-30'))
INTERVALS = (1, 5, 15, 30)
WINDOWS = (2, 6, 24)
EXITS = list(itertools.product((.5, 1., 2.), (1, 6)))
ALLOCATIONS = (.25, .5, .75)
LEVERAGES = (1., 1.5, 2.)


def load_minutes(root):
    manifest = json.loads((root/'manifest.json').read_text())
    grid = pd.date_range('2024-01-01', '2026-10-01', freq='1min', inclusive='left', tz='UTC')
    frames, audit = {}, {}
    for symbol in ('DOGEUSDT', 'SOLUSDT'):
        pieces = {}
        for kind in ('klines', 'markPriceKlines'):
            record = next(r for r in manifest['datasets'] if r['symbol']==symbol and r['kind']==kind)
            path = root/record['file']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:
                raise ValueError('minute checksum mismatch')
            raw = pl.read_parquet(path)
            f = pd.DataFrame({c: raw[c].to_numpy() for c in ('open_time_ms', 'close_time_ms', 'open', 'high', 'low', 'close', 'volume')})
            if len(f)!=record['rows'] or f.open_time_ms.duplicated().any():
                raise ValueError('minute count/duplicates')
            if not (f.close_time_ms==f.open_time_ms+59999).all():
                raise ValueError('invalid candle duration')
            if not ((f.low>0)&(f.low<=f[['open', 'close']].min(axis=1))&(f.high>=f[['open', 'close']].max(axis=1))).all():
                raise ValueError('invalid OHLC')
            f.index = pd.to_datetime(f.open_time_ms, unit='ms', utc=True)
            pieces[kind] = f.reindex(grid)
        trade, mark = pieces['klines'], pieces['markPriceKlines']
        if trade[['open', 'high', 'low', 'close']].isna().any().any():
            raise ValueError('missing trade minute')
        frame = trade[['open', 'high', 'low', 'close']].copy()
        frame['tradable'] = (trade.volume>0).astype(float)
        frame['mark_available'] = mark.open.notna()
        for c in ('open', 'high', 'low', 'close'):
            # Unavailable marks are only placeholders for guaranteed-flat wallets.
            frame['mark_'+c] = mark[c].fillna(trade[c])
        frames[symbol] = frame
        audit[symbol] = dict(minutes=len(frame), missing_trade_minutes=0,
            missing_mark_minutes=[str(t) for t in grid[~frame.mark_available]],
            missing_mark_policy='flat before missing minute; no entry until mark available; placeholder prices only permitted with zero exposure',
            zero_volume_minutes=int((trade.volume==0).sum()))
    return frames, audit


def training_stats(doge, sol):
    daily = doge[1].equity.to_numpy()+sol[1].equity.to_numpy()
    eq = np.r_[10000., daily]
    d, s = doge[0], sol[0]
    months = len(daily)/30.4375
    return dict(pnl=float(eq[-1]-10000), max_drawdown=float((eq/np.maximum.accumulate(eq)-1).min()),
        profitable_closes=d['profitable_closes']+s['profitable_closes'],
        profitable_closes_per_month=(d['profitable_closes']+s['profitable_closes'])/months,
        natural_closes=d['natural_closes']+s['natural_closes'],
        liquidations=d['liquidations']+s['liquidations'], margin_rejections=d['margin_rejections']+s['margin_rejections'],
        fees=d['fees']+s['fees'], funding_pnl=d['funding_pnl']+s['funding_pnl'])


def choose_training(rows):
    chosen, missing = {}, []
    for minutes in INTERVALS:
        eligible = [r for r in rows if r['config']['minutes']==minutes and r['liquidations']==0 and r['natural_closes']>=20]
        if not eligible:
            missing.append(dict(minutes=minutes, reason='no active liquidation-free recipe'))
            continue
        chosen[f'{minutes}m-pnl'] = max(eligible, key=lambda r:r['pnl'])
        frequent = [r for r in eligible if r['pnl']>0 and r['max_drawdown']>=-.35 and r['profitable_closes_per_month']>=4]
        if frequent:
            chosen[f'{minutes}m-frequent'] = max(frequent, key=lambda r:r['pnl'])
        else:
            missing.append(dict(minutes=minutes, reason='no positive-net training recipe with DD<=35% and >=4 profitable natural closes/month'))
    pnl_labels = [k for k in chosen if k.endswith('-pnl')]
    freq_labels = [k for k in chosen if k.endswith('-frequent')]
    pnl = max(pnl_labels, key=lambda k:chosen[k]['pnl'])
    frequent = max(freq_labels, key=lambda k:chosen[k]['pnl']) if freq_labels else None
    return dict(selected=chosen, global_training_pnl=pnl, global_training_frequent=frequent, missing=missing,
        primary=frequent or pnl)


def audit_training_reuse(parent, out, assets, protocol):
    """Prove additional availability boundaries cannot change original recipes."""
    old = json.loads((parent/'protocol.json').read_text())
    for key in ('training', 'periods', 'signal_intervals', 'entry_hours', 'exit_configs', 'doge_allocations',
                'common_per_sleeve_leverage', 'minute_manifest_sha256', 'funding_manifest_sha256', 'inventory_sha256', 'selection'):
        if json.loads(json.dumps(protocol[key]))!=old[key]:
            raise ValueError('reuse changes registered field: '+key)
    # Signals, data preparation, aggregation and ranking must be bytecode-equivalent ASTs.
    for filename, names in [('crypto_fast_breakout.py', ('short_weights', 'prepare')),
            ('crypto_short_window_study.py', ('training_stats', 'choose_training'))]:
        old_tree = ast.parse((parent/'source'/filename).read_text())
        new_tree = ast.parse(Path(__file__).with_name(filename).read_text())
        for name in names:
            before = next(n for n in old_tree.body if isinstance(n, ast.FunctionDef) and n.name==name)
            after = next(n for n in new_tree.body if isinstance(n, ast.FunctionDef) and n.name==name)
            if ast.dump(before)!=ast.dump(after):
                raise ValueError('training logic changed: '+name)
    for name in ('perpetual_sim.py', 'crypto_directional.py'):
        if hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()!=old['source_sha256'][name]:
            raise ValueError('wallet or episode accounting changed')
    affected, checked = [], 0
    for symbol, (data, _) in assets.items():
        tr = data['a']['tradable']>0
        ix = np.arange(len(tr))
        resumes = np.flatnonzero(tr[1:]&~tr[:-1])+1
        for path in sorted((parent/'signals').glob(symbol+'-*.npz')):
            state = np.load(path)['state']
            state[data['blackout']] = 0
            last = np.maximum.accumulate(np.where(tr&(state>=0), ix, -1))
            for i in resumes:
                previous = state[last[i-1]] if last[i-1]>=0 else 0
                old_boundary = max(0, state[i])!=max(0, state[i-1]) or data['ms'][i]%86400000==0
                if state[i]>=0 and state[i]!=previous and not old_boundary:
                    affected.append(dict(signal=path.name, index=int(i)))
            checked += 1
    audit = dict(signals_checked=checked, affected=affected,
        parent_protocol_sha256=hashlib.sha256((parent/'protocol.json').read_bytes()).hexdigest(),
        interpretation='No affected resume orders means added availability boundaries leave all original training paths, fees, funding and rankings unchanged; every selected training wallet is also replayed and compared.')
    (out/'reuse-audit.json').write_text(json.dumps(audit, indent=2))
    if checked!=144 or affected:
        raise ValueError('training reuse not proven; full recomputation required')
    return json.loads((parent/'training.json').read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reuse-training', type=Path)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    root = Path('data/research/doge-sol-minute-2024-2026-09')
    inventory = Path('outputs/crypto-directional-2026-10-19/kalshi-inventory/markets.json')
    roots = dict(DOGEUSDT=Path('data/research/kalshi-universe-proxies-2021-2026-09'),
                 SOLUSDT=Path('data/research/kalshi-universe-gap-supplement-2021-2026-09'))
    sources = [Path(__file__), Path(__file__).with_name('crypto_fast_breakout.py'),
        Path(__file__).with_name('perpetual_sim.py'), Path(__file__).with_name('crypto_doge_sol.py'),
        Path(__file__).with_name('crypto_universe_study.py'), Path(__file__).with_name('crypto_directional.py')]
    protocol = dict(training=TRAIN, periods=PERIODS, signal_intervals=INTERVALS, entry_hours=WINDOWS,
        exit_lookback='one quarter of entry lookback in native bars, minimum1bar', exit_configs=EXITS,
        doge_allocations=ALLOCATIONS, sol_allocation='1 minus DOGE', common_per_sleeve_leverage=LEVERAGES,
        candidate_portfolios=3888, training_sleeve_replays=1296, initial_capital=10000,
        selection='max training net P&L per signal interval with >=20 natural closes and no liquidations; frequent selection also positive net P&L, daily DD<=35%, >=4 profitable closes/month. Global primary is highest training P&L frequent choice, else highest training P&L. No holdout tuning or fallback thresholds.',
        accounting='existing Decimal wallet at every entry, exit, daily size rebalance, funding; exact integer lots and tick rounding. Between mutations, vectorized fixed-contract minute mark equity and first liquidation threshold crossing. Unit parity against canonical minute wrapper.',
        funding='historical Binance rates at actual millisecond events, once, before orders in minute',
        costs='8bps per side primary:4bps taker+4bps spread/slippage. Global training P&L and frequency choices also15/30bps.',
        capital_policy='independent initial collateral sleeves, own compounding, no transfers or periodic allocation rebalancing; target .98 times leverage',
        gaps='force flat at prior available minute open, charge costs, stay flat through unavailable mark minutes, do not count blackout exits as natural profitable closes',
        profit_frequency='net full-position episodes incl funding/fees/rebalance P&L; forced final and data-blackout closes excluded; no withdrawals',
        baseline='frozen preceding4h25%DOGE/75%SOL1.5x (and1x), DOGE0.5ATR, SOL2ATR, max72h;20/10day channels; minute execution on identical dates',
        caveats=['Binance trade/mark/funding proxies, not Kalshi historical executions or funding',
            'assets and dates previously inspected; retrospective chronological follow-up',
            'current fee/tick/multiplier and fixed .5% maintenance/.5% liquidation fee do not recreate dynamic Kalshi risk tiers',
            'no measured quote depth or impact; minute-close DD excludes intraminute troughs',
            '3888-portfolio search can overfit; validation and test never determine recipe selection'],
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        minute_manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
        funding_manifest_sha256={s:hashlib.sha256((p/'manifest.json').read_bytes()).hexdigest() for s,p in roots.items()},
        inventory_sha256=hashlib.sha256(inventory.read_bytes()).hexdigest())
    if args.reuse_training:
        protocol['parent_protocol_sha256'] = hashlib.sha256((args.reuse_training/'protocol.json').read_bytes()).hexdigest()
        protocol['correction'] = 'Added zero-volume resume and mark-availability event boundaries. Training reused only after exhaustive signal-state audit and selected-wallet parity. Restored original4h hourly tradability to any traded minute. No candidate, threshold or selection change; all later replays recomputed.'
    (out/'protocol.json').write_text(json.dumps(protocol, indent=2))
    snap = out/'source'
    snap.mkdir()
    for p in sources:
        shutil.copy2(p, snap/p.name)
    frames, audit = load_minutes(root)
    markets = json.loads(inventory.read_text())['markets']
    assets = {}
    for symbol in frames:
        _, funding, _ = load_asset(roots[symbol], symbol)
        m = next(m for m in markets if m['ticker']==f'KX{symbol.removesuffix("USDT")}PERP')
        specs = dict(multiplier=m['contract_size'], price_tick=str(float(m['tick_size'])/float(m['contract_size'])))
        assets[symbol] = prepare(frames[symbol], funding[symbol]), specs
    (out/'data-audit.json').write_text(json.dumps(audit, indent=2))
    signal_dir = out/'signals'
    if args.reuse_training:
        shutil.copytree(args.reuse_training/'signals', signal_dir)
    else:
        signal_dir.mkdir()

    def signal(symbol, minutes, window, ei, write=False):
        path = signal_dir/f'{symbol}-{minutes}m-{window}h-{ei}.npz'
        if path.exists():
            state = np.load(path)['state']
            return np.where(state<0, np.nan, state*.98)
        tp, hold = EXITS[ei]
        w = short_weights(frames[symbol], minutes, window, tp, hold)
        if write:
            np.savez_compressed(path, state=np.where(np.isnan(w), -1, w>0).astype(np.int8))
        return w

    def replay(symbol, w, fraction, leverage, dates, cost=8, full=False):
        data, specs = assets[symbol]
        return replay_events(data, w, dates, capital=10000*fraction, leverage=leverage,
            symbol=symbol, specs=specs, cost=cost, full_path=full, capture=full)

    if args.reuse_training:
        rows = audit_training_reuse(args.reuse_training, out, assets, protocol)
    else:
        bank = {}
        done = 0
        for symbol, minutes, window, ei in itertools.product(frames, INTERVALS, WINDOWS, range(len(EXITS))):
            started = time.monotonic()
            w = signal(symbol, minutes, window, ei, write=True)
            for allocation, leverage in itertools.product(ALLOCATIONS, LEVERAGES):
                stats, path, _ = replay(symbol, w, allocation, leverage, TRAIN)
                stats.pop('closed_episodes')
                bank[symbol, minutes, window, ei, allocation, leverage] = stats, path
                done += 1
            print(f'training sleeves {done}/1296 {symbol} {minutes}m {window}h exit{ei} elapsed={time.monotonic()-started:.1f}s', flush=True)
        rows = []
        for minutes, window, allocation, leverage, de, se in itertools.product(INTERVALS, WINDOWS, ALLOCATIONS, LEVERAGES, range(6), range(6)):
            d = bank['DOGEUSDT', minutes, window, de, allocation, leverage]
            s = bank['SOLUSDT', minutes, window, se, 1-allocation, leverage]
            cfg = dict(minutes=minutes, entry_hours=window, doge_allocation=allocation, sol_allocation=1-allocation,
                leverage=leverage, doge_exit_id=de, sol_exit_id=se,
                doge_tp_atr=EXITS[de][0], doge_max_hold_hours=EXITS[de][1],
                sol_tp_atr=EXITS[se][0], sol_max_hold_hours=EXITS[se][1])
            rows.append(dict(id=len(rows), config=cfg, **training_stats(d, s)))
    (out/'training.json').write_text(json.dumps(rows, indent=2))
    selection = choose_training(rows)
    if args.reuse_training:
        prior_selection = json.loads((args.reuse_training/'selection.json').read_text())
        if selection!=prior_selection:
            raise AssertionError('training selection changed')
        parity = []
        for label, chosen in selection['selected'].items():
            cfg = chosen['config']
            sleeves = []
            for symbol, fraction, ei in [('DOGEUSDT', cfg['doge_allocation'], cfg['doge_exit_id']), ('SOLUSDT', cfg['sol_allocation'], cfg['sol_exit_id'])]:
                w = signal(symbol, cfg['minutes'], cfg['entry_hours'], ei)
                sleeves.append(replay(symbol, w, fraction, cfg['leverage'], TRAIN))
            actual = training_stats(*sleeves)
            for key, value in actual.items():
                if not np.isclose(value, chosen[key], atol=1e-6, rtol=0):
                    raise AssertionError('corrected training mismatch: '+label+'/'+key)
            parity.append(dict(strategy=label, verified_metrics=list(actual)))
        (out/'selected-training-parity.json').write_text(json.dumps(parity, indent=2))
    (out/'selection.json').write_text(json.dumps(selection, indent=2))
    print('LOCKED SELECTION', json.dumps(selection), flush=True)
    if not args.reuse_training:
        del bank
    results = []
    replays = {}
    for label, chosen in selection['selected'].items():
        cfg = chosen['config']
        for period, dates in PERIODS.items():
            costs = (8, 15, 30) if label in (selection['global_training_pnl'], selection['global_training_frequent']) else (8,)
            for cost in costs:
                key = chosen['id'], period, cost
                if key in replays:
                    results.append(dict(replays[key], strategy=label))
                    continue
                sleeves = []
                for symbol, fraction, ei in [('DOGEUSDT', cfg['doge_allocation'], cfg['doge_exit_id']), ('SOLUSDT', cfg['sol_allocation'], cfg['sol_exit_id'])]:
                    w = signal(symbol, cfg['minutes'], cfg['entry_hours'], ei)
                    sleeves.append(replay(symbol, w, fraction, cfg['leverage'], dates, cost, full=True))
                end_ms = int((pd.Timestamp(dates[1], tz='UTC')+pd.Timedelta(days=1)).timestamp()*1000)-1
                stats, path = combined_metrics(sleeves, end_ms=end_ms)
                row = dict(strategy=label, period=period, cost_bps=cost, config=cfg, **stats)
                results.append(row)
                replays[key] = row
                if cost==8:
                    path.resample('1h').last().to_csv(out/f'{label}-{period}.csv', index_label='timestamp')
                    (out/f'{label}-{period}-episodes.json').write_text(json.dumps([e for s in sleeves for e in s[0]['closed_episodes']], indent=2))
                    (out/f'{label}-{period}-ledgers.json').write_text(json.dumps([s[2] for s in sleeves], indent=2))
        print('evaluated', label, flush=True)
    # Frozen controls: no re-estimation on the new training or test sample.
    for leverage in (1., 1.5):
        label = f'previous-4h-{leverage}x'
        weights = {}
        for symbol, tp in [('DOGEUSDT', .5), ('SOLUSDT', 2.)]:
            f = frames[symbol]
            h = f.resample('1h').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'tradable':'max'})
            w = profit_weights(h, tp, 72)[:, 0]
            weights[symbol] = pd.Series(w, index=h.index).reindex(f.index, method='ffill').to_numpy()
        for period, dates in PERIODS.items():
            sleeves = [replay(s, weights[s], allocation, leverage, dates, full=True) for s, allocation in [('DOGEUSDT', .25), ('SOLUSDT', .75)]]
            end_ms = int((pd.Timestamp(dates[1], tz='UTC')+pd.Timedelta(days=1)).timestamp()*1000)-1
            stats, path = combined_metrics(sleeves, end_ms=end_ms)
            results.append(dict(strategy=label, period=period, cost_bps=8, **stats))
            path.resample('1h').last().to_csv(out/f'{label}-{period}.csv', index_label='timestamp')
        print('evaluated', label, flush=True)
    (out/'summary.json').write_text(json.dumps(dict(selection=selection, results=results), indent=2))
    print('COMPLETE', json.dumps([r for r in results if r['period']=='test' and r['cost_bps']==8]), flush=True)


if __name__=='__main__':
    main()
