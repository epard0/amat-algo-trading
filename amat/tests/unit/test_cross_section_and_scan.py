"""Cross-sectional selection, the point-in-time scanner, and the action ledger.

The properties pinned here are the ones whose failure would be silent: a
selection that depends on NumPy's sort implementation, a scanner that reads a
row it should not, a composite trend score that drifts from the horizons it is
defined as the mean of, and an exception ledger that outlives the data it was
written about.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from amat.config.models import CrossSectionConfig, StrategyConfig, TrendConfig
from amat.data.action_exceptions import load_action_exceptions
from amat.errors import ConfigurationError, DataIntegrityError
from amat.portfolio.cross_section import horizon_agreement, select_cross_section

# -- selection ---------------------------------------------------------------


def _config(**kwargs: object) -> CrossSectionConfig:
    return CrossSectionConfig(enabled=True, **kwargs)


def test_selection_keeps_the_strongest_and_zeroes_the_rest() -> None:
    scores = np.array([0.10, 0.90, 0.00, 0.50, 0.70])
    outcome = select_cross_section(scores, _config(max_positions=2))

    assert outcome.scores.tolist() == [0.0, 0.90, 0.0, 0.0, 0.70]
    assert outcome.selected.tolist() == [False, True, False, False, True]
    assert "max_positions" in outcome.binding


def test_a_selected_score_passes_through_unchanged() -> None:
    """Selection decides membership, never relative size.

    Replacing a kept score with a rank weight would change sizing too, and the
    experiment could not then attribute its result to either change.
    """
    scores = np.array([0.31, 0.77, 0.62])
    outcome = select_cross_section(scores, _config(max_positions=2))
    assert outcome.scores.tolist() == [0.0, 0.77, 0.62]


def test_ties_break_by_universe_index_not_by_sort_implementation() -> None:
    """`tanh` outputs collide exactly at the clip boundary; the winner must not
    depend on which NumPy is installed."""
    scores = np.array([0.5, 0.5, 0.5, 0.5])
    outcome = select_cross_section(scores, _config(max_positions=2))
    assert outcome.selected.tolist() == [True, True, False, False]
    assert outcome.rank.tolist() == [1, 2, 3, 4]


def test_fewer_candidates_than_slots_is_not_an_error() -> None:
    """The count is a cap. It never manufactures a position to fill itself."""
    scores = np.array([0.0, 0.4, 0.0])
    outcome = select_cross_section(scores, _config(max_positions=5))
    assert outcome.selected.tolist() == [False, True, False]
    assert outcome.binding == ()


def test_a_dead_banded_zero_never_becomes_a_position() -> None:
    scores = np.zeros(6)
    outcome = select_cross_section(scores, _config(max_positions=3))
    assert not outcome.selected.any()
    assert outcome.scores.tolist() == [0.0] * 6


def test_non_finite_scores_are_treated_as_no_trend() -> None:
    scores = np.array([np.nan, 0.6, np.inf, 0.2])
    outcome = select_cross_section(scores, _config(max_positions=3))
    assert outcome.selected.tolist() == [False, True, False, True]


# -- horizon agreement -------------------------------------------------------


def test_horizon_agreement_counts_strictly_positive_horizons() -> None:
    horizons = np.array([[1.0, -1.0, 0.0], [1.0, 1.0, 2.0], [-1.0, 1.0, 3.0]])
    assert horizon_agreement(horizons).tolist() == [2, 2, 2]


def test_a_missing_horizon_counts_as_disagreement() -> None:
    """An unavailable horizon is not evidence of a trend."""
    horizons = np.array([[1.0, 1.0], [np.nan, 1.0], [1.0, 1.0]])
    assert horizon_agreement(horizons).tolist() == [2, 3]


def test_agreement_filter_removes_assets_the_horizons_disagree_on() -> None:
    scores = np.array([0.9, 0.8, 0.7])
    horizons = np.array([[1.0, 1.0, -1.0], [1.0, -1.0, 1.0], [1.0, 1.0, 1.0]])
    outcome = select_cross_section(
        scores, _config(max_positions=3, min_horizon_agreement=3), horizon_z=horizons
    )
    assert outcome.selected.tolist() == [True, False, False]
    assert "horizon_agreement" in outcome.binding


def test_an_agreement_rule_without_horizons_raises_rather_than_skipping() -> None:
    """Silently dropping a configured filter would misdescribe the run."""
    with pytest.raises(DataIntegrityError, match="cannot be evaluated"):
        select_cross_section(np.array([0.5]), _config(min_horizon_agreement=2))


# -- configuration -----------------------------------------------------------


def test_enabling_selection_without_a_rule_is_rejected() -> None:
    with pytest.raises(ValueError, match="no-op wearing the name of a change"):
        CrossSectionConfig(enabled=True)


def test_rules_left_behind_a_disabled_flag_are_rejected() -> None:
    with pytest.raises(ValueError, match="enabled is false"):
        CrossSectionConfig(enabled=False, max_positions=5)


def test_an_agreement_threshold_no_horizon_count_can_meet_is_rejected() -> None:
    """Otherwise it validates cleanly and holds 100% cash for the whole run."""
    config = CrossSectionConfig(enabled=True, min_horizon_agreement=4)
    with pytest.raises(ValueError, match="exceeds the 3 configured trend horizons"):
        config.require_horizons(len(TrendConfig().horizons_sessions))


def test_the_default_configuration_is_inert() -> None:
    """Every config written before this section existed keeps its exact meaning."""
    default = CrossSectionConfig()
    assert default.enabled is False
    assert default.max_positions is None
    assert default.min_horizon_agreement == 0


# -- the composite / horizon identity ----------------------------------------


def test_the_composite_score_is_exactly_the_tanh_of_its_horizons() -> None:
    """`horizon_trend_scores` was extracted from `trend_scores`; if the two ever
    drift, the scanner explains ranks the allocator did not produce."""
    from amat import features

    rng = np.random.default_rng(20260820)
    prices = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, size=(800, 7)), axis=0))
    config = TrendConfig()
    returns = features.log_returns(prices)
    sigma = features.annualized_volatility(returns, _volatility_config())

    composite = features.trend_scores(prices, sigma, config, annualization_sessions=252)
    horizons = features.horizon_trend_scores(prices, sigma, config, annualization_sessions=252)

    rebuilt = np.tanh(horizons.mean(axis=0))
    rebuilt[np.abs(rebuilt) < config.deadband_abs] = 0.0
    rebuilt[~np.isfinite(horizons).all(axis=0)] = np.nan
    assert np.array_equal(composite, rebuilt, equal_nan=True)


def _volatility_config():
    from amat.config.models import VolatilityConfig

    return VolatilityConfig()


# -- the scanner -------------------------------------------------------------


def _panel(rows: int = 700, columns: int = 6):
    from amat.backtest.panel import MarketPanel
    from amat.domain.types import Symbol

    rng = np.random.default_rng(7)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, size=(rows, columns)), axis=0))
    sessions = tuple(dt.date(2020, 1, 1) + dt.timedelta(days=index) for index in range(rows))
    symbols = tuple(Symbol(f"S{index}") for index in range(columns))
    return MarketPanel(
        sessions=sessions,
        symbols=symbols,
        adjusted_closes=closes,
        raw_closes=closes,
        window_vwaps=closes,
        window_volumes=np.full((rows, columns), 1e6),
        source="synthetic",
    )


def _strategy(columns: int = 6, **cross: object) -> StrategyConfig:
    clusters = [{"name": "all", "gross_cap": 1.0, "symbols": [f"S{i}" for i in range(columns)]}]
    payload: dict[str, object] = {
        "name": "scan-test",
        "version": "0.0.1",
        "universe": {"clusters": clusters},
    }
    if cross:
        payload["cross_sectional"] = {"enabled": True, **cross}
    return StrategyConfig.model_validate(payload)


def test_a_scan_reads_nothing_after_its_session() -> None:
    """The property every look-ahead defect violates."""
    from amat.scanner import scan_universe

    panel = _panel()
    strategy = _strategy()
    target = panel.sessions[500]

    before = scan_universe(strategy, panel, session=target)

    mutated = panel.adjusted_closes.copy()
    mutated[501:] *= 3.0
    poisoned = _panel()
    object.__setattr__(poisoned, "adjusted_closes", mutated)
    after = scan_universe(strategy, poisoned, session=target)

    assert [asset.as_dict() for asset in before.assets] == [
        asset.as_dict() for asset in after.assets
    ]


def test_a_scan_before_warmup_refuses_rather_than_reporting_a_meaningless_rank() -> None:
    from amat.errors import CausalityError
    from amat.scanner import scan_universe

    panel = _panel()
    with pytest.raises(CausalityError, match="warm-up unsatisfied"):
        scan_universe(_strategy(), panel, session=panel.sessions[100])


def test_a_scan_off_the_session_axis_refuses() -> None:
    from amat.errors import CausalityError
    from amat.scanner import scan_universe

    with pytest.raises(CausalityError, match="not a session in this panel"):
        scan_universe(_strategy(), _panel(), session=dt.date(1999, 1, 1))


def test_the_scan_marks_exactly_what_the_selection_rule_would_hold() -> None:
    from amat.scanner import scan_universe

    result = scan_universe(_strategy(max_positions=2), _panel())
    assert len(result.selected) <= 2
    assert all(asset.rank > 0 for asset in result.selected)
    ranks = [asset.rank for asset in result.eligible]
    assert ranks == sorted(ranks), "eligible assets must be reported in rank order"


def test_scanning_wider_than_the_strategy_trades_is_allowed_and_labelled() -> None:
    """The scanner's whole purpose is seeing assets the configuration omits."""
    from amat.scanner import scan_universe

    strategy = _strategy(columns=3)
    result = scan_universe(strategy, _panel(columns=6))
    outside = [a for a in result.assets if a.cluster == "<not in strategy universe>"]
    assert len(outside) == 3


# -- the corporate-action exception ledger -----------------------------------


_LEDGER = """
[[exception]]
snapshot_id = "snap-1"
symbol = "SHY"
ex_date = 2021-12-01
kind = "cash_dividend"
chosen = 0.018007
rejected = [0.031906]
reason = "A reason long enough to record the evidence behind the decision made."
"""


def test_a_recorded_decision_resolves_its_own_record(tmp_path: Path) -> None:
    path = tmp_path / "ledger.toml"
    path.write_text(_LEDGER, encoding="utf-8")
    ledger = load_action_exceptions(path)

    chosen = ledger.resolve(
        snapshot_id="snap-1",
        symbol="SHY",
        ex_date=dt.date(2021, 12, 1),
        kind="cash_dividend",
        observed=[0.018007, 0.031906],
    )
    assert chosen == 0.018007


def test_a_decision_does_not_apply_to_another_snapshot(tmp_path: Path) -> None:
    """A re-ingest must re-decide, not inherit."""
    path = tmp_path / "ledger.toml"
    path.write_text(_LEDGER, encoding="utf-8")

    assert (
        load_action_exceptions(path).resolve(
            snapshot_id="snap-2",
            symbol="SHY",
            ex_date=dt.date(2021, 12, 1),
            kind="cash_dividend",
            observed=[0.018007, 0.031906],
        )
        is None
    )


def test_a_decision_about_different_values_refuses_to_apply(tmp_path: Path) -> None:
    """The entry describes a record that no longer exists."""
    path = tmp_path / "ledger.toml"
    path.write_text(_LEDGER, encoding="utf-8")

    with pytest.raises(DataIntegrityError, match="made about different data"):
        load_action_exceptions(path).resolve(
            snapshot_id="snap-1",
            symbol="SHY",
            ex_date=dt.date(2021, 12, 1),
            kind="cash_dividend",
            observed=[0.018007, 0.044],
        )


def test_a_missing_ledger_is_empty_not_an_error(tmp_path: Path) -> None:
    """Absence must be permissive; the check it feeds is the strict one."""
    ledger = load_action_exceptions(tmp_path / "absent.toml")
    assert ledger.entries == ()


def test_an_unreadable_ledger_is_not_treated_as_empty(tmp_path: Path) -> None:
    """Otherwise a typo silently re-arms a conflict an operator resolved."""
    path = tmp_path / "broken.toml"
    path.write_text("[[exception]\nnope", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="could not be read"):
        load_action_exceptions(path)


def test_a_reason_too_short_to_be_evidence_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "thin.toml"
    path.write_text(
        _LEDGER.replace(
            '"A reason long enough to record the evidence behind the decision made."',
            '"vendor bug"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="states the evidence"):
        load_action_exceptions(path)


def test_two_decisions_about_one_record_are_rejected(tmp_path: Path) -> None:
    """Which wins would otherwise be decided by file order."""
    path = tmp_path / "dup.toml"
    path.write_text(_LEDGER + _LEDGER, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="decided by file order"):
        load_action_exceptions(path)


def test_the_repository_ledger_resolves_the_real_shy_conflict() -> None:
    """The one conflict in the shipped snapshot, and the reason it was decided."""
    ledger = load_action_exceptions(Path("configs/data/action-exceptions.toml"))
    entries = ledger.for_snapshot("alpaca-sip-2026-08-11")
    entry = entries[("SHY", dt.date(2021, 12, 1), "cash_dividend")]

    assert entry.chosen == 0.018007
    assert entry.rejected == (0.031906,)
    assert "0.018063" in entry.reason, "the reason must cite the pairing evidence"


# -- the split-blind trade replay --------------------------------------------


def _fill(symbol: str, session: dt.date, side, quantity: str, price: str):
    from decimal import Decimal

    from amat.domain.fills import Fill
    from amat.domain.types import Symbol

    return Fill(
        symbol=Symbol(symbol),
        execution_session=session,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        cost_cash=Decimal("0.10"),
        reason=None,
    )


def test_a_trade_replay_that_ignores_a_split_cannot_close_the_position() -> None:
    """The defect a 92-symbol universe exposed and a 12-symbol one never did.

    The accountant applies splits to the ledger, so after a 2:1 the run
    legitimately sells twice what it bought. A replay rebuilt from fills alone
    does not see the split, so the sell looks like a short. Reproduced here
    directly on ``TradeLedger`` so the fix is pinned at its source.
    """
    from decimal import Decimal

    from amat.backtest.attribution import TradeLedger
    from amat.domain.types import Side

    blind = TradeLedger()
    blind.apply(_fill("XLE", dt.date(2025, 12, 2), Side.BUY, "100", "92.22"))
    with pytest.raises(DataIntegrityError, match="exceeds held"):
        blind.apply(_fill("XLE", dt.date(2025, 12, 16), Side.SELL, "200", "45.19"))

    aware = TradeLedger()
    aware.apply(_fill("XLE", dt.date(2025, 12, 2), Side.BUY, "100", "92.22"))
    aware.apply_split("XLE", Decimal("2"))  # ex-date 2025-12-05
    closed = aware.apply(_fill("XLE", dt.date(2025, 12, 16), Side.SELL, "200", "45.19"))
    assert closed is not None, "the position must close, not raise"
    assert aware.open_trade("XLE") is None


def test_a_split_preserves_cost_basis_exactly() -> None:
    """Quantity scales by the ratio and price by its inverse, so notional and
    realised P&L are unchanged. A split is not a profit."""
    from decimal import Decimal

    from amat.backtest.attribution import TradeLedger
    from amat.domain.types import Side

    ledger = TradeLedger()
    ledger.apply(_fill("XLE", dt.date(2025, 12, 2), Side.BUY, "100", "90.00"))
    before = ledger.open_trade("XLE")
    assert before is not None
    notional_before = before.quantity * before.average_price

    ledger.apply_split("XLE", Decimal("2"))
    after = ledger.open_trade("XLE")
    assert after is not None
    assert after.quantity == Decimal("200")
    assert after.average_price == Decimal("45")
    assert after.quantity * after.average_price == notional_before


def test_a_reverse_split_would_have_been_silent_rather_than_loud() -> None:
    """Why this went unnoticed: the dangerous direction does not raise.

    A forward split makes the sell exceed the holding and crashes. A reverse
    split leaves the replay holding *more* than it should, so the sell simply
    under-closes and leaves a phantom position with mis-stated P&L -- no error,
    wrong numbers. v1's only in-universe split was USO's 1-for-8 reverse.
    """
    from decimal import Decimal

    from amat.backtest.attribution import TradeLedger
    from amat.domain.types import Side

    blind = TradeLedger()
    blind.apply(_fill("USO", dt.date(2020, 4, 1), Side.BUY, "800", "5.00"))
    closed = blind.apply(_fill("USO", dt.date(2020, 5, 10), Side.SELL, "100", "40.00"))

    assert closed is not None, "no exception is raised -- that is the hazard"
    residual = blind.open_trade("USO")
    assert residual is not None
    assert residual.quantity == Decimal("700"), "a phantom position survives a full exit"


# -- confirmed price discontinuities -----------------------------------------


_DISCONTINUITY = """
[[price_discontinuity]]
snapshot_id = "snap-1"
symbol = "GDXJ"
session = 2020-03-17
observed_move = 0.6271
tolerance = 0.005
reason = "Sector-wide move; GDX and SIL moved together and no split is recorded."
"""


@pytest.mark.parametrize("ledger_text", [_LEDGER, _DISCONTINUITY])
def test_a_non_date_recorded_at_is_rejected(tmp_path: Path, ledger_text: str) -> None:
    path = tmp_path / "ledger.toml"
    path.write_text(f'{ledger_text}\nrecorded_at = "yesterday"\n', encoding="utf-8")

    with pytest.raises(ConfigurationError, match="non-date recorded_at"):
        load_action_exceptions(path)


def _ledger(tmp_path: Path, text: str):
    path = tmp_path / "ledger.toml"
    path.write_text(text, encoding="utf-8")
    return load_action_exceptions(path)


def test_a_confirmed_jump_is_permitted(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, _DISCONTINUITY)
    assert ledger.permits_move(
        snapshot_id="snap-1", symbol="GDXJ", session=dt.date(2020, 3, 17), move=0.6271
    )


def test_a_confirmation_does_not_generalise_to_other_symbols_or_dates(tmp_path: Path) -> None:
    """It confirms one bar. It is not a raised threshold."""
    ledger = _ledger(tmp_path, _DISCONTINUITY)
    assert not ledger.permits_move(
        snapshot_id="snap-1", symbol="GDX", session=dt.date(2020, 3, 17), move=0.6271
    )
    assert not ledger.permits_move(
        snapshot_id="snap-1", symbol="GDXJ", session=dt.date(2020, 3, 18), move=0.6271
    )
    assert not ledger.permits_move(
        snapshot_id="snap-2", symbol="GDXJ", session=dt.date(2020, 3, 17), move=0.6271
    )


def test_a_confirmation_of_a_different_move_refuses(tmp_path: Path) -> None:
    """Confirming a +62.7% bar says nothing about an +90% bar on the same date."""
    ledger = _ledger(tmp_path, _DISCONTINUITY)
    with pytest.raises(DataIntegrityError, match="made about different data"):
        ledger.permits_move(
            snapshot_id="snap-1", symbol="GDXJ", session=dt.date(2020, 3, 17), move=0.90
        )


def test_a_move_inside_tolerance_still_matches(tmp_path: Path) -> None:
    """A re-pull may differ in the last digit without being different data."""
    ledger = _ledger(tmp_path, _DISCONTINUITY)
    assert ledger.permits_move(
        snapshot_id="snap-1", symbol="GDXJ", session=dt.date(2020, 3, 17), move=0.6290
    )


def test_a_discontinuity_reason_too_short_to_be_evidence_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="states the evidence"):
        _ledger(
            tmp_path,
            _DISCONTINUITY.replace(
                '"Sector-wide move; GDX and SIL moved together and no split is recorded."',
                '"covid"',
            ),
        )


def test_a_non_positive_tolerance_is_rejected(tmp_path: Path) -> None:
    """It could never match anything, so it is a typo, not a strict setting."""
    with pytest.raises(ConfigurationError, match="non-positive tolerance"):
        _ledger(tmp_path, _DISCONTINUITY.replace("tolerance = 0.005", "tolerance = 0.0"))


def test_the_quality_gate_still_fails_on_an_unconfirmed_jump() -> None:
    """The gate is widened by exactly the confirmed bars and by nothing else."""
    import polars as pl

    from amat.data.snapshot import _check_daily_quality

    daily = pl.DataFrame(
        {
            "session_date": [dt.date(2020, 3, 16), dt.date(2020, 3, 17)],
            "symbol": ["ZZZ", "ZZZ"],
            "raw_open": [20.0, 27.0],
            "raw_high": [28.0, 33.0],
            "raw_low": [19.0, 27.0],
            "raw_close": [20.0, 32.5],
            "adjusted_close": [20.0, 32.5],
        }
    )
    with pytest.raises(DataIntegrityError, match="not explained by a"):
        _check_daily_quality(daily, [], snapshot_id="snap-1")


def test_the_repository_ledger_confirms_the_real_gdxj_bar() -> None:
    ledger = load_action_exceptions(Path("configs/data/action-exceptions.toml"))
    assert ledger.permits_move(
        snapshot_id="alpaca-sip-wide92-2026-08-20",
        symbol="GDXJ",
        session=dt.date(2020, 3, 17),
        move=0.6271,
    )
    entry = ledger.discontinuities[0]
    assert "GDX" in entry.reason and "SIL" in entry.reason, "must cite the peer evidence"


# -- the causal risk budget --------------------------------------------------


def test_the_signal_payoff_pairs_yesterdays_score_with_todays_return() -> None:
    """The whole causality argument in one assertion: the score is known before
    the return it is multiplied by."""
    from amat.portfolio.risk_budget import signal_payoffs

    scores = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    returns = np.array([[0.10, 0.20], [0.30, 0.40], [0.50, 0.60]])
    payoff = signal_payoffs(scores, returns)

    assert payoff[0].tolist() == [0.0, 0.0], "row 0 has no prior score to use"
    assert payoff[1].tolist() == [0.30, 0.0], "row 1 uses row 0's scores"
    assert payoff[2].tolist() == [0.0, 0.60], "row 2 uses row 1's scores"


def test_trailing_quality_never_reads_the_future() -> None:
    from amat.portfolio.risk_budget import trailing_quality

    rng = np.random.default_rng(11)
    payoffs = rng.normal(0, 0.01, size=(600, 5))
    full = trailing_quality(payoffs, lookback=252, min_periods=126)
    prefix = trailing_quality(payoffs[:401], lookback=252, min_periods=126)

    assert np.allclose(full[400], prefix[400], equal_nan=True)

    poisoned = payoffs.copy()
    poisoned[401:] *= 50.0
    assert np.allclose(
        full[400], trailing_quality(poisoned, lookback=252, min_periods=126)[400], equal_nan=True
    )


def test_the_budget_reallocates_rather_than_de_risking() -> None:
    """Mean-normalised: the volatility target still decides how much risk there
    is, and this only decides who carries it. A budget that quietly lowered the
    average multiplier would be a de-risking overlay in disguise -- which is the
    exact mechanism three earlier experiments died to."""
    from amat.config.models import RiskBudgetConfig
    from amat.portfolio.risk_budget import budget_multipliers

    rng = np.random.default_rng(3)
    for _ in range(25):
        quality = rng.normal(size=9)
        multipliers = budget_multipliers(quality, RiskBudgetConfig(enabled=True))
        assert abs(multipliers.mean() - 1.0) < 0.05


def test_the_budget_can_never_zero_an_asset() -> None:
    """A budget that reaches zero is a selection rule, and 0006/0007 both found
    selection does not help here."""
    from amat.config.models import RiskBudgetConfig
    from amat.portfolio.risk_budget import budget_multipliers

    config = RiskBudgetConfig(enabled=True)
    worst = budget_multipliers(np.array([-99.0, 0.0, 1.0, 2.0, 3.0]), config)
    assert worst.min() >= config.min_multiplier > 0.0
    assert worst.max() <= config.max_multiplier


def test_an_asset_without_history_is_left_alone() -> None:
    """Absence of evidence is not evidence of a bad asset; a warm-up gap must
    not become a permanent underweight."""
    from amat.config.models import RiskBudgetConfig
    from amat.portfolio.risk_budget import budget_multipliers

    quality = np.array([0.01, np.nan, -0.01, 0.02])
    multipliers = budget_multipliers(quality, RiskBudgetConfig(enabled=True))
    assert multipliers[1] == 1.0


def test_better_quality_never_receives_less_weight() -> None:
    from amat.config.models import RiskBudgetConfig
    from amat.portfolio.risk_budget import budget_multipliers

    quality = np.array([-0.02, 0.00, 0.01, 0.05])
    multipliers = budget_multipliers(quality, RiskBudgetConfig(enabled=True))
    assert list(multipliers) == sorted(multipliers), "multipliers must be monotone in quality"


def test_the_default_risk_budget_is_inert() -> None:
    from amat.config.models import RiskBudgetConfig

    assert RiskBudgetConfig().enabled is False
    assert RiskBudgetConfig().required_warmup_sessions == 0


def test_enabling_the_budget_raises_the_required_warmup() -> None:
    """The budget needs scores before it can measure what they earned, so its
    history sits on top of the trend horizon rather than beside it."""
    from amat.config.models import StrategyConfig

    payload = {
        "name": "warmup-test",
        "version": "0.0.1",
        "universe": {"clusters": [{"name": "a", "gross_cap": 1.0, "symbols": ["SPY", "QQQ"]}]},
        "risk_budget": {"enabled": True, "min_periods_sessions": 126},
        "warmup": {"min_completed_sessions": 252},
    }
    with pytest.raises(ValueError, match=r"warmup\.min_completed_sessions"):
        StrategyConfig.model_validate(payload)

    payload["warmup"] = {"min_completed_sessions": 378}
    assert StrategyConfig.model_validate(payload).warmup.min_completed_sessions == 378


def test_scan_respects_scheduled_membership_on_every_session():
    from amat.portfolio import precompute_decision_inputs
    from amat.portfolio.cross_section import select_on_schedule
    from amat.scanner import scan_universe

    panel = _panel(rows=280)
    strategy = _strategy(max_positions=2, selection_interval_sessions=5)
    cache = precompute_decision_inputs(strategy, panel.adjusted_closes)
    first = max(strategy.warmup.min_completed_sessions - 1, strategy.trend.max_horizon_sessions)
    saw_rank_difference = False
    for index in range(first, 280):
        scan = scan_universe(strategy, panel, session=panel.sessions[index])
        expected = select_on_schedule(
            cache.long_scores,
            cache.sigma_panel,
            strategy.cross_sectional,
            decision_index=index,
            first_decision_index=first,
            horizon_scores=cache.horizon_scores,
        )
        assert {a.symbol for a in scan.selected} == {
            str(symbol) for symbol, value in zip(panel.symbols, expected, strict=True) if value > 0
        }
        assert scan.ranking_refresh_session == panel.sessions[first + (index - first) // 5 * 5]
        saw_rank_difference |= {a.symbol for a in scan.selected} != {
            a.symbol for a in scan.eligible[:2]
        }
    assert saw_rank_difference, "fixture must exercise rank changes between refreshes"


def test_scan_without_selection_admits_every_positive_usable_signal():
    from amat.scanner import scan_universe

    result = scan_universe(_strategy(), _panel())
    assert {a.symbol for a in result.selected} == {a.symbol for a in result.assets if a.score > 0}
    assert result.ranking_refresh_session is None
