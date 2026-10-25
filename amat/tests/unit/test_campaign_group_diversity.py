"""One preferred member per group must remain causal and preserve sizing limits."""

from copy import deepcopy
from pathlib import Path

import numpy as np

from amat.config.models import StrategyConfig
from amat.data.snapshot import load_panel
from amat.portfolio import decide, precompute_decision_inputs
from autoresearch.improvements.campaign_audit import sealed


def test_group_diversity_bounds_and_prefix_future_invariance() -> None:
    c = (
        Path(__file__).resolve().parents[2]
        / "outputs/research-campaigns/2026-10-19-12h-performance"
    )
    parent = sealed(c / "cycles/025-band-cap-quality/study/protocol.json")["configs"][
        "cap_quality_band"
    ]
    values = deepcopy(parent)
    values["adaptive_rotation"]["max_per_group"] = 1
    config = StrategyConfig.model_validate(values)
    symbols = list(map(str, config.universe.symbols))
    panel, _ = load_panel(Path(sealed(c / "protocol.json")["snapshot"]), symbols, with_context=True)
    prices, dates = panel.adjusted_closes[:700], panel.sessions[:700]
    cache = precompute_decision_inputs(config, prices, sessions=dates)
    assert cache.adaptive_scores is not None
    for group in config.adaptive_rotation.groups:
        columns = [symbols.index(str(s)) for s in group.symbols]
        assert np.all((cache.adaptive_scores[:, columns] > 0).sum(axis=1) <= 1)
    for index in (251, 300, 499, 650):
        prefix = precompute_decision_inputs(
            config, prices[: index + 1], sessions=dates[: index + 1]
        )
        altered = prices.copy()
        altered[index + 1 :] *= np.linspace(0.5, 2, len(symbols))
        future = precompute_decision_inputs(config, altered, sessions=dates)
        assert prefix.adaptive_scores is not None and future.adaptive_scores is not None
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
