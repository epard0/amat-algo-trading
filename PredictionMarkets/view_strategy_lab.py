#!/usr/bin/env python3
"""Print the independent strategy-lab wallet leaderboard."""

from __future__ import annotations

import argparse
import sqlite3
from decimal import Decimal
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", nargs="?", type=Path, default=Path("strategy_lab.db"))
    args = parser.parse_args()
    if not args.database.exists():
        parser.error(f"strategy lab database not found: {args.database}")
    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT * FROM lab_wallets ORDER BY CAST(realized_pnl AS REAL) DESC"
    ).fetchall()
    print(f"{'strategy':28} {'cash':>9} {'pnl':>9} {'trades':>7} {'W-L':>9} {'win%':>7} {'open':>5}")
    for row in rows:
        open_count = connection.execute(
            "SELECT COUNT(*) FROM lab_positions WHERE strategy=? AND status='OPEN'",
            (row["strategy"],),
        ).fetchone()[0]
        decided = row["wins"] + row["losses"]
        win_rate = (100 * row["wins"] / decided) if decided else 0
        print(
            f"{row['strategy']:28} ${Decimal(row['cash_balance']):>8.2f} "
            f"${Decimal(row['realized_pnl']):>8.2f} {row['trades']:>7} "
            f"{row['wins']}-{row['losses']:>6} {win_rate:>6.1f}% {open_count:>5}"
        )
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
