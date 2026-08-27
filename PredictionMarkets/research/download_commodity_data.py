#!/usr/bin/env python3
"""Download strict one-minute OHLCV datasets used by directional models.

Yahoo only exposes a short rolling window of one-minute history. The files are
training priors, not Kalshi settlement sources; commodity settlement prices
continue to come from Pyth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf


SYMBOLS = {
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "WTI": "CL=F",
    "BNB": "BNB-USD",
    "DOGE": "DOGE-USD",
    "HYPE": "HYPE32196-USD",
    "NEAR": "NEAR-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "ZEC": "ZEC-USD",
}


def download(output_dir: Path, *, period: str = "7d") -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded_at = datetime.now(timezone.utc).isoformat()
    assets: dict[str, dict] = {}
    for asset, symbol in SYMBOLS.items():
        frame = yf.download(
            symbol,
            period=period,
            interval="1m",
            auto_adjust=False,
            actions=False,
            progress=False,
            threads=False,
            timeout=30,
        )
        if frame.empty:
            raise RuntimeError(
                f"yfinance returned no one-minute rows for {asset} ({symbol})"
            )
        if getattr(frame.columns, "nlevels", 1) > 1:
            frame.columns = frame.columns.get_level_values(0)
        required = ["Open", "High", "Low", "Close", "Volume"]
        frame = frame[required].dropna(subset=["Open", "High", "Low", "Close"])
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        if len(frame) < 1_000:
            raise RuntimeError(f"only {len(frame)} usable one-minute rows for {asset}")
        path = output_dir / f"{asset}_1m.csv"
        frame.to_csv(path, index_label="timestamp")
        assets[asset] = {
            "symbol": symbol,
            "rows": len(frame),
            "start": frame.index[0].isoformat(),
            "end": frame.index[-1].isoformat(),
            "path": str(path),
            "interval": "1m",
            "source": "Yahoo Finance via yfinance",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    manifest = {
        "schema_version": 2,
        "downloaded_at": downloaded_at,
        "period": period,
        "interval": "1m",
        "forecast_horizon_minutes": 15,
        "columns": ["timestamp", "Open", "High", "Low", "Close", "Volume"],
        "assets": assets,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/yfinance_1m"))
    parser.add_argument("--period", default="7d")
    args = parser.parse_args()
    print(json.dumps(download(args.output_dir, period=args.period), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
