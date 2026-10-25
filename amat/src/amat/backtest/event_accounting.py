"""Timestamp-ordered accounting events sharing the original cash/share equations."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

from amat.backtest.accounting import SessionAccountant
from amat.domain.actions import CorporateAction
from amat.domain.fills import Fill
from amat.domain.ledgers import LedgerSnapshot, PeriodAttribution
from amat.domain.types import Price, Symbol
from amat.errors import AccountingError


class EventAccountant(SessionAccountant):
    """Same session can have multiple events; no fake daily session IDs are used."""

    def __init__(self) -> None:
        super().__init__()
        self.last_timestamp: dt.datetime | None = None
        self.event_ids: set[str] = set()

    def advance_at(  # noqa: PLR0917 - shared accounting inputs plus event identity
        self,
        timestamp: dt.datetime,
        event_id: str,
        previous: LedgerSnapshot,
        session: dt.date,
        fills: Sequence[Fill],
        marks: Mapping[Symbol, Price],
        *,
        actions: Sequence[CorporateAction] = (),
    ) -> tuple[LedgerSnapshot, PeriodAttribution]:
        if timestamp.tzinfo is None or not event_id or event_id in self.event_ids:
            raise ValueError("invalid or duplicate accounting event")
        if self.last_timestamp is not None and timestamp < self.last_timestamp:
            raise ValueError("accounting event clock moved backwards")
        result = super().advance(previous, session, fills, marks, actions=actions)
        self.last_timestamp = timestamp
        self.event_ids.add(event_id)
        return result

    def _reconcile(
        self, previous: LedgerSnapshot, current: LedgerSnapshot, attribution: PeriodAttribution
    ) -> None:
        if current.session_date < previous.session_date:
            raise ValueError("accounting session moved backwards")
        residual = current.equity - previous.equity - attribution.explained_change
        if abs(residual) > self._tolerance:
            raise AccountingError(f"event attribution residual {residual}")
