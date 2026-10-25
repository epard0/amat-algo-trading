"""Independent execution-accounting regression cases for realistic scenarios."""
import importlib.util
import sys
from pathlib import Path

import numpy as np

scripts = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("prop_audit", scripts / "prop_strategy_audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def bars():
    return np.tile([100., 100.01, 99.99, 100., 100000.], (390, 1))


def test_flat_market_costs_and_integer_position():
    result = audit.risk_day(bars(), np.ones(390), 50000, 48000, .01, 1.)
    # floor(100 / 1.03) = 97 shares, two $1 commissions + 194*.01
    assert result["trades"] == 1
    assert np.isclose(result["pnl"], -3.94)
    assert result["fills"] == 2


def test_gap_stop_fills_at_gap_not_requested_stop():
    b = bars()
    b[2:, :4] -= 5
    target = np.ones(390)
    target[2:] = 0
    result = audit.risk_day(b, target, 50000, 48000, .01, 1.)
    assert np.isclose(result["pnl"], -488.94)
    assert result["stops"] == 1


def test_stop_churn_is_limited_and_equity_is_conserved():
    b = bars()
    b[:, 2] = 98
    result = audit.risk_day(b, np.ones(390), 50000, 48000, .01, 1.)
    assert result["trades"] <= 3
    assert result["trades"] == 2  # daily guard exhausted
    assert np.isclose(result["pnl"], result["gross"] - result["costs"])


def test_risk_floor_monitored_intrabar():
    b = bars()
    b[2:, :4] -= 30
    result = audit.risk_day(b, np.ones(390), 50000, 48000, .01, 1.)
    assert result["breach"]
    assert result["equity"] < 48000  # adverse gap can overshoot


def test_noise_band_gap_adjustment_changes_false_entry():
    b = bars()
    b[:, :4] += 1
    b[:, 3] += .1
    history = np.tile(bars(), (14, 1, 1))
    t = audit.corrected_targets(b, history, 105, "noise_momentum", None)
    assert not np.any(t)


def test_configured_risk_and_entry_limit_are_enforced():
    b = bars()
    b[:, 2] = 98
    r = audit.risk_day(b, np.ones(390), 50000, 48000, .01, 1.,
                       risk_per_trade=25, daily_limit=50, max_entries=1)
    assert r["trades"] == 1
    assert -27 < r["pnl"] < -24
