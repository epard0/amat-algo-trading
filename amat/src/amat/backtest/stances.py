"""Assembling a v4 stance blend from a switching configuration and a panel.

One function, used by the CLI and by the falsification harness, so the
candidate and its controls are built by literally the same code path -- the
only difference between them is the lambda series swapped in.
"""

from __future__ import annotations

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import StanceBlend
from amat.config.switching import SwitchingStrategyConfig
from amat.domain.types import Symbol
from amat.errors import DataIntegrityError
from amat.regime.switching import breadth_blend_weights

__all__ = ["assemble_stance", "blend_series"]


def blend_series(config: SwitchingStrategyConfig, panel: MarketPanel) -> np.ndarray:
    """The lambda series the switching rule produces for this panel."""
    rule = config.switching
    return breadth_blend_weights(
        panel.adjusted_closes,
        horizon_sessions=rule.breadth_horizon_sessions,
        smoothing_half_life_sessions=rule.smoothing_half_life_sessions,
        floor_breadth=rule.floor_breadth,
        full_breadth=rule.full_breadth,
    )


def assemble_stance(
    config: SwitchingStrategyConfig,
    panel: MarketPanel,
    *,
    weights: np.ndarray | None = None,
) -> StanceBlend:
    """Build the runner's :class:`StanceBlend` for ``config`` over ``panel``.

    Args:
        config: The validated v4 configuration.
        panel: The full trend-universe panel; the defensive stance's columns
            are narrowed from it, so both stances read one dataset.
        weights: Override lambda series (the falsification controls). ``None``
            computes the configured breadth rule.

    Raises:
        DataIntegrityError: if a defensive symbol is missing from the panel.
            (The config validator already forbids this; the check here defends
            against a panel loaded for the wrong universe.)
    """
    defensive_universe = config.defensive.to_universe()
    panel_symbols = list(panel.symbols)
    columns: list[int] = []
    for symbol in defensive_universe.symbols:
        try:
            columns.append(panel_symbols.index(Symbol(symbol)))
        except ValueError as exc:
            message = f"defensive stance symbol {symbol} is not in the panel"
            raise DataIntegrityError(message) from exc

    return StanceBlend(
        strategy=config.defensive,
        universe=defensive_universe,
        adjusted_closes=panel.adjusted_closes[:, columns],
        weights=blend_series(config, panel) if weights is None else weights,
    )
