"""The immutable market panel a backtest reads.

One object carries every series the strategy needs, with a single shared session
axis and a single shared symbol order. Keeping them together is what makes the
contract's three-way price split auditable:

* ``adjusted_closes`` -- research features only;
* ``raw_closes`` -- valuation marks only;
* ``window_vwaps`` / ``window_volumes`` -- simulated execution only.

A caller cannot accidentally value a position at an adjusted price, or form a
signal from a raw one, without saying so explicitly.

Context inputs
--------------
``adjusted_highs``, ``adjusted_lows`` and ``session_volumes`` are optional and
exist for :mod:`amat.context`, which computes trailing support/resistance and
volume state. They are optional because every runner built before them
constructs a panel without them, and because a synthetic or execution-only panel
has no honest values to supply. :meth:`MarketPanel.context_inputs` fails closed
rather than substituting closes for highs, so a caller asking for levels on a
panel that has none gets an error instead of a support level that is really just
the lowest close.

``adjusted_opens`` is independently optional, for candle research.
:meth:`MarketPanel.candle_inputs` requires actual opens, highs and lows and
retains unavailable cells for strict propagation by the pattern evaluator.

These prices are on the **adjusted** basis, and that is a correctness requirement rather
than a preference. Levels are compared against ``adjusted_closes`` -- the series
the trend signal uses -- so a raw high and an adjusted close would be
incommensurable, and the comparison would drift further apart every year on a
high-distribution instrument. AGG's adjustment factor over this sample is 0.745:
its raw high sits 34% above its adjusted close for no market reason at all. A
split is the sharper version of the same problem, where every trailing level
would appear to break at once on the ex-date.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np

from amat.domain.bars import ExecutionWindowBar
from amat.domain.types import Price, SessionDate, Symbol
from amat.errors import DataIntegrityError

__all__ = ["MarketPanel"]

#: ``adjusted_highs``, ``adjusted_lows``, ``session_volumes`` -- supplied together.
_CONTEXT_FIELDS = 3


@dataclass(frozen=True, slots=True)
class MarketPanel:
    """Aligned market series for one universe over one session range.

    Attributes:
        sessions: Strictly increasing exchange session dates, length ``T``.
        symbols: Column order, length ``N``. Matches the universe declaration.
        adjusted_closes: ``(T, N)`` fully adjusted closes, for features only.
        raw_closes: ``(T, N)`` raw closes, for valuation only.
        window_vwaps: ``(T, N)`` raw VWAP of each session's execution window.
        window_volumes: ``(T, N)`` shares traded in that window. Zero means the
            window cannot fill and a missed fill must be recorded.
        source: Provider identifier propagated into the run manifest.
        adjusted_highs: Optional ``(T, N)`` session highs on the adjusted basis.
        adjusted_lows: Optional ``(T, N)`` session lows on the adjusted basis.
        adjusted_opens: Optional ``(T, N)`` session opens on the adjusted basis.
            Independent of context inputs; absent opens disable candle evaluation.
        session_volumes: Optional ``(T, N)`` full-session share volume. Distinct
            from ``window_volumes``, which covers only the execution window.
    """

    sessions: tuple[SessionDate, ...]
    symbols: tuple[Symbol, ...]
    adjusted_closes: np.ndarray
    raw_closes: np.ndarray
    window_vwaps: np.ndarray
    window_volumes: np.ndarray
    source: str
    adjusted_highs: np.ndarray | None = None
    adjusted_lows: np.ndarray | None = None
    session_volumes: np.ndarray | None = None
    adjusted_opens: np.ndarray | None = None

    def __post_init__(self) -> None:
        shape = (len(self.sessions), len(self.symbols))
        for name in ("adjusted_closes", "raw_closes", "window_vwaps", "window_volumes"):
            array = getattr(self, name)
            if array.shape != shape:
                message = f"{name} has shape {array.shape}, expected {shape}"
                raise DataIntegrityError(message)
        for name in ("adjusted_highs", "adjusted_lows", "session_volumes", "adjusted_opens"):
            array = getattr(self, name)
            if array is not None and array.shape != shape:
                message = f"{name} has shape {array.shape}, expected {shape}"
                raise DataIntegrityError(message)
        # All three or none. A panel with highs but no lows would let
        # `resistance_levels` succeed while `support_levels` silently returned
        # nothing, and the asymmetry would read as "this instrument has no
        # support" rather than "this panel was built wrong".
        supplied = [
            name
            for name in ("adjusted_highs", "adjusted_lows", "session_volumes")
            if getattr(self, name) is not None
        ]
        if supplied and len(supplied) != _CONTEXT_FIELDS:
            message = (
                f"context inputs are all-or-nothing; got only {supplied}. "
                "Supply adjusted_highs, adjusted_lows and session_volumes together."
            )
            raise DataIntegrityError(message)
        if any(
            later <= earlier
            for earlier, later in zip(self.sessions, self.sessions[1:], strict=False)
        ):
            message = "sessions must be strictly increasing with no duplicates"
            raise DataIntegrityError(message)
        if len(set(self.symbols)) != len(self.symbols):
            message = "symbols must be unique"
            raise DataIntegrityError(message)
        if not self.source:
            message = "panel must record its data source"
            raise DataIntegrityError(message)

    def __len__(self) -> int:
        return len(self.sessions)

    def marks_at(self, index: int) -> Mapping[Symbol, Price]:
        """Raw closing marks for valuation at ``index``.

        A non-finite close is omitted rather than imputed, so a holding with no
        valid mark surfaces as an accounting failure instead of being silently
        carried at a stale price.
        """
        row = self.raw_closes[index]
        return {
            symbol: _to_price(value)
            for symbol, value in zip(self.symbols, row, strict=True)
            if np.isfinite(value) and value > 0
        }

    def windows_at(self, index: int) -> Mapping[Symbol, ExecutionWindowBar]:
        """Execution windows available at ``index``.

        A symbol with no finite VWAP is absent from the mapping, which the
        execution model records as ``NO_EXECUTION_BAR``. A symbol with a valid
        VWAP but zero volume is present and untradable, which it records as
        ``ZERO_VOLUME`` -- the two are deliberately distinguishable.
        """
        session = self.sessions[index]
        # Label the window in the exchange's own clock so the UTC instants are
        # right in both EST and EDT months. The prices come from the stored
        # VWAPs either way; only the label was previously wrong (a hardcoded
        # 14:30 UTC is 09:30 New York only in winter).
        zone = ZoneInfo("America/New_York")
        start = dt.datetime.combine(session, dt.time(9, 30), tzinfo=zone).astimezone(dt.UTC)
        end = dt.datetime.combine(session, dt.time(9, 35), tzinfo=zone).astimezone(dt.UTC)

        windows: dict[Symbol, ExecutionWindowBar] = {}
        for column, symbol in enumerate(self.symbols):
            vwap = self.window_vwaps[index, column]
            volume = self.window_volumes[index, column]
            if not np.isfinite(vwap) or vwap <= 0 or not np.isfinite(volume) or volume < 0:
                continue
            windows[symbol] = ExecutionWindowBar(
                symbol=symbol,
                session_date=session,
                window_start=start,
                window_end=end,
                vwap=_to_price(vwap),
                volume=Decimal(str(int(volume))),
                source=self.source,
            )
        return windows

    @property
    def has_context_inputs(self) -> bool:
        """Whether trailing levels and volume state can be computed."""
        return self.adjusted_highs is not None

    def context_inputs(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """``(closes, highs, lows, volumes)`` for :mod:`amat.context`.

        Raises:
            DataIntegrityError: if the panel carries no highs, lows, or volumes.
                Failing here is the point. The tempting fallback -- pass closes
                in place of highs and lows -- produces a "support level" that is
                the lowest *close*, which is a different and usually higher
                number than the lowest low. It would compute, it would look
                plausible, and every level it reported would be wrong.
        """
        if self.adjusted_highs is None or self.adjusted_lows is None:
            message = (
                "panel carries no session highs or lows, so trailing levels cannot be "
                "computed; load it with load_panel(..., with_context=True)"
            )
            raise DataIntegrityError(message)
        if self.session_volumes is None:  # pragma: no cover - blocked by __post_init__
            message = "panel carries no session volumes"
            raise DataIntegrityError(message)
        return self.adjusted_closes, self.adjusted_highs, self.adjusted_lows, self.session_volumes

    @property
    def has_candle_inputs(self) -> bool:
        """Whether adjusted OHLC series exist (individual cells may be unavailable)."""
        return (
            self.adjusted_opens is not None
            and self.adjusted_highs is not None
            and self.adjusted_lows is not None
        )

    def candle_inputs(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return ``(opens, highs, lows, closes)`` on one adjusted price basis.

        Missing series fail closed. As with context inputs, nonfinite cells are
        retained as unavailable observations for the evaluator to propagate;
        they are never imputed from another field or another session.
        """
        if self.adjusted_opens is None or self.adjusted_highs is None or self.adjusted_lows is None:
            message = (
                "panel carries no complete adjusted OHLC inputs; load it with "
                "load_panel(..., with_context=True) from a snapshot containing raw_open"
            )
            raise DataIntegrityError(message)
        return self.adjusted_opens, self.adjusted_highs, self.adjusted_lows, self.adjusted_closes

    def slice_symbols(self, symbols: Sequence[Symbol]) -> MarketPanel:
        """Restrict to ``symbols``, preserving their given order."""
        index_of = {symbol: column for column, symbol in enumerate(self.symbols)}
        missing = [symbol for symbol in symbols if symbol not in index_of]
        if missing:
            message = f"panel is missing symbols: {', '.join(missing)}"
            raise DataIntegrityError(message)
        columns = [index_of[symbol] for symbol in symbols]

        def narrow(array: np.ndarray | None) -> np.ndarray | None:
            return None if array is None else array[:, columns]

        return MarketPanel(
            sessions=self.sessions,
            symbols=tuple(symbols),
            adjusted_closes=self.adjusted_closes[:, columns],
            raw_closes=self.raw_closes[:, columns],
            window_vwaps=self.window_vwaps[:, columns],
            window_volumes=self.window_volumes[:, columns],
            source=self.source,
            adjusted_highs=narrow(self.adjusted_highs),
            adjusted_lows=narrow(self.adjusted_lows),
            session_volumes=narrow(self.session_volumes),
            adjusted_opens=narrow(self.adjusted_opens),
        )


def _to_price(value: float) -> Price:
    """Convert a float series entry to an exact price at cent precision.

    This is the one-directional float boundary declared in
    :mod:`amat.domain.types`: statistical series are float, and they become
    exact exactly once, here, on the way into valuation and execution.
    """
    return Decimal(str(round(float(value), 6)))
