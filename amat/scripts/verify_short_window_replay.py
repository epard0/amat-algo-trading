"""Actual training-data parity with canonical minute wrapper at stressed costs."""
from pathlib import Path
import argparse
import json
import numpy as np

from autoresearch.crypto_fast_breakout import prepare, replay_events
from autoresearch.crypto_short_window_study import load_minutes
from autoresearch.crypto_intraday import run_intraday
from autoresearch.crypto_universe_study import load_asset


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output
    frames, _ = load_minutes(Path('data/research/doge-sol-minute-2024-2026-09'))
    markets = json.loads(Path('outputs/crypto-directional-2026-10-19/kalshi-inventory/markets.json').read_text())['markets']
    rows = []
    for symbol, folder in [('DOGEUSDT', 'kalshi-universe-proxies-2021-2026-09'), ('SOLUSDT', 'kalshi-universe-gap-supplement-2021-2026-09')]:
        _, funding, _ = load_asset(Path('data/research')/folder, symbol)
        market = next(m for m in markets if m['ticker']==f'KX{symbol.removesuffix("USDT")}PERP')
        specs = dict(multiplier=market['contract_size'], price_tick=str(float(market['tick_size'])/float(market['contract_size'])))
        # Deterministic frequent position changes independent of future returns.
        frame = frames[symbol]
        w = np.where(np.arange(len(frame))%137<61, .98, 0.)
        dates = ('2024-10-27', '2024-10-30')  # Includes actual zero-volume outage.
        fast, path, _ = replay_events(prepare(frame, funding[symbol]), w, dates,
            capital=2500, leverage=2, symbol=symbol, specs=specs, cost=30, full_path=True)
        slow, reference, _, _ = run_intraday({symbol:frame}, {symbol:funding[symbol]}, w[:,None]*2, dates,
            capital=2500, leverage_limit=2, contract_specs={symbol:specs}, cost=30)
        error = float(np.max(np.abs(path.equity-reference.equity)))
        if error>1e-6:
            raise AssertionError('real-data minute path mismatch')
        for metric in ('pnl', 'fees', 'funding_pnl', 'liquidations', 'margin_rejections'):
            if not np.isclose(fast[metric], slow[metric], atol=1e-6):
                raise AssertionError(metric+' mismatch')
        rows.append(dict(symbol=symbol, dates=dates, cost_bps_per_side=30, leverage=2,
            maximum_equity_path_error=error, canonical_pnl=slow['pnl'], matched_metrics=['pnl','fees','funding_pnl','liquidations','margin_rejections']))
    (root/'real-data-parity.json').write_text(json.dumps(rows, indent=2))
    print(json.dumps(rows, indent=2))


if __name__=='__main__':
    main()
