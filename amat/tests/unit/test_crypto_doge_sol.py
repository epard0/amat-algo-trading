import numpy as np
import pandas as pd
import pytest

from autoresearch.crypto_directional import directional_weights, run_directional
from autoresearch.crypto_doge_sol import portfolio_metrics, profit_weights, select_training


def market(n=24*75):
    index = pd.date_range('2021-01-01', periods=n, freq='1h', tz='UTC')
    price = 100+np.arange(n)*.025+np.sin(np.arange(n)/30)
    f = pd.DataFrame({k: price.copy() for k in ('open', 'high', 'low', 'close', 'mark_open', 'mark_high', 'mark_low', 'mark_close')}, index=index)
    f['tradable'] = 1.
    return f


def test_original_tp_recipe_is_preserved():
    f = market()
    expected = directional_weights({'X': f}, dict(sizing='full', leverage=1, tp_atr=1.), {'X': 1})
    np.testing.assert_allclose(profit_weights(f, 1., 72), expected, equal_nan=True)


@pytest.mark.parametrize('tp,hold', [(None, None), (.5, 24), (1., 72), (2., 24)])
def test_targets_cannot_see_future_candles(tp, hold):
    f = market()
    before = profit_weights(f, tp, hold)
    f.loc[f.index[1201]:, ['open', 'high', 'low', 'close']] *= 5
    after = profit_weights(f, tp, hold)
    np.testing.assert_allclose(before[:1202], after[:1202], equal_nan=True)


def test_exact_sleeve_aggregation_equals_existing_two_asset_wallets():
    f = market(72)
    w = np.full((72, 1), .7)
    w[24:48] = 0
    dates = ('2021-01-01', '2021-01-03')
    # Funding is actually charged in both the independent and combined replay.
    funding = {int(f.index[8].timestamp()*1000)//3600000: (int(f.index[8].timestamp()*1000), '.001')}
    sleeves = [run_directional({s: f}, {s: funding}, w, dates, capital=5000) for s in ('D', 'S')]
    stats, path = portfolio_metrics([s[1] for s in sleeves], [s[0] for s in sleeves])
    expected, expected_path, _, _ = run_directional({'D': f, 'S': f}, {'D': funding, 'S': funding}, np.column_stack([w[:, 0], w[:, 0]]), dates)
    assert stats['pnl'] == pytest.approx(expected['pnl'])
    np.testing.assert_allclose(path.equity, expected_path.equity)
    assert stats['natural_closes'] == 2
    assert stats['forced_terminal_closes'] == 2


def test_selection_maximizes_pnl_only_within_registered_constraints():
    base = dict(liquidations=0, natural_closes=10, profitable_closes_per_month=3)
    rows = [dict(base, id='profit', pnl=1000, max_drawdown=-.6),
            dict(base, id='balanced', pnl=700, max_drawdown=-.3),
            dict(base, id='conservative', pnl=400, max_drawdown=-.2),
            dict(base, id='too_infrequent', pnl=900, max_drawdown=-.1, profitable_closes_per_month=1)]
    selected = select_training(rows)
    assert selected['max_pnl']['id'] == 'profit'
    assert selected['balanced']['id'] == 'balanced'
    assert selected['lower_drawdown']['id'] == 'conservative'
    assert select_training(rows[:1])['balanced'] is None


def test_mismatched_sleeve_times_are_rejected():
    p = pd.DataFrame({'equity': [5000]}, index=pd.date_range('2021-01-01', periods=1, tz='UTC'))
    with pytest.raises(ValueError, match='time grids'):
        portfolio_metrics([p, p.shift(freq='1h')], [])
