from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.strategy_brain.strategies.crypto_15min_predictor import (
    Crypto15MinutePredictor,
    PriceCandle,
)


def candles(prices: list[str]) -> list[PriceCandle]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        PriceCandle(
            timestamp=start + timedelta(minutes=index),
            open=Decimal(price),
            high=Decimal(price),
            low=Decimal(price),
            close=Decimal(price),
            volume=Decimal("100"),
        )
        for index, price in enumerate(prices)
    ]


def test_probability_favors_yes_when_spot_is_above_target() -> None:
    predictor = Crypto15MinutePredictor()
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = predictor.predict(
        candles(["100", "100.1", "100.2", "100.4", "100.5", "100.7", "100.9", "101.0"]),
        target_price=Decimal("100"),
        close_time=now + timedelta(minutes=2),
        now=now,
    )
    assert result.probability_yes > Decimal("0.5")


def test_probability_favors_no_when_spot_is_below_target() -> None:
    predictor = Crypto15MinutePredictor()
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = predictor.predict(
        candles(["100", "99.9", "99.8", "99.6", "99.5", "99.3", "99.1", "99.0"]),
        target_price=Decimal("100"),
        close_time=now + timedelta(minutes=2),
        now=now,
    )
    assert result.probability_yes < Decimal("0.5")


def test_predictor_requires_enough_history() -> None:
    predictor = Crypto15MinutePredictor()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        predictor.predict(
            candles(["100"] * 7),
            target_price=Decimal("100"),
            close_time=now + timedelta(minutes=2),
            now=now,
        )
    except ValueError as exc:
        assert "At least 8" in str(exc)
    else:
        raise AssertionError("Expected insufficient history to fail")


# ------------------------------------------------- distance vs drift in z --
#
# The model's z is (distance + drift*minutes)/sigma. Guards that mean to ask
# "is the strike far enough away that the settlement index cannot decide this"
# want the distance half, and are satisfied by the drift half if handed the sum.


def _flat_candles(price: float, count: int = 45) -> list[PriceCandle]:
    """A perfectly still market: zero drift, so z is distance alone."""
    base = datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)
    return [
        PriceCandle(
            timestamp=base + timedelta(minutes=i),
            open=Decimal(str(price)), high=Decimal(str(price)),
            low=Decimal(str(price)), close=Decimal(str(price)),
            volume=Decimal("1"),
        )
        for i in range(count)
    ]


def test_distance_z_strips_the_drift_term() -> None:
    """With no momentum at all the two z values must coincide."""
    candles = _flat_candles(100.0)
    prediction = Crypto15MinutePredictor().predict(
        candles=candles,
        target_price=Decimal("100.5"),
        close_time=candles[-1].timestamp + timedelta(minutes=5),
        now=candles[-1].timestamp,
    )
    assert abs(prediction.distance_z - prediction.z_score) < Decimal("0.001")


def test_a_trending_market_puts_drift_into_z_but_not_into_distance_z() -> None:
    """A rising tape moves z away from distance_z. That gap is the forecast, and
    it is what must not satisfy a distance floor.
    """
    base = datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)
    candles = [
        PriceCandle(
            timestamp=base + timedelta(minutes=i),
            open=Decimal(str(100 + i * 0.05)), high=Decimal(str(100 + i * 0.05 + 0.02)),
            low=Decimal(str(100 + i * 0.05 - 0.02)), close=Decimal(str(100 + i * 0.05)),
            volume=Decimal("1"),
        )
        for i in range(45)
    ]
    spot = float(candles[-1].close)
    prediction = Crypto15MinutePredictor().predict(
        candles=candles,
        target_price=Decimal(str(round(spot, 6))),   # strike sitting on spot
        close_time=candles[-1].timestamp + timedelta(minutes=5),
        now=candles[-1].timestamp,
    )
    # Spot is on the strike, so the distance half is ~0 whatever the model
    # thinks; the aggregate z is carried entirely by the upward drift.
    assert abs(prediction.distance_z) < Decimal("0.05")
    assert abs(prediction.z_score) > abs(prediction.distance_z)


def test_the_gate_that_lost_money_now_refuses_that_trade() -> None:
    """Reconstruction of the -$8.66 BTC scalp of 20 Aug 2026.

    Spot was $7.12 above a $69,305.38 strike with 190s left: 0.16 sigma, a coin
    flip. The aggregate z was 0.58 and cleared the 0.50 floor that exists to
    refuse exactly this, because 73% of it was drift. It settled the other way.

    The floor is unchanged; what changed is which quantity it is applied to.
    """
    from execution.scalp_gate import check_pattern

    minimum = Decimal("0.50")
    # The measured decomposition of that trade.
    z_total, z_distance = Decimal("0.58"), Decimal("0.16")

    passed_before = check_pattern(
        "CONSOLIDATION", Decimal("0.55"), z_total,
        blocked_regimes=("VOLATILE_CHOP",), minimum_confidence=Decimal("0"),
        minimum_abs_z=minimum,
    )
    passes_now = check_pattern(
        "CONSOLIDATION", Decimal("0.55"), z_distance,
        blocked_regimes=("VOLATILE_CHOP",), minimum_confidence=Decimal("0"),
        minimum_abs_z=minimum,
    )
    assert passed_before.allowed, "the aggregate z is what let this trade through"
    assert not passes_now.allowed, "distance alone must refuse it"
    assert "too close to call" in passes_now.reason
