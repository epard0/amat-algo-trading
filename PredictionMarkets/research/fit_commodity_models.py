#!/usr/bin/env python3
"""Fit per-asset 15-minute direction priors from one-minute OHLCV bars."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score


FEATURES = (
    "return_1",
    "return_3",
    "return_8",
    "return_15",
    "range_5",
    "volume_z",
    "ema_spread",
    "close_vs_ema20",
    "macd_signal",
    "rsi_14",
    "bollinger_z",
    "candle_pressure_5",
    "indicator_score",
    "hour_sin",
    "hour_cos",
)
BASE_FEATURES = (
    "return_1",
    "return_3",
    "return_8",
    "return_15",
    "range_5",
    "volume_z",
    "hour_sin",
    "hour_cos",
)
EXPECTED_ASSETS = {
    "GOLD",
    "SILVER",
    "WTI",
    "BNB",
    "DOGE",
    "HYPE",
    "NEAR",
    "SOL",
    "XRP",
    "ZEC",
}
MINIMUM_DEPLOYMENT_AUC = 0.55
FORECAST_HORIZON = 15


def feature_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp")
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    close = frame["Close"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    log_close = np.log(close)
    result = pd.DataFrame(index=frame.index)
    for period in (1, 3, 8, 15):
        result[f"return_{period}"] = log_close.diff(period) / period
    result["range_5"] = ((high - low) / close).rolling(5).mean()
    volume = np.log1p(frame["Volume"].astype(float))
    result["volume_z"] = (volume - volume.rolling(32).mean()) / volume.rolling(
        32
    ).std().replace(0, np.nan)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    result["ema_spread"] = (ema12 - ema26) / close
    result["close_vs_ema20"] = (close - ema20) / close
    macd = ema12 - ema26
    result["macd_signal"] = (macd - macd.ewm(span=9, adjust=False).mean()) / close
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(14).mean()
    losses = (-delta.clip(upper=0)).rolling(14).mean()
    both_flat = (gains == 0) & (losses == 0)
    rsi = 100 - 100 / (1 + gains / losses.replace(0, np.nan))
    rsi = rsi.mask((losses == 0) & ~both_flat, 100).mask(both_flat, 50)
    result["rsi_14"] = (rsi - 50) / 50
    mean20 = close.rolling(20).mean()
    std20 = close.rolling(20).std().replace(0, np.nan)
    result["bollinger_z"] = (close - mean20) / std20
    body = (close - frame["Open"].astype(float)) / (high - low).replace(0, np.nan)
    result["candle_pressure_5"] = body.rolling(5).mean()
    result["indicator_score"] = (
        np.sign(result["ema_spread"])
        + np.sign(result["macd_signal"])
        + np.sign(result["rsi_14"])
        + np.sign(result["close_vs_ema20"])
    ) / 4
    hour = frame.index.hour + frame.index.minute / 60
    result["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    result["hour_cos"] = np.cos(2 * np.pi * hour / 24)

    future_close = close.shift(-FORECAST_HORIZON)
    future_time = frame.index.to_series().shift(-FORECAST_HORIZON)
    continuous = (future_time - frame.index.to_series()).dt.total_seconds() <= 20 * 60
    result["label"] = (
        (future_close > close).astype(float).where(continuous & future_close.notna())
    )
    return result.replace([np.inf, -np.inf], np.nan).dropna()


def fit_directory(data_dir: Path, output: Path) -> dict:
    artifacts = {
        "schema_version": 2,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_interval": "1m",
        "forecast_horizon_minutes": FORECAST_HORIZON,
        "validation": "chronological 80/20 holdout with a 15-row purge gap",
        "assets": {},
    }
    for path in sorted(data_dir.glob("*_1m.csv")):
        asset = path.stem.removesuffix("_1m")
        frame = feature_frame(path)
        split = int(len(frame) * 0.80)
        # The label looks 15 rows ahead. Purging the last training horizon keeps
        # its targets entirely outside the validation period.
        train, test = frame.iloc[: split - FORECAST_HORIZON], frame.iloc[split:]
        if (
            len(test) < 100
            or train["label"].nunique() < 2
            or test["label"].nunique() < 2
        ):
            raise RuntimeError(f"{asset}: insufficient chronological classes/rows")
        means = train[list(FEATURES)].mean()
        scales = train[list(FEATURES)].std().replace(0, 1)
        x_train = (train[list(FEATURES)] - means) / scales
        x_test = (test[list(FEATURES)] - means) / scales
        model = LogisticRegression(C=0.20, max_iter=2_000, class_weight="balanced")
        model.fit(x_train, train["label"])
        probabilities = model.predict_proba(x_test)[:, 1]
        test_auc = float(roc_auc_score(test["label"], probabilities))
        baseline_means = train[list(BASE_FEATURES)].mean()
        baseline_scales = train[list(BASE_FEATURES)].std().replace(0, 1)
        baseline = LogisticRegression(C=0.20, max_iter=2_000, class_weight="balanced")
        baseline.fit(
            (train[list(BASE_FEATURES)] - baseline_means) / baseline_scales,
            train["label"],
        )
        baseline_probabilities = baseline.predict_proba(
            (test[list(BASE_FEATURES)] - baseline_means) / baseline_scales
        )[:, 1]
        baseline_auc = float(roc_auc_score(test["label"], baseline_probabilities))
        indicator_auc_delta = test_auc - baseline_auc
        deployable = test_auc >= MINIMUM_DEPLOYMENT_AUC and indicator_auc_delta >= 0
        artifacts["assets"][asset] = {
            "feature_names": list(FEATURES),
            "feature_means": [float(means[name]) for name in FEATURES],
            "feature_scales": [float(scales[name]) for name in FEATURES],
            "coefficients": [float(value) for value in model.coef_[0]],
            "intercept": float(model.intercept_[0]),
            "blend_weight": float(min(0.25, (test_auc - 0.5) * 1.5))
            if deployable
            else 0.0,
            "deployment_status": "active" if deployable else "shadow_only",
            "minimum_deployment_auc": MINIMUM_DEPLOYMENT_AUC,
            "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "train_rows": len(train),
            "test_rows": len(test),
            "test_accuracy": float(accuracy_score(test["label"], probabilities >= 0.5)),
            "test_log_loss": float(log_loss(test["label"], probabilities)),
            "test_auc": test_auc,
            "baseline_test_auc": baseline_auc,
            "indicator_auc_delta": indicator_auc_delta,
            "positive_rate_train": float(train["label"].mean()),
            "positive_rate_test": float(test["label"].mean()),
            "train_end": train.index[-1].isoformat(),
            "test_start": test.index[0].isoformat(),
            "test_end": test.index[-1].isoformat(),
        }
    if set(artifacts["assets"]) != EXPECTED_ASSETS:
        missing = EXPECTED_ASSETS - set(artifacts["assets"])
        extra = set(artifacts["assets"]) - EXPECTED_ASSETS
        raise RuntimeError(
            f"unexpected datasets: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(artifacts, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/yfinance_1m"))
    parser.add_argument(
        "--out", type=Path, default=Path("models/commodity_directional.json")
    )
    args = parser.parse_args()
    print(json.dumps(fit_directory(args.data_dir, args.out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
