# Fixed audit year and numeric reconciliation tolerances.
# ruff: noqa: PLR2004
import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.data.snapshot import load_corporate_actions, load_panel
from amat.domain.actions import ActionKind
from amat.domain.types import Side
from autoresearch.improvements.study import digest, write

parser = argparse.ArgumentParser(description="Native 2023 per-asset accounting attribution at 6bps")
parser.add_argument(
    "--config", type=Path, default=Path("amat/configs/strategy/amat_group_selection_daily.toml")
)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--reference-record", type=Path, required=True)
args = parser.parse_args()
root = Path("amat")
loaded = load_strategy_config(args.config)
cfg = loaded.strategy
snapshot = root / "data/snapshots/alpaca-sip-core-2026-10-13-verified"
symbols = list(map(str, cfg.universe.symbols))
panel, _ = load_panel(snapshot, symbols, with_context=True)
actions = load_corporate_actions(snapshot, symbols, sessions=panel.sessions)
result = run_backtest(
    cfg,
    panel,
    cfg.to_universe(),
    execution_start=dt.date(2017, 1, 3),
    one_way_bps=6,
    actions_by_session=actions,
    max_entry_window_participation=0.01,
)
positions = np.zeros(len(symbols))
dates = {d: i for i, d in enumerate(panel.sessions)}
previous = 100000.0
points = []
for record in result.sessions:
    t = dates[record.session]
    p = positions.copy()
    marks = panel.raw_closes[t - 1].copy()
    distributions = np.zeros(len(p))
    fees = np.zeros(len(p))
    traded = np.zeros(len(p))
    for a in actions.get(record.session, ()):
        j = symbols.index(str(a.symbol))
        if a.kind == ActionKind.SPLIT:
            p[j] *= float(a.ratio)
            marks[j] /= float(a.ratio)
        else:
            distributions[j] += p[j] * float(a.cash_per_share)
    pnl = p * (panel.raw_closes[t] - marks) + distributions
    for f in record.fills:
        j = symbols.index(str(f.symbol))
        signed = float(f.quantity) * (1 if f.side == Side.BUY else -1)
        pnl[j] += signed * (panel.raw_closes[t, j] - float(f.price)) - float(f.cost_cash)
        fees[j] += float(f.cost_cash)
        traded[j] += abs(signed) * float(f.price)
        p[j] += signed
    equity = float(record.equity)
    if record.session.year == 2023:
        if abs(float(pnl.sum()) - (equity - previous)) >= 1e-4:
            raise ValueError("Asset P&L does not reconcile")
        points.append(
            {
                "date": str(record.session),
                "opening_equity": previous,
                "equity": equity,
                "asset_pnl": pnl.tolist(),
                "fees": fees.tolist(),
                "exposure": (p * panel.raw_closes[t] / equity).tolist(),
                "traded": traded.tolist(),
            }
        )
    previous = equity
    positions = p
opening = points[0]["opening_equity"]
ending = points[-1]["equity"]
benchmark = {}
for s in ["SPY", "QQQ", "IWM", "GLD", "IEF", "USO", "UUP"]:
    j = symbols.index(s)
    t = dates[dt.date(2023, 1, 3)]
    end = dates[dt.date(2023, 12, 29)]
    benchmark[s] = float(panel.adjusted_closes[end, j] / panel.adjusted_closes[t - 1, j] - 1)
value = {
    "schema": "native-2023-attribution/1",
    "cost_bps": 6,
    "opening_equity": opening,
    "ending_equity": ending,
    "net_return": ending / opening - 1,
    "symbols": symbols,
    "assets": [
        {
            "symbol": s,
            "pnl": sum(r["asset_pnl"][j] for r in points),
            "contribution": sum(r["asset_pnl"][j] for r in points) / opening,
            "costs": sum(r["fees"][j] for r in points),
            "average_weight": float(np.mean([r["exposure"][j] for r in points])),
            "turnover": sum(r["traded"][j] for r in points) / opening,
        }
        for j, s in enumerate(symbols)
    ],
    "benchmarks": benchmark,
    "points": points,
}
reference = json.loads(args.reference_record.read_text())
if digest(reference) != args.reference_record.with_suffix(".sha256").read_text().strip():
    raise ValueError("Reference digest mismatch")
if abs(value["net_return"] - reference["metrics"]["2023"]["net_return"]) >= 1e-10:
    raise ValueError("Annual reference mismatch")
value["baseline_sessions_sha256"] = digest(reference["sessions"])
value["candidate_sessions_sha256"] = digest(reference["sessions"])
value["source_record"] = str(args.reference_record.resolve())
value["strategy_sha256"] = loaded.content_sha256
out = args.output
out.mkdir(parents=True, exist_ok=False)
write(out / "attribution.json", value)
(out / "attribution.sha256").write_text(digest(value))
print(json.dumps({k: v for k, v in value.items() if k != "points"}, indent=2))
