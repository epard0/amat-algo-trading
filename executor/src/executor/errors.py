"""Executor failure modes.

Every error here is a *refusal to act*. The executor's default on ambiguity is
to submit nothing and say why: an unplaced trade costs a day of tracking error,
a wrong trade costs money and takes a human to unwind.
"""

from __future__ import annotations

__all__ = [
    "BrokerError",
    "ConfigurationError",
    "ExecutorError",
    "PlanError",
    "ReconciliationError",
]


class ExecutorError(Exception):
    """Base class for every executor failure."""


class ConfigurationError(ExecutorError):
    """Missing or invalid configuration: credentials, paths, SMTP settings."""


class PlanError(ExecutorError):
    """The plan could not be built, parsed, or is not valid for today."""


class BrokerError(ExecutorError):
    """The broker rejected a request, or could not be reached."""


class OrderOutcomeUnknownError(BrokerError):
    """A POST may have succeeded. Persist uncertainty and stop further orders."""


class ReconciliationError(ExecutorError):
    """Broker state and strategy state disagree in a way a human must resolve."""


class BatchPendingError(ExecutorError):
    """A staged batch is waiting for broker fills, not a failed phase attempt."""
