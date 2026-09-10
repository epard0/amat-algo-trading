"""Learned-regime warm-up warning: explicit, and never a hard error.

``learned_regime.fit_window_sessions`` trailing history is required before the
KMeans layer produces its first fit; until then every scalar is
``unknown_scalar`` and the layer is a no-op. Nothing validates that
``warmup.min_completed_sessions`` actually covers that window, so a shipped
configuration such as ``amat_v12_adaptive11.toml`` (warmup 378, fit window
504) has always run the layer inert for its opening 126 measured sessions
without recording that anywhere. This module pins the explicit warning added
for that gap -- deliberately a recorded warning rather than a validator,
because the existing v11/v12 configurations are hashed artifacts that must
keep validating.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from amat.backtest.runner import run_backtest
from amat.cli.main import _command_validate_config
from amat.config.loading import load_strategy_config
from amat.config.models import LearnedRegimeConfig, learned_regime_warmup_warning
from amat.data.synthetic import generate_panel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
V12_CONFIG = PROJECT_ROOT / "configs" / "strategy" / "amat_v12_adaptive11.toml"


@pytest.fixture(scope="module")
def v12_strategy():
    return load_strategy_config(V12_CONFIG).strategy


class TestTheHelperFunction:
    def test_none_when_the_layer_is_disabled(self, canonical_config_path) -> None:
        strategy = load_strategy_config(canonical_config_path).strategy
        assert strategy.learned_regime.enabled is False
        assert learned_regime_warmup_warning(strategy) is None

    def test_none_when_the_warmup_covers_the_fit_window(self, canonical_config_path) -> None:
        strategy = load_strategy_config(canonical_config_path).strategy
        covered = strategy.model_copy(
            update={
                "learned_regime": LearnedRegimeConfig(enabled=True, fit_window_sessions=252),
            }
        )
        assert covered.warmup.min_completed_sessions == 252
        assert learned_regime_warmup_warning(covered) is None

    def test_the_shipped_v12_configuration_is_flagged(self, v12_strategy) -> None:
        # warmup 378 < fit_window 504: the exact gap this task closes.
        assert v12_strategy.warmup.min_completed_sessions == 378
        assert v12_strategy.learned_regime.fit_window_sessions == 504
        warning = learned_regime_warmup_warning(v12_strategy)
        assert warning is not None
        assert warning.startswith("learned_regime: inert for the first 126 measured sessions")
        assert "fit_window_sessions (504)" in warning
        assert "warmup.min_completed_sessions (378)" in warning
        assert f"unknown_scalar {v12_strategy.learned_regime.unknown_scalar}" in warning


class TestTheRunnerRecordsTheWarning:
    def test_a_shorter_warmup_records_an_explicit_warning(self, canonical_config_path) -> None:
        strategy = load_strategy_config(canonical_config_path).strategy
        adaptive = strategy.model_copy(
            update={
                "learned_regime": LearnedRegimeConfig(
                    enabled=True, fit_window_sessions=300, seed=7
                ),
            }
        )
        # Default warmup.min_completed_sessions is 252, 48 short of 300.
        assert adaptive.warmup.min_completed_sessions == 252
        panel = generate_panel(adaptive.to_universe(), sessions=700, seed=3)
        result = run_backtest(
            adaptive, panel, adaptive.to_universe(), initial_cash=Decimal("1200000")
        )
        assert any(
            w.startswith("learned_regime: inert for the first 48 measured sessions")
            for w in result.warnings
        )

    def test_a_warmup_covering_the_fit_window_records_no_warning(
        self, canonical_config_path
    ) -> None:
        strategy = load_strategy_config(canonical_config_path).strategy
        adaptive = strategy.model_copy(
            update={
                "learned_regime": LearnedRegimeConfig(
                    enabled=True, fit_window_sessions=252, seed=7
                ),
            }
        )
        panel = generate_panel(adaptive.to_universe(), sessions=700, seed=3)
        result = run_backtest(
            adaptive, panel, adaptive.to_universe(), initial_cash=Decimal("1200000")
        )
        assert not any(w.startswith("learned_regime:") for w in result.warnings)

    def test_the_disabled_layer_records_no_warning(self, canonical_config_path) -> None:
        strategy = load_strategy_config(canonical_config_path).strategy
        panel = generate_panel(strategy.to_universe(), sessions=700, seed=3)
        result = run_backtest(
            strategy, panel, strategy.to_universe(), initial_cash=Decimal("1200000")
        )
        assert not any(w.startswith("learned_regime:") for w in result.warnings)


class TestValidateConfigPrintsTheWarning:
    def test_validate_config_prints_the_warning_for_v12(self, capsys) -> None:
        exit_code = _command_validate_config(V12_CONFIG)
        assert exit_code == 0
        out = capsys.readouterr().out
        warning_lines = [
            line for line in out.splitlines() if line.startswith("Warning: learned_regime")
        ]
        assert len(warning_lines) == 1
        assert "126 measured sessions" in warning_lines[0]

    def test_validate_config_is_silent_for_a_config_without_the_layer(
        self, capsys, canonical_config_path
    ) -> None:
        exit_code = _command_validate_config(canonical_config_path)
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Warning" not in out
