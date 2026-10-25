"""Executed by the existing research interpreter, never imported by the trading runtime."""

import hashlib
import json
import sys
from pathlib import Path


def main():
    request = json.load(sys.stdin)
    root = Path(request["amat_root"]).resolve()
    profile = request.get("profile")
    if profile:
        for name, expected in profile["sources"].items():
            actual = hashlib.sha256((root / "src/autoresearch" / name).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError("configured crypto strategy source changed")
        actual = request["source_sha256"]
    else:
        source = root / "src/autoresearch/crypto_perpetual_study.py"
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != request["source_sha256"]:
            raise ValueError("primary crypto strategy source changed")
    sys.path.insert(0, str(root / "src"))
    import numpy as np
    import pandas as pd
    from autoresearch.crypto_perpetual_study import strategy_weights

    frames = {}
    for ticker, records in request["candles"].items():
        frame = pd.DataFrame(records).set_index("timestamp_ms")
        frame.index = pd.to_datetime(frame.index, unit="ms", utc=True)
        frame = frame.astype(float)
        # The existing engine publishes completed signal bars at the next boundary.
        # Add an unavailable observation at that boundary to obtain that causal decision.
        frame.loc[pd.to_datetime(request["as_of_ms"], unit="ms", utc=True)] = np.nan
        frames[ticker] = frame
    if profile:
        from autoresearch.crypto_directional import directional_weights
        from autoresearch.crypto_doge_sol import profit_weights

        values = []
        reasons = {}
        exit_policy = {}
        from exit_diagnostics import exit_reason

        for leg in profile["legs"]:
            frame = frames[leg["ticker"]]
            if leg["engine"] == "trend_short":
                from autoresearch.crypto_shadow_trend import decision_path

                path, why, policy = decision_path(frame, leg)
                value = path[-1]
                exit_policy[leg["ticker"]] = policy
            elif leg["engine"] == "adx_exits":
                from autoresearch.crypto_adx_switch import market_context
                from autoresearch.crypto_volatility_switch import regime_plan

                context = market_context(frame)
                rule = dict(family="switch", feature="adx14", threshold=20, ladder_when="high")
                path, why, _, _ = regime_plan(context, rule)
                value = path[-1] * float(leg["leverage"])
            elif leg["engine"] == "profit":
                path = profit_weights(frame, leg.get("tp_atr"), leg.get("max_hold_hours"))[:, 0]
                value = path[-1]
                value *= float(leg["leverage"])
            elif leg["engine"] == "directional":
                cfg = dict(
                    sizing=leg.get("sizing", "full"),
                    leverage=float(leg["leverage"]),
                    tp_atr=leg.get("tp_atr"),
                )
                path = directional_weights(
                    {"asset": frame}, cfg, {"asset": leg.get("direction", 1)}
                )[:, 0]
                value = path[-1]
            else:
                cfg = dict(
                    timeframe=4,
                    lookback=20,
                    exit_ratio=2,
                    sizing="vol20",
                    leverage=float(leg["leverage"]),
                )
                path = strategy_weights({"asset": frame}, cfg)[:, 0]
                value = path[-1]
            if leg["engine"] in ("adx_exits", "trend_short"):
                from autoresearch.crypto_partial_exits import REASONS

                reasons[leg["ticker"]] = REASONS.get(int(why[-1]), "hold")
            else:
                reasons[leg["ticker"]] = exit_reason(frame, path, leg)
            if profile["name"] == "doge-sol-adx14":
                raw = float(path[-1])
                entered = None
                if np.isfinite(raw) and raw > 0:
                    episode_path = pd.Series(path).ffill().fillna(0).to_numpy()
                    start = len(path) - 1
                    while start > 0 and episode_path[start - 1] > 0:
                        start -= 1
                    entered = int(frame.index[start].timestamp() * 1000)
                if leg["engine"] == "adx_exits" and reasons[leg["ticker"]] == "baseline_exit":
                    reasons[leg["ticker"]] = exit_reason(
                        frame, path, dict(leg, tp_atr=2.0, max_hold_hours=72)
                    )
                exit_policy[leg["ticker"]] = dict(
                    level=raw if np.isfinite(raw) else None,
                    entry_ms=entered,
                    reason=reasons[leg["ticker"]],
                )
            values.append(value * float(leg["allocation"]))
        values = np.asarray(values)
    else:
        values = strategy_weights(frames, request["config"])[-1] / len(frames)
        reasons = {ticker: "strategy" for ticker in frames}
    ready = bool(np.isfinite(values).all())
    result = {
        "version": "crypto-perps-decision/1",
        "request_id": request["request_id"],
        "as_of_ms": request["as_of_ms"],
        "source_sha256": actual,
        "ready": ready,
        "reasons": reasons,
        "weights": {t: str(float(v)) for t, v in zip(frames, values, strict=True)} if ready else {},
    }
    if profile and profile["name"] == "doge-sol-adx14":
        result["exit_policy"] = exit_policy
    if profile and any(leg["engine"] == "trend_short" for leg in profile["legs"]):
        result["signed_exit_policy"] = exit_policy
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
