"""Point-in-time asset scan: what the strategy sees, ranked, at one instant.

This answers "which assets look best right now, and why" using *exactly* the
features the allocator consumes -- the same volatility estimate, the same
per-horizon normalized trends, the same dead-band, the same selection rule.
That identity is the point. A scanner with its own indicator definitions would
eventually disagree with the strategy, and the disagreement would be discovered
during a live session rather than in a test.

Causality
---------
A scan is computed from the panel prefix through the requested session and
nothing after it. The features are the causal recursions
:func:`amat.portfolio.precompute_decision_inputs` builds, so a scan dated
``t`` is bit-identical to what the strategy decided from at ``t`` -- including
when ``t`` is years in the past. This is what makes the scanner usable as a
research instrument on history rather than only as a live dashboard.

What a rank is and is not
-------------------------
The rank orders assets by a volatility-normalized multi-horizon trend. It is a
statement about the *strength and consistency of a price trend*, which is the
only thing this strategy claims to measure. It is not a forecast, not a
valuation, and not a judgement about an asset's quality. An asset can rank
first and lose money; the frozen core's own history is 32.7% winning trades.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from amat.backtest.panel import MarketPanel
from amat.config.models import StrategyConfig
from amat.errors import CausalityError
from amat.portfolio import precompute_decision_inputs
from amat.portfolio.cross_section import horizon_agreement, select_cross_section

__all__ = ["AssetScan", "ScanResult", "scan_universe"]


@dataclass(frozen=True, slots=True)
class AssetScan:
    """One asset's standing at one session."""

    symbol: str
    rank: int
    """1-based rank among eligible assets; ``0`` when the asset is not eligible."""
    score: float
    """The dead-banded composite in ``[0, 1]``. Zero means no trend, by contract."""
    horizon_z: tuple[float, ...]
    """Per-horizon volatility-normalized trends, in configured horizon order."""
    horizons_positive: int
    annualized_volatility: float
    selected: bool
    """Whether the configured selection rule would hold this asset."""
    cluster: str

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "rank": self.rank,
            "score": self.score,
            "horizon_z": list(self.horizon_z),
            "horizons_positive": self.horizons_positive,
            "annualized_volatility": self.annualized_volatility,
            "selected": self.selected,
            "cluster": self.cluster,
        }


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Every asset at one session, strongest first."""

    session: dt.date
    horizons: tuple[int, ...]
    assets: tuple[AssetScan, ...]
    selection_enabled: bool
    max_positions: int | None
    min_horizon_agreement: int

    @property
    def eligible(self) -> tuple[AssetScan, ...]:
        """Assets with a live trend, in rank order."""
        return tuple(asset for asset in self.assets if asset.rank > 0)

    @property
    def selected(self) -> tuple[AssetScan, ...]:
        return tuple(asset for asset in self.assets if asset.selected)

    def as_dict(self) -> dict[str, object]:
        return {
            "session": self.session.isoformat(),
            "horizons_sessions": list(self.horizons),
            "selection_enabled": self.selection_enabled,
            "max_positions": self.max_positions,
            "min_horizon_agreement": self.min_horizon_agreement,
            "eligible_count": len(self.eligible),
            "selected_count": len(self.selected),
            "assets": [asset.as_dict() for asset in self.assets],
        }


def scan_universe(
    strategy: StrategyConfig,
    panel: MarketPanel,
    *,
    session: dt.date | None = None,
) -> ScanResult:
    """Rank every asset in ``panel`` as of ``session``.

    Args:
        strategy: Validated configuration. Its trend, volatility, and
            cross-sectional sections define every number reported.
        panel: Market data. Columns need not match the strategy's own universe
            -- scanning a wider panel than the strategy trades is the point of
            a scanner, so the caller may pass any panel the features support.
        session: Decision session. Defaults to the panel's last session. Rows
            after it are never read.

    Raises:
        CausalityError: if ``session`` is not in the panel, or falls before the
            configured warm-up is satisfied. Reporting a rank from a
            half-warmed volatility estimate would be a number with no meaning
            and no warning attached to it.
    """
    sessions = list(panel.sessions)
    target = session or sessions[-1]
    if target not in sessions:
        message = (
            f"{target.isoformat()} is not a session in this panel "
            f"({sessions[0].isoformat()}..{sessions[-1].isoformat()})"
        )
        raise CausalityError(message)

    index = sessions.index(target)
    completed = index + 1
    if completed < strategy.warmup.min_completed_sessions:
        message = (
            f"warm-up unsatisfied at {target.isoformat()}: {completed} completed sessions, "
            f"need {strategy.warmup.min_completed_sessions}"
        )
        raise CausalityError(message)

    # Slice first, compute second -- nothing after the decision session is ever
    # materialised, so a look-ahead defect has nowhere to hide.
    prefix = panel.adjusted_closes[: index + 1]
    from amat import features  # noqa: PLC0415 - peer layer; module-scope import cycles

    precomputed = precompute_decision_inputs(strategy, prefix)
    sigma_panel = precomputed.sigma_panel
    horizons = features.horizon_trend_scores(
        prefix,
        sigma_panel,
        strategy.trend,
        annualization_sessions=strategy.volatility.annualization_sessions,
    )

    row = precomputed.long_scores[-1]
    sigma_row = sigma_panel[-1]
    horizon_row = horizons[:, -1, :]
    safe = np.where(np.isfinite(row), np.nan_to_num(row, nan=0.0), 0.0)
    agreeing = horizon_agreement(horizon_row)

    selection = strategy.cross_sectional
    outcome = select_cross_section(
        safe,
        selection,
        horizon_z=horizon_row if selection.min_horizon_agreement > 0 else None,
    )

    cluster_of = {
        symbol: cluster.name for cluster in strategy.universe.clusters for symbol in cluster.symbols
    }

    assets = [
        AssetScan(
            symbol=str(symbol),
            rank=int(outcome.rank[column]),
            score=float(safe[column]),
            horizon_z=tuple(float(value) for value in horizon_row[:, column]),
            horizons_positive=int(agreeing[column]),
            annualized_volatility=float(sigma_row[column]),
            selected=bool(outcome.selected[column]),
            cluster=cluster_of.get(str(symbol), "<not in strategy universe>"),
        )
        for column, symbol in enumerate(panel.symbols)
    ]
    # Ranked assets first in rank order, then everything else by symbol, so the
    # interesting rows are at the top of a 92-line table.
    assets.sort(key=lambda asset: (asset.rank == 0, asset.rank, asset.symbol))

    return ScanResult(
        session=target,
        horizons=tuple(strategy.trend.horizons_sessions),
        assets=tuple(assets),
        selection_enabled=selection.enabled,
        max_positions=selection.max_positions,
        min_horizon_agreement=selection.min_horizon_agreement,
    )
