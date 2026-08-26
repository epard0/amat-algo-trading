"""The three review passes and the report they produce.

A pass reads completed market data and the account's current holdings, and
returns a frozen :class:`ReviewReport`. Nothing here decides anything: the
target weights it displays come from :func:`amat.portfolio.decide`, the same
function the backtest calls, so a review can never disagree with the strategy
about what the strategy wants.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.config.models import StrategyConfig
from amat.context import build_context_panels, resistance_levels, support_levels
from amat.errors import DataIntegrityError
from amat.portfolio import decide
from deskreview.metrics import LevelBreak, PullbackState, detect_level_breaks, pullback_state

__all__ = ["BandState", "HoldingObservation", "ReviewReport", "build_review"]

PassName = Literal["morning", "midday", "evening"]

#: Trailing window for support/resistance, in sessions. Matches the default in
#: :func:`amat.context.build_context_panels` so a review and a feature computed
#: elsewhere refer to the same level rather than two different 60-ish numbers.
LEVEL_WINDOW = 63

#: Lookback for the pullback description.
PULLBACK_WINDOW = 21


@dataclass(frozen=True, slots=True)
class BandState:
    """One instrument's position relative to the two bands that actually exist.

    Attributes:
        symbol: Instrument.
        target_weight: What the allocator wants, from the last completed close.
        current_weight: What the account holds now.
        gap: ``target_weight - current_weight``.
        band: The configured ``rebalance.min_weight_delta``.
        band_fraction: ``abs(gap) / band``. At or above 1.0 a trade fires.
        would_trade: Whether the gap has reached the band.
        held: Whether the account currently holds the instrument.
        wanted: Whether the allocator wants a non-zero weight.
    """

    symbol: str
    target_weight: float
    current_weight: float
    band: float

    @property
    def gap(self) -> float:
        return self.target_weight - self.current_weight

    @property
    def band_fraction(self) -> float:
        return abs(self.gap) / self.band if self.band > 0 else 0.0

    @property
    def is_transition(self) -> bool:
        """Whether this is a zero to non-zero crossing, in either direction."""
        return (self.target_weight > 0) != (self.current_weight > 0)

    @property
    def would_trade(self) -> bool:
        """Whether :func:`amat.execution.build_instructions` would emit an order.

        This mirrors that function's rule exactly, which is the whole reason the
        property exists::

            if abs(delta_weight) < rebalance.min_weight_delta and not transition:
                continue

        so a trade fires when the gap reaches the band **or** when the position
        crosses zero in either direction, however small the crossing is. An
        earlier version tested only the band, and on live data it reported "no
        trade" for a 1.33% entry into USO -- a transition the runner would have
        executed. A review that disagrees with the runner about what the runner
        will do is worse than no review, because it is believed.

        ``tests/unit/test_deskreview.py`` asserts the agreement against the real
        ``build_instructions`` rather than against a restatement of it here.
        """
        return self.is_transition or self.band_fraction >= 1.0

    @property
    def held(self) -> bool:
        return self.current_weight > 0.0

    @property
    def wanted(self) -> bool:
        return self.target_weight > 0.0

    @property
    def transition(self) -> str:
        """Plain description of what the gap represents."""
        if self.wanted and not self.held:
            return "entry"
        if self.held and not self.wanted:
            return "exit"
        if not self.held and not self.wanted:
            return "flat"
        return "increase" if self.gap > 0 else "reduce"


@dataclass(frozen=True, slots=True)
class HoldingObservation:
    """What is true about a position now.

    Deliberately carries no target and no stop. v1 defines neither, and a field
    named ``stop_price`` would be read as one whatever its docstring said. See
    the table in this package's ``__init__``.
    """

    symbol: str
    weight: float
    close: float
    drawdown_from_peak: float
    """Fraction below the highest close since the review window began."""

    distance_to_support: float
    """Signed fraction: positive means price is above trailing support."""

    target_price: None = None
    """v1 has no profit target. Always ``None``, and that is the honest value."""

    stop_price: None = None
    """v1 has no stop-loss. Always ``None``, and that is the honest value."""


@dataclass(frozen=True, slots=True)
class ReviewReport:
    """One completed review. Frozen: a narration layer may read, never write."""

    pass_name: PassName
    generated_at: dt.datetime
    as_of_session: dt.date
    strategy_name: str
    strategy_version: str
    config_sha256: str
    data_source: str

    bands: tuple[BandState, ...]
    level_breaks: tuple[LevelBreak, ...]
    pullbacks: tuple[PullbackState, ...]
    holdings: tuple[HoldingObservation, ...]
    alignment: dict[str, float]
    news_articles: int
    caveats: tuple[str, ...] = field(default=())

    @property
    def would_trade(self) -> tuple[BandState, ...]:
        """Instruments whose gap has reached the rebalance band."""
        return tuple(band for band in self.bands if band.would_trade)

    def as_dict(self) -> dict[str, object]:
        """Plain mapping for the journal. Every value is a number or a string."""
        return {
            "pass": self.pass_name,
            "generated_at": self.generated_at.isoformat(),
            "as_of_session": self.as_of_session.isoformat(),
            "strategy": f"{self.strategy_name} v{self.strategy_version}",
            "config_sha256": self.config_sha256,
            "data_source": self.data_source,
            "bands": [
                {
                    "symbol": band.symbol,
                    "target_weight": band.target_weight,
                    "current_weight": band.current_weight,
                    "gap": band.gap,
                    "band_fraction": band.band_fraction,
                    "would_trade": band.would_trade,
                    "transition": band.transition,
                }
                for band in self.bands
            ],
            "level_breaks": [
                {
                    "symbol": item.symbol,
                    "kind": item.kind,
                    "level": item.level,
                    "close": item.close,
                    "distance": item.distance,
                }
                for item in self.level_breaks
            ],
            "pullbacks": [
                {
                    "symbol": item.symbol,
                    "drawdown_from_high": item.drawdown_from_high,
                    "range_ratio": item.range_ratio,
                    "close_location": item.close_location,
                    "contracting": item.contracting,
                    "closing_strong": item.closing_strong,
                    "exhausted": item.exhausted,
                }
                for item in self.pullbacks
            ],
            "holdings": [
                {
                    "symbol": item.symbol,
                    "weight": item.weight,
                    "close": item.close,
                    "drawdown_from_peak": item.drawdown_from_peak,
                    "distance_to_support": item.distance_to_support,
                    "target_price": item.target_price,
                    "stop_price": item.stop_price,
                }
                for item in self.holdings
            ],
            "alignment": self.alignment,
            "news_articles": self.news_articles,
            "caveats": list(self.caveats),
        }


def build_review(
    pass_name: PassName,
    strategy: StrategyConfig,
    panel: MarketPanel,
    *,
    current_weights: dict[str, float],
    config_sha256: str,
    generated_at: dt.datetime,
    news_articles: int = 0,
    news_available: bool = False,
) -> ReviewReport:
    """Compute one review pass.

    Args:
        pass_name: Which of the three passes this is. It changes the caveats and
            the narration emphasis, not the arithmetic -- the same numbers are
            true at 09:00 and at 16:00, and computing them differently by time of
            day would be a way to get two answers from one dataset.
        strategy: The configuration whose bands and universe apply.
        panel: Completed sessions, loaded with ``with_context=True``.
        current_weights: Symbol to fraction-of-equity actually held.
        news_articles: Count knowable at ``generated_at`` from the news store.
        news_available: Whether the news store was reachable at all. Zero
            articles because the store is empty and zero because nothing was
            published are different facts and must not render identically.

    Raises:
        DataIntegrityError: if the panel carries no context inputs, or is too
            short for the trailing windows.
    """
    if not panel.has_context_inputs:
        message = (
            "a review needs session highs, lows and volume; "
            "load the panel with load_panel(..., with_context=True)"
        )
        raise DataIntegrityError(message)

    closes, highs, lows, volumes = panel.context_inputs()
    index = len(panel) - 1
    if index < LEVEL_WINDOW:
        message = f"panel has {len(panel)} sessions; need more than {LEVEL_WINDOW} for levels"
        raise DataIntegrityError(message)

    universe = strategy.to_universe()
    symbols = tuple(str(symbol) for symbol in panel.symbols)

    allocation = decide(strategy, closes, universe, decision_index=index)
    targets = {
        str(symbol): float(weight) for symbol, weight in allocation.as_mapping(universe).items()
    }

    band_width = float(strategy.rebalance.min_weight_delta)
    bands = tuple(
        BandState(
            symbol=symbol,
            target_weight=targets.get(symbol, 0.0),
            current_weight=float(current_weights.get(symbol, 0.0)),
            band=band_width,
        )
        for symbol in symbols
    )

    resistance = resistance_levels(highs, LEVEL_WINDOW)
    support = support_levels(lows, LEVEL_WINDOW)
    breaks = tuple(detect_level_breaks(symbols, closes, resistance, support, index=index))

    context = build_context_panels(closes, highs, lows, volumes).at(index)
    alignment = {
        symbol: float(value)
        for symbol, value in zip(symbols, context.alignment, strict=True)
        if np.isfinite(value)
    }

    pullbacks: list[PullbackState] = []
    holdings: list[HoldingObservation] = []
    for column, symbol in enumerate(symbols):
        series_close = closes[: index + 1, column]
        series_high = highs[: index + 1, column]
        series_low = lows[: index + 1, column]
        pullbacks.append(
            pullback_state(symbol, series_close, series_high, series_low, window=PULLBACK_WINDOW)
        )
        weight = float(current_weights.get(symbol, 0.0))
        if weight <= 0:
            continue
        window_closes = series_close[-LEVEL_WINDOW:]
        peak = float(np.nanmax(window_closes))
        close = float(series_close[-1])
        floor = float(support[index, column])
        holdings.append(
            HoldingObservation(
                symbol=symbol,
                weight=weight,
                close=close,
                drawdown_from_peak=(close / peak - 1.0) if peak > 0 else 0.0,
                distance_to_support=(close / floor - 1.0) if floor > 0 else float("nan"),
            )
        )

    return ReviewReport(
        pass_name=pass_name,
        generated_at=generated_at,
        as_of_session=panel.sessions[index],
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=config_sha256,
        data_source=panel.source,
        bands=bands,
        level_breaks=breaks,
        pullbacks=tuple(pullbacks),
        holdings=tuple(holdings),
        alignment=alignment,
        news_articles=news_articles,
        caveats=_caveats(pass_name, panel, news_available, news_articles),
    )


def _caveats(
    pass_name: PassName, panel: MarketPanel, news_available: bool, news_articles: int
) -> tuple[str, ...]:
    """What a reader must know before believing any line of the report."""
    caveats = [
        "OBSERVATION ONLY. This review forms no decision, sizes nothing, and submits "
        "nothing. The strategy's targets come from amat.planning.build_plan.",
        f"Computed from completed sessions through {panel.sessions[-1].isoformat()}. "
        "No intraday or pre-market data is used.",
        "v1 defines no profit target and no stop-loss, so both are reported as absent "
        "rather than estimated. Drawdown and distance-to-support are observations, "
        "not action levels.",
        "Exhaustion and level-break thresholds are declared constants, not fitted "
        "parameters. Neither has been shown to predict anything.",
    ]
    if not news_available:
        caveats.append(
            "News store unreachable or empty: no high-impact-event check was performed. "
            "This is not the same as 'no events'."
        )
    elif news_articles == 0:
        caveats.append(
            "News store reachable and returned nothing knowable at this time. The store "
            "collects forward only, so an empty result is expected until history accrues."
        )
    if pass_name in {"midday", "evening"}:
        caveats.append(
            f"The {pass_name} pass reports the same completed-session data as the morning "
            "pass. Intraday 4H/1H bars are not yet ingested, so nothing here has been "
            "updated since the last close."
        )
    return tuple(caveats)
