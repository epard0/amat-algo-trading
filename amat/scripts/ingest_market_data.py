"""Pull one immutable market-data snapshot.

Usage::

    uv run python scripts/ingest_market_data.py --snapshot-id alpaca-sip-2026-08-08

Requires ``ALPACA_MARKET_API_KEY`` and ``ALPACA_MARKET_API_SECRET`` in the
environment. Credential values are never printed or written anywhere.

This is the only script in the repository that touches the network. It reads
historical bars and corporate actions; it has no other capability.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from amat.data import AlpacaMarketData, IngestionPlan, ingest_snapshot  # noqa: E402
from amat.errors import AmatError  # noqa: E402

#: The expanded research universe, grouped the way the strategy clusters it.
#: Every symbol has continuous SIP coverage from 2016-01-04, which is what makes
#: a complete-row covariance estimate possible over the whole sample.
UNIVERSE: tuple[str, ...] = (
    # US broad equity
    "SPY",
    "QQQ",
    "IWM",
    "VTI",
    # US sectors
    "XLK",
    "XLF",
    "XLE",
    "XLV",
    "XLI",
    # International equity
    "EFA",
    "EEM",
    "VEA",
    "VWO",
    "EWJ",
    "FXI",
    # Rates and credit
    "TLT",
    "IEF",
    "SHY",
    "AGG",
    "LQD",
    "HYG",
    "TIP",
    "EMB",
    # Metals
    "GLD",
    "SLV",
    # Commodities
    "DBC",
    "USO",
    # Real estate
    "VNQ",
    "IYR",
    # US dollar
    "UUP",
)

#: The wide research universe, added 2026-08-20 for the cross-sectional
#: selection work (registered experiment 0006).
#:
#: INCLUSION RULE, fixed before any return was measured: a candidate is in if
#: and only if Alpaca SIP returns continuous daily bars from 2016-01-04, the
#: same test that produced ``UNIVERSE``. 65 candidates were probed and 64
#: passed; IGSB returned nothing for January 2016 and is therefore absent. No
#: symbol was included or excluded on the basis of its performance, because the
#: whole point of widening is to stop choosing which assets to hold.
#:
#: The candidates were chosen for *factor* coverage rather than count -- the
#: remaining US sectors, size/style/factor tilts, single-country international,
#: the rates and credit ladder, real assets, and G10 currencies. That selection
#: is a judgement about what an ETF *holds*, made from fund structure and never
#: from returns, which is what keeps it clear of hindsight.
WIDE_UNIVERSE: tuple[str, ...] = (
    *UNIVERSE,
    # Remaining US sectors (XLC excluded: launched June 2018, fails the rule)
    "XLP",
    "XLY",
    "XLU",
    "XLB",
    "XLRE",
    # Size, style, and factor tilts
    "IJH",
    "IJR",
    "MDY",
    "IWD",
    "IWF",
    "MTUM",
    "QUAL",
    "USMV",
    "VLUE",
    "SPLV",
    "RSP",
    # Single-country and regional international
    "EWG",
    "EWU",
    "EWY",
    "EWT",
    "EWZ",
    "EWC",
    "EWA",
    "EWH",
    "EWW",
    "INDA",
    "IEV",
    "EZU",
    "ACWI",
    "IEFA",
    "IEMG",
    # Rates and credit ladder
    "IEI",
    "TLH",
    "SHV",
    "BIL",
    "BND",
    "BNDX",
    "MBB",
    "JNK",
    "VCIT",
    "VCSH",
    "MUB",
    "PFF",
    "STIP",
    # Real assets
    "GDX",
    "GDXJ",
    "XOP",
    "XME",
    "DBA",
    "DBB",
    "UNG",
    "PPLT",
    "SLX",
    "WOOD",
    # G10 currencies
    "FXE",
    "FXY",
    "FXB",
    "FXF",
    "FXA",
    "FXC",
    # Real estate breadth
    "REM",
    "SCHH",
)

_UNIVERSES: dict[str, tuple[str, ...]] = {
    "core": UNIVERSE,
    "wide": WIDE_UNIVERSE,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument(
        "--universe",
        choices=sorted(_UNIVERSES),
        default="core",
        help=(
            "Which declared symbol list to pull. `core` is the 30-symbol list every "
            "snapshot before 2026-08-20 used and is the default so that re-running this "
            "script reproduces them; `wide` is the 92-symbol list for experiment 0006."
        ),
    )
    parser.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2016, 1, 4))
    parser.add_argument(
        "--end",
        type=dt.date.fromisoformat,
        default=dt.date.today(),  # noqa: DTZ011 - exclusive bound; today's session is not complete
        help="Exclusive upper bound. The current session is never ingested.",
    )
    parser.add_argument("--feed", default="sip")
    args = parser.parse_args()

    symbols = _UNIVERSES[args.universe]
    plan = IngestionPlan(symbols, args.start, args.end)
    client = AlpacaMarketData(feed=args.feed)

    def say(message: str) -> None:
        print(f"[{dt.datetime.now(tz=dt.UTC):%H:%M:%S}] {message}", flush=True)

    try:
        directory, manifest = ingest_snapshot(
            client,
            plan,
            root=ROOT,
            snapshot_id=args.snapshot_id,
            feed=args.feed,
            checkpoint_path=ROOT / "data" / "interim" / f"{args.snapshot_id}.windows.json",
            progress=say,
        )
    except AmatError as exc:
        print(f"ingestion failed: {exc}", file=sys.stderr)
        return 1

    say(f"wrote {directory.relative_to(ROOT)}")
    say(
        f"{manifest.session_count} sessions, {manifest.first_session}..{manifest.last_session}, "
        f"{len(manifest.symbols)} symbols, window coverage {manifest.window_coverage:.4f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
