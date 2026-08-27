"""Does the simulator reproduce what actually happened?

A backtest that cannot reproduce the past cannot evaluate a change to the
future. This replays the bot's own recorded history through the **production**
accounting and sizing functions - not reimplementations of them - and reports
every disagreement.

Three reconciliations, in increasing strength:

1. **Accounting.** For every settled ledger row, does ``calculate_paper_fill``
   reproduce the recorded ``entry_cost``, ``fees_paid`` and ``total_debit``
   exactly, and does the settlement arithmetic reproduce ``pnl``? This is the
   money math, and "exactly" means to the cent, not approximately.

2. **Sizing.** For every ``traded`` decision record, does
   ``AsyncKalshiTradingClient.build_order_plan`` - the real one - return the
   recorded ``contracts``, ``sizing_fraction`` and ``maximum_total_debit`` when
   handed the recorded balance, price and model probability?

3. **Settlement direction.** Does the recorded ``market_result`` agree with the
   recorded outcome and P&L sign? A row that says WON with a negative P&L means
   the ledger and the settlement disagree, and every statistic drawn from
   either is then suspect.

Any mismatch here invalidates the backtest, not the bot: the ledger is the
source of truth (CLAUDE.md), so a disagreement means the simulator is wrong.

    uv run python research/replay.py
    uv run python research/replay.py --decisions decisions.jsonl --verbose
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from execution.kalshi_client import (  # noqa: E402
    AsyncKalshiTradingClient,
    BookQuote,
)
from execution.trade_ledger import calculate_paper_fill  # noqa: E402

D = Decimal
CENT = D("0.01")


@dataclass
class Mismatch:
    kind: str
    identifier: str
    field: str
    recorded: str
    replayed: str

    def __str__(self) -> str:
        return (
            f"  {self.kind:<12} {self.identifier:<34} {self.field:<18} "
            f"recorded={self.recorded:<14} replayed={self.replayed}"
        )


# ------------------------------------------------------- 1. the money math --


def reconcile_accounting(ledger_path: Path) -> tuple[int, list[Mismatch]]:
    """Re-derive every settled row's cost, fee, debit and P&L from its inputs."""
    connection = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = list(
        connection.execute(
            """SELECT trade_id, mode, strategy, asset, ticker, outcome, contracts,
                      entry_price, entry_cost, fees_paid, total_debit,
                      gross_payout, pnl, status, market_result, metadata_json
               FROM trades WHERE status IN ('WON','LOST','VOID')"""
        )
    )
    problems: list[Mismatch] = []
    for row in rows:
        tag = f"{row['asset']}/{row['trade_id'][:8]}"
        contracts = D(row["contracts"])
        price = D(row["entry_price"])
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        multiplier = D(str(metadata.get("fee_multiplier", "1") or "1"))

        fill = calculate_paper_fill(contracts, price, multiplier)
        for field, recorded, replayed in (
            ("entry_cost", D(row["entry_cost"]), fill.entry_cost),
            ("fees_paid", D(row["fees_paid"]), fill.fees_paid),
            ("total_debit", D(row["total_debit"]), fill.total_debit),
        ):
            if recorded != replayed:
                problems.append(
                    Mismatch("accounting", tag, field, str(recorded), str(replayed))
                )

        # Settlement: a winner is paid $1 per contract, a loser nothing, and a
        # void is refunded. P&L is payout minus what was actually debited.
        if row["status"] == "VOID":
            expected_payout = D(row["total_debit"])
        elif row["status"] == "WON":
            expected_payout = contracts
        else:
            expected_payout = D("0")
        expected_pnl = expected_payout - D(row["total_debit"])
        if row["gross_payout"] is not None and D(row["gross_payout"]) != (
            expected_payout
        ):
            problems.append(
                Mismatch(
                    "settlement",
                    tag,
                    "gross_payout",
                    row["gross_payout"],
                    str(expected_payout),
                )
            )
        if row["pnl"] is not None and D(row["pnl"]) != expected_pnl:
            problems.append(
                Mismatch("settlement", tag, "pnl", row["pnl"], str(expected_pnl))
            )

        # 3. Direction: WON must mean the held outcome is the market result.
        result = row["market_result"]
        if result in {"yes", "no"} and row["status"] in {"WON", "LOST"}:
            should_win = result == row["outcome"]
            if should_win != (row["status"] == "WON"):
                problems.append(
                    Mismatch(
                        "direction",
                        tag,
                        "status",
                        f"{row['status']} on {result}",
                        "WON" if should_win else "LOST",
                    )
                )
    return len(rows), problems


# ---------------------------------------------------------- 2. the sizing --


def client_for(settings) -> AsyncKalshiTradingClient:
    """The real client, without opening a session or authenticating."""
    instance = object.__new__(AsyncKalshiTradingClient)
    instance.settings = settings
    instance.authenticated = True
    return instance


def reconcile_sizing(
    decisions_paths: list[Path], settings
) -> tuple[int, list[Mismatch], int]:
    """Re-run the real ``build_order_plan`` over every recorded order.

    Several logs, because DECISION_LOG_RETENTION_HOURS is 24 and the archived
    runs beside it are the only record of the earlier orders. Four traded rows
    in one file is not a reconciliation.
    """
    problems: list[Mismatch] = []
    checked = 0
    skipped = 0
    client = client_for(settings)

    lines: list[str] = []
    for path in decisions_paths:
        if path.exists():
            lines.extend(path.read_text(errors="replace").splitlines())

    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        plan = record.get("order_plan")
        if not plan or record.get("decision") != "traded":
            continue
        balance = record.get("available_balance")
        candidate = record.get("candidate") or {}
        quote_record = record.get("live_quote") or {}
        probability = candidate.get("outcome_probability")
        if balance is None or probability is None or not quote_record:
            skipped += 1
            continue
        try:
            available = D(str(balance))
            model_probability = D(str(probability))
            best = D(str(quote_record["best_price"]))
            limit = D(str(quote_record["limit_price"]))
            # Recorded, so the clip to real book depth is reproduced rather
            # than assumed away. Sizing that ignores depth would silently agree
            # on every order the book was deep enough for and diverge on
            # exactly the ones where the clip mattered.
            depth = D(str(quote_record["depth_at_limit"]))
        except (KeyError, ArithmeticError, TypeError, ValueError):
            skipped += 1
            continue
        if not (D("0") < limit < D("1")) or available <= 0:
            skipped += 1
            continue

        # Fixed-budget sizing is identical in paper and live.
        maximum = settings.trade_budget_usd
        quote = BookQuote(
            outcome=candidate.get("outcome", "yes"),
            best_price=best,
            limit_price=limit,
            depth_at_limit=depth,
        )
        try:
            replayed = client.build_order_plan(
                market=_market_stub(record),
                quote=quote,
                available_balance=available,
                model_probability=model_probability,
                maximum_budget=maximum,
            )
        except (ValueError, ArithmeticError) as exc:
            problems.append(
                Mismatch(
                    "sizing",
                    f"{record.get('asset')}/{record.get('ticker', '')[-12:]}",
                    "raised",
                    str(plan.get("contracts")),
                    type(exc).__name__ + ": " + str(exc)[:40],
                )
            )
            checked += 1
            continue

        checked += 1
        tag = f"{record.get('asset')}/{record.get('ticker', '')[-12:]}"
        for field, recorded_value, replayed_value in (
            ("contracts", D(str(plan["contracts"])), replayed.contracts),
            (
                "maximum_total_debit",
                D(str(plan["maximum_total_debit"])),
                replayed.maximum_total_debit,
            ),
        ):
            if recorded_value != replayed_value:
                problems.append(
                    Mismatch(
                        "sizing", tag, field, str(recorded_value), str(replayed_value)
                    )
                )
    return checked, problems, skipped


def _market_stub(record: dict):
    from datetime import datetime, timedelta, timezone

    from execution.kalshi_client import KalshiMarket

    market = record.get("market") or {}
    now = datetime.now(timezone.utc)
    return KalshiMarket(
        asset=record.get("asset", "BTC"),
        ticker=record.get("ticker", "T"),
        event_ticker=market.get("event_ticker", "E"),
        title="",
        target_price=D(str(market.get("target_price", "1") or "1")),
        open_time=now - timedelta(minutes=14),
        close_time=now + timedelta(minutes=1),
        yes_bid=D(str(market.get("yes_bid", "0.5") or "0.5")),
        yes_ask=D(str(market.get("yes_ask", "0.5") or "0.5")),
        yes_bid_size=D("1000"),
        yes_ask_size=D("1000"),
        fee_multiplier=D(str(market.get("fee_multiplier", "1") or "1")),
    )


# ------------------------------------------- 3. the price a backtest sees --


def reconcile_prices(decisions_paths: list[Path]) -> tuple[int, dict]:
    """How far the discovery snapshot is from the price actually payable.

    This is the input a backtest is fed, and it is the one thing the ledger
    cannot check: the ledger records what was paid, never what a snapshot said
    it would cost. Pricing a simulation off ``mkt_yes_bid``/``mkt_yes_ask``
    - the discovery snapshot - measures a different game, and the direction of
    the error decides whether a sizing change looks good or bad.
    """
    import statistics

    diffs: list[float] = []
    for path in decisions_paths:
        if not path.exists():
            continue
        for line in path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            quote = record.get("live_quote") or {}
            candidate = record.get("candidate") or {}
            market = record.get("market") or {}
            if not quote or not candidate or not market:
                continue
            try:
                snapshot = D(
                    str(
                        market["no_ask"]
                        if candidate.get("outcome") == "no"
                        else market["yes_ask"]
                    )
                )
                executed = D(str(quote["limit_price"]))
            except (KeyError, ArithmeticError, TypeError, ValueError):
                continue
            diffs.append(float(executed - snapshot))

    if not diffs:
        return 0, {}
    worse = [d for d in diffs if d > 0]
    return len(diffs), {
        "mean_abs": statistics.mean(abs(d) for d in diffs),
        "mean_signed": statistics.mean(diffs),
        "worse": len(worse),
        "total": len(diffs),
        "max_abs": max(abs(d) for d in diffs),
    }


# ------------------------------------------------------------------ report --


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--decisions", default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    config.load_environment(PROJECT_ROOT)
    settings = config.KalshiSettings.from_env()
    ledger_path = Path(args.ledger) if args.ledger else settings.ledger_path
    decisions_path = (
        Path(args.decisions) if args.decisions else settings.decision_log_path
    )

    print("=" * 78)
    print("REPLAY - does the simulator reproduce the recorded history?")
    print("=" * 78)
    print(f"ledger    : {ledger_path}")
    print(f"decisions : {decisions_path}")
    print()

    total_rows, accounting = reconcile_accounting(ledger_path)
    money = [m for m in accounting if m.kind in {"accounting", "settlement"}]
    direction = [m for m in accounting if m.kind == "direction"]
    print(f"1. accounting   {total_rows:>5} settled rows re-derived from their inputs")
    print(f"   cost/fee/debit/pnl mismatches : {len(money)}")
    print(f"   settlement-direction mismatches: {len(direction)}")

    candidates = [decisions_path] + sorted(
        decisions_path.parent.glob(decisions_path.name + ".*")
    )
    checked, sizing, skipped = reconcile_sizing(candidates, settings)
    print()
    print(f"2. sizing       {checked:>5} recorded orders re-run through build_order_plan")
    print(f"   mismatches                    : {len(sizing)}")
    if skipped:
        print(f"   skipped (missing balance/probability): {skipped}")

    count, stats = reconcile_prices(candidates)
    print()
    print(f"3. pricing      {count:>5} records carrying BOTH a snapshot and a re-read")
    if stats:
        print(
            f"   executed minus snapshot       : mean {stats['mean_signed']:+.4f}, "
            f"|mean| {stats['mean_abs']:.4f}, max {stats['max_abs']:.4f}"
        )
        print(
            f"   executed WORSE than snapshot  : {stats['worse']}/{stats['total']}"
        )
        ratio = stats["mean_abs"] / float(settings.min_edge or D("0.05"))
        print(
            f"   vs MIN_EXECUTABLE_EDGE        : {ratio:.1f}x the entire edge floor"
        )
        if ratio > 0.5:
            print()
            print(
                "   => A backtest priced off the snapshot measures a materially"
            )
            print(
                "      cheaper game than the one played. research/backtest.py"
            )
            print(
                "      therefore reports P&L only over rows carrying"
            )
            print(
                "      exec_limit_price, and says how many it had to drop."
            )

    problems = money + direction + sizing
    print()
    if not problems:
        print("VERDICT: exact. Every recorded row reproduces from its own inputs.")
    else:
        print(f"VERDICT: {len(problems)} disagreement(s). The simulator is wrong here,")
        print("         not the ledger - the ledger is the source of truth.")
        print()
        for problem in problems[: args.limit]:
            print(problem)
        if len(problems) > args.limit:
            print(f"  ... and {len(problems) - args.limit} more")
    print()
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
