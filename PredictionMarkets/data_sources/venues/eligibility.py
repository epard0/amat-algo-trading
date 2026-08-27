"""Whether this operator may use a venue at all, decided before any request.

**This module is not legal advice and does not contain a legal opinion.** It
encodes two things and nothing else: publicly checkable *regulatory facts* about
each venue, and the operator's own explicit attestation about their
jurisdiction. It refuses by default. Where the law is unsettled it says so and
still refuses, because "contested" is not "permitted".

The design rule is that a wrong answer must fail closed. An unknown venue, an
unknown jurisdiction, a venue whose status has not been reviewed since a stated
date - all refuse. The only way to reach a venue is for the operator to declare
a jurisdiction and for that venue to be recorded as serving it.

Why this is a module rather than a comment: Michigan matters specifically. The
Michigan Gaming Control Board has taken enforcement positions against event
contract operators, and CFTC-designated contract markets contend the Commodity
Exchange Act preempts state gaming law. That dispute is live. A bot that
silently opens an account somewhere on the losing side of it is a problem its
author will discover late, so the refusal is in code and on by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Access(str, Enum):
    """How settled this venue's availability is for a given jurisdiction."""

    AVAILABLE = "available"
    """Documented as serving this jurisdiction, with no known active dispute."""

    CONTESTED = "contested"
    """Operating, but a regulator in this jurisdiction disputes its right to.

    Treated as REFUSED unless the operator opts in explicitly and per venue.
    An unresolved dispute is a risk the operator takes knowingly or not at all.
    """

    BLOCKED = "blocked"
    """The venue does not serve this jurisdiction, or has been ordered not to."""

    UNKNOWN = "unknown"
    """Not researched, or researched and unresolved. Refuses."""


@dataclass(frozen=True)
class VenueStatus:
    """What is publicly known about one venue's standing.

    ``reviewed_on`` is load-bearing. Every field here describes a fast-moving
    regulatory situation, and a stale record that still reads AVAILABLE is
    worse than no record: it is a refusal that stopped happening without anyone
    deciding it should. ``is_stale`` is what callers check.
    """

    venue: str
    regulator: str
    """e.g. 'CFTC-designated contract market (DCM)', 'unregistered', 'broker'."""

    access: dict[str, Access]
    """Jurisdiction code -> access. 'US-MI' is the one this repo cares about."""

    reviewed_on: date
    notes: str = ""
    citations: tuple[str, ...] = ()
    api: str = "none"
    """'public', 'authenticated', 'none' - market data, not orders."""

    short_interval_crypto: bool = False
    """Whether it lists 15-minute-or-shorter crypto up/down contracts."""

    def access_for(self, jurisdiction: str) -> Access:
        return self.access.get(jurisdiction, Access.UNKNOWN)

    def is_stale(self, today: date, *, max_age_days: int = 90) -> bool:
        return (today - self.reviewed_on).days > max_age_days


@dataclass(frozen=True)
class Eligibility:
    """The answer, and the reason for it. Always both."""

    allowed: bool
    venue: str
    jurisdiction: str
    access: Access
    reason: str

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.allowed


@dataclass(frozen=True)
class OperatorDeclaration:
    """What the operator states about themselves. Not inferred, ever.

    Nothing here is derived from an IP address, a locale or a timezone. A
    jurisdiction guessed from the network is wrong exactly when it matters -
    on a VPN, while travelling - and guessing it would replace a decision the
    operator must make with one this code is not entitled to make.
    """

    jurisdiction: str
    """ISO-ish code, e.g. 'US-MI'."""

    accept_contested: frozenset[str] = field(default_factory=frozenset)
    """Venues whose CONTESTED status the operator accepts, named one by one.

    A blanket 'accept all contested' flag is deliberately not offered: the
    point of the setting is that each is a separate, considered risk.
    """


def check_eligibility(
    status: VenueStatus,
    declaration: OperatorDeclaration,
    *,
    today: date,
    max_age_days: int = 90,
) -> Eligibility:
    """Decide, conservatively, whether this venue may be contacted.

    Order matters: staleness is checked BEFORE access, so a record that has
    aged out cannot pass on the strength of a review nobody has repeated.
    """
    jurisdiction = declaration.jurisdiction
    if not jurisdiction:
        return Eligibility(
            False,
            status.venue,
            jurisdiction,
            Access.UNKNOWN,
            "no jurisdiction declared; this is not inferred from the network",
        )

    if status.is_stale(today, max_age_days=max_age_days):
        age = (today - status.reviewed_on).days
        return Eligibility(
            False,
            status.venue,
            jurisdiction,
            Access.UNKNOWN,
            f"venue status last reviewed {age} days ago (limit {max_age_days}); "
            "re-verify before relying on it. A stale record that still reads "
            "'available' is a refusal that stopped happening silently.",
        )

    access = status.access_for(jurisdiction)
    if access is Access.AVAILABLE:
        return Eligibility(True, status.venue, jurisdiction, access, "available")
    if access is Access.BLOCKED:
        return Eligibility(
            False,
            status.venue,
            jurisdiction,
            access,
            f"does not serve {jurisdiction}: {status.notes or 'blocked'}",
        )
    if access is Access.CONTESTED:
        if status.venue in declaration.accept_contested:
            return Eligibility(
                True,
                status.venue,
                jurisdiction,
                access,
                "contested in this jurisdiction; the operator has accepted "
                f"that risk explicitly for {status.venue}",
            )
        return Eligibility(
            False,
            status.venue,
            jurisdiction,
            access,
            f"a regulator in {jurisdiction} disputes this venue's right to "
            f"operate. {status.notes} Not permitted unless named in "
            "accept_contested.",
        )
    return Eligibility(
        False,
        status.venue,
        jurisdiction,
        Access.UNKNOWN,
        f"no researched status for {jurisdiction}; refusing by default",
    )
