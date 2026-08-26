"""Domain error hierarchy.

Every error carries enough identifying context (symbol, session, expected
invariant) to reproduce the failure. The project fails closed: ambiguous data,
missing execution bars, and unreconciled accounting raise rather than fall back
to an imputed value.
"""

from __future__ import annotations

__all__ = [
    "AccountingError",
    "AmatError",
    "CausalityError",
    "ConfigurationError",
    "DataIntegrityError",
    "ImmutableArtifactError",
    "UnavailableDataError",
]


class AmatError(Exception):
    """Base class for every error raised by this project."""


class ConfigurationError(AmatError):
    """Configuration is missing, malformed, or violates the strategy contract."""


class DataIntegrityError(AmatError):
    """A record violates a declared schema, calendar, or market-data invariant.

    Raised for impossible OHLC relationships, duplicate or non-session dates,
    negative volume, partial provider pagination, and unexplained coverage gaps.
    """


class CausalityError(AmatError):
    """An operation would use information not available at the decision timestamp.

    Raised when a feature window, covariance sample, corporate action, or fill
    would consume an observation at or after the execution session.
    """


class UnavailableDataError(AmatError):
    """Required data is absent and may not be imputed.

    A missing or zero-volume execution window raises this so the caller records
    a missed fill instead of substituting a prior close or a forward fill.
    """


class AccountingError(AmatError):
    """The ledger identity does not reconcile within declared tolerance.

    ``equity == cash + sum(quantity * mark)`` must hold every session, and every
    position change must trace to a fill or a corporate action.
    """


class ImmutableArtifactError(AmatError):
    """A write was attempted against immutable evidence.

    Raw vendor snapshots and validated outputs are append-only and
    checksum-addressed. New results create a new versioned artifact.
    """
