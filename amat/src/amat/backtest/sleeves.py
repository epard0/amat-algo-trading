"""Per-asset sleeve studies: one independent account per symbol.

Why sleeves exist, and what they are not
----------------------------------------
The combined portfolio answers "how did the strategy do?" It answers "how did
the strategy do *on gold*?" only through attribution, which is confounded: GLD's
contribution to the portfolio depends on what the constraint stack left for it
after SPY, TLT and everything else had taken their share. A symbol can look weak
purely because a cluster cap kept crowding it out.

A sleeve removes that confound by giving each symbol its own account, its own
starting equity, and no competition. Reading the two side by side separates the
signal's behaviour on an instrument from the allocator's treatment of it.

A sleeve is a **diagnostic, not a deployable strategy**. It is a concentrated,
single-instrument, long-or-cash account, and the portfolio-level risk machinery
that makes the real strategy defensible -- cluster caps, correlation management,
the diversification floor -- is either inapplicable or disabled in it. Sleeve
returns must never be summed, averaged, or presented as portfolio performance:
29 sleeves each risking their own equity is 29x the capital and none of the
diversification.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import numpy as np

from amat.backtest.panel import MarketPanel
from amat.backtest.runner import DecisionOverlay, RunResult, run_backtest
from amat.config.models import StrategyConfig
from amat.domain.actions import CorporateAction
from amat.domain.types import Cash, SessionDate, Symbol
from amat.errors import AmatError, DataIntegrityError

__all__ = [
    "DEFAULT_COMBINED_EQUITY",
    "DEFAULT_SLEEVE_EQUITY",
    "ENGINE_LABELS",
    "STUDY_ENGINES",
    "SleeveEngine",
    "SleeveOutcome",
    "SleeveStudy",
    "run_sleeve_study",
    "sleeve_strategy",
]

#: Starting equity for each sleeve. Equal bases are what make per-asset profit
#: and loss directly comparable in currency rather than only in percent.
DEFAULT_SLEEVE_EQUITY = Decimal("100000")

#: Starting equity for the combined portfolio. Every strategy view -- the
#: combined portfolio and each single-asset sleeve -- opens with the same
#: 100,000, so all reported currency figures read as "per 100k deployed". The
#: allocator spreads it across assets; nothing is pre-assigned per symbol.
DEFAULT_COMBINED_EQUITY = Decimal("100000")

#: Which runner a study exercises.
#:
#: One runner, since the v2 managed overlay was rejected on 2026-08-10 and its
#: implementation removed. The literal is kept rather than collapsed to a bare
#: string because every rendered artifact names the engine that produced it, and
#: a future second engine must be a deliberate addition to this type rather than
#: an unlabelled number appearing in a table.
type SleeveEngine = Literal["v1-frozen"]

#: Human-readable engine descriptions. Every rendered artifact carries one, so a
#: reader never has to infer which runner a table came from.
ENGINE_LABELS: dict[str, str] = {
    "v1-frozen": "v1 frozen contract — signal, vol target, constraints; no risk overlay",
}

#: Which runners each ``--engine`` selection expands to. One entry, for now.
STUDY_ENGINES: dict[str, tuple[SleeveEngine, ...]] = {
    "v1": ("v1-frozen",),
}


@dataclass(frozen=True, slots=True)
class SleeveOutcome:
    """One symbol's independent account, or the reason it could not be run."""

    symbol: str
    result: RunResult | None
    failure: str | None = None

    @property
    def ran(self) -> bool:
        return self.result is not None

    @property
    def net_pnl(self) -> Cash:
        """Profit and loss in currency. Zero for a sleeve that did not run."""
        if self.result is None:
            return Decimal(0)
        return self.result.final_equity - self.result.initial_equity

    @property
    def total_return(self) -> float:
        if self.result is None or self.result.initial_equity <= 0:
            return 0.0
        return float(self.result.final_equity / self.result.initial_equity) - 1.0


@dataclass(frozen=True, slots=True)
class SleeveStudy:
    """The combined portfolio plus one independent sleeve per symbol."""

    combined: RunResult
    sleeves: tuple[SleeveOutcome, ...]
    sleeve_equity: Cash
    strategy_name: str
    strategy_version: str
    engine: SleeveEngine = "v1-frozen"

    @property
    def engine_label(self) -> str:
        """The description every report of this study must carry."""
        return ENGINE_LABELS[self.engine]

    @property
    def ran_sleeves(self) -> tuple[SleeveOutcome, ...]:
        return tuple(outcome for outcome in self.sleeves if outcome.ran)

    @property
    def failed_sleeves(self) -> tuple[SleeveOutcome, ...]:
        return tuple(outcome for outcome in self.sleeves if not outcome.ran)

    def by_net_pnl(self) -> tuple[SleeveOutcome, ...]:
        """Sleeves that ran, best profit first."""
        return tuple(sorted(self.ran_sleeves, key=lambda outcome: outcome.net_pnl, reverse=True))


def _actions_for(
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None,
    symbol: str,
) -> Mapping[SessionDate, Sequence[CorporateAction]] | None:
    """Narrow a universe-wide action map to one symbol, dropping empty sessions."""
    if actions_by_session is None:
        return None
    narrowed: dict[SessionDate, Sequence[CorporateAction]] = {}
    for session, actions in actions_by_session.items():
        kept = tuple(action for action in actions if str(action.symbol) == symbol)
        if kept:
            narrowed[session] = kept
    return narrowed


def sleeve_strategy(strategy: StrategyConfig, symbol: str) -> StrategyConfig:
    """Derive a single-symbol configuration from a portfolio configuration.

    Two settings change, and both are structural rather than tuning choices:

    * the universe becomes one cluster holding one symbol, with a 100% cluster
      cap;
    * the symbol cap goes to 100%, because a cap whose purpose is to limit one
      name's share of a *portfolio* has no meaning in an account that holds
      only that name -- leaving a portfolio-level 20% cap in place would report
      an 80%-cash account rather than the instrument's behaviour.

    Everything else -- signal, volatility target, costs, execution -- is
    inherited unchanged, so the sleeve exercises the same code. The config's
    risk-overlay fields (``max_positions``, ``min_effective_bets``) are also
    inherited unchanged: they are inert under the only selectable engine, the
    v1-frozen runner, which has no risk overlay.
    """
    payload = strategy.model_dump()
    payload["name"] = f"{strategy.name}-sleeve-{symbol}"
    payload["universe"] = {"clusters": [{"name": "sleeve", "gross_cap": 1.0, "symbols": [symbol]}]}
    payload["constraints"]["max_symbol_weight"] = 1.0
    return StrategyConfig.model_validate(payload)


def run_sleeve_study(
    strategy: StrategyConfig,
    panel: MarketPanel,
    *,
    combined_equity: Cash = DEFAULT_COMBINED_EQUITY,
    sleeve_equity: Cash = DEFAULT_SLEEVE_EQUITY,
    symbols: Sequence[str] | None = None,
    one_way_bps: float | None = None,
    engine: SleeveEngine = "v1-frozen",
    actions_by_session: Mapping[SessionDate, Sequence[CorporateAction]] | None = None,
    weight_scalars: np.ndarray | None = None,
    decision_overlay: DecisionOverlay | None = None,
    progress: Callable[[str], None] | None = None,
) -> SleeveStudy:
    """Run the combined portfolio and one independent sleeve per symbol.

    The combined portfolio and every sleeve each open with 100,000 by default,
    so every reported currency figure reads as "per 100,000 deployed" whichever
    view it comes from. The combined portfolio's funding is deliberately
    independent of the sleeve list: the run trades the whole universe whether
    or not sleeves were requested, and ``--no-sleeves`` (an empty list) must
    not change what the portfolio is.

    A sleeve that fails is recorded with its reason and the study continues. One
    instrument with a data gap must not cost the other twenty-eight their
    results, and a silently dropped sleeve would misrepresent coverage.

    ``actions_by_session`` is narrowed to each sleeve's own symbol before that
    sleeve runs. The accountant would ignore an action for an unheld instrument
    anyway, but passing the whole universe's dividends into a single-symbol
    account makes the sleeve's inputs untrue on their face, and a later reader
    checking why GLD's sleeve saw a TLT distribution would be right to worry.

    ``weight_scalars`` (the v3 conditioning series) applies to the **combined
    portfolio only**. Sleeves exist to isolate the raw signal's behaviour on one
    instrument; conditioning them would fold the portfolio-level overlay back
    into the very diagnostic built to exclude portfolio-level effects.
    ``decision_overlay`` (the v6 per-asset regime/liquidity overlay) follows
    the same rule for the same reason: combined portfolio only, sleeves stay
    the unconditioned diagnostic.

    Raises:
        DataIntegrityError: if the panel does not carry every requested symbol,
            or if the combined run itself fails.
    """
    say = progress or (lambda _message: None)
    wanted = (
        tuple(symbols)
        if symbols is not None
        else tuple(str(symbol) for symbol in strategy.universe.symbols)
    )
    missing = [symbol for symbol in wanted if Symbol(symbol) not in panel.symbols]
    if missing:
        message = f"panel lacks requested sleeve symbols: {', '.join(missing)}"
        raise DataIntegrityError(message)

    universe = strategy.to_universe()
    say(f"[{engine}] combined portfolio: {len(universe.symbols)} symbols at {combined_equity}")
    combined = run_backtest(
        strategy,
        panel.slice_symbols([Symbol(s) for s in universe.symbols]),
        universe,
        initial_cash=combined_equity,
        one_way_bps=one_way_bps,
        actions_by_session=actions_by_session,
        weight_scalars=weight_scalars,
        decision_overlay=decision_overlay,
    )

    outcomes: list[SleeveOutcome] = []
    for position, symbol in enumerate(wanted, start=1):
        say(f"[{engine}] sleeve {position}/{len(wanted)}: {symbol}")
        sleeve_config = sleeve_strategy(strategy, symbol)
        sleeve_universe = sleeve_config.to_universe()
        try:
            result = run_backtest(
                sleeve_config,
                panel.slice_symbols([Symbol(symbol)]),
                sleeve_universe,
                initial_cash=sleeve_equity,
                one_way_bps=one_way_bps,
                actions_by_session=_actions_for(actions_by_session, symbol),
            )
        except AmatError as exc:
            outcomes.append(SleeveOutcome(symbol=symbol, result=None, failure=str(exc)))
            say(f"sleeve {symbol} failed: {exc}")
        else:
            outcomes.append(SleeveOutcome(symbol=symbol, result=result))

    return SleeveStudy(
        combined=combined,
        sleeves=tuple(outcomes),
        sleeve_equity=sleeve_equity,
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        engine=engine,
    )
