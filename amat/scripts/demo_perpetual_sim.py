"""Save deterministic long/short multiplier examples; no external orders."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
from autoresearch.perpetual_sim import ContractSpec, PerpOrder, PerpetualSimulator, KalshiStylePaperAPI


def main():
    p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
    results=[]
    for multiplier in ('0.001','0.002'):
        for side in ('bid','ask'):
            spec=ContractSpec('SIM-BTC-PERP',multiplier=multiplier,max_leverage=2,
                              fee_rate='.001',slippage_rate='.0005')
            sim=PerpetualSimulator(spec,'10000'); api=KalshiStylePaperAPI(sim)
            # Limits are marketable, execution uses supplied quote plus slippage.
            opening=PerpOrder('open','SIM-BTC-PERP',side,'100','61000' if side=='bid' else '59000')
            api.create_order(opening.kalshi_payload(),bid='60000',ask='60000',mark='60000',timestamp_ms=0)
            sim.mark_to_market('55000',1)
            sim.apply_funding(event_id='example-8h',rate='.0001',mark='55000',timestamp_ms=2)
            closing=PerpOrder('close','SIM-BTC-PERP','ask' if side=='bid' else 'bid',
                              '100','54000' if side=='bid' else '56000',reduce_only=True)
            api.create_order(closing.kalshi_payload(),bid='55000',ask='55000',mark='55000',timestamp_ms=3)
            expected=sim.initial_collateral+sim.realized_pnl+sim.funding_pnl-sim.fees
            if sim.contracts != 0 or sim.cash != expected:
                raise AssertionError('accounting identity failed')
            results.append(dict(direction='long' if side=='bid' else 'short',multiplier=multiplier,
                entry_quote='60000',exit_quote='55000',funding='one illustrative +0.01% event',
                state=sim.position(),ledger=sim.ledger))
    (args.output/'demo.json').write_text(json.dumps(results,indent=2))
    lines=['# Simulated perpetual multiplier demonstration','',
           '100 contracts; entry quote 60,000, exit quote 55,000; 10bps fee and 5bps slippage each side; one illustrative +0.01% funding event; initial collateral 10,000. These are synthetic scenarios, not historical perpetual returns.','',
           '| Direction | Multiplier | Gross realized P&L after slippage | Funding P&L | Fees | Final equity |',
           '|---|---:|---:|---:|---:|---:|']
    for r in results:
        s=r['state']; lines.append(f"| {r['direction']} | {r['multiplier']} | {s['realized_pnl']} | {s['funding_pnl']} | {s['fees']} | {s['equity']} |")
    lines+=['','Increasing leverage alone does not multiply P&L for a fixed position. Increasing multiplier or contract count increases exposure and P&L, and increases margin requirements.','',
            'Contract multipliers and all cost/margin parameters here are illustrative, not Kalshi market specifications.']
    (args.output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps([{k:r[k] for k in ('direction','multiplier','state')} for r in results],indent=2))


if __name__=='__main__': main()
