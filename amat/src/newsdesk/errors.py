"""Failure modes, named so a caller can tell them apart.

Deliberately mirrors ``amat.errors`` in shape without importing it: the two
packages stay independent, and a shared exception base would be an import edge
between them.
"""

from __future__ import annotations

__all__ = [
    "ConfigurationError",
    "IngestionIncompleteError",
    "NewsdeskError",
    "ProviderSuspendedError",
    "StoreIntegrityError",
    "UntrustedContentError",
    "VendorResponseError",
]


class NewsdeskError(Exception):
    """Base for every error this package raises."""


class ConfigurationError(NewsdeskError):
    """A credential, path, or setting is missing or unusable.

    Messages name environment *variables*, never their values.
    """


class VendorResponseError(NewsdeskError):
    """A vendor returned something that cannot be trusted as a complete answer."""


class IngestionIncompleteError(VendorResponseError):
    """Pagination ended before coverage was proven complete.

    Separate from the general vendor error because it is the one failure that
    looks like success: a short page reads as "no more news" rather than "the
    request failed", and a feature series silently missing a day is worse than
    one that is absent.
    """


class ProviderSuspendedError(NewsdeskError):
    """A provider has failed too many consecutive runs to keep trying.

    Both vendors meter by quota -- Marketaux by the day, Adanos by the month --
    so repeating a run that fails for a *standing* reason (revoked key, wrong
    tier, exhausted plan) burns the allowance that a fixed configuration would
    have used. Retrying cannot fix a 401. The breaker trips after
    :data:`newsdesk.ingest.SUSPENSION_THRESHOLD` consecutive ``failed`` runs
    and stays tripped until a human passes ``--force`` or a run succeeds.
    """


class StoreIntegrityError(NewsdeskError):
    """The database rejected a write, or its contents contradict an invariant."""


class UntrustedContentError(NewsdeskError):
    """Vendor content violated a size, encoding, or shape limit."""
