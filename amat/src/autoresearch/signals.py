"""Chronological signal construction, train-only linear fitting and hysteresis."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.features import annualized_volatility, log_returns, trend_scores
from autoresearch.contracts import Candidate, Program
from autoresearch.genetic.adapter import apply_genetic_pattern, require_genetic_program
from autoresearch.labels import forward_returns, purged_stop
from autoresearch.patterns import pattern_values
from autoresearch.registry import EPSILON, describe, feature_values
from autoresearch.routing import route_scores
from autoresearch.specs import SignalSpec, TradingPolicy

MIN_FIT_ROWS = 40
RIDGE_ALPHAS = (0.1, 1.0, 10.0)


@dataclass(frozen=True)
class SignalTrace:
    scores: np.ndarray
    raw_scores: np.ndarray
    predictors: np.ndarray
    baseline: np.ndarray
    fits: list[dict[str, Any]]
    features: list[dict[str, Any]]
    pattern_scores: np.ndarray | None = None
    routing_scores: np.ndarray | None = None
    genetic_scores: np.ndarray | None = None


def baseline_scores(panel: MarketPanel, candidate: Candidate) -> np.ndarray:
    strategy = candidate.config.strategy
    sigma = annualized_volatility(log_returns(panel.adjusted_closes), strategy.volatility)
    raw = trend_scores(
        panel.adjusted_closes,
        sigma,
        strategy.trend,
        annualization_sessions=strategy.volatility.annualization_sessions,
    )
    return np.asarray(np.maximum(np.nan_to_num(raw, nan=0.0), 0.0))


def policy_scores(raw: np.ndarray, available: np.ndarray, policy: TradingPolicy) -> np.ndarray:
    """State is per asset; unavailable data always exits, even during minimum hold."""
    result = np.zeros_like(raw)
    for column in range(raw.shape[1]):
        active, confirmations, age = False, 0, 0
        for index in range(len(raw)):
            value = raw[index, column]
            if not available[index, column] or not np.isfinite(value):
                active, confirmations, age = False, 0, 0
                continue
            if not active:
                confirmations = confirmations + 1 if value >= policy.entry_threshold else 0
                if confirmations >= policy.confirmation_sessions:
                    active, age, confirmations = True, 0, 0
            elif age >= policy.min_hold_sessions and value <= policy.exit_threshold:
                active, confirmations, age = False, 0, 0
            if active:
                result[index, column] = (
                    result[index - 1, column]
                    if age > 0 and age < policy.min_hold_sessions and value <= policy.exit_threshold
                    else value
                )
                age += 1
    return result


def fit_feature_transforms(
    values: np.ndarray, signal: SignalSpec, start: int, stop: int
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    transformed = values.copy()
    states = []
    for column, term in enumerate(signal.terms):
        if term.feature.transform != "train_zscore":
            continue
        training = values[start:stop, :, column]
        mean = np.zeros(training.shape[1])
        scale = np.ones(training.shape[1])
        valid_assets = []
        for asset in range(training.shape[1]):
            sample = training[:, asset]
            sample = sample[np.isfinite(sample)]
            valid_assets.append(len(sample) >= MIN_FIT_ROWS)
            if len(sample):
                mean[asset] = float(sample.mean())
                scale[asset] = max(float(sample.std()), EPSILON)
        transformed[:, :, column] = np.clip(
            (values[:, :, column] - mean) / scale, -term.feature.clip, term.feature.clip
        )
        transformed[:, np.logical_not(valid_assets), column] = np.nan
        states.append(
            {
                "term": column,
                "mean": mean.tolist(),
                "scale": scale.tolist(),
                "valid_assets": valid_assets,
                "fit_start": start,
                "fit_stop": stop,
            }
        )
    return transformed, states


def design(values: np.ndarray, signal: SignalSpec) -> np.ndarray:
    columns = [
        np.tanh((values[:, :, i] - term.center) / term.scale) for i, term in enumerate(signal.terms)
    ]
    for interaction in signal.interactions:
        left, right = columns[interaction.left], columns[interaction.right]
        operations = {
            "product": left * right,
            "difference": (left - right) / 2,
            "minimum": np.minimum(left, right),
            "maximum": np.maximum(left, right),
            # Smooth gate in [-1, 1]; neutral (zero) when confirmation is absent.
            "gate": left * (right + 1) / 2,
        }
        columns.append(operations[interaction.operation])
    patterns = pattern_values(values, signal)
    columns.extend(2 * patterns[:, :, i] - 1 for i in range(patterns.shape[-1]))
    return np.stack(columns, axis=-1)


def fit_linear(x: np.ndarray, y: np.ndarray, alpha: float, count: int) -> dict[str, Any]:
    x, y = x.reshape(-1, x.shape[-1]), y.reshape(-1)
    valid = np.isfinite(x).all(axis=1) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if len(y) < MIN_FIT_ROWS:
        raise ValueError("insufficient matured observations to fit signal")
    mean, scale = x.mean(axis=0), np.maximum(x.std(axis=0), EPSILON)
    z = (x - mean) / scale
    centered = y - y.mean()
    association = np.abs(np.mean(z * centered[:, None], axis=0))
    selected = np.argsort(-association, kind="stable")[:count]
    reduced = z[:, selected]
    # Normalize the loss by sample count so alpha means the same thing as the
    # training window expands. No shuffled row split or all-sample scaling.
    coef = np.linalg.solve(
        reduced.T @ reduced / len(y) + alpha * np.eye(len(selected)), reduced.T @ centered / len(y)
    )
    return {
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "selected": selected.tolist(),
        "coefficients": coef.tolist(),
        "intercept": float(y.mean()),
        "alpha": alpha,
        "training_rows": len(y),
    }


def linear_predict(x: np.ndarray, state: dict[str, Any]) -> np.ndarray:
    z = (x - np.array(state["mean"])) / np.array(state["scale"])
    return np.asarray(
        z[:, :, state["selected"]] @ np.array(state["coefficients"]) + state["intercept"]
    )


def fit_fold(
    values: np.ndarray, labels: np.ndarray, signal: SignalSpec, program: Program, start: int
) -> tuple[np.ndarray, dict[str, Any], np.ndarray]:
    begin = program.baseline.config.strategy.warmup.min_completed_sessions - 1
    stop = purged_stop(start, signal.horizon, program.evaluation.embargo_sessions)
    transformed, preprocess = fit_feature_transforms(values, signal, begin, stop)
    x = design(transformed, signal)
    state: dict[str, Any] = {
        "prediction_start": start,
        "fit_start": begin,
        "fit_stop": stop,
        "latest_label_exit": stop - 1 + signal.horizon + 1,
        "preprocessing": preprocess,
        "learner": signal.learner,
    }
    state["feature_quantiles"] = []
    patterns = pattern_values(transformed, signal)
    state["pattern_quantiles"] = []
    for column in range(patterns.shape[-1]):
        sample = patterns[begin:stop, :, column]
        sample = sample[np.isfinite(sample)]
        state["pattern_quantiles"].append(
            np.quantile(sample, [0.2, 0.4, 0.6, 0.8]).tolist() if len(sample) else []
        )
    for column in range(values.shape[-1]):
        sample = transformed[begin:stop, :, column]
        sample = sample[np.isfinite(sample)]
        state["feature_quantiles"].append(
            np.quantile(sample, [0.2, 0.4, 0.6, 0.8]).tolist() if len(sample) else []
        )
    if signal.learner == "weighted":
        weights = np.array(
            [term.weight for term in signal.terms]
            + [interaction.weight for interaction in signal.interactions]
            + [pattern.weight for pattern in signal.patterns]
        )
        forecast = (x @ weights) / np.abs(weights).sum()
        state["weights"] = weights.tolist()
        return np.clip((forecast + 1) / 2, 0, 1), state, transformed
    inner_start = stop - program.evaluation.inner_validation_sessions
    inner_stop = purged_stop(inner_start, signal.horizon, program.evaluation.embargo_sessions)
    if inner_stop - begin < MIN_FIT_ROWS:
        raise ValueError("initial training period too short for the purged inner split")
    inner_values, inner_preprocess = fit_feature_transforms(values, signal, begin, inner_stop)
    inner_x = design(inner_values, signal)
    choices = []
    for count in sorted({1, x.shape[-1]}):
        for alpha in RIDGE_ALPHAS:
            fitted = fit_linear(inner_x[begin:inner_stop], labels[begin:inner_stop], alpha, count)
            predicted = linear_predict(inner_x[inner_start:stop], fitted)
            actual = labels[inner_start:stop]
            valid = np.isfinite(predicted) & np.isfinite(actual)
            if valid.sum() < MIN_FIT_ROWS:
                continue
            loss = float(np.mean((predicted[valid] - actual[valid]) ** 2))
            choices.append((loss, count, alpha))
    if not choices:
        raise ValueError("no usable inner validation labels")
    loss, count, alpha = min(choices)
    fitted = fit_linear(x[begin:stop], labels[begin:stop], alpha, count)
    state.update(
        {
            "model": fitted,
            "inner": {
                "fit_stop": inner_stop,
                "latest_label_exit": inner_stop - 1 + signal.horizon + 1,
                "validation_start": inner_start,
                "validation_stop": stop,
                "preprocessing": inner_preprocess,
                "loss": loss,
                "feature_count": count,
                "alpha": alpha,
                "tried": len(choices),
            },
        }
    )
    return (np.tanh(linear_predict(x, fitted)) + 1) / 2, state, transformed


def build_signal(panel: MarketPanel, candidate: Candidate, program: Program) -> SignalTrace:
    """Preserve the native path; apply only the explicitly versioned candle extension."""
    if candidate.genetic_pattern:
        require_genetic_program(candidate.genetic_pattern, program.schema_version)
    trace = _build_native_signal(panel, candidate, program)
    if candidate.genetic_pattern is None:
        return trace
    scores, matches = apply_genetic_pattern(panel, trace.scores, candidate.genetic_pattern)
    return replace(trace, scores=scores, genetic_scores=matches)


def _build_native_signal(panel: MarketPanel, candidate: Candidate, program: Program) -> SignalTrace:
    base = baseline_scores(panel, candidate)
    signal = candidate.signal
    if signal is None:
        return SignalTrace(base, base, np.empty((*base.shape, 0)), base, [], [])
    symbols = [str(symbol) for symbol in panel.symbols]
    groups = tuple(
        tuple(symbols.index(str(s)) for s in cluster.symbols)
        for cluster in candidate.config.strategy.universe.clusters
    )
    values = np.stack(
        [feature_values(panel, term.feature, groups=groups) for term in signal.terms], axis=-1
    )
    if signal.router:
        return build_routed_signal(panel, candidate, program, base, values)
    raw = np.zeros_like(base)
    predictors = np.full_like(values, np.nan)
    pattern_scores = np.full((*base.shape, len(signal.patterns)), np.nan)
    available = np.zeros_like(base, dtype=bool)
    labels = forward_returns(panel, signal.horizon)
    sigma = annualized_volatility(
        log_returns(panel.adjusted_closes), candidate.config.strategy.volatility
    )
    labels = np.clip(labels / np.maximum(sigma * np.sqrt(signal.horizon / 252), EPSILON), -5, 5)
    first = next(i for i, date in enumerate(panel.sessions) if date >= program.measured_start)
    # Always refit at the same absolute boundaries, including when a longer
    # checkpoint panel is loaded. Prefix predictions must remain identical.
    fits = []
    for start in range(first, len(panel), program.evaluation.refit_sessions):
        end = min(len(panel), start + program.evaluation.refit_sessions)
        prediction, state, transformed = fit_fold(values, labels, signal, program, start)
        raw[start:end] = (1 - signal.baseline_mix) * prediction[
            start:end
        ] + signal.baseline_mix * base[start:end]
        predictors[start:end] = transformed[start:end]
        pattern_scores[start:end] = pattern_values(transformed, signal)[start:end]
        available[start:end] = np.isfinite(raw[start:end]) & np.isfinite(
            transformed[start:end]
        ).all(axis=-1)
        state.update(
            {
                "prediction_session": str(panel.sessions[start]),
                "fit_stop_session": str(panel.sessions[state["fit_stop"] - 1]),
            }
        )
        fits.append(state)
    raw = np.clip(np.nan_to_num(raw, nan=0.0), 0, 1)
    scores = policy_scores(raw, available, signal.policy)
    return SignalTrace(
        scores,
        raw,
        predictors,
        base,
        fits,
        [describe(term.feature) for term in signal.terms],
        pattern_scores,
    )


def build_routed_signal(
    panel: MarketPanel,
    candidate: Candidate,
    program: Program,
    base: np.ndarray,
    values: np.ndarray,
) -> SignalTrace:
    signal = candidate.signal
    if signal is None or signal.router is None:
        raise ValueError("routed signal requires a router")
    first = next(i for i, date in enumerate(panel.sessions) if date >= program.measured_start)
    begin = program.baseline.config.strategy.warmup.min_completed_sessions - 1
    routed = route_scores(values, signal, begin, first)
    raw = (1 - signal.baseline_mix) * routed.raw + signal.baseline_mix * base
    available = routed.available.copy()
    available[:first] = False
    raw = np.where(available, np.clip(raw, 0, 1), 0.0)
    state: dict[str, Any] = {
        "learner": "rule_router",
        "router": signal.router.model_dump(mode="json"),
        "prediction_start": first,
        "prediction_session": str(panel.sessions[first]),
        "fit_start": begin,
        "fit_stop": first,
        "fit_stop_session": str(panel.sessions[first - 1]),
        "interpretation": "pre-anchor features only; no outcome labels or fitted probabilities",
        "feature_quantiles": [],
        "routing_quantiles": [],
        "pattern_quantiles": [],
    }
    for name, array in (("feature_quantiles", values), ("routing_quantiles", routed.components)):
        for column in range(array.shape[-1]):
            sample = array[begin:first, :, column]
            sample = sample[np.isfinite(sample)]
            state[name].append(
                np.quantile(sample, [0.2, 0.4, 0.6, 0.8]).tolist() if len(sample) else []
            )
    if signal.router.mode == "training_mean":
        state["static_trend_weights"] = [
            float(value) if np.isfinite(value) else None for value in routed.components[first, :, 2]
        ]
    return SignalTrace(
        scores=policy_scores(raw, available, signal.policy),
        raw_scores=raw,
        predictors=values,
        baseline=base,
        fits=[state],
        features=[describe(term.feature) for term in signal.terms],
        routing_scores=routed.components,
    )
