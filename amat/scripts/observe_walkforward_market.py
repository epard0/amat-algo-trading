"""Read-only forward evidence while frozen walk-forward promotion gate is closed.

No venue broker or order-submission client is constructed. Hypothetical book
walks are execution diagnostics, not strategy fills or paper P&L.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PERPS = ROOT.parent / "kalshi-perps"
sys.path.insert(0, str(PERPS / "src"))
from kalshi_perps.domain import registry, dec
from kalshi_perps.marketdata import Feed
from kalshi_perps.venue import Transport


def ordered_levels(quote):
    levels = {}
    for side in ("bids", "asks"):
        rows = [(dec(p), dec(q)) for p, q in quote[side]]
        if any(p <= 0 or q < 0 for p, q in rows):
            raise ValueError("invalid book level")
        if len({p for p, _ in rows}) != len(rows):
            raise ValueError("duplicate book price")
        levels[side] = sorted([(p, q) for p, q in rows if q > 0], reverse=side == "bids")
    if not levels["bids"] or not levels["asks"]:
        raise ValueError("two-sided executable book required")
    if levels["bids"][0][0] >= levels["asks"][0][0]:
        raise ValueError("crossed book")
    return levels


def book_walk(quote, notional=1000):
    levels = ordered_levels(quote)
    mid = (levels["bids"][0][0] + levels["asks"][0][0]) / 2
    desired = int(dec(notional) / mid)
    out = {}
    for side in ("bids", "asks"):
        remaining = desired
        value = dec(0)
        count = dec(0)
        for price, qty in levels[side]:
            qty = min(dec(qty), dec(remaining))
            value += qty * dec(price)
            count += qty
            remaining -= qty
            if remaining <= 0:
                break
        vwap = value / count if count else None
        adverse = (
            (vwap / mid - 1) * 10000 * (1 if side == "asks" else -1) if vwap is not None else None
        )
        out[side] = dict(
            requested_contracts=desired,
            filled_contracts=str(count),
            unfilled_contracts=str(remaining),
            vwap=str(vwap) if vwap is not None else None,
            one_way_cost_bps_including_4bps_fee=float(adverse + 4) if adverse is not None else None,
        )
    return out


def quote_metrics(quotes, instruments):
    from kalshi_perps.domain import book

    metrics = {}
    for ticker, q in quotes.items():
        levels = book(q, instruments[ticker])
        bid, ask = levels["bids"][0][0], levels["asks"][0][0]
        mid = (bid + ask) / 2
        metrics[ticker] = dict(
            bid=str(bid),
            ask=str(ask),
            mark=q["mark"],
            spread_bps=float((ask - bid) / mid * 10000),
            contract_size=str(instruments[ticker].contract_size),
            contract_step=str(instruments[ticker].step),
            hypothetical_1000usd_book_walk=book_walk(q),
        )
    return metrics


def observe(out):
    summary = json.loads((out / "summary.json").read_text())
    if summary["winner"] is not None:
        raise ValueError("observer is standby-only; promotion needs frozen strategy adapter")
    instruments = registry(json.loads((PERPS / "configs/instruments.json").read_text()))
    instruments = {k: instruments[k] for k in ("KXDOGEPERP", "KXSOLPERP")}
    transport = Transport("production", writable=False)
    feed = Feed(transport, instruments)
    event = feed.snapshot(15000)
    statefile = out / "forward-observation-state.json"
    previous = json.loads(statefile.read_text()) if statefile.exists() else {}
    # Overlap a day because native funding can publish after its economic timestamp.
    start = previous.get("start_ms", event["timestamp_ms"])
    funding = feed.funding(
        max(start - 86400000, event["timestamp_ms"] - 2 * 86400000), event["timestamp_ms"]
    )
    metrics = quote_metrics(event["quotes"], instruments)
    receipt = dict(
        observed_at=datetime.now(timezone.utc).isoformat(),
        timestamp_ms=event["timestamp_ms"],
        mode="read_only_observation",
        metrics_version=2,
        promotion="blocked: no candidate passed frozen robustness gate",
        venue_orders=0,
        paper_strategy_fills=0,
        quotes=event["quotes"],
        metrics=metrics,
        native_published_funding=funding,
        execution_evidence="Hypothetical current depth only; not actual trades or proof of future fills",
        protocol_sha256=hashlib.sha256((out / "protocol.json").read_bytes()).hexdigest(),
    )
    with (out / "forward-observations.jsonl").open("a") as f:
        f.write(json.dumps(receipt) + "\n")
    state = dict(
        start_ms=start,
        last_success_ms=event["timestamp_ms"],
        observations=previous.get("observations", 0) + 1,
        last=receipt,
        withdrawals="disabled",
    )
    tmp = statefile.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(statefile)
    (out / "forward-observation-error.json").unlink(missing_ok=True)
    transport.client.close()
    return dict(
        timestamp_ms=event["timestamp_ms"],
        observations=state["observations"],
        metrics=metrics,
        published_funding_events=len(funding),
        promotion=receipt["promotion"],
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--output", type=Path, default=ROOT / "outputs/crypto-doge-sol-walkforward-2026-10-19"
    )
    p.add_argument("--loop", action="store_true")
    args = p.parse_args()
    while True:
        try:
            print(json.dumps(observe(args.output)), flush=True)
        except Exception as exc:
            # Avoid exception bodies containing HTTP request headers or credentials.
            error = dict(
                timestamp=datetime.now(timezone.utc).isoformat(),
                error_type=type(exc).__name__,
                state="observation_failed_no_trading",
            )
            (args.output / "forward-observation-error.json").write_text(json.dumps(error, indent=2))
            print(json.dumps(error), flush=True)
            if not args.loop:
                raise SystemExit(1)
        if not args.loop:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
