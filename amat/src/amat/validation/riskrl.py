"""Conservative offline per-asset stop/take-profit learning for v6.

This is an episodic contextual bandit, the smallest reinforcement-learning
formulation justified by the data.  Each completed base-strategy round trip is
an episode and each stop/reward-risk pair is an action.  Actions are ranked on
the training window only, shrunk toward the no-exit baseline, and activated for
an asset only when the ranking also beats no-exit on the later validation
window.  A failed or thin asset falls back to no explicit exit.

The procedure does not claim an "optimal" live stop.  It finds the least-bad
member of a declared grid on burned history and makes overfitting visible.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from amat.backtest.attribution import ClosedTrade
from amat.backtest.exits import ExitRules
from amat.backtest.panel import MarketPanel
from amat.config.v6 import RiskLearningConfig
from amat.domain.types import Symbol

__all__ = [
    "AssetRiskPolicy",
    "RiskAction",
    "RiskLearningResult",
    "learn_asset_risk_policies",
]


@dataclass(frozen=True, slots=True)
class RiskAction:
    """One bandit action; ``None``/``None`` is the base strategy exit."""

    stop_loss: float | None
    take_profit: float | None

    @property
    def label(self) -> str:
        if self.stop_loss is None:
            return "no_explicit_exit"
        assert self.take_profit is not None  # noqa: S101 - constructed as a pair below
        return f"sl{self.stop_loss:.0%}_tp{self.take_profit:.0%}"


@dataclass(frozen=True, slots=True)
class AssetRiskPolicy:
    """One asset's training choice and validation gate."""

    symbol: str
    selected: RiskAction
    activated: bool
    training_episodes: int
    validation_episodes: int
    training_utility: float
    training_baseline_utility: float
    validation_utility: float
    validation_baseline_utility: float
    validation_improvement_lcb: float
    rationale: str


@dataclass(frozen=True, slots=True)
class RiskLearningResult:
    """Per-asset policies and only the rules that survived validation."""

    policies: tuple[AssetRiskPolicy, ...]
    exit_rules: dict[Symbol, ExitRules]
    caveat: str = (
        "Training and validation are both burned history. Activated means a grid action "
        "held on the ranking-blind slice, not that it is out-of-sample or safe for live use."
    )


def _actions(config: RiskLearningConfig) -> tuple[RiskAction, ...]:
    return (
        RiskAction(None, None),
        *(
            RiskAction(stop, stop * ratio)
            for stop in config.stop_losses
            for ratio in config.reward_risk_ratios
        ),
    )


def _utility(values: np.ndarray, downside_aversion: float) -> float:
    if values.size == 0:
        return float("-inf")
    downside = values[values < 0.0]
    downside_deviation = float(np.sqrt(np.mean(np.square(downside)))) if downside.size else 0.0
    return float(values.mean() - downside_aversion * downside_deviation)


def _simulated_return(
    trade: ClosedTrade,
    action: RiskAction,
    panel: MarketPanel,
    *,
    session_index: dict[dt.date, int],
    symbol_index: dict[Symbol, int],
    round_trip_cost_rate: float,
) -> float:
    if action.stop_loss is None:
        return trade.return_pct
    start = session_index.get(trade.entry_session)
    end = session_index.get(trade.exit_session)
    if start is None or end is None or end <= start:
        return trade.return_pct
    column = symbol_index[trade.symbol]
    entry = panel.adjusted_closes[start, column]
    stop_price = entry * (1.0 - action.stop_loss)
    assert action.take_profit is not None  # noqa: S101
    profit_price = entry * (1.0 + action.take_profit)
    for row in range(start + 1, end + 1):
        # Daily bars do not reveal intraday ordering. If both levels print on
        # one session, stop-first is the conservative, non-flattering choice.
        if panel.adjusted_lows is not None and panel.adjusted_lows[row, column] <= stop_price:
            return -action.stop_loss - round_trip_cost_rate
        if panel.adjusted_highs is not None and panel.adjusted_highs[row, column] >= profit_price:
            return action.take_profit - round_trip_cost_rate
    return trade.return_pct


def learn_asset_risk_policies(
    trades: tuple[ClosedTrade, ...] | list[ClosedTrade],
    panel: MarketPanel,
    config: RiskLearningConfig,
    *,
    one_way_bps: float,
) -> RiskLearningResult:
    """Rank per-asset exit actions with shrinkage and a separate validation gate."""
    session_index = {session: row for row, session in enumerate(panel.sessions)}
    symbol_index = {symbol: column for column, symbol in enumerate(panel.symbols)}
    actions = _actions(config)
    round_trip_cost_rate = 2.0 * one_way_bps / 10_000.0
    policies: list[AssetRiskPolicy] = []
    rules: dict[Symbol, ExitRules] = {}

    for symbol in panel.symbols:
        episodes = [trade for trade in trades if trade.symbol == symbol]
        training = [trade for trade in episodes if trade.entry_session <= config.train_end]
        validation = [trade for trade in episodes if trade.entry_session > config.train_end]
        train_rewards = {
            action: np.asarray(
                [
                    _simulated_return(
                        trade,
                        action,
                        panel,
                        session_index=session_index,
                        symbol_index=symbol_index,
                        round_trip_cost_rate=round_trip_cost_rate,
                    )
                    for trade in training
                ]
            )
            for action in actions
        }
        validation_rewards = {
            action: np.asarray(
                [
                    _simulated_return(
                        trade,
                        action,
                        panel,
                        session_index=session_index,
                        symbol_index=symbol_index,
                        round_trip_cost_rate=round_trip_cost_rate,
                    )
                    for trade in validation
                ]
            )
            for action in actions
        }
        baseline = actions[0]
        base_train = _utility(train_rewards[baseline], config.downside_aversion)
        base_validation = _utility(validation_rewards[baseline], config.downside_aversion)

        if len(training) < config.min_training_episodes or not validation:
            selected = baseline
            selected_train = base_train
            selected_validation = base_validation
            activated = False
            validation_lcb = float("-inf")
            rationale = "insufficient independent episodes; no explicit exit activated"
        else:
            shrunk: dict[RiskAction, float] = {}
            for action in actions:
                raw = _utility(train_rewards[action], config.downside_aversion)
                count = len(train_rewards[action])
                shrunk[action] = (count * raw + config.prior_strength * base_train) / (
                    count + config.prior_strength
                )
            selected = max(actions, key=lambda action: (shrunk[action], action.label))
            selected_train = shrunk[selected]
            selected_validation = _utility(validation_rewards[selected], config.downside_aversion)
            differences = validation_rewards[selected] - validation_rewards[baseline]
            difference_mean = float(differences.mean())
            difference_se = (
                float(differences.std(ddof=1) / np.sqrt(len(differences)))
                if len(differences) > 1
                else float("inf")
            )
            validation_lcb = difference_mean - config.validation_confidence_z * difference_se
            activated = (
                selected.stop_loss is not None
                and selected_train > base_train
                and validation_lcb >= config.min_validation_improvement
            )
            if activated:
                rationale = "training winner also cleared the later validation utility gate"
                assert selected.stop_loss is not None  # noqa: S101
                rules[symbol] = ExitRules(
                    stop_loss=selected.stop_loss,
                    take_profit=selected.take_profit,
                    reentry_embargo_sessions=config.reentry_embargo_sessions,
                )
            elif selected.stop_loss is None:
                rationale = "no explicit exit ranked first on training"
            else:
                rationale = "training winner failed the later validation utility gate"

        policies.append(
            AssetRiskPolicy(
                symbol=str(symbol),
                selected=selected,
                activated=activated,
                training_episodes=len(training),
                validation_episodes=len(validation),
                training_utility=selected_train,
                training_baseline_utility=base_train,
                validation_utility=selected_validation,
                validation_baseline_utility=base_validation,
                validation_improvement_lcb=validation_lcb,
                rationale=rationale,
            )
        )
    return RiskLearningResult(policies=tuple(policies), exit_rules=rules)
