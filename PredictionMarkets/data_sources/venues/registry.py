"""The one place the venue list is written down.

`SERIES_BY_ASSET` in `config.py` is the precedent and the reason: every copy of
a list like this that lived somewhere else went stale silently rather than
failing. So the catalogue is here, the eligibility gate reads it here, and
anything that wants to know which venues exist asks here.

**None of this is legal advice.** Each entry records publicly checkable
regulatory facts with citations and a review date; `eligibility.check_eligibility`
turns those plus the operator's own declaration into a yes or no, and it
refuses on anything it has not been told. An entry with `Access.UNKNOWN` is the
default and the safe state - it means nobody has verified this venue for that
jurisdiction, not that it is unavailable.

    uv run python -m data_sources.venues.registry --jurisdiction US-MI
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import Any

from data_sources.venues.eligibility import (
    Access,
    OperatorDeclaration,
    VenueStatus,
    check_eligibility,
)

# The date the entries below were last checked against primary sources. Bump it
# only when the check is actually repeated: `is_stale` turns this into a
# refusal after 90 days, which is the point.
REVIEWED_ON = date(2026, 8, 20)

MICHIGAN = "US-MI"


VENUES: dict[str, VenueStatus] = {
    "kalshi": VenueStatus(
        venue="kalshi",
        regulator="CFTC-designated contract market (DCM), designated 2020-11-03",
        access={MICHIGAN: Access.AVAILABLE},
        reviewed_on=REVIEWED_ON,
        api="authenticated",
        short_interval_crypto=True,
        notes=(
            "Michigan's action against Kalshi is SPORTS ONLY. Nessel v. KalshiEX "
            "is pleaded under the Lawful Sports Betting Act; the prayer for "
            "relief asks to abate an 'internet sports betting operation', and "
            "the complaint does not mention crypto, bitcoin or ethereum once. "
            "Sports contracts have been geofenced in MI since ~2026-08-12; "
            "non-sports markets are reported to have remained available. "
            "CAVEAT: Michigan's theory - that a wager is not exempt merely for "
            "being called an event contract - is not textually limited to "
            "sports, and CEA preemption is unsettled (3d Cir. for Kalshi with "
            "a dissent; 6th Cir. argued 2026-07-30 and undecided; several "
            "district courts against). No state has yet pleaded a CRYPTO event "
            "contract as gambling."
        ),
        citations=(
            "https://www.michigan.gov/ag/-/media/Project/Websites/AG/releases/2026/March/Kalshi-Complaint.pdf",
            "https://www.michigan.gov/mgcb/news/2026/06/30/mgcb-reacts-to-temporary-restraining-order-against-kalshiex",
            "https://www2.ca3.uscourts.gov/opinarch/251922p.pdf",
            "https://kalshi-public-docs.s3.amazonaws.com/contract_terms/CRYPTO15M.pdf",
        ),
    ),
    "polymarket": VenueStatus(
        venue="polymarket",
        regulator="not US-registered (offshore)",
        access={MICHIGAN: Access.BLOCKED},
        reviewed_on=REVIEWED_ON,
        api="public",
        short_interval_crypto=True,
        notes=(
            "The GLOBAL platform, which is what data_sources/polymarket/ reads. "
            "It has the 15-minute crypto series (btc-updown-15m-<ts>) but "
            "blocks the United States at country level - 'US - United States' "
            "is on its restricted list - and its terms prohibit VPN "
            "circumvention. Reading public market data is not trading, but no "
            "order path may ever be added here from a US jurisdiction."
        ),
        citations=(
            "https://help.polymarket.com/en/articles/13364163-geographic-restrictions",
            "https://polymarket.com/crypto/15M",
        ),
    ),
    "polymarket_us": VenueStatus(
        venue="polymarket_us",
        regulator="CFTC-designated contract market (QCX LLC), designated 2025-07-09",
        access={MICHIGAN: Access.UNKNOWN},
        reviewed_on=REVIEWED_ON,
        api="public",
        short_interval_crypto=False,
        notes=(
            "A DIFFERENT exchange from global Polymarket, with its own API and "
            "its own product set. Its crypto catalogue was enumerated live: the "
            "shortest horizon is about one MONTH (btc-100k-12-31-2026 and "
            "similar). There is no crypto tag and no 15-minute, hourly or even "
            "daily up/down series, so it cannot carry this strategy. Michigan "
            "is reported blocked by secondary sources but no primary state list "
            "was found, hence UNKNOWN. Notably it settles BTC on the same CF "
            "Benchmarks BRTI family Kalshi uses, so if it ever lists "
            "short-interval crypto the settlement model transfers."
        ),
        citations=(
            "https://docs.polymarket.us/api-reference/introduction.md",
            "https://www.prnewswire.com/news-releases/polymarket-acquires-cftc-licensed-exchange-and-clearinghouse-qcex-for-112-million-302509626.html",
        ),
    ),
    "robinhood": VenueStatus(
        venue="robinhood",
        regulator="futures commission merchant (FCM), not an exchange",
        access={MICHIGAN: Access.BLOCKED},
        reviewed_on=REVIEWED_ON,
        api="none",
        short_interval_crypto=True,
        notes=(
            "Lists 'BTC 15 min' markets, but they ARE Kalshi's KXBTC15M book: "
            "the contract terms on a Robinhood market page link to Kalshi's "
            "CRYPTO15M.pdf, and Robinhood Derivatives routes to KalshiEX, "
            "ForecastEx or Rothera. Same book, one more intermediary - no "
            "second venue and no cross-venue arbitrage. Its only documented API "
            "is for spot crypto; there is no event-contract API to call."
        ),
        citations=(
            "https://robinhood.com/us/en/support/articles/event-contracts-overview/",
            "https://robinhood.com/us/en/support/articles/crypto-api/",
        ),
    ),
    "coinbase": VenueStatus(
        venue="coinbase",
        regulator="introducing broker (Coinbase Financial Markets, FCM)",
        access={MICHIGAN: Access.BLOCKED},
        reviewed_on=REVIEWED_ON,
        api="none",
        short_interval_crypto=True,
        notes=(
            "Routes into Kalshi's own order book - the same market, not a "
            "second one. Its page embeds Kalshi tickers with a -KALSHI suffix "
            "and strikes match Kalshi's API to the last digit. No documented "
            "event-contract API (the Advanced Trade product_type enum is closed "
            "to SPOT/FUTURE/FUTURE_GROUP), plus an undisclosed broker markup on "
            "top of Kalshi's fee and a 2-hour batched capital cycle."
        ),
        citations=(
            "https://www.coinbase.com/blog/system-update-the-future-of-finance-is-on-coinbase",
            "https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/products/list-products.md",
        ),
    ),
    "crypto_com": VenueStatus(
        venue="crypto_com",
        regulator="CFTC DCM + DCO (CDNA, designated 2004-02-18 as HedgeStreet)",
        access={MICHIGAN: Access.UNKNOWN},
        reviewed_on=REVIEWED_ON,
        api="none",
        short_interval_crypto=False,
        notes=(
            "The genuine open question. CDNA certified a Bitcoin Event Contract "
            "in April 2026 settling to a U-BTC index computed once per second "
            "over a trailing 60 seconds - a cadence that would PERMIT intraday "
            "expiries - but the filing does not state a listing frequency and "
            "the catalogue is in-app. Probing the public instruments endpoint "
            "returned 942 instruments, all CCY_PAIR / PERPETUAL_SWAP / FUTURE "
            "and zero event contracts. It withdrew SPORTS contracts from "
            "Michigan voluntarily. Answering this needs a funded account."
        ),
        citations=(
            "https://www.cftc.gov/sites/default/files/filings/ptc/26/04/ptc04012642967.pdf",
            "https://sbcamericas.com/2025/12/16/cryptocom-sports-contracts-shutdown/",
        ),
    ),
    "forecastex": VenueStatus(
        venue="forecastex",
        regulator="CFTC DCM + DCO (ForecastEx, designated 2024-06-24); IBKR is the FCM",
        access={MICHIGAN: Access.AVAILABLE},
        reviewed_on=REVIEWED_ON,
        api="authenticated",
        short_interval_crypto=False,
        notes=(
            "Documented Web API and TWS API, and no Michigan-specific action "
            "found - but the product set is economic, climate and political. "
            "There is no crypto, so nothing here for this strategy. Structural "
            "quirk worth knowing: ForecastEx instruments cannot be sold; a "
            "position is flattened by buying the opposing contract."
        ),
        citations=(
            "https://www.interactivebrokers.com/campus/ibkr-api-page/event-contracts/",
            "https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations",
        ),
    ),
    "railbird_dkex": VenueStatus(
        venue="railbird_dkex",
        regulator="CFTC DCM (Railbird, designated 2025-06-13); now DraftKings-owned",
        access={MICHIGAN: Access.UNKNOWN},
        reviewed_on=REVIEWED_ON,
        api="none",
        short_interval_crypto=False,
        notes=(
            "Rebranded DKeX. The site is a shell with no browsable markets and "
            "no API documentation - docs.railbird.com and api.railbird.com do "
            "not resolve. Focus appears to be sports. Note its binaries pay "
            "$0/$100 rather than Kalshi's $0/$1, so any future adapter must not "
            "assume the Kalshi scale."
        ),
        citations=("https://www.railbirdexchange.com/",),
    ),
    "predictit": VenueStatus(
        venue="predictit",
        regulator="CFTC DCM (Aristotle Exchange, designated 2025-09-05)",
        access={MICHIGAN: Access.UNKNOWN},
        reviewed_on=REVIEWED_ON,
        api="public",
        short_interval_crypto=False,
        notes=(
            "Political markets only, and the JSON feed at /api/marketdata/all/ "
            "is undocumented, labelled non-commercial, and has no published "
            "rate limit and no order API. Nothing here for this strategy."
        ),
        citations=("https://www.predictit.org/api/marketdata/all/",),
    ),
}


def status_for(venue: str) -> VenueStatus:
    """The catalogued status, or a refusing placeholder for an unknown venue.

    An unknown name is not an error here - it is a venue nobody has researched,
    which is exactly the state the gate is built to refuse.
    """
    known = VENUES.get(venue)
    if known is not None:
        return known
    return VenueStatus(
        venue=venue,
        regulator="unknown",
        access={},
        reviewed_on=REVIEWED_ON,
        notes="not in the catalogue; nothing is known about this venue",
    )


def usable_venues(
    declaration: OperatorDeclaration, *, today: date | None = None
) -> list[str]:
    """Venues this operator may contact, cheapest possible answer."""
    moment = today or date.today()
    return [
        name
        for name, status in VENUES.items()
        if check_eligibility(status, declaration, today=moment).allowed
    ]


def report(declaration: OperatorDeclaration, *, today: date | None = None) -> str:
    moment = today or date.today()
    lines = [
        "",
        "=" * 78,
        f"PREDICTION VENUES for {declaration.jurisdiction or '(no jurisdiction)'}",
        "=" * 78,
        "Not legal advice. Regulatory facts with citations, plus your own",
        "declaration; the gate refuses anything it has not been told.",
        "",
        f"  {'venue':<14}{'15m crypto':<12}{'api':<15}{'access':<12}verdict",
        "  " + "-" * 72,
    ]
    for name, status in sorted(VENUES.items()):
        verdict = check_eligibility(status, declaration, today=moment)
        lines.append(
            f"  {name:<14}{'yes' if status.short_interval_crypto else 'no':<12}"
            f"{status.api:<15}{verdict.access.value:<12}"
            f"{'USABLE' if verdict.allowed else 'refused'}"
        )
    lines.append("")
    for name, status in sorted(VENUES.items()):
        verdict = check_eligibility(status, declaration, today=moment)
        if verdict.allowed:
            continue
        lines.append(f"  {name}: {verdict.reason}")
    lines.append("")
    return "\n".join(lines)


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jurisdiction", default=MICHIGAN)
    parser.add_argument(
        "--accept-contested",
        default="",
        help="comma-separated venue names whose contested status you accept",
    )
    args = parser.parse_args(argv)
    declaration = OperatorDeclaration(
        jurisdiction=args.jurisdiction,
        accept_contested=frozenset(
            name.strip() for name in args.accept_contested.split(",") if name.strip()
        ),
    )
    print(report(declaration))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
