"""``deskreview`` command: run one review pass and journal it.

Deliberately has no ``--submit``, no ``--execute``, and no broker argument. The
command reads data and writes a report; there is no code path from here to an
account.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path

from amat.config.loading import load_strategy_config
from amat.data.snapshot import load_panel
from deskreview.passes import ReviewReport, build_review

__all__ = ["build_parser", "main"]

_EXIT_OK = 0
_EXIT_ERROR = 2

DEFAULT_JOURNAL = Path("outputs/reviews")


def _load_weights(path: Path | None) -> dict[str, float]:
    """Read current portfolio weights, failing closed on anything ambiguous.

    A dropped symbol looks exactly like a symbol the account does not hold, and
    the review would then report an "entry" for something already owned. So
    every malformation raises instead of being skipped.
    """
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        message = f"cannot read weights file {path}: {exc}"
        raise SystemExit(message) from exc
    except json.JSONDecodeError as exc:
        message = f"weights file {path} is not valid JSON: {exc}"
        raise SystemExit(message) from exc
    if not isinstance(payload, dict):
        message = f"weights file {path} must be a JSON object of symbol to fraction"
        raise SystemExit(message)

    weights: dict[str, float] = {}
    for symbol, value in payload.items():
        if not isinstance(symbol, str) or not symbol.strip():
            message = f"weights file {path} has an empty symbol"
            raise SystemExit(message)
        try:
            fraction = float(value)
        except (TypeError, ValueError) as exc:
            message = f"weights file {path}: {symbol} has a non-numeric weight {value!r}"
            raise SystemExit(message) from exc
        if not math.isfinite(fraction):
            message = f"weights file {path}: {symbol} weight is not finite"
            raise SystemExit(message)
        if fraction < 0:
            message = (
                f"weights file {path}: {symbol} weight {fraction} is negative; v1 is long-only"
            )
            raise SystemExit(message)
        weights[symbol.strip().upper()] = fraction
    return weights


def _count_news(database: Path | None, as_of: dt.datetime) -> tuple[int, bool]:
    """Articles knowable at ``as_of``, and whether the store answered at all.

    Two corrections from the 2026-08-11 audit live here. The count is a real
    ``COUNT(*)`` rather than ``len()`` of a limited query -- the old version
    silently saturated at 1,000 and the saturated number was journaled as if it
    were the population. And the exception net is narrow: a *storage* failure
    (corrupt file, lock, schema mismatch) degrades to "store unavailable" as
    designed, but a programming error in this code now raises instead of being
    laundered into the same caveat.
    """
    if database is None or not database.is_file():
        return 0, False
    try:
        # to_utc_z is the store's single timestamp authority. This previously
        # hand-built the same shape inline, which worked -- until the store
        # tightened its format enforcement, at which point a drifted copy would
        # have raised in here and been swallowed as "store unreachable". One
        # format, one function.
        from newsdesk.errors import NewsdeskError  # noqa: PLC0415 - optional dependency
        from newsdesk.store import NewsStore, to_utc_z  # noqa: PLC0415
    except ImportError:
        return 0, False
    try:
        with NewsStore(database) as store:
            return store.count_knowable_at(to_utc_z(as_of)), True
    except (sqlite3.Error, NewsdeskError, OSError):
        return 0, False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deskreview",
        description=(
            "Run one scheduled market review pass. Observation only: forms no "
            "decision, sizes nothing, submits nothing."
        ),
    )
    parser.add_argument("pass_name", choices=("morning", "midday", "evening"))
    parser.add_argument("config", type=Path, help="Path to a TOML strategy configuration.")
    parser.add_argument(
        "--snapshot", type=Path, required=True, help="Snapshot directory under data/snapshots/."
    )
    parser.add_argument(
        "--weights",
        type=Path,
        default=None,
        help='JSON file mapping symbol to fraction of equity, e.g. {"SPY": 0.12}.',
    )
    parser.add_argument(
        "--news-db",
        type=Path,
        default=Path("data/news/news.sqlite3"),
        help="Point-in-time news store. Absent or empty is reported, not assumed benign.",
    )
    parser.add_argument(
        "--journal", type=Path, default=DEFAULT_JOURNAL, help="Directory for JSON-lines output."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser


def render_text(report: ReviewReport) -> str:
    """Human-readable review. Every number here came from deskreview.metrics."""
    lines = [
        "=" * 78,
        f"{report.pass_name.upper()} REVIEW — {report.strategy_name} v{report.strategy_version}",
        "=" * 78,
        f"  as of session   {report.as_of_session.isoformat()}",
        f"  generated       {report.generated_at.isoformat()}",
        f"  config          {report.config_sha256[:16]}",
        f"  data            {report.data_source}",
        "",
    ]

    for caveat in report.caveats:
        lines.append(f"  ! {caveat}")
    lines.append("")

    lines.append("LEVELS CROSSED ON THE LAST COMPLETED SESSION")
    if report.level_breaks:
        for item in report.level_breaks:
            lines.append(
                f"  {item.symbol:<5} broke {item.kind:<10} {item.level:>10.2f} "
                f"close {item.close:>10.2f} ({item.distance:+.2%})"
            )
    else:
        lines.append("  none")
    lines.append("")

    lines.append(
        "BANDS — target vs held. A trade fires at 100% of the rebalance band, "
        "OR on any entry/exit however small."
    )
    lines.append(f"  {'SYM':<5} {'TARGET':>8} {'HELD':>8} {'GAP':>8} {'BAND%':>8}  {'':<9} TRADES")
    for band in sorted(report.bands, key=lambda item: -item.band_fraction):
        if band.target_weight == 0 and band.current_weight == 0:
            continue
        lines.append(
            f"  {band.symbol:<5} {band.target_weight:>8.2%} {band.current_weight:>8.2%} "
            f"{band.gap:>+8.2%} {band.band_fraction:>7.0%}  {band.transition:<9} "
            f"{'YES' if band.would_trade else 'no'}"
        )
    lines.append("")

    lines.append("HOLDINGS — observations. v1 defines no target and no stop.")
    if report.holdings:
        lines.append(f"  {'SYM':<5} {'WEIGHT':>8} {'CLOSE':>10} {'DD/PEAK':>9} {'TO SUPP':>9}")
        for holding in report.holdings:
            lines.append(
                f"  {holding.symbol:<5} {holding.weight:>8.2%} {holding.close:>10.2f} "
                f"{holding.drawdown_from_peak:>+9.2%} {holding.distance_to_support:>+9.2%}"
            )
    else:
        lines.append("  flat")
    lines.append("")

    exhausted = [state for state in report.pullbacks if state.exhausted]
    lines.append("PULLBACKS — narrow bar closing strong, after a decline (description only)")
    if exhausted:
        for state in exhausted:
            lines.append(
                f"  {state.symbol:<5} drawdown {state.drawdown_from_high:>+7.2%} "
                f"range {state.range_ratio:>5.2f}x  close at {state.close_location:>4.0%} of bar"
            )
    else:
        lines.append("  none meet both declared conditions")
    lines.append("")

    lines.append(f"NEWS knowable at this time: {report.news_articles}")
    lines.append("=" * 78)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    resolved = load_strategy_config(args.config)
    strategy = resolved.strategy
    symbols = [str(symbol) for symbol in strategy.universe.symbols]
    panel, _manifest = load_panel(args.snapshot, symbols, with_context=True)

    generated_at = dt.datetime.now(tz=dt.UTC)
    articles, available = _count_news(args.news_db, generated_at)

    report = build_review(
        args.pass_name,
        strategy,
        panel,
        current_weights=_load_weights(args.weights),
        config_sha256=resolved.content_sha256,
        generated_at=generated_at,
        news_articles=articles,
        news_available=available,
    )

    journal: Path = args.journal
    journal.mkdir(parents=True, exist_ok=True)
    path = journal / f"{report.as_of_session.isoformat()}.{report.pass_name}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report.as_dict(), sort_keys=True, default=str) + "\n")

    print(
        json.dumps(report.as_dict(), indent=2, sort_keys=True, default=str)
        if args.json
        else render_text(report)
    )
    return _EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit as exit_code:
        if isinstance(exit_code.code, str):
            print(exit_code.code, file=sys.stderr)
            raise SystemExit(_EXIT_ERROR) from None
        raise
