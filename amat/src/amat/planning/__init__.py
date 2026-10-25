"""Turn a strategy's latest decision into an execution plan.

This is the orchestration layer between the engine and
:mod:`amat.execution.intent`. It answers one question -- *given data through the
last completed session, and an account holding these positions, what should be
traded next?* -- by driving the strategy through its single decision entry point
rather than re-deriving anything.

Why this is short
-----------------
The v1 decision is a pure function of the price panel, so the decision that would
execute next is :func:`amat.portfolio.decide` evaluated at the last index. No
replay, no accumulated state, no inherited history.

It was not always this short. The rejected v2 overlay was path-dependent -- guard
stack, trade ledger, expectancy window, regime fit, Kelly estimate -- so its
terminal decision could only be read out of a full replay from inception, and
that replay's state was *simulated* rather than realized: a real account opened
today would have inherited a drawdown level and a measured edge from trades that
were never placed. Removing the overlay removed that whole class of problem
along with it.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Literal

from amat.backtest.panel import MarketPanel
from amat.config.models import StrategyConfig
from amat.domain.instruments import Universe
from amat.domain.types import Cash
from amat.errors import DataIntegrityError
from amat.execution.intent import ExecutionPlan, build_execution_plan
from amat.portfolio import decide, precompute_decision_inputs

__all__ = ["PlanEngine", "build_plan"]

type PlanEngine = Literal["v1-frozen"]


def build_plan(
    strategy: StrategyConfig,
    panel: MarketPanel,
    universe: Universe,
    *,
    engine: PlanEngine,
    current_positions: Mapping[str, Decimal],
    account_equity: Cash,
    config_sha256: str,
    snapshot_id: str,
    code_revision: str,
    appended_sessions: int = 0,
    generated_at: dt.datetime | None = None,
    extra_caveats: tuple[str, ...] = (),
    forced_zero_symbols: tuple[str, ...] = (),
) -> ExecutionPlan:
    """Form the decision for the panel's last session and plan its execution.

    Args:
        strategy: Validated configuration.
        panel: Market data through the last completed session. The final row is
            treated as the decision session, so the caller is responsible for
            having excluded any session still in progress.
        universe: Universe matching the panel's symbol order.
        engine: Which engine forms the decision.
        current_positions: Share count per symbol in the real account.
        account_equity: The real account's equity.
        config_sha256: Content digest of the configuration.
        snapshot_id: Data snapshot the panel was loaded from.
        code_revision: Revision of the code forming the decision.
        appended_sessions: Sessions appended past the snapshot's frozen digest.
        generated_at: Timezone-aware creation instant.
        extra_caveats: Provenance or data-quality warnings from an optional
            universe overlay.
        forced_zero_symbols: Recognized exit-only symbols whose computed target
            must be replaced with zero after allocation.

    Raises:
        DataIntegrityError: if the panel is empty.
    """
    if len(panel.sessions) == 0:
        message = "cannot plan from an empty panel"
        raise DataIntegrityError(message)

    as_of_session = panel.sessions[-1]
    marks = {str(symbol): price for symbol, price in panel.marks_at(len(panel) - 1).items()}

    inputs = precompute_decision_inputs(strategy, panel.adjusted_closes, sessions=panel.sessions)
    allocation = decide(
        strategy,
        panel.adjusted_closes,
        universe,
        decision_index=len(panel) - 1,
        precomputed=inputs,
    )
    targets = {str(symbol): float(w) for symbol, w in allocation.as_mapping(universe).items()}
    for symbol in forced_zero_symbols:
        if symbol in targets:
            targets[symbol] = 0.0

    plan = build_execution_plan(
        strategy,
        engine=engine,
        as_of_session=as_of_session,
        target_weights=targets,
        marks=marks,
        current_positions=current_positions,
        account_equity=account_equity,
        config_sha256=config_sha256,
        snapshot_id=snapshot_id,
        code_revision=code_revision,
        appended_sessions=appended_sessions,
        generated_at=generated_at,
        extra_caveats=extra_caveats,
    )
    # A single snapshot supplies both endpoints on the same adjustment basis.
    # Retain the actual exchange-session sequence, including missing marks, so
    # a monitoring outage cannot silently turn five observations into five days.
    history = []
    for index in range(max(0, len(panel) - 65), len(panel)):
        prices = {}
        for column, symbol in enumerate(panel.symbols):
            raw = float(panel.raw_closes[index, column])
            adjusted = float(panel.adjusted_closes[index, column])
            prices[str(symbol)] = (
                {"raw_close": raw, "adjusted_close": adjusted}
                if all(math.isfinite(value) and value > 0 for value in (raw, adjusted))
                else None
            )
        history.append({"session": str(panel.sessions[index]), "prices": prices})
    return replace(
        plan,
        monitoring={
            "schema": "trade-context/1",
            "as_of_session": str(as_of_session),
            "price_history": history,
            "forecast_status": "not_emitted_by_this_model",
            "decision_diagnostics": {
                "trend_scores": {
                    str(symbol): float(score) if math.isfinite(float(score)) else None
                    for symbol, score in zip(panel.symbols, inputs.long_scores[-1], strict=True)
                },
                "score_basis": "core trend score before selection/allocation; not a probability",
                "binding_constraints": list(allocation.binding),
                "forecast_portfolio_volatility_pre": (
                    allocation.forecast_volatility_pre
                    if math.isfinite(allocation.forecast_volatility_pre)
                    else None
                ),
                "forecast_portfolio_volatility_post": (
                    allocation.forecast_volatility_post
                    if math.isfinite(allocation.forecast_volatility_post)
                    else None
                ),
            },
        },
    )
