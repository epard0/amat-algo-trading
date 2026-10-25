"""Explicit simulated state for the optional daily active-risk runner hook."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Any

import numpy as np

from amat.domain.ledgers import LedgerSnapshot
from amat.domain.types import Symbol
from amat.risk.active import ActiveRiskConfig, RiskState, apply_active_risk, initial_state


class DailyRiskPolicy:
    def __init__(self, config: ActiveRiskConfig, shocks: tuple[dict[str, float], ...]) -> None:
        self.config = config
        self.shocks = shocks
        self.state: RiskState | None = None
        self.records: list[dict[str, Any]] = []

    def __call__(
        self,
        targets: dict[Symbol, float],
        ledger: LedgerSnapshot,
        session: dt.date,
        covariance: np.ndarray,
    ) -> dict[Symbol, float]:
        clock = dt.datetime.combine(session, dt.time(12), dt.UTC)
        if self.state is None:
            self.state = initial_state(
                "simulated-daily", "active-research/1", clock, float(ledger.equity)
            )
        else:
            self.state = self.state.observe(clock, float(ledger.equity))
        result = apply_active_risk(
            {str(s): w for s, w in targets.items()},
            {str(s): w for s, w in ledger.weights().items()},
            self.state,
            self.config,
            self.shocks,
            covariance=covariance,
        )
        self.state = result.state
        self.records.append(
            {
                "session": str(session),
                "reasons": result.reasons,
                "stress_before": result.stress_loss_before,
                "stress_after": result.stress_loss_after,
                "state": asdict(self.state),
            }
        )
        return {s: result.targets[str(s)] for s in targets}
