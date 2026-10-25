"""Causal condition discovery over ragged symbol histories; never order authority."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl

from amat.config.models import StrategyConfig
from amat.data.action_exceptions import load_action_exceptions
from amat.data.snapshot import _check_daily_quality, load_manifest
from amat.errors import DataIntegrityError
from amat.portfolio import precompute_decision_inputs
from amat.portfolio.cross_section import horizon_agreement

_MAX_DURATION = 252
_MAX_RESULTS = 100
_LIQUIDITY_WINDOW = 20


def _measure(
    strategy: StrategyConfig,
    data: pl.DataFrame,
    *,
    first: int,
    condition_sessions: int,
    minimum_dollar_volume: float,
) -> dict[str, Any]:
    cache = precompute_decision_inputs(strategy, data["adjusted_close"].to_numpy().reshape(-1, 1))
    scores = cache.long_scores[:, 0]
    sigma = cache.sigma_panel[:, 0]
    eligible = np.isfinite(scores) & (scores > 0) & np.isfinite(sigma) & (sigma > 0)
    agreeing = np.zeros(len(scores), dtype=int)
    if cache.horizon_scores is not None:
        agreeing = horizon_agreement(cache.horizon_scores[:, :, 0])
        eligible &= agreeing >= strategy.cross_sectional.min_horizon_agreement
    eligible[:first] = False
    streak = 0
    for value in eligible[::-1]:
        if not value:
            break
        streak += 1
    dollar_volume = cast(
        float,
        (
            data["raw_close"].tail(_LIQUIDITY_WINDOW) * data["raw_volume"].tail(_LIQUIDITY_WINDOW)
        ).mean(),
    )
    if (
        streak < condition_sessions
        or not np.isfinite(dollar_volume)
        or dollar_volume < minimum_dollar_volume
    ):
        return {
            "reason": "duration or trailing liquidity requirement not met",
            "qualifying_sessions": streak,
        }
    return {
        "score": float(scores[-1]),
        "qualifying_sessions": streak,
        "annualized_volatility": float(sigma[-1]),
        "horizons_positive": int(agreeing[-1]),
        "average_daily_dollar_volume_20_sessions": dollar_volume,
        "selected": True,
    }


def screen_conditions(
    strategy: StrategyConfig,
    snapshot: Path,
    *,
    session: dt.date | None = None,
    condition_sessions: int = 5,
    top: int = 20,
    minimum_dollar_volume: float = 1_000_000,
) -> dict[str, Any]:
    """Find trends that have qualified for N *past* completed exchange sessions.

    Each asset uses its own contiguous calendar prefix, so a late listing cannot
    shorten every other asset's history. A gap restarts warm-up. Universe membership
    is the saved dataset's membership, not an invented historical constituent list.
    No claim is made about how long future conditions will persist.
    """
    if strategy.adaptive_rotation.enabled:
        raise ValueError(
            "Use the portfolio scan for adaptive rotation; "
            "duration screens use base trend conditions"
        )
    if not 1 <= condition_sessions <= _MAX_DURATION or not 1 <= top <= _MAX_RESULTS:
        raise ValueError("condition sessions must be 1..252 and top must be 1..100")
    if not np.isfinite(minimum_dollar_volume) or minimum_dollar_volume < 0:
        raise ValueError("minimum dollar volume must be finite and nonnegative")
    manifest = load_manifest(snapshot)
    frame = pl.read_parquet(snapshot / "daily.parquet")
    target = session or manifest.last_session
    frame = frame.filter(pl.col("session_date") <= target).sort("symbol", "session_date")
    calendar = frame.filter(pl.col("symbol") == "SPY")["session_date"].unique().sort().to_list()
    if target not in calendar:
        raise DataIntegrityError("requested session is absent from the SPY reference calendar")
    calendar_index = {date: index for index, date in enumerate(calendar)}
    first = max(strategy.warmup.min_completed_sessions - 1, strategy.trend.max_horizon_sessions)
    exceptions = load_action_exceptions(snapshot.parents[2] / "configs/data/action-exceptions.toml")
    matches, excluded = [], []
    for key, source in frame.partition_by("symbol", as_dict=True).items():
        data = source
        symbol = str(key[0])
        if data["session_date"][-1] != target:
            excluded.append(
                {"symbol": symbol, "reason": "no completed observation at the requested session"}
            )
            continue
        dates = data["session_date"].to_list()
        indices = [calendar_index.get(date, -1) for date in dates]
        last_gap = max(
            (
                i
                for i in range(1, len(indices))
                if indices[i] != indices[i - 1] + 1 or indices[i - 1] < 0
            ),
            default=0,
        )
        data = data.slice(last_gap)
        if data.height < first + condition_sessions:
            excluded.append(
                {
                    "symbol": symbol,
                    "reason": "insufficient contiguous history for warm-up and requested duration",
                }
            )
            continue
        try:
            _check_daily_quality(data, [], snapshot_id=manifest.snapshot_id, exceptions=exceptions)
        except DataIntegrityError as exc:
            excluded.append({"symbol": symbol, "reason": str(exc)})
            continue
        closes = data["adjusted_close"].to_numpy().reshape(-1, 1)
        if not np.all(np.isfinite(closes)) or np.any(closes <= 0):
            excluded.append(
                {"symbol": symbol, "reason": "nonfinite or nonpositive adjusted prices"}
            )
            continue
        measured = {
            "symbol": symbol,
            **_measure(
                strategy,
                data,
                first=first,
                condition_sessions=condition_sessions,
                minimum_dollar_volume=minimum_dollar_volume,
            ),
        }
        if "reason" in measured:
            excluded.append(measured)
        else:
            matches.append(measured)
    matches.sort(key=lambda row: (-row["score"], row["symbol"]))
    for rank, row in enumerate(matches, start=1):
        row["rank"] = rank
    return {
        "schema": "strategy-condition-screen/1",
        "session": str(target),
        "snapshot_id": manifest.snapshot_id,
        "condition_sessions": condition_sessions,
        "duration_scope": "past completed trading sessions; no future persistence forecast",
        "selection_scope": (
            "discovery_only; does not expand the trading universe or form portfolio weights"
        ),
        "minimum_average_daily_dollar_volume": minimum_dollar_volume,
        "scanned_symbols": len(manifest.symbols),
        "matched_count": len(matches),
        "assets": matches[:top],
        "excluded": excluded,
        "universe_limitation": (
            "Saved curated universe; not delisting-complete. "
            "Later listings and gaps retain individual warm-up."
        ),
    }
