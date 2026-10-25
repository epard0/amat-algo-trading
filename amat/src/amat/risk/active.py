"""Opt-in causal portfolio risk policies; legacy strategy fields stay inert."""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

PSD_TOLERANCE = 1e-10
MIN_VARIANCE = 1e-15


class ActiveRiskConfig(BaseModel):
    """Research limits are action triggers, never guaranteed loss ceilings."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    max_drawdown: float = Field(default=0.10, gt=0, lt=1)
    max_daily_loss: float = Field(default=0.03, gt=0, lt=1)
    stress_budget: float = Field(default=0.04, gt=0, lt=1)
    breach_action: Literal["block_increases", "reduce", "exit"] = "reduce"
    reduction_scalar: float = Field(default=0.5, ge=0, le=1)
    cooldown_sessions: int = Field(default=5, ge=1)
    max_positive_variance_share: float | None = Field(default=None, gt=0, le=1)


@dataclass(frozen=True)
class RiskState:
    """Cash-flow-adjusted unit wealth anchored to this account, not a backtest."""

    account_id: str
    policy_id: str
    timestamp: dt.datetime
    session: dt.date
    equity: float
    nav: float = 1.0
    high_water: float = 1.0
    session_nav: float = 1.0
    session_number: int = 0
    blocked_until: int = -1
    allowed_weights: tuple[tuple[str, float], ...] = ()

    def observe(
        self, timestamp: dt.datetime, equity: float, *, external_flow: float = 0.0
    ) -> RiskState:
        """Flows are credited at this observation after any preceding market move.

        Intraperiod flows require their own dated observation; an unknown flow
        time cannot be reconstructed from two endpoint equity values.
        """
        _aware(timestamp)
        if not all(math.isfinite(v) for v in (equity, external_flow)) or equity <= 0:
            raise ValueError("risk observations require finite positive equity")
        if timestamp < self.timestamp:
            raise ValueError("risk observation moved backwards")
        if timestamp == self.timestamp:
            if equity != self.equity or external_flow:
                raise ValueError("changed duplicate risk observation")
            return self
        nav = self.nav * ((equity - external_flow) / self.equity)
        if nav <= 0:
            raise ValueError("external flow exceeds observed equity")
        session = timestamp.astimezone(dt.UTC).date()
        # Callers pass a session-date clock at UTC noon for daily observations;
        # intraday regular US sessions also have the same UTC/exchange date.
        changed = session != self.session
        return replace(
            self,
            timestamp=timestamp,
            session=session,
            equity=equity,
            nav=nav,
            high_water=max(self.high_water, nav),
            session_nav=self.nav if changed else self.session_nav,
            session_number=self.session_number + int(changed),
        )


def _aware(timestamp: dt.datetime) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("risk clock must be timezone aware")


def initial_state(
    account_id: str, policy_id: str, timestamp: dt.datetime, equity: float
) -> RiskState:
    """Create an explicit actual/simulated anchor without historical inheritance."""
    _aware(timestamp)
    if not account_id or not policy_id or not math.isfinite(equity) or equity <= 0:
        raise ValueError("invalid risk anchor")
    return RiskState(account_id, policy_id, timestamp, timestamp.date(), equity)


@dataclass(frozen=True)
class RiskDecision:
    targets: dict[str, float]
    state: RiskState
    reasons: tuple[str, ...]
    stress_loss_before: float
    stress_loss_after: float


def apply_active_risk(  # noqa: PLR0912, PLR0915 - explicit ordered risk constraints
    targets: dict[str, float],
    held: dict[str, float],
    state: RiskState,
    config: ActiveRiskConfig,
    shocks: tuple[dict[str, float], ...],
    *,
    stale: bool = False,
    covariance: np.ndarray | None = None,
) -> RiskDecision:
    """Only reduce targets; stress cases and variance shares are explicit inputs."""
    if any(not math.isfinite(v) or v < 0 for v in held.values()):
        raise ValueError("invalid observed holdings weights")
    symbols = tuple(targets)
    w = np.array([targets[s] for s in symbols], dtype=float)
    if not np.isfinite(w).all() or np.any(w < 0) or w.sum() > 1 + 1e-9:
        raise ValueError("targets must be finite, long-only and unlevered")
    if not shocks or any(set(case) != set(symbols) for case in shocks):
        raise ValueError("stress scenarios must cover the complete universe")
    matrix = np.array([[case[s] for s in symbols] for case in shocks])
    if not np.isfinite(matrix).all() or np.any(matrix < -1) or np.any(matrix > 1):
        raise ValueError("stress returns must be finite and inside [-1,1]")
    loss_before = max(0.0, float(-(matrix @ w).min()))
    reasons = []
    breach = state.nav / state.high_water - 1 <= -config.max_drawdown
    daily = state.nav / state.session_nav - 1 <= -config.max_daily_loss
    if breach or daily:
        active = state.session_number < state.blocked_until
        ceiling = (
            state.allowed_weights
            if active
            else tuple(
                (
                    s,
                    held.get(s, 0.0)
                    * (
                        config.reduction_scalar
                        if config.breach_action == "reduce"
                        else 0.0
                        if config.breach_action == "exit"
                        else 1.0
                    ),
                )
                for s in symbols
            )
        )
        state = replace(
            state,
            allowed_weights=ceiling,
            blocked_until=max(state.blocked_until, state.session_number + config.cooldown_sessions),
        )
        reasons.append("drawdown" if breach else "daily_loss")
    if stale or state.session_number < state.blocked_until:
        if stale or config.breach_action == "block_increases":
            w = np.minimum(w, [held.get(s, 0.0) for s in symbols])
        else:
            limits = dict(state.allowed_weights)
            w = np.minimum(w, [min(limits.get(s,0.0),held.get(s,0.0)) for s in symbols])
        reasons.append("stale" if stale else "cooldown")
    if loss_before > config.stress_budget:
        current = max(0.0, float(-(matrix @ w).min()))
        if current > config.stress_budget:
            w *= config.stress_budget / current
        reasons.append("stress_budget")
    if config.max_positive_variance_share is not None:
        if covariance is None or covariance.shape != (len(w), len(w)):
            raise ValueError("variance-share constraint requires aligned covariance")
        if not np.isfinite(covariance).all() or not np.allclose(covariance, covariance.T):
            raise ValueError("invalid covariance")
        if np.linalg.eigvalsh(covariance).min() < -PSD_TOLERANCE:
            raise ValueError("covariance must be positive semidefinite")
        for _ in range(64):
            contribution = np.maximum(w * (covariance @ w), 0)
            total = contribution.sum()
            if total <= MIN_VARIANCE:
                w[:] = 0
                reasons.append("variance_share_negligible_cash")
                break
            fraction = contribution / total
            offending = fraction > config.max_positive_variance_share + 1e-9
            if not offending.any():
                break
            w[offending] *= np.sqrt(config.max_positive_variance_share / fraction[offending])
        else:
            w[:] = 0
            reasons.append("variance_share_infeasible_cash")
        reasons.append("positive_variance_share")
    return RiskDecision(
        dict(zip(symbols, map(float, w), strict=True)),
        state,
        tuple(reasons),
        loss_before,
        max(0.0, float(-(matrix @ w).min())),
    )


def risk_checkpoint(state: RiskState) -> dict[str, object]:
    """Serializable account-bound state with an integrity seal."""
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415
    from dataclasses import asdict  # noqa: PLC0415

    body = {"schema": "active-risk-state/1", "state": asdict(state)}
    value = json.loads(json.dumps(body, default=str, sort_keys=True, allow_nan=False))
    value["sha256"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return dict(value)


def restore_risk_checkpoint(value: dict[str, object], account_id: str, policy_id: str) -> RiskState:
    """Refuse cross-account resets, edited checkpoints and invalid numerical state."""
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    body = {k: v for k, v in value.items() if k != "sha256"}
    if body.get("schema") != "active-risk-state/1" or hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()
    ).hexdigest() != value.get("sha256"):
        raise ValueError("changed risk checkpoint")
    raw = body["state"]
    if not isinstance(raw, dict):
        raise ValueError("invalid risk state")
    data = dict(raw)
    data["timestamp"] = dt.datetime.fromisoformat(data["timestamp"])
    data["session"] = dt.date.fromisoformat(data["session"])
    data["allowed_weights"] = tuple(tuple(pair) for pair in data["allowed_weights"])
    state = RiskState(**data)
    if (
        state.account_id != account_id
        or state.policy_id != policy_id
        or not all(
            math.isfinite(v) and v > 0
            for v in (state.equity, state.nav, state.high_water, state.session_nav)
        )
        or state.nav > state.high_water
        or state.session_number < 0
    ):
        raise ValueError("risk checkpoint identity or state invalid")
    _aware(state.timestamp)
    return state
