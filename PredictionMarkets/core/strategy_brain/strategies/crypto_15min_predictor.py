"""A transparent, testable probability model for Kalshi 15-minute crypto markets."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from statistics import fmean, pstdev
from typing import Sequence

from core.strategy_brain.strategies.candles import PriceCandle
from core.strategy_brain.strategies.regime_model import RegimeMixture
from core.strategy_brain.strategies.commodity_model import CommodityDirectionalModel
from core.strategy_brain.strategies.market_regime import (
    NEUTRAL,
    RegimeAssessment,
    classify,
    effective_drift_minutes,
)

__all__ = ["PriceCandle", "Prediction", "Crypto15MinutePredictor"]


@dataclass(frozen=True)
class Prediction:
    probability_yes: Decimal
    current_spot: Decimal
    target_price: Decimal
    minutes_remaining: Decimal
    distance_return: Decimal
    momentum_return: Decimal
    volatility_per_minute: Decimal
    # Regime-aware fields. Defaulted so existing constructions stay valid.
    regime: str = NEUTRAL
    regime_confidence: Decimal = Decimal("0")
    raw_momentum_return: Decimal = Decimal("0")
    effective_drift_minutes: Decimal = Decimal("0")
    effective_volatility: Decimal = Decimal("0")
    applied_shrinkage: Decimal = Decimal("0")
    raw_probability_yes: Decimal = Decimal("0.5")
    # Signed distance from the strike in standard deviations of the terminal
    # distribution. Positive means spot is projected above the target.
    #
    # This is the quantity the probability is a monotone function of, and it is
    # what a late-market entry must be judged on rather than the probability
    # itself: as the horizon shrinks the standard deviation collapses, so a 95%
    # probability can mean "far from the strike" or "one tick away with seconds
    # left". Those are the same number and entirely different risks, and only
    # the second can be flipped by Kalshi settling on a different index than the
    # one the model prices from.
    z_score: Decimal = Decimal("0")
    # The same quantity with the drift term removed: purely how far spot sits
    # from the strike, in terminal standard deviations.
    #
    # ``z_score`` above is ``(distance + drift * drift_minutes) / sigma``, and
    # the two halves are different kinds of thing. Distance is a fact about the
    # present; drift is a forecast about the next few minutes. A guard that asks
    # "is the strike far enough away that the settlement index cannot decide
    # this" wants the fact, and is satisfied by the forecast if handed the sum.
    #
    # That is not hypothetical. On 20 Aug 2026 a scalp bought YES with 190s left
    # while spot was $7.12 from a $69,305 strike - 0.16 sigma, a coin flip. The
    # aggregate z was 0.58 and cleared the 0.50 floor meant to refuse exactly
    # that trade, because 73% of it was drift. It settled the other way for
    # -$8.66. Across the recorded dataset drift supplies over half of |z| in 29
    # of 558 evaluations, so this is a live hazard, not a one-off.
    distance_z: Decimal = Decimal("0")
    regime_reasons: tuple[str, ...] = ()
    regime_features: dict = field(default_factory=dict)
    directional_probability_yes: Decimal = Decimal("0.5")
    directional_blend_weight: Decimal = Decimal("0")


class Crypto15MinutePredictor:
    """Estimate the chance that settlement is at or above the Kalshi target.

    The model combines target distance, short-horizon momentum, and realized
    one-minute volatility, then adjusts all three for the prevailing regime
    (see ``market_regime``). It is deliberately conservative: drift is clipped,
    decays over the remaining horizon rather than extrapolating linearly, and
    the resulting probability is shrunk toward 50%. This is a heuristic, not a
    profitability guarantee, and should be calibrated on out-of-sample Kalshi
    settlement data before increasing risk.

    Why the regime matters. Crypto one-minute charts cycle through steep
    impulses, flat shelves, sharp reversals, and high-variance chop. A single
    momentum term treats them identically and is worst exactly where it costs
    most: immediately after a turn, where it reads the new leg as fresh trend
    and extrapolates it across the whole interval. Here, momentum is scaled by
    ``drift_multiplier`` and decayed with ``momentum_half_life_minutes``,
    volatility is scaled by ``volatility_multiplier``, and low-conviction
    regimes shrink harder toward 50%.
    """

    def __init__(
        self,
        probability_shrinkage: Decimal = Decimal("0.15"),
        drift_clip_sigma: Decimal = Decimal("0.35"),
        regime_aware: bool = True,
        regime_mixture: "RegimeMixture | None" = None,
        directional_model: "CommodityDirectionalModel | None" = None,
    ) -> None:
        self.probability_shrinkage = probability_shrinkage
        self.drift_clip_sigma = drift_clip_sigma
        self.regime_aware = regime_aware
        # Optional fitted mixture. When present it replaces the hand-tuned
        # lookup with a posterior-weighted blend; when absent (the default) the
        # classifier is unchanged. Passing an artifact is the only way to turn
        # this on, so the learned path can never engage by accident.
        self.regime_mixture = regime_mixture
        self.directional_model = directional_model

    def predict(
        self,
        candles: Sequence[PriceCandle],
        target_price: Decimal,
        close_time: datetime,
        now: datetime | None = None,
    ) -> Prediction:
        if len(candles) < 8:
            raise ValueError("At least 8 one-minute candles are required")
        if target_price <= 0:
            raise ValueError("Target price must be positive")

        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if close_time.tzinfo is None:
            close_time = close_time.replace(tzinfo=timezone.utc)

        minutes_remaining_float = max(
            (close_time - now).total_seconds() / 60.0,
            1.0 / 60.0,
        )
        closes = [float(candle.close) for candle in candles]
        if any(price <= 0 for price in closes):
            raise ValueError("Candle prices must be positive")

        log_returns = [
            math.log(current / previous)
            for previous, current in zip(closes, closes[1:])
        ]
        recent_returns = log_returns[-15:]
        volatility = max(pstdev(recent_returns), 1e-6)

        fast_momentum = math.log(closes[-1] / closes[-4]) / 3.0
        slow_momentum = fmean(recent_returns[-10:])
        raw_drift = (0.65 * fast_momentum) + (0.35 * slow_momentum)
        max_drift = volatility * float(self.drift_clip_sigma)
        clipped_drift = max(-max_drift, min(max_drift, raw_drift))

        assessment = self._assess(candles)

        # Regime response. A hostile regime cannot flip the sign of the drift,
        # only reduce how far it is trusted and how long it is assumed to last.
        drift = clipped_drift * assessment.drift_multiplier
        drift_minutes = effective_drift_minutes(
            minutes_remaining_float,
            assessment.momentum_half_life_minutes,
        )
        effective_volatility = volatility * assessment.volatility_multiplier

        distance = math.log(closes[-1] / float(target_price))
        projected_distance = distance + drift * drift_minutes
        standard_deviation = effective_volatility * math.sqrt(
            minutes_remaining_float
        )
        z_score = projected_distance / max(standard_deviation, 1e-6)
        # Distance alone, on the same scale, for the guards that mean distance.
        distance_z = distance / max(standard_deviation, 1e-6)
        raw_probability = 0.5 * (1.0 + math.erf(z_score / math.sqrt(2.0)))

        shrink = min(
            0.60,
            float(self.probability_shrinkage) + assessment.extra_shrinkage,
        )
        probability = (raw_probability * (1.0 - shrink)) + (0.5 * shrink)
        directional_probability = 0.5
        directional_weight = 0.0
        if self.directional_model is not None:
            scored = self.directional_model.predict(candles)
            if scored is not None:
                directional_probability = scored
                directional_weight = self.directional_model.blend_weight
                base_logit = math.log(max(1e-6, probability) / max(1e-6, 1 - probability))
                direction_logit = math.log(max(1e-6, scored) / max(1e-6, 1 - scored))
                probability = 1.0 / (
                    1.0 + math.exp(-(base_logit + directional_weight * direction_logit))
                )
        probability = max(0.01, min(0.99, probability))

        return Prediction(
            probability_yes=Decimal(str(probability)),
            current_spot=Decimal(str(closes[-1])),
            target_price=target_price,
            minutes_remaining=Decimal(str(minutes_remaining_float)),
            distance_return=Decimal(str(distance)),
            momentum_return=Decimal(str(drift)),
            volatility_per_minute=Decimal(str(volatility)),
            regime=assessment.regime,
            regime_confidence=Decimal(str(assessment.confidence)),
            raw_momentum_return=Decimal(str(clipped_drift)),
            effective_drift_minutes=Decimal(str(drift_minutes)),
            effective_volatility=Decimal(str(effective_volatility)),
            applied_shrinkage=Decimal(str(shrink)),
            raw_probability_yes=Decimal(str(raw_probability)),
            z_score=Decimal(str(z_score)),
            distance_z=Decimal(str(distance_z)),
            regime_reasons=assessment.reasons,
            regime_features=assessment.features.as_dict(),
            directional_probability_yes=Decimal(str(directional_probability)),
            directional_blend_weight=Decimal(str(directional_weight)),
        )

    def _assess(self, candles: Sequence[PriceCandle]) -> RegimeAssessment:
        if self.regime_aware:
            assessment = classify(candles)
            if self.regime_mixture is not None:
                return self._blend_with_mixture(assessment)
            return assessment
        return RegimeAssessment(
            regime=NEUTRAL,
            confidence=0.0,
            drift_multiplier=1.0,
            # A half-life this long is effectively linear extrapolation over a
            # 15-minute interval, which reproduces the pre-regime model.
            momentum_half_life_minutes=1e6,
            volatility_multiplier=1.0,
            extra_shrinkage=0.0,
            reasons=("regime classification disabled",),
            features=classify(candles).features,
        )

    def _blend_with_mixture(
        self, assessment: RegimeAssessment
    ) -> RegimeAssessment:
        """Replace the hand-tuned response with a posterior-weighted blend.

        The hand classifier still runs: it produces the feature vector, and its
        label is retained for the decision log so a learned run stays comparable
        with historical records. Only the four multipliers are replaced.

        Any failure falls back to the hand-tuned assessment. A model artifact is
        an optimisation; it must never be able to stop the bot from pricing.
        """
        mixture = self.regime_mixture
        if mixture is None:
            return assessment
        try:
            blend = mixture.blend(assessment.features.as_dict())
        except Exception:  # noqa: BLE001 - never let an artifact break pricing
            return assessment
        return replace(
            assessment,
            drift_multiplier=blend.drift_multiplier,
            momentum_half_life_minutes=blend.momentum_half_life_minutes,
            volatility_multiplier=blend.volatility_multiplier,
            extra_shrinkage=blend.extra_shrinkage,
            reasons=assessment.reasons
            + (
                f"mixture blend over {len(blend.posterior)} learned state(s); "
                f"dominant {blend.dominant_state} at {blend.confidence:.0%}",
            ),
        )
