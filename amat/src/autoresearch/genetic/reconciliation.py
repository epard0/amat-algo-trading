"""Explain vendor adjustment revisions without altering frozen research evidence."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import exchange_calendars as xcals  # type: ignore[import-untyped]
import polars as pl

from amat.data.alpaca import DailyBar, DividendEvent, SplitEvent
from amat.data.snapshot import load_manifest

_MAX_EXAMPLES = 5


def reconciliation_report(  # noqa: PLR0912, PLR0915, PLR0917 — sequential audit with a single report
    old_snapshot: Path,
    raw_bars: Sequence[DailyBar],
    adjusted_bars: Sequence[DailyBar],
    new_dividends: Sequence[DividendEvent],
    new_splits: Sequence[SplitEvent],
    through: dt.date,
    *,
    symbols: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Compare full refreshed history and explain *only* new cash adjustments.

    Actions must cover the entire original interval through ``through``. Exact
    historical action repeats are collapsed, but conflicting historical values
    remain part of the comparison. This does not resolve those conflicts or
    grant the refreshed data any validation/promotion authority.
    """
    manifest = load_manifest(old_snapshot)
    wanted = tuple(symbols) if symbols is not None else tuple(sorted({b.symbol for b in raw_bars}))
    issues: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema_version": "genetic-adjustment-reconciliation/1",
        "status": "refused",
        "old_snapshot_id": manifest.snapshot_id,
        "old_partition_sha256": manifest.partition_sha256,
        "through": through.isoformat(),
        "symbols": list(wanted),
        "issues": issues,
        "assets": [],
        "rounding_rule": "0.005 * (1 + expected_factor) + 1e-10",
        "authority": "data reconciliation only; no historical validation transfer",
    }
    if not wanted or len(set(wanted)) != len(wanted) or not set(wanted) <= set(manifest.symbols):
        issues.append({"code": "invalid_universe"})
        return report
    if through < manifest.last_session:
        issues.append({"code": "cutoff_precedes_snapshot"})
        return report
    calendar = xcals.get_calendar("XNYS", start=str(manifest.first_session), end=str(through))
    sessions = [day.date() for day in calendar.schedule.index]
    expected = {(day, symbol) for day in sessions for symbol in wanted}
    maps: list[dict[tuple[dt.date, str], DailyBar]] = []
    for name, bars in (("raw", raw_bars), ("adjusted", adjusted_bars)):
        mapping = {(bar.session_date, bar.symbol): bar for bar in bars}
        maps.append(mapping)
        if len(mapping) != len(bars):
            issues.append({"code": "duplicate_bars", "input": name})
        if set(mapping) != expected:
            issues.append(
                {
                    "code": "coverage_mismatch",
                    "input": name,
                    "missing_cells": len(expected - set(mapping)),
                    "unexpected_cells": len(set(mapping) - expected),
                }
            )
        invalid = sum(
            not all(math.isfinite(v) and v > 0 for v in (b.open, b.high, b.low, b.close))
            or not math.isfinite(b.volume)
            or b.volume < 0
            or b.high < max(b.open, b.low, b.close)
            or b.low > min(b.open, b.high, b.close)
            for b in bars
        )
        if invalid:
            issues.append({"code": "invalid_bars", "input": name, "count": invalid})
    raw, adjusted = maps
    old_rows = (
        pl.read_parquet(old_snapshot / "daily.parquet")
        .filter(pl.col("symbol").is_in(wanted))
        .to_dicts()
    )
    old_keys = {(r["session_date"], r["symbol"]) for r in old_rows}
    old_expected = {key for key in expected if key[0] <= manifest.last_session}
    if old_keys != old_expected or len(old_rows) != len(old_keys):
        issues.append({"code": "original_coverage_mismatch"})
    old_actions = (
        pl.read_parquet(old_snapshot / "actions.parquet")
        .filter(pl.col("symbol").is_in(wanted))
        .to_dicts()
    )
    historical = {
        (r["symbol"], r["ex_date"], r["kind"], float(r["value"]))
        for r in old_actions
        if manifest.first_session <= r["ex_date"] <= manifest.last_session
    }
    refreshed = {(e.symbol, e.ex_date, "cash_dividend", e.rate) for e in new_dividends} | {
        (e.symbol, e.ex_date, "split", e.ratio) for e in new_splits
    }
    if any(
        s not in wanted
        or not manifest.first_session <= day <= through
        or not math.isfinite(value)
        or value <= 0
        for s, day, _, value in refreshed
    ):
        issues.append({"code": "invalid_action"})
    refreshed_history = {event for event in refreshed if event[1] <= manifest.last_session}
    if refreshed_history != historical:
        issues.append(
            {
                "code": "historical_actions_changed",
                "removed": len(historical - refreshed_history),
                "added": len(refreshed_history - historical),
            }
        )
    tail_actions = sorted(event for event in refreshed if event[1] > manifest.last_session)
    factors = dict.fromkeys(wanted, 1.0)
    action_details: dict[str, list[dict[str, Any]]] = {s: [] for s in wanted}
    seen: set[tuple[str, dt.date, str]] = set()
    for symbol, day, kind, value in tail_actions:
        if symbol not in factors:
            continue
        action_key = (symbol, day, kind)
        if action_key in seen:
            issues.append(
                {"code": "conflicting_new_actions", "symbol": symbol, "ex_date": str(day)}
            )
            continue
        seen.add(action_key)
        if kind == "split":
            issues.append({"code": "unsupported_new_split", "symbol": symbol, "ex_date": str(day)})
            continue
        if day not in sessions or sessions.index(day) == 0:
            issues.append({"code": "invalid_ex_date", "symbol": symbol, "ex_date": str(day)})
            continue
        prior = raw.get((sessions[sessions.index(day) - 1], symbol))
        if prior is None or not math.isfinite(prior.close) or not 0 < value < prior.close:
            issues.append(
                {"code": "invalid_dividend_anchor", "symbol": symbol, "ex_date": str(day)}
            )
            continue
        factor = 1 - value / prior.close
        factors[symbol] *= factor
        action_details[symbol].append(
            {
                "ex_date": str(day),
                "rate": value,
                "preceding_raw_close": prior.close,
                "factor": factor,
            }
        )
    raw_fields = {
        "raw_open": "open",
        "raw_high": "high",
        "raw_low": "low",
        "raw_close": "close",
        "raw_volume": "volume",
    }
    for symbol in wanted:
        changed = unexplained = raw_revisions = compared = 0
        max_error = 0.0
        examples: list[dict[str, Any]] = []
        factor = factors[symbol]
        tolerance = 0.005 * (1 + factor) + 1e-10
        for row in old_rows:
            if row["symbol"] != symbol:
                continue
            key = (row["session_date"], symbol)
            if key not in raw or key not in adjusted:
                continue
            compared += 1
            revised = [
                name for name, field in raw_fields.items() if row[name] != getattr(raw[key], field)
            ]
            raw_revisions += bool(revised)
            old_close, new_close = float(row["adjusted_close"]), adjusted[key].close
            changed += old_close != new_close
            error = abs(new_close - old_close * factor)
            max_error = max(max_error, error)
            # Without any new actions even a cent revision has no explanation.
            bad = error > tolerance if action_details[symbol] else new_close != old_close
            unexplained += bool(bad)
            if (bad or revised) and len(examples) < _MAX_EXAMPLES:
                examples.append(
                    {
                        "session": str(key[0]),
                        "old_adjusted_close": old_close,
                        "new_adjusted_close": new_close,
                        "absolute_error": error,
                        "raw_revised_fields": revised,
                    }
                )
        asset = {
            "symbol": symbol,
            "compared_rows": compared,
            "adjusted_changed_rows": changed,
            "raw_revised_rows": raw_revisions,
            "unexplained_rows": unexplained,
            "expected_factor": factor,
            "absolute_tolerance": tolerance,
            "maximum_absolute_error": max_error,
            "new_cash_actions": action_details[symbol],
            "examples": examples,
        }
        report["assets"].append(asset)
        if raw_revisions:
            issues.append({"code": "raw_history_revised", "symbol": symbol, "rows": raw_revisions})
        if unexplained:
            issues.append({"code": "unexplained_adjustment", "symbol": symbol, "rows": unexplained})
    report["compared_rows"] = sum(a["compared_rows"] for a in report["assets"])
    report["status"] = "passed" if not issues else "refused"
    return report
