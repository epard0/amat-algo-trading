import pytest

from autoresearch.funded_rsi_diagnostics import paired


def test_paired_differences_and_winner_removal():
    plain = [{'date': str(i), 'pnl': 1, 'contracts': 1} for i in range(15)]
    filtered = [{'date': str(i), 'pnl': 3, 'contracts': 1} for i in range(15)]
    result = paired(filtered, plain)
    assert result['difference_diagnostics']['total_pnl'] == 30
    assert result['difference_diagnostics']['bootstrap_mean_pnl_p05'] == 2
    assert result['filtered_pnl_without_largest_winning_day'] == 42
    plain[0]['date'] = 'wrong'
    with pytest.raises(ValueError, match='Unaligned'):
        paired(filtered, plain)
