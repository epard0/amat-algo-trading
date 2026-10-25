import pytest

from autoresearch.funded_robustness import activity_eligible, cost_sensitivity, stability


def test_no_trade_or_sparse_scenario_cannot_win():
    assert not activity_eligible([{'entries': 0}])
    assert not activity_eligible([{'entries': 20}, {'entries': 9}])
    assert activity_eligible([{'entries': 10}, {'entries': 20}])


def test_discrete_position_sizing_is_separate_from_flat_cost_drag():
    a = [{'date': '2026-01-20', 'pnl': 600, 'contracts': 2, 'reason': 'session_end'}]
    b = [{'date': '2026-01-20', 'pnl': 299, 'contracts': 1, 'reason': 'session_end'}]
    r = cost_sensitivity(a, b)
    assert r['quantity_changed_days'] == 1
    assert r['total_pnl_change'] == -301
    assert r['exit_reason_changed_days'] == 0


def test_weak_chronological_block_cannot_be_hidden_by_average():
    rows = [{'pnl': p, 'contracts': 1} for p in [100]*10 + [-20]*5]
    result = stability(rows)
    assert result['mean_daily_pnl'] > 0
    assert result['worst_block_mean'] == -20
    assert result['contract_round_trips'] == 15
    assert stability(rows) == result


def test_constant_losses_fail_bootstrap_and_blocks():
    result = stability([{'pnl': -3, 'contracts': 1, 'reason': 'stop_gap'}]*20)
    assert result['bootstrap_mean_pnl_p05'] == -3
    assert result['worst_block_mean'] == -3
    assert result['stop_exits'] == 20


def test_short_history_rejected():
    with pytest.raises(ValueError):
        stability([{'pnl': 1, 'contracts': 1}]*14)
