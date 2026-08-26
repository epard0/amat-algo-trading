"""Per-position exit rules: stop-loss and take-profit as a research overlay.

Why an overlay and not part of the frozen configuration
-------------------------------------------------------
The frozen v1 core has no per-trade exits by design: positions shrink and
close through the signal deadband and volatility targeting. Whether explicit
stops or profit-taking *improve* on that is an empirical question (registered
experiment 0003), so the rules live beside ``weight_scalars`` and ``stance``
as the third runner overlay: absent by default, judged under split
discipline, and never a silent change to v1. If a rule set ever survives the
full protocol it earns a place in the configuration schema through the
registry door -- not before.

Semantics, stated exactly
-------------------------
* The **basis** of a position is the raw price of the fill that took the
  holding from flat to long, converted to the adjusted basis at the fill
  session (``fill_price * adjusted_close / raw_close``). Later size
  adjustments -- the volatility target resizes constantly -- do not move the
  basis; it clears only when the position returns to flat. The adjusted
  basis makes the rules split-safe and dividend-inclusive: a dividend cannot
  fake a loss and a split cannot fake a breach.
* At decision session ``t`` a held position **breaches** when its adjusted
  return from basis reaches ``-stop_loss`` or ``+take_profit``. A breach
  forces the symbol's target weight to zero in the decision formed at ``t``
  and executed at ``t + lag`` -- the same next-window discipline as every
  other decision. Nothing exits on the signal bar.
* A breach starts a **re-entry embargo**: the symbol's target is forced to
  zero for ``reentry_embargo_sessions`` consecutive decisions starting at
  the breach. Without an embargo a stop is a one-session flicker -- the
  signal that held the position yesterday would buy it back tomorrow, paying
  two spreads for nothing. While a breach persists (the position has not yet
  flattened), the embargo end rolls forward from the latest breached
  decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from amat.domain.fills import Fill
from amat.domain.positions import Position
from amat.domain.types import Side, Symbol
from amat.errors import ConfigurationError

__all__ = ["ExitPolicy", "ExitRules", "apply_exit_rules", "track_entry_bases"]

_ZERO = Decimal(0)


def _held_quantity(positions: Mapping[Symbol, Position], symbol: Symbol) -> Decimal:
    entry = positions.get(symbol)
    return entry.quantity if entry is not None else _ZERO


#: A take-profit above this is a configuration typo, not a strategy.
_MAX_TAKE_PROFIT = 10.0


@dataclass(frozen=True, slots=True)
class ExitRules:
    """A stop-loss / take-profit pair with a fixed re-entry embargo.

    Attributes:
        stop_loss: Exit when the position's adjusted return from basis reaches
            ``-stop_loss``. ``None`` disables the stop.
        take_profit: Exit when it reaches ``+take_profit``. ``None`` disables.
        reentry_embargo_sessions: Decisions (including the breach decision)
            during which a positive target for the breached symbol is forced
            to zero. Fixed at registration for experiment 0003 -- one month,
            the shortest signal horizon -- to keep the search surface small.

    Raises:
        ConfigurationError: when both rules are ``None`` (pass ``None``
            instead of a no-op object), a rule lies outside its domain, or
            the embargo is shorter than one session.
    """

    stop_loss: float | None = None
    take_profit: float | None = None
    reentry_embargo_sessions: int = 21

    def __post_init__(self) -> None:
        if self.stop_loss is None and self.take_profit is None:
            message = (
                "ExitRules with neither stop_loss nor take_profit is a no-op; "
                "pass exit_rules=None to mean 'no exit rules'"
            )
            raise ConfigurationError(message)
        if self.stop_loss is not None and not 0.0 < self.stop_loss < 1.0:
            message = f"stop_loss must lie strictly inside (0, 1), got {self.stop_loss}"
            raise ConfigurationError(message)
        if self.take_profit is not None and not 0.0 < self.take_profit < _MAX_TAKE_PROFIT:
            message = (
                f"take_profit must lie strictly inside (0, {_MAX_TAKE_PROFIT}), "
                f"got {self.take_profit}"
            )
            raise ConfigurationError(message)
        if self.reentry_embargo_sessions < 1:
            message = (
                f"reentry_embargo_sessions must be at least 1, got {self.reentry_embargo_sessions}"
            )
            raise ConfigurationError(message)

    @property
    def label(self) -> str:
        """A compact human-readable name, e.g. ``sl10%+tp20%``."""
        parts = []
        if self.stop_loss is not None:
            parts.append(f"sl{self.stop_loss:.0%}")
        if self.take_profit is not None:
            parts.append(f"tp{self.take_profit:.0%}")
        return "+".join(parts)


ExitPolicy = ExitRules | Mapping[Symbol, ExitRules]
"""One global rule or an explicit per-asset rule map."""


def track_entry_bases(
    entry_basis: dict[Symbol, float],
    *,
    fills: Sequence[Fill],
    positions: Mapping[Symbol, Position],
    adjusted_row: np.ndarray,
    raw_row: np.ndarray,
    symbol_columns: Mapping[Symbol, int],
) -> None:
    """Update the per-symbol adjusted entry basis after a session settles.

    Clears the basis of every symbol that returned to flat, then records a
    basis for every buy fill that established a fresh position. Corporate
    actions change quantities without fills and leave the adjusted basis
    untouched -- correctly, because the adjusted series is continuous across
    them.
    """
    for symbol in [held for held in entry_basis if _held_quantity(positions, held) <= 0]:
        del entry_basis[symbol]
    for fill in fills:
        if fill.side is not Side.BUY or fill.symbol in entry_basis:
            continue
        if _held_quantity(positions, fill.symbol) <= 0:  # pragma: no cover - a buy holds
            continue
        column = symbol_columns[fill.symbol]
        ratio = float(adjusted_row[column]) / float(raw_row[column])
        entry_basis[fill.symbol] = float(fill.price) * ratio


def apply_exit_rules(
    rules: ExitPolicy,
    targets: Mapping[Symbol, float],
    *,
    positions: Mapping[Symbol, Position],
    entry_basis: Mapping[Symbol, float],
    embargo_until: dict[Symbol, int],
    decision_index: int,
    adjusted_row: np.ndarray,
    symbol_columns: Mapping[Symbol, int],
) -> dict[Symbol, float]:
    """Force breached and embargoed symbols' targets to zero, causally.

    Reads only the decision session's own adjusted close, the basis of fills
    that already settled, and embargoes set by earlier decisions -- nothing
    after ``decision_index`` can influence the outcome.

    ``embargo_until`` maps a symbol to the first decision index at which
    re-entry is allowed again; this function both consults and extends it.
    """
    adjusted = dict(targets)
    for symbol, position in positions.items():
        if position.quantity <= 0:
            continue
        basis = entry_basis.get(symbol)
        if basis is None or basis <= 0:
            continue
        symbol_rules = rules if isinstance(rules, ExitRules) else rules.get(symbol)
        if symbol_rules is None:
            continue
        column = symbol_columns[symbol]
        position_return = float(adjusted_row[column]) / basis - 1.0
        breached = (
            symbol_rules.stop_loss is not None and position_return <= -symbol_rules.stop_loss
        ) or (symbol_rules.take_profit is not None and position_return >= symbol_rules.take_profit)
        if breached:
            adjusted[symbol] = 0.0
            embargo_until[symbol] = decision_index + symbol_rules.reentry_embargo_sessions
    for symbol, first_allowed in embargo_until.items():
        if decision_index < first_allowed and adjusted.get(symbol, 0.0) > 0.0:
            adjusted[symbol] = 0.0
    return adjusted
