"""``newsdesk`` command: ingest news windows and inspect what the store knows.

Network reality: the AMAT sandbox cannot reach either vendor host, so ingestion
subcommands are for the operator's own shell, with credentials exported by name
(``MARKETAUX_API_TOKEN``, ``ADANOS_API_KEY``). The inspection subcommands are
offline and safe anywhere.

Before the first production pull, re-validate the recovered vendor contracts
live -- both were reconstructed through mediated fetches:

* Marketaux: confirm ``meta{found,returned,limit,page}`` and the six-digit
  ``published_at`` against a real response; confirm the free-tier limits.
* Adanos: pull ``https://api.adanos.org/openapi.json`` whole and diff its GET
  surface against this client's single ``/news/stocks/v1/`` prefix; confirm
  whether ``published_at`` on mention rows is ISO or epoch.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from newsdesk.errors import NewsdeskError, ProviderSuspendedError
from newsdesk.store import DEFAULT_DATABASE_PATH, NewsStore, to_utc_z

__all__ = ["build_parser", "main"]

_EXIT_OK = 0
_EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newsdesk",
        description=(
            "Point-in-time news intake. Read-only against vendors, append-only "
            "against the store; forms no signal and touches no account."
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"Store location (default: {DEFAULT_DATABASE_PATH}).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    marketaux = commands.add_parser(
        "ingest-marketaux",
        help="Pull one published_after/published_before window of news.",
        description=(
            "Overlap consecutive windows deliberately: the vendor does not "
            "document boundary inclusivity, the store is idempotent, and a "
            "boundary gap is undetectable later. Free tier: 100 requests/day, "
            "3 articles per request."
        ),
    )
    marketaux.add_argument(
        "--symbols",
        default="",
        help=(
            "Optional comma-separated tickers, e.g. SPY,QQQ,GLD. Omit for broad "
            "market news used by theme discovery."
        ),
    )
    marketaux.add_argument(
        "--published-after", required=True, help="YYYY-MM-DD or YYYY-MM-DDTHH:MM (UTC)."
    )
    marketaux.add_argument("--published-before", default=None, help="Same forms; optional.")
    marketaux.add_argument("--language", default="en")
    marketaux.add_argument(
        "--force",
        action="store_true",
        help=(
            "Attempt the pull even though the provider is suspended after 3 "
            "consecutive failed runs. Pass this only after fixing the recorded cause."
        ),
    )

    adanos = commands.add_parser(
        "ingest-adanos",
        help="Pull one ticker's news mentions (Professional tier only).",
    )
    adanos.add_argument("--ticker", required=True)
    adanos.add_argument("--start", required=True, type=dt.date.fromisoformat)
    adanos.add_argument("--end", required=True, type=dt.date.fromisoformat)
    adanos.add_argument(
        "--force",
        action="store_true",
        help=(
            "Attempt the pull even though the provider is suspended after 3 "
            "consecutive failed runs. Pass this only after fixing the recorded cause."
        ),
    )

    commands.add_parser(
        "coverage",
        help="Every ingestion run and its outcome -- read this before trusting a query.",
    )

    knowable = commands.add_parser(
        "knowable",
        help="What the store could have known at an instant (ingested_at filter).",
    )
    knowable.add_argument(
        "--as-of",
        default=None,
        help="ISO-8601 with timezone (default: now). Filters on OUR ingestion clock.",
    )
    knowable.add_argument(
        "--symbol",
        default=None,
        help=(
            "Filter to articles whose CURRENT version carries this vendor-assigned "
            "entity tag. Tags are the vendor's matching, not article content: an "
            "untagged article about the company is invisible to this filter, so "
            "symbol-filtered counts are lower bounds."
        ),
    )
    knowable.add_argument("--limit", type=int, default=25)

    export = commands.add_parser(
        "export-scalars",
        help="Write the v3 attention-spike weight-scalar CSV for a date window.",
        description=(
            "One row per calendar date: the point-in-time de-risk scalar derived "
            "from how many articles were knowable by the end of that UTC day. "
            "Consumed by `amat study --scalars`. Until months of forward "
            "collection exist, every scalar is 1.0 and v3 degenerates to v1 -- "
            "that is correct behaviour, not an error."
        ),
    )
    export.add_argument("--start", required=True, type=dt.date.fromisoformat)
    export.add_argument("--end", required=True, type=dt.date.fromisoformat)
    export.add_argument("--out", required=True, type=Path, help="CSV destination.")
    export.add_argument(
        "--symbols",
        default=None,
        help=(
            "Comma-separated tickers; the day's count sums per-symbol knowable "
            "counts (vendor entity tags -- a lower bound). Omit to count every "
            "article in the store."
        ),
    )
    export.add_argument("--baseline-sessions", type=int, default=63)
    export.add_argument("--spike-multiple", type=float, default=3.0)
    export.add_argument("--min-baseline-articles", type=int, default=10)
    export.add_argument("--derisk-scalar", type=float, default=0.5)

    discover = commands.add_parser(
        "discover-assets",
        help="Export short-lived asset candidates from positive point-in-time news themes.",
        description=(
            "Match knowable headlines to the reviewed theme taxonomy, require positive "
            "bounded sentiment, and write an inert JSON artifact for `amat plan "
            "--news-assets`. News controls eligibility only; the strategy still applies "
            "its trend signal and every risk cap."
        ),
    )
    discover.add_argument(
        "--themes",
        type=Path,
        default=Path("configs/news/themes.toml"),
        help="Reviewed event-to-symbol taxonomy (default configs/news/themes.toml).",
    )
    discover.add_argument("--out", required=True, type=Path, help="JSON artifact destination.")
    discover.add_argument(
        "--as-of",
        default=None,
        help="Aware ISO-8601 instant; default now. Store reads filter on ingested_at.",
    )
    discover.add_argument(
        "--exclude-symbols",
        default="",
        help="Comma-separated base-universe symbols that need not be rediscovered.",
    )
    discover.add_argument("--lookback-hours", type=int, default=72)
    discover.add_argument("--min-sentiment", type=float, default=0.15)
    discover.add_argument("--min-articles", type=int, default=1)
    discover.add_argument("--max-assets", type=int, default=8)
    discover.add_argument("--ttl-hours", type=int, default=30)

    return parser


def _command_ingest_marketaux(store: NewsStore, args: argparse.Namespace) -> int:
    from newsdesk.clients.marketaux import MarketauxClient  # noqa: PLC0415 - network path
    from newsdesk.ingest import ingest_marketaux  # noqa: PLC0415

    outcome = ingest_marketaux(
        store,
        MarketauxClient(),
        symbols=[s.strip().upper() for s in args.symbols.split(",") if s.strip()],
        published_after=args.published_after,
        published_before=args.published_before,
        language=args.language,
        force=args.force,
    )
    print(outcome.summary)
    return _EXIT_OK if outcome.status == "complete" else _EXIT_ERROR


def _command_ingest_adanos(store: NewsStore, args: argparse.Namespace) -> int:
    from newsdesk.clients.adanos import AdanosClient  # noqa: PLC0415 - network path
    from newsdesk.ingest import ingest_adanos_mentions  # noqa: PLC0415

    outcome = ingest_adanos_mentions(
        store,
        AdanosClient(),
        ticker=args.ticker.strip().upper(),
        start=args.start,
        end=args.end,
        force=args.force,
    )
    print(outcome.summary)
    return _EXIT_OK if outcome.status == "complete" else _EXIT_ERROR


def _command_coverage(store: NewsStore) -> int:
    runs = store.coverage()
    if not runs:
        print("no ingestion runs recorded; the store has never been written to")
        return _EXIT_OK
    stale = 0
    for run in runs:
        window = f"{run.get('query_window_start') or '?'} .. {run.get('query_window_end') or '?'}"
        line = (
            f"run {run['run_id']:>4}  {run['provider_name']:<10} {run['status']:<10} "
            f"{window:<28} started={run['started_at']} "
            f"pages={run['pages_fetched']} stored={run['rows_stored']}"
        )
        if run["status"] == "running":
            # A run that is genuinely in flight and one whose process was
            # killed look identical in the table. The reader has to decide
            # from the start time; what must not happen is the state passing
            # unremarked.
            stale += 1
            line += "  <-- still 'running': in flight, or its process died without settling"
        reason = run.get("failure_reason")
        if reason:
            line += f"  [{reason}]"
        print(line)
    if stale:
        print(
            f"{stale} run(s) show 'running'. If no ingest is actually in flight, those are "
            "crashed runs: their windows are unrepresented and should be re-pulled."
        )
    return _EXIT_OK


def _command_knowable(store: NewsStore, args: argparse.Namespace) -> int:
    if args.as_of is None:
        as_of = to_utc_z(dt.datetime.now(tz=dt.UTC))
    else:
        moment = dt.datetime.fromisoformat(args.as_of)
        if moment.tzinfo is None:
            print("--as-of must carry a timezone; a naive instant is ambiguous", file=sys.stderr)
            return _EXIT_ERROR
        as_of = to_utc_z(moment)

    articles = store.knowable_at(as_of, symbol=args.symbol, limit=args.limit)
    total = store.count_knowable_at(as_of, symbol=args.symbol)
    print(f"knowable at {as_of}" + (f" for {args.symbol}" if args.symbol else "") + f": {total}")
    if len(articles) < total:
        print(f"  (showing {len(articles)} of {total}; raise --limit for more)")
    if not articles:
        print(
            "  nothing. The store collects forward from first ingestion; an empty "
            "result before then is expected, not an absence of news in the world."
        )
        return _EXIT_OK
    for article in articles:
        print(f"  [{article.published_at}] ({article.provider}) {article.title[:90]}")
        print(f"      ingested {article.ingested_at}  {article.url[:90]}")
    return _EXIT_OK


def _command_export_scalars(store: NewsStore, args: argparse.Namespace) -> int:
    from newsdesk.features import (  # noqa: PLC0415
        AttentionParameters,
        attention_scalars,
        render_scalar_csv,
    )

    symbols = (
        [part.strip().upper() for part in args.symbols.split(",") if part.strip()]
        if args.symbols
        else []
    )
    parameters = AttentionParameters(
        baseline_sessions=args.baseline_sessions,
        spike_multiple=args.spike_multiple,
        min_baseline_articles=args.min_baseline_articles,
        derisk_scalar=args.derisk_scalar,
    )
    rows = attention_scalars(
        store, start=args.start, end=args.end, symbols=symbols, parameters=parameters
    )
    text = render_scalar_csv(
        rows,
        symbols=symbols,
        parameters=parameters,
        generated_at=to_utc_z(dt.datetime.now(tz=dt.UTC)),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    derisked = sum(1 for _date, scalar, _count in rows if scalar < 1.0)
    print(f"wrote {len(rows)} dates to {args.out}; {derisked} de-risked")
    if derisked == 0:
        print(
            "  every scalar is 1.0 -- with this store's coverage, v3 is currently "
            "identical to v1. The series becomes informative only as forward "
            "collection accrues."
        )
    return _EXIT_OK


def _command_discover_assets(store: NewsStore, args: argparse.Namespace) -> int:
    from newsdesk.discovery import (  # noqa: PLC0415
        DiscoveryParameters,
        carry_retired_candidates,
        discover_assets,
        load_themes,
    )

    if args.as_of is None:
        as_of = dt.datetime.now(tz=dt.UTC)
    else:
        as_of = dt.datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
        if as_of.tzinfo is None:
            print("--as-of must carry a timezone", file=sys.stderr)
            return _EXIT_ERROR
    parameters = DiscoveryParameters(
        lookback_hours=args.lookback_hours,
        min_sentiment=args.min_sentiment,
        min_articles=args.min_articles,
        max_assets=args.max_assets,
        ttl_hours=args.ttl_hours,
    )
    excluded = [token.strip().upper() for token in args.exclude_symbols.split(",") if token.strip()]
    themes = load_themes(args.themes)
    artifact = discover_assets(
        store,
        themes=themes,
        as_of=as_of,
        parameters=parameters,
        excluded_symbols=excluded,
    )
    if args.out.is_file():
        try:
            previous = json.loads(args.out.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            previous = {}
        if isinstance(previous, dict):
            artifact = carry_retired_candidates(
                artifact,
                previous,
                themes=themes,
                as_of=as_of,
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    active = sum(bool(candidate.get("active", True)) for candidate in artifact["candidates"])
    retired = len(artifact["candidates"]) - active
    print(f"wrote {active} active and {retired} exit-only news asset(s) to {args.out}")
    for candidate in artifact["candidates"]:
        print(
            f"  {candidate['symbol']:<6} {candidate['theme']:<24} "
            f"sentiment={candidate['sentiment']:+.3f} articles={candidate['article_count']}"
            f" {'ACTIVE' if candidate.get('active', True) else 'EXIT-ONLY'}"
        )
    return _EXIT_OK


def _dispatch(store: NewsStore, args: argparse.Namespace) -> int:
    if args.command == "ingest-marketaux":
        return _command_ingest_marketaux(store, args)
    if args.command == "ingest-adanos":
        return _command_ingest_adanos(store, args)
    if args.command == "coverage":
        return _command_coverage(store)
    if args.command == "export-scalars":
        return _command_export_scalars(store, args)
    if args.command == "discover-assets":
        return _command_discover_assets(store, args)
    return _command_knowable(store, args)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with NewsStore(args.db) as store:
        try:
            return _dispatch(store, args)
        except ProviderSuspendedError as suspended:
            print(str(suspended), file=sys.stderr)
            return _EXIT_ERROR
        except NewsdeskError as failure:
            print(str(failure), file=sys.stderr)
            return _EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
