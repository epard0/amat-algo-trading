"""Regression evidence for simulation measurement and action reconstruction."""

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from test_simulation import SYMBOLS, _snapshot, _strategy

from amat.data.snapshot import load_panel
from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.fills import MissedFillReason
from amat.simulation import SimulatedLiveAccount, _position_quantity


def test_simulation_applies_observed_one_percent_cap_and_whole_shares(tmp_path, monkeypatch):
    from amat import simulation

    directory, _ = _snapshot(tmp_path)
    panel, manifest = load_panel(directory, list(SYMBOLS))
    panel = replace(panel, window_volumes=np.full_like(panel.window_volumes, 550.0))
    monkeypatch.setattr(simulation, "load_panel", lambda *_args: (panel, manifest))
    actual_runner = simulation.run_backtest
    captured = []

    def replay(*args, **kwargs):
        result = actual_runner(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(simulation, "run_backtest", replay)
    report = SimulatedLiveAccount(
        _strategy(), snapshot_directory=directory, config_sha256="c" * 64
    ).step()
    fills = [f for record in captured[0].sessions for f in record.fills if f.side == "buy"]
    assert fills
    assert all(f.quantity == f.quantity.to_integral_value() and f.quantity <= 5 for f in fills)
    assert any(
        missed.reason == MissedFillReason.PARTICIPATION_CAP
        for record in captured[0].sessions
        for missed in record.missed
    )
    assert report.research_eligible
    assert report.as_dict()["research_eligible"] is True


def test_uncovered_live_action_tail_is_explicitly_ineligible(tmp_path, monkeypatch):
    from amat import simulation

    directory, _ = _snapshot(tmp_path)
    panel, manifest = load_panel(directory, list(SYMBOLS))
    manifest = manifest.model_copy(update={"last_session": panel.sessions[-2]})
    monkeypatch.setattr(
        simulation, "extend_panel_to_present", lambda *_args, **_kwargs: (panel, manifest, 1)
    )
    report = SimulatedLiveAccount(
        _strategy(), snapshot_directory=directory, config_sha256="c" * 64, client=object()
    ).step()
    assert report.research_eligible is False
    assert report.as_dict()["research_eligible"] is False
    assert any("effect on return is unknown" in text for text in report.caveats)


def test_position_reconstruction_applies_splits_before_same_session_fills():
    first = dt.date(2026, 1, 2)
    second = dt.date(2026, 1, 5)
    third = dt.date(2026, 1, 6)
    result = SimpleNamespace(
        sessions=(
            SimpleNamespace(
                session=first,
                fills=(SimpleNamespace(symbol="SPY", signed_quantity=Decimal("10")),),
            ),
            SimpleNamespace(
                session=second,
                fills=(SimpleNamespace(symbol="SPY", signed_quantity=Decimal("-2")),),
            ),
            SimpleNamespace(
                session=third,
                fills=(SimpleNamespace(symbol="SPY", signed_quantity=Decimal("1")),),
            ),
        )
    )
    actions = {
        second: [CorporateAction("SPY", second, ActionKind.SPLIT, ratio=Decimal("1.5"))],
        third: [
            CorporateAction("SPY", third, ActionKind.SPLIT, ratio=Decimal("0.5")),
            CorporateAction("SPY", third, ActionKind.CASH_DIVIDEND, cash_per_share=Decimal(1)),
        ],
    }
    # 10 * 1.5 - 2 = 13, then 13 * 0.5 + 1 = 7.5. Dividends add no shares.
    assert _position_quantity(result, "SPY", actions_by_session=actions) == Decimal("7.5")
    assert _position_quantity(result, "SPY") == Decimal("9")


def test_split_covered_simulation_reports_weights_matching_native_ledger(tmp_path, monkeypatch):
    from amat import simulation

    directory, _ = _snapshot(tmp_path)
    panel, manifest = load_panel(directory, list(SYMBOLS))
    split_index = 280
    raw = panel.raw_closes.copy()
    windows = panel.window_vwaps.copy()
    # Every instrument splits, so any surviving holding exercises reconstruction.
    raw[split_index:] /= 1.5
    windows[split_index:] /= 1.5
    panel = replace(panel, raw_closes=raw, window_vwaps=windows)
    session = panel.sessions[split_index]
    actions = {
        session: [
            CorporateAction(symbol, session, ActionKind.SPLIT, ratio=Decimal("1.5"))
            for symbol in SYMBOLS
        ]
    }
    monkeypatch.setattr(simulation, "load_panel", lambda *_args: (panel, manifest))
    monkeypatch.setattr(simulation, "load_corporate_actions", lambda *_args, **_kwargs: actions)
    report = SimulatedLiveAccount(
        _strategy(), snapshot_directory=directory, config_sha256="c" * 64
    ).step()
    assert report.positions
    implied = float(report.cash / report.equity) + sum(report.positions.values())
    assert abs(implied - 1) < 1e-12


def test_current_best_simulation_targets_match_exported_native_plan(tmp_path, monkeypatch):
    from amat import simulation
    from amat.config.loading import load_strategy_config
    from amat.data.synthetic import generate_panel
    from amat.planning import build_plan

    resolved = load_strategy_config(
        Path(__file__).resolve().parents[2]
        / "configs/strategy/amat_group_selection_volatility.toml"
    )
    strategy = resolved.strategy
    panel = generate_panel(strategy.to_universe(), sessions=340, seed=41, trend_strength=0.5)
    manifest = SimpleNamespace(snapshot_id="synthetic-foundation", last_session=panel.sessions[-1])
    monkeypatch.setattr(simulation, "load_panel", lambda *_args: (panel, manifest))
    monkeypatch.setattr(simulation, "load_corporate_actions", lambda *_args, **_kwargs: {})
    report = SimulatedLiveAccount(
        strategy, snapshot_directory=tmp_path, config_sha256=resolved.content_sha256
    ).step()
    plan = build_plan(
        strategy,
        panel,
        strategy.to_universe(),
        engine="v1-frozen",
        current_positions={},
        account_equity=Decimal("100000"),
        config_sha256=resolved.content_sha256,
        snapshot_id=manifest.snapshot_id,
        code_revision="test",
    )
    assert report.next_session_targets == {
        symbol: weight for symbol, weight in plan.target_weights.items() if weight > 0
    }
