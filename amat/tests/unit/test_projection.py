"""The forward projection: hand-checked mechanics, determinism, and refusals.

The projection is a resampling statement, so what the tests pin is arithmetic
identity where it is exactly computable (constant returns, funding scaling),
bit-level determinism, and the fail-closed edges. No test asserts anything
about market outcomes -- there are none here to assert.
"""

from __future__ import annotations

import numpy as np
import pytest

from amat.errors import DataIntegrityError
from amat.validation.projection import (
    PROJECTION_PERCENTILES,
    project_forward,
    session_returns,
)

_YEAR = 252


def _constant_returns(rate: float, sessions: int = 600) -> np.ndarray:
    return np.full(sessions, rate, dtype=np.float64)


class TestSessionReturns:
    def test_hand_computed_relatives(self) -> None:
        curve = [100.0, *[100.0] * 251, 110.0, 99.0]
        returns = session_returns(curve)
        assert returns[-2] == pytest.approx(0.10)
        assert returns[-1] == pytest.approx(99.0 / 110.0 - 1.0)
        assert returns.size == len(curve) - 1

    def test_a_short_curve_is_refused(self) -> None:
        with pytest.raises(DataIntegrityError, match="at least"):
            session_returns([100.0] * 200)

    def test_non_positive_equity_is_refused(self) -> None:
        curve = [100.0] * 300
        curve[150] = 0.0
        with pytest.raises(DataIntegrityError, match="non-positive"):
            session_returns(curve)


class TestProjectForward:
    def test_constant_returns_project_exactly(self) -> None:
        # Every resampled draw of a constant series is the same number, so the
        # projection is exactly computable by hand at every percentile.
        rate = 0.001
        study = project_forward(
            _constant_returns(rate),
            strategy="test v0",
            funding=100_000.0,
            horizon_years=(1, 2),
            paths=16,
            seed=7,
        )
        one_year, two_year = study.horizons
        assert one_year.sessions == _YEAR
        expected_1y = 100_000.0 * (1.0 + rate) ** _YEAR
        for key in one_year.terminal_equity:
            assert one_year.terminal_equity[key] == pytest.approx(expected_1y)
        assert one_year.max_drawdown["p50"] == 0.0
        assert one_year.probability_below_funding == 0.0
        assert one_year.probability_drawdown_worse_20pct == 0.0
        expected_2y = 100_000.0 * (1.0 + rate) ** (2 * _YEAR)
        assert two_year.terminal_equity["p50"] == pytest.approx(expected_2y)

    def test_funding_scales_every_outcome_linearly(self) -> None:
        rng = np.random.default_rng(3)
        returns = rng.normal(0.0003, 0.01, size=700)
        small = project_forward(
            returns, strategy="s", funding=1.0, horizon_years=(1,), paths=64, seed=11
        )
        large = project_forward(
            returns, strategy="s", funding=250_000.0, horizon_years=(1,), paths=64, seed=11
        )
        for key in small.horizons[0].terminal_equity:
            assert large.horizons[0].terminal_equity[key] == pytest.approx(
                250_000.0 * small.horizons[0].terminal_equity[key]
            )
        # Returns and drawdowns are funding-invariant.
        assert large.horizons[0].total_return == pytest.approx(small.horizons[0].total_return)
        assert large.horizons[0].max_drawdown == pytest.approx(small.horizons[0].max_drawdown)

    def test_identical_seeds_reproduce_bit_identically(self) -> None:
        rng = np.random.default_rng(5)
        returns = rng.normal(0.0, 0.01, size=500)
        first = project_forward(
            returns, strategy="s", funding=1000.0, horizon_years=(1, 3), paths=32, seed=42
        )
        second = project_forward(
            returns, strategy="s", funding=1000.0, horizon_years=(1, 3), paths=32, seed=42
        )
        assert first.as_dict() == second.as_dict()
        assert first.wealth_bands == second.wealth_bands
        third = project_forward(
            returns, strategy="s", funding=1000.0, horizon_years=(1, 3), paths=32, seed=43
        )
        assert third.as_dict() != first.as_dict()

    def test_horizons_are_nested_prefixes(self) -> None:
        # The 1y distribution must equal the prefix of the longest-horizon
        # paths, so running (1,) alone and (1, 5) together agree exactly at 1y.
        rng = np.random.default_rng(9)
        returns = rng.normal(0.0005, 0.012, size=800)
        alone = project_forward(
            returns, strategy="s", funding=100.0, horizon_years=(1,), paths=48, seed=1
        )
        nested = project_forward(
            returns, strategy="s", funding=100.0, horizon_years=(1, 5), paths=48, seed=1
        )
        assert nested.horizons[0].terminal_equity == alone.horizons[0].terminal_equity
        assert nested.horizons[0].max_drawdown == alone.horizons[0].max_drawdown

    def test_percentile_grid_is_complete_and_ordered(self) -> None:
        rng = np.random.default_rng(2)
        study = project_forward(
            rng.normal(0.0, 0.01, size=400),
            strategy="s",
            funding=1000.0,
            horizon_years=(1,),
            paths=128,
            seed=6,
        )
        terminal = study.horizons[0].terminal_equity
        keys = [f"p{percentile:02d}" for percentile in PROJECTION_PERCENTILES]
        assert list(terminal) == keys
        values = [terminal[key] for key in keys]
        assert values == sorted(values)
        assert len(study.wealth_bands["p50"]) == study.horizons[-1].sessions

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"funding": 0.0}, "funding"),
            ({"horizon_years": ()}, "at least one"),
            ({"horizon_years": (0,)}, "positive"),
            ({"paths": 1}, "paths"),
        ],
    )
    def test_bad_inputs_fail_closed(self, kwargs, match) -> None:
        base = {
            "strategy": "s",
            "funding": 100.0,
            "horizon_years": (1,),
            "paths": 4,
            "seed": 0,
        }
        with pytest.raises(DataIntegrityError, match=match):
            project_forward(_constant_returns(0.0), **{**base, **kwargs})
