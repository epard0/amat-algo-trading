"""Daily market-data records and their integrity invariants.

A :class:`DailyBar` carries both raw and adjusted closes because the strategy
contract splits their use:

* research features use the fully adjusted close;
* execution uses raw intraday prices;
* valuation uses the raw close plus an explicit corporate-action ledger.

Keeping both on one record makes the split auditable and prevents a caller from
silently reaching for whichever series happens to be in scope.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from amat.domain.types import Price, SessionDate, Symbol, parse_symbol
from amat.errors import DataIntegrityError

__all__ = ["AdjustmentMode", "DailyBar", "ExecutionWindowBar"]

AdjustmentMode = str
"""Vendor adjustment mode recorded verbatim, e.g. ``"split_and_dividend"``.

Stored as text rather than an enum so an unrecognized vendor value surfaces in
the manifest instead of being coerced into the nearest known option.
"""


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One completed daily session for one instrument.

    Attributes:
        symbol: Normalized ticker.
        session_date: The exchange trading date this bar summarizes.
        raw_open, raw_high, raw_low, raw_close: Unadjusted prices, used for
            execution and valuation.
        adjusted_close: Fully adjusted close, used only for research features.
        volume: Session share volume. Zero is legal and meaningful: it marks a
            window that cannot produce a fill.
        source: Provider identifier, propagated into the run manifest.
        adjustment_mode: Vendor adjustment mode for ``adjusted_close``.

    Raises:
        DataIntegrityError: on any impossible price or volume relationship.
    """

    symbol: Symbol
    session_date: SessionDate
    raw_open: Price
    raw_high: Price
    raw_low: Price
    raw_close: Price
    adjusted_close: Price
    volume: Decimal
    source: str
    adjustment_mode: AdjustmentMode

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", parse_symbol(self.symbol))
        self._require_positive_prices()
        self._require_consistent_range()
        self._require_valid_volume()
        if not self.source:
            message = f"{self.symbol} {self.session_date.isoformat()}: missing data source"
            raise DataIntegrityError(message)

    def _require_positive_prices(self) -> None:
        fields = (
            ("raw_open", self.raw_open),
            ("raw_high", self.raw_high),
            ("raw_low", self.raw_low),
            ("raw_close", self.raw_close),
            ("adjusted_close", self.adjusted_close),
        )
        for name, value in fields:
            if not isinstance(value, Decimal):
                message = (
                    f"{self.symbol} {self.session_date.isoformat()}: "
                    f"{name} must be Decimal, received {type(value).__name__}"
                )
                raise DataIntegrityError(message)
            if not value.is_finite() or value <= 0:
                message = (
                    f"{self.symbol} {self.session_date.isoformat()}: "
                    f"{name} must be finite and positive, received {value}"
                )
                raise DataIntegrityError(message)

    def _require_valid_volume(self) -> None:
        """Apply to volume the same type and finiteness checks the prices get.

        Finiteness is checked before the sign comparison: ``Decimal("NaN") < 0``
        raises ``InvalidOperation``, which would escape the ``AmatError``
        hierarchy, and ``float("nan") < 0`` is ``False``, which would let a
        corrupt row through as a permanently untradable session.
        """
        label = f"{self.symbol} {self.session_date.isoformat()}"
        if not isinstance(self.volume, Decimal):
            message = f"{label}: volume must be Decimal, received {type(self.volume).__name__}"
            raise DataIntegrityError(message)
        if not self.volume.is_finite():
            message = f"{label}: volume must be finite, received {self.volume}"
            raise DataIntegrityError(message)
        if self.volume < 0:
            message = f"{label}: negative volume {self.volume}"
            raise DataIntegrityError(message)

    def _require_consistent_range(self) -> None:
        highest = max(self.raw_open, self.raw_close, self.raw_low)
        lowest = min(self.raw_open, self.raw_close, self.raw_high)
        if self.raw_high < highest:
            message = (
                f"{self.symbol} {self.session_date.isoformat()}: "
                f"high {self.raw_high} is below open/close/low maximum {highest}"
            )
            raise DataIntegrityError(message)
        if self.raw_low > lowest:
            message = (
                f"{self.symbol} {self.session_date.isoformat()}: "
                f"low {self.raw_low} is above open/close/high minimum {lowest}"
            )
            raise DataIntegrityError(message)

    @property
    def is_tradable(self) -> bool:
        """Whether this session could support a simulated fill.

        A zero-volume session is not tradable. Callers must record a missed
        fill rather than substituting a neighbouring price.

        Note this is a *session*-level property. The execution model must gate
        on :attr:`ExecutionWindowBar.is_tradable` instead, because a symbol can
        trade zero shares inside the execution window while the whole session
        shows non-zero volume.
        """
        return self.volume > 0


@dataclass(frozen=True, slots=True)
class ExecutionWindowBar:
    """Aggregated raw trading inside one intraday execution window.

    The strategy contract prices fills at the raw-price VWAP of the configured
    opening window. That quantity is not derivable from a daily OHLC bar under
    any reading, so it is carried on its own record. Without this, the frozen
    ``price_method = "vwap"`` in the strategy configuration would be
    unsatisfiable and an implementer would silently fall back to the session
    open -- which the validation protocol classifies as a robustness
    *sensitivity*, not the canonical fill.

    Attributes:
        symbol: Normalized ticker.
        session_date: Exchange trading date containing the window.
        window_start, window_end: Timezone-aware UTC instants bounding the
            window. Stored as UTC instants rather than local times so that a
            daylight-saving transition cannot silently move the window.
        vwap: Volume-weighted average raw price traded inside the window.
        volume: Shares traded inside the window. Zero is legal and means the
            window cannot produce a fill.
        source: Provider identifier, propagated into the run manifest.

    Raises:
        DataIntegrityError: on a non-positive or non-finite VWAP, an invalid
            volume, a non-UTC or misordered window, or a missing source.
    """

    symbol: Symbol
    session_date: SessionDate
    window_start: dt.datetime
    window_end: dt.datetime
    vwap: Price
    volume: Decimal
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", parse_symbol(self.symbol))
        label = f"{self.symbol} {self.session_date.isoformat()}"

        for name, value in (("vwap", self.vwap), ("volume", self.volume)):
            if not isinstance(value, Decimal):
                message = f"{label}: {name} must be Decimal, received {type(value).__name__}"
                raise DataIntegrityError(message)
            if not value.is_finite():
                message = f"{label}: {name} must be finite, received {value}"
                raise DataIntegrityError(message)

        if self.vwap <= 0:
            message = f"{label}: vwap must be strictly positive, received {self.vwap}"
            raise DataIntegrityError(message)
        if self.volume < 0:
            message = f"{label}: negative window volume {self.volume}"
            raise DataIntegrityError(message)

        for name, instant in (
            ("window_start", self.window_start),
            ("window_end", self.window_end),
        ):
            if instant.tzinfo is None or instant.utcoffset() != dt.timedelta(0):
                message = f"{label}: {name} must be a timezone-aware UTC datetime"
                raise DataIntegrityError(message)
        if self.window_end <= self.window_start:
            message = (
                f"{label}: window_end {self.window_end.isoformat()} must be after "
                f"window_start {self.window_start.isoformat()}"
            )
            raise DataIntegrityError(message)

        if not self.source:
            message = f"{label}: missing data source"
            raise DataIntegrityError(message)

    @property
    def is_tradable(self) -> bool:
        """Whether this window can support a simulated fill.

        A zero-volume window records a missed fill. A neighbouring price is
        never substituted.
        """
        return self.volume > 0
