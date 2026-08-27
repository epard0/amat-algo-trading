"""Isolated paper-only observer for Kalshi complementary-pair arbitrage."""

from __future__ import annotations

from decimal import Decimal
from collections.abc import Callable
from typing import Any

from core.strategy_brain.strategies.pair_arb import assess_pair_arb


class PairArbEngine:
    """Hunter, risk, fee, and simulated-execution handoff with narrow seams.

    No order method exists here. Kalshi offers no atomic two-leg order and its
    single net book should match a crossed pair away before a REST poll sees it.
    The engine therefore records shadow opportunities separately from real and
    directional paper trades; it cannot accidentally inherit live permission.
    """

    def __init__(
        self,
        *,
        settings: Any,
        ledger: Any,
        decisions: Any,
        mode_provider: Callable[[], str] = lambda: "paper",
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.decisions = decisions
        self.mode_provider = mode_provider
        self.scans = 0
        self.approved = 0
        self.modeled_pnl = Decimal("0")
        self._active_episodes: set[str] = set()

    def scan(self, *, run_id: str, cycle_id: str, market: Any, snapshot: Any) -> Any:
        self.scans += 1
        assessment = assess_pair_arb(
            yes_bid_levels=snapshot.yes_levels,
            no_bid_levels=snapshot.no_levels,
            fixed_budget=self.settings.trade_budget_usd,
            fee_multiplier=market.fee_multiplier,
            minimum_net_edge=self.settings.pair_arb_min_net_edge,
            minimum_leg_price=self.settings.pair_arb_min_leg_price,
            minimum_contracts=self.settings.min_contracts,
        )
        # One modeled capture per continuous crossed-book episode. Recounting
        # every five-second snapshot (or every tiny depth change) would reuse
        # liquidity the first hypothetical pair already consumed.
        simulated = assessment.approved and market.ticker not in self._active_episodes
        if simulated:
            self._active_episodes.add(market.ticker)
            self.approved += 1
            self.modeled_pnl += assessment.modeled_profit
        elif not assessment.approved:
            self._active_episodes.discard(market.ticker)
        self.ledger.record_pair_arb_observation(
            run_id=run_id,
            cycle_id=cycle_id,
            market=market,
            observed_at=snapshot.observed_at,
            assessment=assessment,
            simulated=simulated,
        )
        self.decisions.record_event(
            "pair_arb_scan",
            mode=self.mode_provider(),
            environment=self.settings.environment,
            cycle_id=cycle_id,
            ticker=market.ticker,
            asset=market.asset,
            approved=assessment.approved,
            simulated=simulated,
            gate=assessment.gate,
            reason=assessment.reason,
            yes_ask=str(assessment.yes_ask),
            no_ask=str(assessment.no_ask),
            pair_cost=str(assessment.pair_cost),
            guaranteed_payout="1",
            net_edge=str(assessment.net_edge),
            contracts=str(assessment.contracts),
            modeled_profit=str(
                assessment.modeled_profit if simulated else Decimal("0")
            ),
            execution_assumption="same_snapshot_equal_depth_non_atomic_shadow",
        )
        return assessment

    def record_funnel(self, occasion: str) -> None:
        self.decisions.record_event(
            "pair_arb_funnel",
            mode=self.mode_provider(),
            environment=self.settings.environment,
            occasion=occasion,
            scans=self.scans,
            simulated_pairs=self.approved,
            modeled_pnl=str(self.modeled_pnl),
        )
