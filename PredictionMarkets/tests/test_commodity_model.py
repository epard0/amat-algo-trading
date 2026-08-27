from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

from config import PROJECT_ROOT
from core.strategy_brain.strategies.candles import PriceCandle
from core.strategy_brain.strategies.commodity_model import (
    directional_features,
    load_commodity_models,
)
from core.strategy_brain.strategies.crypto_15min_predictor import (
    Crypto15MinutePredictor,
)


def candles(count: int = 520) -> list[PriceCandle]:
    start = datetime(2026, 8, 20, tzinfo=timezone.utc)
    result = []
    for index in range(count):
        price = Decimal("100") + Decimal(index) / Decimal("100")
        result.append(
            PriceCandle(
                start + timedelta(minutes=index),
                price,
                price + Decimal("0.02"),
                price - Decimal("0.02"),
                price,
                Decimal(1000 + index),
            )
        )
    return result


EXPECTED = {"GOLD", "SILVER", "WTI", "BNB", "DOGE", "HYPE", "NEAR", "SOL", "XRP", "ZEC"}


def test_shipped_directional_artifact_is_reproducible() -> None:
    manifest = json.loads((PROJECT_ROOT / "data/yfinance_1m/manifest.json").read_text())
    artifact = json.loads(
        (PROJECT_ROOT / "models/commodity_directional.json").read_text()
    )
    assert manifest["schema_version"] == artifact["schema_version"] == 2
    assert manifest["interval"] == artifact["training_interval"] == "1m"
    assert (
        manifest["forecast_horizon_minutes"]
        == artifact["forecast_horizon_minutes"]
        == 15
    )
    assert set(manifest["assets"]) == set(artifact["assets"]) == EXPECTED
    for asset, row in manifest["assets"].items():
        dataset = PROJECT_ROOT / row["path"]
        assert hashlib.sha256(dataset.read_bytes()).hexdigest() == row["sha256"]
        assert artifact["assets"][asset]["dataset_sha256"] == row["sha256"]
        trained = artifact["assets"][asset]
        assert trained["deployment_status"] in {"active", "shadow_only"}
        assert (trained["blend_weight"] > 0) == (
            trained["deployment_status"] == "active"
        )
        if trained["deployment_status"] == "active":
            assert trained["test_auc"] >= trained["minimum_deployment_auc"]
            assert trained["indicator_auc_delta"] >= 0


def test_shadow_model_is_scored_but_cannot_move_base_probability() -> None:
    models = load_commodity_models(PROJECT_ROOT / "models/commodity_directional.json")
    rows = candles()
    now = rows[-1].timestamp
    close = now + timedelta(minutes=5)
    base = Crypto15MinutePredictor().predict(rows, Decimal("105"), close, now)
    with_shadow = Crypto15MinutePredictor(directional_model=models["GOLD"]).predict(
        rows, Decimal("105"), close, now
    )
    assert with_shadow.directional_blend_weight == 0
    assert Decimal("0") < with_shadow.directional_probability_yes < Decimal("1")
    assert with_shadow.probability_yes == base.probability_yes


def test_bullish_and_bearish_indicators_have_opposite_polarity() -> None:
    rising = candles(60)
    falling = [
        PriceCandle(
            row.timestamp,
            Decimal("200") - (row.close - Decimal("100")),
            Decimal("200.02") - (row.close - Decimal("100")),
            Decimal("199.98") - (row.close - Decimal("100")),
            Decimal("200") - (row.close - Decimal("100")),
            row.volume,
        )
        for row in rising
    ]
    bullish = directional_features(rising)
    bearish = directional_features(falling)
    assert bullish is not None and bearish is not None
    assert bullish["indicator_score"] > 0
    assert bearish["indicator_score"] < 0
    assert bullish["rsi_14"] > 0 > bearish["rsi_14"]
