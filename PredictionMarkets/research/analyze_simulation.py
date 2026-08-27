"""Analyze one strict simulation ledger without inventing fills.

The opportunity scan is deliberately executable: a hypothetical entry pays the
recorded ask, a hypothetical exit receives a later recorded bid, both legs pay
the configured quadratic taker fee, and both top-of-book sizes must cover the
fixed dollar stake.  Results are descriptive candidates from one observed run,
not an out-of-sample profitability claim.

    uv run python research/analyze_simulation.py data/simulations/RUN/trade_ledger.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from pathlib import Path
from statistics import median
from typing import Any


D = Decimal
CENTICENT = D("0.0001")
ONE = D("1")
CONTRACT_STEP = D("0.01")


def fee_per_contract(price: Decimal, multiplier: Decimal) -> Decimal:
    raw = D("0.07") * multiplier * price * (ONE - price)
    return raw.quantize(CENTICENT, rounding=ROUND_CEILING)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


@dataclass(frozen=True)
class Opportunity:
    ticker: str
    outcome: str
    entered_at: str
    exited_at: str
    seconds_to_close_at_entry: float
    entry_ask: float
    exit_bid: float
    net_gain_per_contract: float
    contracts_at_fixed_budget: float
    modeled_net_profit: float


def load_rows(connection: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT * FROM market_snapshots
        WHERE run_id = ?
        ORDER BY ticker, observed_at
        """,
        (run_id,),
    ).fetchall()


def outcome_prices(row: sqlite3.Row, outcome: str) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    yes_bid = D(row["yes_bid"])
    yes_ask = D(row["yes_ask"])
    if outcome == "yes":
        return yes_bid, yes_ask, D(row["yes_bid_size"]), D(row["yes_ask_size"])
    return ONE - yes_ask, ONE - yes_bid, D(row["yes_ask_size"]), D(row["yes_bid_size"])


def best_opportunity(
    ticker_rows: list[sqlite3.Row],
    outcome: str,
    fixed_budget: Decimal,
    minimum_gain: Decimal,
    maximum_spread: Decimal,
) -> Opportunity | None:
    best: Opportunity | None = None
    for entry_index, entry in enumerate(ticker_rows[:-1]):
        _, ask, _, ask_size = outcome_prices(entry, outcome)
        if not D("0") < ask < ONE:
            continue
        multiplier = D(entry["fee_multiplier"])
        entry_fee = fee_per_contract(ask, multiplier)
        contracts = (fixed_budget / (ask + entry_fee)).quantize(
            CONTRACT_STEP, rounding=ROUND_DOWN
        )
        if contracts <= 0:
            continue
        if ask_size < contracts:
            continue
        for exit_row in ticker_rows[entry_index + 1 :]:
            bid, later_ask, bid_size, _ = outcome_prices(exit_row, outcome)
            if later_ask - bid > maximum_spread or bid_size < contracts:
                continue
            exit_fee = fee_per_contract(bid, D(exit_row["fee_multiplier"]))
            gain = bid - ask - entry_fee - exit_fee
            if gain < minimum_gain:
                continue
            candidate = Opportunity(
                ticker=entry["ticker"],
                outcome=outcome,
                entered_at=entry["observed_at"],
                exited_at=exit_row["observed_at"],
                seconds_to_close_at_entry=float(entry["seconds_to_close"]),
                entry_ask=float(ask),
                exit_bid=float(bid),
                net_gain_per_contract=float(gain),
                contracts_at_fixed_budget=float(contracts),
                modeled_net_profit=float(gain * contracts),
            )
            if best is None or candidate.modeled_net_profit > best.modeled_net_profit:
                best = candidate
    return best


def analyze(
    path: Path,
    *,
    run_id: str | None,
    fixed_budget: Decimal,
    minimum_gain: Decimal,
    maximum_spread: Decimal,
) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        run = connection.execute(
            "SELECT * FROM data_runs WHERE run_id = ?" if run_id else
            "SELECT * FROM data_runs ORDER BY started_at DESC LIMIT 1",
            (run_id,) if run_id else (),
        ).fetchone()
        if run is None:
            raise ValueError("no matching data run")
        rows = load_rows(connection, run["run_id"])
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            grouped[row["ticker"]].append(row)

        spreads = [float(D(row["spread"])) for row in rows]
        implied = [float(D(row["implied_probability_yes"])) for row in rows]
        cadence = []
        switches = 0
        for ticker_rows in grouped.values():
            times = [datetime.fromisoformat(row["observed_at"]) for row in ticker_rows]
            cadence.extend(
                (later - earlier).total_seconds()
                for earlier, later in zip(times, times[1:])
                if later > earlier
            )
            favored = [D(row["implied_probability_yes"]) >= D("0.5") for row in ticker_rows]
            switches += sum(left != right for left, right in zip(favored, favored[1:]))

        opportunities = []
        for ticker_rows in grouped.values():
            for outcome in ("yes", "no"):
                found = best_opportunity(
                    ticker_rows, outcome, fixed_budget, minimum_gain, maximum_spread
                )
                if found is not None:
                    opportunities.append(found)
        opportunities.sort(key=lambda item: item.modeled_net_profit, reverse=True)

        trades = connection.execute(
            """
            SELECT strategy, outcome, status, exchange_status, contracts,
                   total_debit, pnl, entry_sequence
            FROM trades
            WHERE placed_at >= ? AND (? IS NULL OR placed_at <= ?)
            ORDER BY placed_at
            """,
            (run["started_at"], run["ended_at"], run["ended_at"]),
        ).fetchall()
        realized = [D(row["pnl"]) for row in trades if row["pnl"] is not None]
        portfolio = connection.execute(
            """
            SELECT COUNT(*) AS points, MIN(CAST(current_balance AS REAL)) AS low,
                   MAX(CAST(current_balance AS REAL)) AS high,
                   MIN(observed_at) AS first, MAX(observed_at) AS last
            FROM portfolio_snapshots WHERE run_id = ?
            """,
            (run["run_id"],),
        ).fetchone()
        depth_rows = connection.execute(
            """
            SELECT COUNT(*) FROM orderbook_levels
            WHERE snapshot_id IN (
                SELECT snapshot_id FROM market_snapshots WHERE run_id = ?
            )
            """,
            (run["run_id"],),
        ).fetchone()[0]
        has_pair_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='pair_arb_observations'"
        ).fetchone()
        pair = (
            connection.execute(
                """
                SELECT COUNT(*) AS scans, SUM(approved) AS approved,
                       SUM(simulated) AS simulated,
                       COALESCE(SUM(CAST(modeled_profit AS REAL)), 0) AS pnl,
                       SUM(CASE WHEN gate = 'book' THEN 1 ELSE 0 END)
                           AS incomplete_books,
                       SUM(CASE WHEN gate != 'book'
                           AND CAST(pair_cost AS REAL) < 1 THEN 1 ELSE 0 END)
                           AS crossed_books,
                       SUM(CASE WHEN gate != 'book'
                           AND CAST(net_edge AS REAL) > 0 THEN 1 ELSE 0 END)
                           AS positive_after_fees,
                       MIN(CASE WHEN gate != 'book'
                           THEN CAST(pair_cost AS REAL) END) AS minimum_pair_cost,
                       MAX(CASE WHEN gate != 'book'
                           THEN CAST(net_edge AS REAL) END) AS maximum_net_edge,
                       MIN(CASE WHEN gate NOT IN ('book', 'stale_leg')
                           THEN CAST(pair_cost AS REAL) END)
                           AS minimum_guarded_pair_cost,
                       MAX(CASE WHEN gate NOT IN ('book', 'stale_leg')
                           THEN CAST(net_edge AS REAL) END)
                           AS maximum_guarded_net_edge
                FROM pair_arb_observations WHERE run_id = ?
                """,
                (run["run_id"],),
            ).fetchone()
            if has_pair_table
            else None
        )
        pair_gates = (
            {
                row["gate"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT gate, COUNT(*) AS count
                    FROM pair_arb_observations WHERE run_id = ?
                    GROUP BY gate ORDER BY gate
                    """,
                    (run["run_id"],),
                )
            }
            if has_pair_table
            else {}
        )
        return {
            "run": dict(run),
            "integrity": integrity,
            "collection": {
                "market_snapshots": len(rows),
                "depth_rows": depth_rows,
                "portfolio_snapshots": portfolio["points"],
                "unique_contracts": len(grouped),
                "first_observation": portfolio["first"],
                "last_observation": portfolio["last"],
                "median_cadence_seconds": median(cadence) if cadence else 0,
            },
            "market": {
                "median_spread": median(spreads) if spreads else 0,
                "p90_spread": percentile(spreads, 0.90),
                "minimum_implied_yes": min(implied) if implied else None,
                "maximum_implied_yes": max(implied) if implied else None,
                "favored_side_switches": switches,
            },
            "opportunity_assumptions": {
                "fixed_budget_usd": float(fixed_budget),
                "minimum_net_gain_per_contract": float(minimum_gain),
                "maximum_exit_spread": float(maximum_spread),
                "top_of_book_depth_required": True,
                "both_taker_fees_included": True,
            },
            # These deliberately select the best later quote and are therefore
            # hindsight diagnostics, not causal trade signals or assumed fills.
            "best_hindsight_opportunities": [asdict(item) for item in opportunities],
            "trades": {
                "count": len(trades),
                "settled_or_exited": len(realized),
                "wins": sum(value > 0 for value in realized),
                "losses": sum(value < 0 for value in realized),
                "net_pnl": float(sum(realized, D("0"))),
                "take_profit_exits": sum(row["exchange_status"] == "take_profit" for row in trades),
                "second_legs": sum(row["entry_sequence"] == 2 for row in trades),
            },
            "portfolio": {
                "minimum_balance": portfolio["low"],
                "maximum_balance": portfolio["high"],
            },
            "pair_arb_shadow": {
                "scans": pair["scans"] if pair else 0,
                "approved_snapshots": (pair["approved"] or 0) if pair else 0,
                "simulated_distinct_episodes": (pair["simulated"] or 0) if pair else 0,
                "modeled_pnl": pair["pnl"] if pair else 0,
                "incomplete_books": (pair["incomplete_books"] or 0) if pair else 0,
                "crossed_books": (pair["crossed_books"] or 0) if pair else 0,
                "positive_after_fees": (pair["positive_after_fees"] or 0) if pair else 0,
                "gate_counts": pair_gates,
                "minimum_pair_cost": pair["minimum_pair_cost"] if pair else None,
                "maximum_net_edge": pair["maximum_net_edge"] if pair else None,
                "minimum_guarded_pair_cost": (
                    pair["minimum_guarded_pair_cost"] if pair else None
                ),
                "maximum_guarded_net_edge": (
                    pair["maximum_guarded_net_edge"] if pair else None
                ),
                "live_executable": False,
            },
        }
    finally:
        connection.close()


def render(report: dict[str, Any]) -> str:
    run = report["run"]
    collection = report["collection"]
    market = report["market"]
    trades = report["trades"]
    pair = report["pair_arb_shadow"]
    complete_cost = (
        "n/a"
        if pair["minimum_pair_cost"] is None
        else f"{pair['minimum_pair_cost']:.4f}"
    )
    guarded_edge = (
        "n/a"
        if pair["maximum_guarded_net_edge"] is None
        else f"{pair['maximum_guarded_net_edge']:+.4f}"
    )
    lines = [
        f"run {run['run_id']} | {run['status']} | {run['strategy']} {run['assets_json']}",
        f"integrity={report['integrity']} | {collection['market_snapshots']} market snapshots | "
        f"{collection['depth_rows']} depth rows | {collection['unique_contracts']} contracts",
        f"cadence median={collection['median_cadence_seconds']:.2f}s | "
        f"spread median={market['median_spread']:.3f}, p90={market['p90_spread']:.3f}",
        f"implied YES range={market['minimum_implied_yes']}..{market['maximum_implied_yes']} | "
        f"favored-side switches={market['favored_side_switches']}",
        f"trades={trades['count']} | settled/exited={trades['settled_or_exited']} | "
        f"W/L={trades['wins']}/{trades['losses']} | net P&L={trades['net_pnl']:+.2f} | "
        f"take-profits={trades['take_profit_exits']} | second legs={trades['second_legs']}",
        f"pair-arb shadow scans={pair['scans']} | approved={pair['approved_snapshots']} | "
        f"distinct simulated episodes={pair['simulated_distinct_episodes']} | "
        f"incomplete books={pair['incomplete_books']} | "
        f"modeled P&L={pair['modeled_pnl']:+.4f} (never live-executable)",
        f"pair books crossed={pair['crossed_books']} | positive after fees="
        f"{pair['positive_after_fees']} | gates={pair['gate_counts']}",
        f"pair extrema: minimum complete cost={complete_cost} | "
        f"best non-stale net edge={guarded_edge}",
        "best hindsight take-profit paths (fee/depth adjusted; not trade signals):",
    ]
    opportunities = report["best_hindsight_opportunities"]
    if not opportunities:
        lines.append("  none cleared every assumption in this sample")
    for item in opportunities[:10]:
        lines.append(
            "  {ticker} {outcome}: ask {entry_ask:.2f} -> bid {exit_bid:.2f}, "
            "net {net_gain_per_contract:.4f}/contract, modeled ${modeled_net_profit:.2f}".format(**item)
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--fixed-budget", type=Decimal, default=D("10"))
    parser.add_argument("--minimum-gain", type=Decimal, default=D("0.05"))
    parser.add_argument("--maximum-spread", type=Decimal, default=D("0.03"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = analyze(
        args.ledger,
        run_id=args.run_id,
        fixed_budget=args.fixed_budget,
        minimum_gain=args.minimum_gain,
        maximum_spread=args.maximum_spread,
    )
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
