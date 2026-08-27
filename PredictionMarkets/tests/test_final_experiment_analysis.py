from __future__ import annotations

import pytest

from research.final_experiment_analysis import _matched_exit_comparison


def _position(strategy: str, ticker: str, status: str, pnl: str) -> dict[str, str]:
    return {
        "strategy": strategy,
        "ticker": ticker,
        "outcome": "yes",
        "status": status,
        "pnl": pnl,
    }


def test_matched_exit_comparison_separates_upside_capture_and_loss_avoidance() -> None:
    rows = [
        _position("hold_to_settlement", "WINNER", "WON", "0.75"),
        _position("fixed_take_profit", "WINNER", "EXITED", "0.11"),
        _position("adaptive_take_profit", "WINNER", "EXITED", "0.64"),
        _position("hold_to_settlement", "LOSER", "LOST", "-0.80"),
        _position("fixed_take_profit", "LOSER", "EXITED", "-0.35"),
        _position("adaptive_take_profit", "LOSER", "EXITED", "-0.20"),
    ]

    fixed, adaptive = _matched_exit_comparison(rows)

    assert fixed["winner_upside_capture"] == pytest.approx(0.11 / 0.75)
    assert fixed["loss_avoided"] == pytest.approx(0.45)
    assert adaptive["winner_upside_capture"] == pytest.approx(0.64 / 0.75)
    assert adaptive["loss_avoided"] == pytest.approx(0.60)


def test_matched_exit_comparison_does_not_compare_unmatched_markets() -> None:
    rows = [_position("fixed_take_profit", "NO_HOLD", "EXITED", "0.11")]

    fixed, adaptive = _matched_exit_comparison(rows)

    assert fixed["matched_positions"] == 0
    assert adaptive["matched_positions"] == 0
