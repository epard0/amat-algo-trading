#!/usr/bin/env python3
"""Build the standardized training/backtest dataset.

One row per *evaluation*, not per trade. That is the whole point: the ledger has
46 trades, and the decision log has 558 evaluations that computed a full
prediction. The ~512 that never traded are the counterfactuals — a gate refused,
and nobody asked whether refusing was right — and they are the majority of the
signal available for tuning a threshold. They are worthless without a label, so
`research/settlements.py` backfills the venue's own answer for every ticker.

    uv run python research/settlements.py      # authoritative labels first
    uv run python research/dataset.py          # then the dataset
    uv run python research/dataset.py --report # what is in it

Output is JSONL beside a `schema.json`, because this project ships no dataframe
dependency and a stream is what the rest of it already speaks.

Three things in the schema exist because of specific measurements, not taste:

**`model_z_from_distance` and `model_z_from_drift` are separate columns.** The
model's `z_score` is `(distance + drift*minutes) / sigma`, and the two halves
behave completely differently. On the -$8.66 BTC loss of 20 Aug the aggregate z
was 0.58 - comfortably past the 0.50 floor that was supposed to refuse
too-close-to-call markets - while the distance alone was **0.16 sigma**. 73% of
that z was drift. Any search over that threshold has to be able to see the
halves, or it will re-derive the same broken gate.

**`label_source` is mandatory and never mixed silently.** A strike synthesised
from the Kraken interval open differs from Kalshi's CF Benchmarks strike by a
p95 of 0.13%, which is 0.9 sigma of a 15-minute terminal distribution - and it is
asset-dependent (BTC 0.17 sigma, HYPE and DOGE above 1.0). Synthetic labels are
therefore wrong precisely where markets are decided. Venue labels are
authoritative; anything else must be filtered on `label_margin_sigma`.

**Money stays a string.** Every price and P&L is exact text, as everywhere else
in this project; the consumer casts once. Feature and model values are floats,
because that is what they are.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SCHEMA_VERSION = 1
DEFAULT_OUT = PROJECT_ROOT / "data" / "dataset" / "samples.jsonl"

# The 15 regime features, in the order market_regime.RegimeFeatures exposes them.
FEATURE_FIELDS = (
    "realized_vol_fast", "realized_vol_slow", "vol_ratio", "efficiency_ratio",
    "return_autocorrelation", "range_compression", "impulse_sigma",
    "impulse_direction", "minutes_since_impulse", "reversal_retracement",
    "close_location", "wick_rejection", "travel_direction",
    "recent_travel_sigma", "bars_available",
)

MODEL_FLOAT_FIELDS = (
    "probability_yes", "raw_probability_yes", "z_score", "distance_return",
    "momentum_return", "raw_momentum_return", "volatility_per_minute",
    "effective_volatility", "effective_drift_minutes", "applied_shrinkage",
    "regime_confidence", "minutes_remaining",
)


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _s(value: Any) -> str | None:
    return None if value is None else str(value)


def decompose_z(prediction: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """Split the model's z into its distance and drift halves.

    ``z = (distance + drift * drift_minutes) / (vol * sqrt(minutes))``. The
    distance half is a fact about where spot sits relative to the strike; the
    drift half is a forecast. A guard that means to ask "is the strike far
    enough away that the settlement index cannot decide this" wants the first
    and is routinely satisfied by the second.

    Returns ``(terminal_sigma, z_from_distance, z_from_drift)``.
    """
    distance = _f(prediction.get("distance_return"))
    vol = _f(prediction.get("effective_volatility"))
    minutes = _f(prediction.get("minutes_remaining"))
    z_total = _f(prediction.get("z_score"))
    if None in (distance, vol, minutes) or minutes <= 0 or vol <= 0:
        return None, None, None
    sigma = vol * math.sqrt(minutes)
    if sigma <= 0:
        return None, None, None
    z_distance = distance / sigma
    z_drift = (z_total - z_distance) if z_total is not None else None
    return sigma, z_distance, z_drift


def load_settlements(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("ticker"):
                out[record["ticker"]] = record
    return out


def load_trades(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Ledger rows keyed by ticker, so an evaluation can be joined to its trade."""
    if not path.is_file():
        return {}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in connection.execute("SELECT * FROM trades")]
    finally:
        connection.close()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["ticker"], []).append(row)
    return grouped


def iter_decisions(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a torn last line is expected while the bot is writing
            if record.get("record_type") == "decision":
                yield record


def build_sample(
    record: dict[str, Any],
    settlements: dict[str, dict[str, Any]],
    trades: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """One evaluation -> one row, or None if it never computed a prediction.

    An evaluation blocked before the model ran (445 of them are refused on
    ``entry_window`` alone) carries no features and cannot train anything. It is
    still a fact about the bot's behaviour, but it is not a sample.
    """
    prediction = record.get("prediction")
    if not prediction:
        return None

    ticker = record.get("ticker")
    market = record.get("market") or {}
    features = prediction.get("regime_features") or {}
    sigma, z_distance, z_drift = decompose_z(prediction)

    settlement = settlements.get(ticker, {})
    result = settlement.get("result")
    settled_yes = None if result not in ("yes", "no") else (result == "yes")

    # How safe this row's label is. Expressed in terminal sigmas of distance
    # from the strike: a market that settled a long way from its strike is
    # labelled correctly no matter which index measured it, and one that settled
    # on top of the strike is a coin flip that Kraken and CF Benchmarks can
    # legitimately disagree about.
    label_margin = abs(z_distance) if z_distance is not None else None

    gates = record.get("gates") or []
    failed = [g["name"] for g in gates if not g.get("allowed")]

    sample: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "sample_id": record.get("decision_id"),
        "source": "live_decision",
        "cycle_id": record.get("cycle_id"),
        "timestamp": record.get("timestamp"),
        "mode": record.get("mode"),
        "environment": record.get("environment"),
        "asset": record.get("asset"),
        "ticker": ticker,
        "event_ticker": market.get("event_ticker"),
        "seconds_to_close": _f(record.get("seconds_to_close")),

        # --- label -------------------------------------------------------
        "label_settled_yes": settled_yes,
        "label_result": result,
        "label_source": "venue" if settled_yes is not None else None,
        "label_exchange_status": settlement.get("status"),
        "label_margin_sigma": label_margin,

        # --- model -------------------------------------------------------
        "model_regime": prediction.get("regime"),
        "model_terminal_sigma": sigma,
        "model_z_from_distance": z_distance,
        "model_z_from_drift": z_drift,
    }
    for field in MODEL_FLOAT_FIELDS:
        sample[f"model_{field}"] = _f(prediction.get(field))

    for field in FEATURE_FIELDS:
        sample[f"feat_{field}"] = _f(features.get(field))

    # --- market state (money as exact text) ------------------------------
    sample.update({
        "mkt_yes_bid": _s(market.get("yes_bid")),
        "mkt_yes_ask": _s(market.get("yes_ask")),
        "mkt_no_ask": _s(market.get("no_ask")),
        "mkt_implied_probability_yes": _s(market.get("implied_probability_yes")),
        "mkt_fee_multiplier": _s(market.get("fee_multiplier")),
        "mkt_target_price": _s(market.get("target_price") or prediction.get("target_price")),
        "mkt_spot": _s(prediction.get("current_spot")),
        "mkt_close_time": market.get("close_time"),
    })

    # --- the price actually payable, where the bot got far enough to read it -
    #
    # The block above is the DISCOVERY SNAPSHOT, and it is not what a trade
    # costs. Measured across 58 records carrying both, the executed limit is
    # 10.1 cents from the snapshot on average and worse in 53 of 58 - twice
    # MIN_EXECUTABLE_EDGE, in the unfavourable direction. A backtest priced off
    # the snapshot is therefore measuring a materially cheaper game than the
    # one being played, and it will rate any sizing change too kindly, because
    # size multiplies a phantom edge.
    #
    # Only the candidate selected in a pass gets a live re-read, so this is
    # absent on most rows. Absent means "not measurable here", never "equal to
    # the snapshot".
    quote = record.get("live_quote") or {}
    sample.update({
        "exec_best_price": _s(quote.get("best_price")),
        "exec_limit_price": _s(quote.get("limit_price")),
        "exec_depth_at_limit": _s(quote.get("depth_at_limit")),
        "exec_buffered_price": _s(quote.get("buffered_price")),
        "exec_live_edge": _s(quote.get("live_edge")),
    })

    # --- what the bot did ------------------------------------------------
    sample.update({
        "act_decision": record.get("decision"),
        "act_blocked_by": record.get("blocked_by"),
        "act_reason": record.get("decision_reason"),
        "act_gates_evaluated": [g.get("name") for g in gates],
        "act_gates_failed": failed,
        "act_thresholds": record.get("thresholds") or {},
        "act_late_entry": record.get("late_entry"),
        "act_applied_floors": record.get("applied_floors"),
    })

    # --- realized outcome, when this evaluation became a trade -----------
    trade = None
    for candidate in trades.get(ticker, []):
        if candidate.get("trade_id") == record.get("trade_id"):
            trade = candidate
            break
    if trade is None and record.get("decision") == "traded":
        # The decision names no trade_id on every path; fall back to the one
        # trade on this ticker in this mode, which the ledger's UNIQUE
        # (mode, environment, ticker, strategy) makes unambiguous per strategy.
        same = [c for c in trades.get(ticker, []) if c.get("mode") == record.get("mode")]
        trade = same[0] if len(same) == 1 else None
    if trade:
        sample.update({
            "res_trade_id": trade["trade_id"],
            "res_strategy": trade["strategy"],
            "res_outcome": trade["outcome"],
            "res_status": trade["status"],
            "res_contracts": _s(trade["contracts"]),
            "res_entry_price": _s(trade["entry_price"]),
            "res_total_debit": _s(trade["total_debit"]),
            "res_pnl": _s(trade["pnl"]),
        })
    else:
        for key in ("res_trade_id", "res_strategy", "res_outcome", "res_status",
                    "res_contracts", "res_entry_price", "res_total_debit", "res_pnl"):
            sample[key] = None
    return sample


def build(
    decisions: Path, settlements_path: Path, ledger: Path
) -> list[dict[str, Any]]:
    settlements = load_settlements(settlements_path)
    trades = load_trades(ledger)
    out = []
    for record in iter_decisions(decisions):
        sample = build_sample(record, settlements, trades)
        if sample is not None:
            out.append(sample)
    return out


def schema_of(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """A description of the columns, written beside the data.

    Emitted from the rows themselves rather than declared separately, so the
    schema file cannot drift from what was actually written.
    """
    columns: dict[str, dict[str, Any]] = {}
    for sample in samples:
        for key, value in sample.items():
            entry = columns.setdefault(key, {"types": set(), "nulls": 0, "n": 0})
            entry["n"] += 1
            if value is None:
                entry["nulls"] += 1
            else:
                entry["types"].add(type(value).__name__)
    return {
        "schema_version": SCHEMA_VERSION,
        "rows": len(samples),
        "columns": {
            key: {
                "types": sorted(entry["types"]) or ["null"],
                "null_fraction": round(entry["nulls"] / entry["n"], 4) if entry["n"] else 1.0,
            }
            for key, entry in sorted(columns.items())
        },
    }


def report(samples: list[dict[str, Any]]) -> None:
    print(f"samples: {len(samples)}")
    if not samples:
        return
    labelled = [s for s in samples if s["label_settled_yes"] is not None]
    print(f"  with a venue label            : {len(labelled)} "
          f"({len(labelled)/len(samples):.0%})")
    traded = [s for s in samples if s["res_trade_id"]]
    print(f"  that became a trade           : {len(traded)}")
    print(f"  counterfactual (labelled, not traded): {len(labelled) - len(traded)}")
    print(f"\nby asset      : {dict(Counter(s['asset'] for s in samples).most_common())}")
    print(f"blocked_by    : {dict(Counter(s['act_blocked_by'] for s in samples).most_common())}")

    both = [s for s in labelled
            if s["model_z_from_distance"] is not None and s["model_z_from_drift"] is not None]
    if both:
        drift_share = [
            abs(s["model_z_from_drift"]) /
            (abs(s["model_z_from_distance"]) + abs(s["model_z_from_drift"]))
            for s in both
            if (abs(s["model_z_from_distance"]) + abs(s["model_z_from_drift"])) > 0
        ]
        drift_share.sort()
        mid = drift_share[len(drift_share)//2]
        print(f"\nz decomposition over {len(both)} labelled rows:")
        print(f"  median share of |z| coming from DRIFT: {mid:.0%}")
        print(f"  rows where drift supplies >50% of |z|: "
              f"{sum(1 for d in drift_share if d > 0.5)}")

    if labelled:
        yes = sum(1 for s in labelled if s["label_settled_yes"])
        print(f"\nbase rate P(yes) over labelled rows: {yes/len(labelled):.1%}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--decisions", type=Path, default=PROJECT_ROOT / "decisions.jsonl")
    parser.add_argument("--settlements", type=Path,
                        default=PROJECT_ROOT / "data" / "settlements.jsonl")
    parser.add_argument("--ledger", type=Path, default=PROJECT_ROOT / "trade_ledger.db")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", action="store_true", help="describe and do not write")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace the dataset instead of merging into it "
                             "(discards every sample whose decision log has rotated)")
    args = parser.parse_args(argv)

    samples = build(args.decisions, args.settlements, args.ledger)
    report(samples)
    if args.report:
        return 0

    # Merge with whatever is already there, keyed on sample_id.
    #
    # This is the load-bearing part of the whole design. DECISION_LOG_RETENTION_HOURS
    # is 24, deliberately - the raw log reaches 38 GB a year and nothing downstream
    # treats it as durable. But the *samples* are 1 KB each and they are the only
    # record of what the bot saw on a market it refused. Once decisions.jsonl
    # rotates they cannot be rebuilt from anything: the Kalshi book at that instant
    # is gone. Overwriting here would silently discard every day but the last, and
    # the gate analysis needs months.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, Any]] = {}
    if args.out.is_file() and not args.overwrite:
        with args.out.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("sample_id"):
                    existing[row["sample_id"]] = row
    before = len(existing)
    for sample in samples:
        if sample.get("sample_id"):
            # A re-run relabels rows whose market has settled since last time.
            existing[sample["sample_id"]] = sample
    merged = sorted(existing.values(), key=lambda r: (r.get("timestamp") or "", r["sample_id"]))

    with args.out.open("w", encoding="utf-8") as handle:
        for sample in merged:
            handle.write(json.dumps(sample, sort_keys=True) + "\n")
    schema_path = args.out.with_name("schema.json")
    schema_path.write_text(json.dumps(schema_of(merged), indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {args.out}: {len(merged)} rows "
          f"({before} already there, {len(merged) - before} new)")
    print(f"wrote {schema_path}")
    print(f"unique markets: {len({r['ticker'] for r in merged})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
