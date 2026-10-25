"""The registered five-session interaction retains causal daily risk decisions."""

from pathlib import Path

import numpy as np

from amat.config.models import StrategyConfig
from amat.data.snapshot import load_panel
from amat.portfolio import decide, precompute_decision_inputs
from autoresearch.improvements.campaign_audit import sealed


def test_registered_five_session_selection_matches_prefix_and_future_mutation() -> None:
    campaign = (
        Path(__file__).resolve().parents[2]
        / "outputs/research-campaigns/2026-10-19-12h-performance"
    )
    design = sealed(campaign / "selection-design-023.json")
    config = StrategyConfig.model_validate(design["config"])
    panel, _ = load_panel(
        Path(sealed(campaign / "protocol.json")["snapshot"]),
        list(map(str, config.universe.symbols)),
        with_context=True,
    )
    prices = panel.adjusted_closes[:600]
    dates = panel.sessions[:600]
    cache = precompute_decision_inputs(config, prices, sessions=dates)
    for index in (251, 252, 255, 256, 499):
        prefix = precompute_decision_inputs(
            config, prices[: index + 1], sessions=dates[: index + 1]
        )
        poisoned = prices.copy()
        poisoned[index + 1 :] *= np.linspace(0.4, 2.1, prices.shape[1])
        future = precompute_decision_inputs(config, poisoned, sessions=dates)
        assert cache.adaptive_scores is not None
        assert prefix.adaptive_scores is not None
        assert future.adaptive_scores is not None
        np.testing.assert_array_equal(cache.adaptive_scores[: index + 1], prefix.adaptive_scores)
        np.testing.assert_array_equal(
            cache.adaptive_scores[: index + 1], future.adaptive_scores[: index + 1]
        )
        full = decide(config, prices, config.to_universe(), decision_index=index, precomputed=cache)
        partial = decide(
            config,
            prices[: index + 1],
            config.to_universe(),
            decision_index=index,
            precomputed=prefix,
        )
        np.testing.assert_allclose(full.weights, partial.weights, rtol=0, atol=1e-12)
        assert full.gross_exposure <= 1 + 1e-12
        assert max(full.weights) <= 0.4 + 1e-12
