"""Declared hypothetical price paths, calibrated only before the evaluation anchor."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.validation.distributions import clustered_innovations, joint_innovations

GENERATOR_VERSION = 2
CANDLE_GENERATOR_VERSION = 3
OPEN_DISPLACEMENT_SIGMA = 0.05
CALIBRATION = 252
MIN_HISTORY = 63
CRASH_OFFSET = 63
RECOVERY_END = 83
CORRELATION_BREAK = 126
LIQUIDITY_RECOVERY = 83
HALT_END = 68
GAP_SIZE = 0.25


@dataclass(frozen=True)
class ScenarioSpec:
    distribution: str
    assumption: str
    degrees_of_freedom: float = 5.0
    # Inclusive last required offset from the evaluation anchor. Short paths
    # may be generated for replay, but cannot claim coverage of later events.
    required_through_offset: int = 1


SCENARIO_SPECS = {
    "bull": ScenarioSpec("gaussian", "+12% annual log drift"),
    "bear": ScenarioSpec("gaussian", "-20% annual log drift"),
    "range": ScenarioSpec("gaussian", "log-price displacement persistence 0.93"),
    "whipsaw": ScenarioSpec("gaussian", "alternating 10-session pressure of +/-0.004", 5, 20),
    "crash_recovery": ScenarioSpec(
        "gaussian", "-0.22 log shock at offset 63; +0.011 for 20 sessions; -0.18 gap", 5, 83
    ),
    "correlation_break": ScenarioSpec(
        "gaussian", "correlation 0.35 to 0.95 at offset 126; volatility x1.8; drift -15%", 5, 127
    ),
    "gaussian": ScenarioSpec("gaussian", "zero log drift, constant calibrated volatility"),
    "student_t_5": ScenarioSpec("student_t", "shared radial Student's t shocks; df=5"),
    "student_t_3": ScenarioSpec(
        "student_t", "shared radial Student's t shocks; df=3; infinite fourth moment", 3
    ),
    "laplace": ScenarioSpec(
        "laplace", "shared exponential variance; unit-variance Laplace marginals"
    ),
    "skewed": ScenarioSpec("skewed", "centered skew-normal common/asset factors; shape=-4"),
    "volatility_mixture": ScenarioSpec(
        "gaussian_mixture", "8% shared high-volatility state; scales 0.65/2.5; normalized variance"
    ),
    "volatility_cluster": ScenarioSpec(
        "gaussian", "GJR-GARCH: alpha=0.08, beta=0.88, negative-shock coefficient=0.04"
    ),
    "gap_down": ScenarioSpec(
        "gaussian", "common -0.25 log-price and execution gap at offset 63", 5, 64
    ),
    "gap_up": ScenarioSpec(
        "gaussian", "common +0.25 log-price and execution gap at offset 63", 5, 64
    ),
    "liquidity_crisis": ScenarioSpec(
        "student_t",
        "offsets 63:83: volatility x2.5, volume x0.01; -0.18 shock at 63; no fills at 63:68",
        5,
        83,
    ),
    "flat": ScenarioSpec("gaussian", "zero close returns and execution gaps after the anchor"),
}
SCENARIOS = tuple(SCENARIO_SPECS)


def scenario_metadata(kind: str, *, count: int, with_candles: bool = False) -> dict[str, Any]:
    spec = SCENARIO_SPECS[kind]
    metadata = {
        **asdict(spec),
        "generator_version": GENERATOR_VERSION,
        "generated_sessions": count,
        "coverage_complete": count > spec.required_through_offset,
        "innovation_correlation": 0.35,
        "calibration_sessions": CALIBRATION,
        "daily_sigma_bounds": [0.004, 0.04],
        "interpretation": (
            "declared log-return process; scenario counts are not forecast probabilities"
        ),
    }

    if with_candles:
        metadata.update(
            generator_version=CANDLE_GENERATOR_VERSION,
            candle_open_model=(
                "previous synthetic close * exp(synthetic execution gap + independent "
                "Gaussian displacement with sigma=0.05*calibrated daily sigma); "
                "flat scenario has zero displacement; observed prefix opens preserved; "
                "assets without usable calibration opens remain unavailable"
            ),
        )
    return metadata


def panel_digest(panel: MarketPanel) -> str:
    digest = hashlib.sha256()
    digest.update(str((panel.sessions, panel.symbols, panel.source)).encode())
    for values in (
        panel.adjusted_closes,
        panel.raw_closes,
        panel.window_vwaps,
        panel.window_volumes,
        panel.adjusted_highs,
        panel.adjusted_lows,
        panel.session_volumes,
    ):
        if values is not None:
            digest.update(str(values.shape).encode())
            digest.update(np.asarray(values, dtype="<f8").tobytes())
    if panel.adjusted_opens is not None:
        digest.update(b"adjusted_opens")
        digest.update(str(panel.adjusted_opens.shape).encode())
        digest.update(np.asarray(panel.adjusted_opens, dtype="<f8").tobytes())
    return digest.hexdigest()


def scenario_panel(  # noqa: PLR0912, PLR0915 - atomic joint scenario construction
    source: MarketPanel, *, first: int, kind: str, seed: int
) -> MarketPanel:
    if kind not in SCENARIOS:
        raise ValueError(f"unknown scenario: {kind}")
    if type(first) is not int or not MIN_HISTORY <= first < len(source):
        raise ValueError("scenario needs calibration history and a forward interval")
    closes, source_highs, source_lows, source_volumes = source.context_inputs()
    history = closes[max(0, first - CALIBRATION - 1) : first]
    if not np.isfinite(history).all() or np.any(history <= 0):
        raise ValueError("scenario calibration needs finite positive history")
    sigma = np.clip(np.diff(np.log(history), axis=0).std(axis=0, ddof=1), 0.004, 0.04)
    count, assets = len(source) - first, len(source.symbols)
    time = np.arange(count)
    moves, noise = scenario_moves(sigma, count=count, kind=kind, seed=seed)
    adjusted = np.array(closes, dtype=float, copy=True)
    # Total-return hypothetical world: historical calibration prices are converted
    # too. No corporate actions may be applied on top of these price relatives.
    # Only read the calibration prefix: invalid or revised future source bars
    # must have no effect on a generated world, even through floating errors.
    windows = np.empty_like(adjusted)
    windows[:first] = source.window_vwaps[:first] * closes[:first] / source.raw_closes[:first]
    gaps = np.random.default_rng(seed + 2).normal(0, sigma * 0.3, (count, assets))
    if kind == "crash_recovery":
        gaps[time == CRASH_OFFSET] -= 0.18
    elif kind in ("gap_down", "gap_up"):
        gaps[time == CRASH_OFFSET] += -GAP_SIZE if kind == "gap_down" else GAP_SIZE
    elif kind == "liquidity_crisis":
        gaps = 0.35 * noise + gaps * 0.5
        gaps[time == CRASH_OFFSET] -= 0.18
    elif kind == "flat":
        gaps[:] = 0
    elif SCENARIO_SPECS[kind].distribution != "gaussian" or kind == "volatility_cluster":
        gaps = 0.35 * moves + gaps * 0.5
    high, low = np.array(source_highs, dtype=float), np.array(source_lows, dtype=float)
    spread = np.abs(noise) * 0.5 + 0.002
    opens = None
    if source.has_candle_inputs:
        observed_opens, _, _, _ = source.candle_inputs()
        opens = np.array(observed_opens, dtype=float, copy=True)
        # This is a declared hypothetical open process, not an observed VWAP
        # relabeled as an open. A separate RNG stream keeps legacy HLC/volume
        # draws unchanged and generated prefixes independent of horizon length.
        displacement = np.random.default_rng(seed + 3).normal(
            0, sigma * OPEN_DISPLACEMENT_SIGMA, (count, assets)
        )
        if kind == "flat":
            displacement[:] = 0
        # A series with no observed usable open in the calibration interval
        # cannot acquire candle support just by entering a hypothetical world.
        begin = max(0, first - CALIBRATION)
        observed = observed_opens[begin:first]
        open_supported = (
            np.isfinite(observed)
            & (observed > 0)
            & np.isfinite(source_lows[begin:first])
            & np.isfinite(source_highs[begin:first])
            & (source_lows[begin:first] > 0)
            & (source_lows[begin:first] <= observed)
            & (observed <= source_highs[begin:first])
        ).any(axis=0)

    # Do not clip or silently redraw extreme tails. An unrepresentable path is
    # an explicit failure, not a more flattering truncated distribution.
    try:
        with np.errstate(over="raise", under="raise", invalid="raise"):
            adjusted[first:] = closes[first - 1] * np.exp(np.cumsum(moves, axis=0))
            previous = np.vstack([adjusted[first - 1], adjusted[first:-1]])
            windows[first:] = previous * np.exp(gaps)
            high[first:] = np.maximum(adjusted[first:], windows[first:]) * np.exp(spread)
            low[first:] = np.minimum(adjusted[first:], windows[first:]) * np.exp(-spread)
            if opens is not None:
                opens[first:] = previous * np.exp(gaps + displacement)
                opens[first:, ~open_supported] = np.nan
                high[first:] = np.fmax(high[first:], opens[first:] * np.exp(spread))
                low[first:] = np.fmin(low[first:], opens[first:] * np.exp(-spread))
    except FloatingPointError as exc:
        raise ValueError(f"scenario {kind} exceeded finite positive price range") from exc
    volumes = np.array(source_volumes, dtype=float)
    base_volume = np.median(source_volumes[max(0, first - CALIBRATION) : first], axis=0)
    if not np.isfinite(base_volume).all() or np.any(base_volume <= 0):
        raise ValueError("scenario requires valid pre-anchor full-session volume")
    surprise = np.clip(np.abs(moves) / sigma, 0, 5)
    volumes[first:] = base_volume * (1 + 0.3 * surprise)
    if kind == "liquidity_crisis":
        volumes[first:][(time >= CRASH_OFFSET) & (time < LIQUIDITY_RECOVERY)] *= 0.01
    window_volumes = np.array(source.window_volumes, dtype=float)
    window_volumes[first:] = volumes[first:] * 0.05
    if kind == "liquidity_crisis":
        window_volumes[first:][(time >= CRASH_OFFSET) & (time < HALT_END)] = 0
    return MarketPanel(
        sessions=source.sessions,
        symbols=source.symbols,
        adjusted_closes=adjusted,
        raw_closes=adjusted.copy(),
        window_vwaps=windows,
        window_volumes=window_volumes,
        adjusted_highs=high,
        adjusted_lows=low,
        session_volumes=volumes,
        adjusted_opens=opens,
        source=(
            f"scenario({kind}, seed={seed}, "
            f"version={CANDLE_GENERATOR_VERSION if opens is not None else GENERATOR_VERSION})"
        ),
    )


def scenario_moves(  # noqa: PLR0912 - explicit dispatch over the frozen stress recipes
    sigma: np.ndarray, *, count: int, kind: str, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    if kind not in SCENARIOS:
        raise ValueError(f"unknown scenario: {kind}")
    sigma = np.asarray(sigma, dtype=float)
    if sigma.ndim != 1 or not sigma.size or not np.isfinite(sigma).all() or np.any(sigma <= 0):
        raise ValueError("sigma must be a nonempty vector of finite positive volatilities")
    spec = SCENARIO_SPECS[kind]
    time = np.arange(count)
    correlation = np.full((count, 1), 0.35)
    if kind == "correlation_break":
        correlation[time >= CORRELATION_BREAK] = 0.95
    innovations = joint_innovations(
        count=count,
        assets=len(sigma),
        seed=seed,
        distribution=spec.distribution,
        degrees_of_freedom=spec.degrees_of_freedom,
        correlation=correlation,
    )
    if kind == "volatility_cluster":
        innovations = clustered_innovations(innovations)
    noise = innovations * sigma
    moves = noise.copy()
    if kind == "bull":
        moves += 0.12 / 252
    elif kind == "bear":
        moves -= 0.20 / 252
    elif kind == "range":
        level = np.zeros(len(sigma))
        for index in range(count):
            next_level = 0.93 * level + noise[index]
            moves[index] = next_level - level
            level = next_level
    elif kind == "whipsaw":
        moves = noise * 0.5 + np.where((time // 10) % 2 == 0, 0.004, -0.004)[:, None]
    elif kind == "crash_recovery":
        moves[time == CRASH_OFFSET] -= 0.22
        moves[(time > CRASH_OFFSET) & (time <= RECOVERY_END)] += 0.011
    elif kind == "correlation_break":
        moves[time >= CORRELATION_BREAK] = noise[time >= CORRELATION_BREAK] * 1.8 - 0.15 / 252
    elif kind in ("gap_down", "gap_up"):
        moves[time == CRASH_OFFSET] += -GAP_SIZE if kind == "gap_down" else GAP_SIZE
    elif kind == "liquidity_crisis":
        crisis = (time >= CRASH_OFFSET) & (time < LIQUIDITY_RECOVERY)
        noise[crisis] *= 2.5
        moves = noise.copy()
        moves[time == CRASH_OFFSET] -= 0.18
    elif kind == "flat":
        moves[:] = 0
        noise[:] = 0
    return moves, noise
