"""Read-only scaffolding for additional prediction-market venues.

Nothing in this package can place an order, hold funds, or sign a transaction,
and that is the same line ``data_sources/polymarket/`` already draws. Reading a
second venue is a data question; trading one is a live-money decision needing
its own fee model, settlement handling, duplicate protection and circuit
breakers, and it is not made here.

Three pieces:

- ``base`` - one normalised market and book shape, so no caller downstream
  learns a venue name or a venue's price conventions.
- ``eligibility`` - a hard gate, refusing by default, deciding whether this
  operator may contact a venue at all. **Not legal advice**: it records
  publicly checkable regulatory facts and the operator's own declaration.
- ``adapters`` / ``registry`` - the venues themselves, and the single place the
  list of them is written down.
"""

from data_sources.venues.adapters import (
    KalshiVenue,
    PolymarketVenue,
    UnimplementedVenue,
)
from data_sources.venues.base import (
    PredictionVenue,
    VenueBook,
    VenueLevel,
    VenueMarket,
)
from data_sources.venues.eligibility import (
    Access,
    Eligibility,
    OperatorDeclaration,
    VenueStatus,
    check_eligibility,
)

__all__ = [
    "Access",
    "Eligibility",
    "KalshiVenue",
    "OperatorDeclaration",
    "PolymarketVenue",
    "PredictionVenue",
    "UnimplementedVenue",
    "VenueBook",
    "VenueLevel",
    "VenueMarket",
    "VenueStatus",
    "check_eligibility",
]
