"""Trade evidence retains intent, observes maturity and never invents forecasts."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from amat.config.v6 import load_v6_config
from amat.data.synthetic import generate_panel
from amat.reporting.trade_monitor import combine, document, write_monitor
from autoresearch.contracts import Candidate, Program
from autoresearch.entry_model import forecast_key
from autoresearch.experiments import Simulation
from autoresearch.specs import EntryModelSpec
from autoresearch.trade_monitor import _forecast_rows, build_monitor, markouts
from autoresearch.worker import persist_run

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def simulation():
    candidate = Candidate(config=load_v6_config(ROOT / "configs/strategy/amat_v6.toml").config)
    panel = replace(
        generate_panel(candidate.config.strategy.to_universe(), sessions=620),
        source="scenario(trade-monitor-test)",
    )
    program = Program(
        name="trade-monitor-test",
        load_start=panel.sessions[0],
        measured_start=panel.sessions[503],
        train_end=panel.sessions[-1],
        validation_end=panel.sessions[-1] + dt.timedelta(days=1),
        baseline=candidate,
        frozen_files={},
        dependencies={},
    )
    return Simulation(panel, program, None)


def test_orders_survive_settlement_and_anchor_does_not_count_activity(simulation, tmp_path):
    candidate = simulation.program.baseline
    result = simulation.run(candidate)
    assert result.sessions[0].instructions == ()
    assert any(row.instructions for row in result.sessions)
    for record in result.sessions:
        for fill in record.fills:
            order = next(
                o for o in record.instructions if (o.symbol, o.side) == (fill.symbol, fill.side)
            )
            assert fill.quantity <= order.quantity
            assert order.signal_session < fill.execution_session == order.execution_session
    persist_run(tmp_path, tmp_path / "primary", result, candidate, simulation.program.digest())
    saved = json.loads((tmp_path / "primary/daily.json").read_text())
    assert sum(len(row["instructions"]) for row in saved) > 0


def test_real_runner_monitor_matches_intents_and_does_not_call_scores_forecasts(simulation):
    result = simulation.run(simulation.program.baseline)
    monitor = build_monitor(simulation, simulation.program.baseline, result, run_id="primary")
    assert len(monitor["events"]) >= result.total_fills
    filled = [row for row in monitor["events"] if row["filled_quantity"] != "0"]
    assert filled
    assert all(row["instruction_status"] == "matched" for row in filled)
    assert all(row["decision_session"] < row["execution_session"] for row in filled)
    assert all(row["forecasts"] == [] for row in filled)
    assert any(row["signal_score"] is not None for row in filled)
    assert {row["regime"] for row in filled} <= {"range", "trend", "stress"}
    assert all(row["outcomes"]["60"]["status"] == "pending" for row in filled[-2:])
    assert json.dumps(monitor, allow_nan=False)


def test_missed_execution_is_retained(simulation):
    result = simulation.run(simulation.program.baseline, missed_every=2)
    monitor = build_monitor(simulation, simulation.program.baseline, result, run_id="missed")
    missing = [row for row in monitor["events"] if "stress_dropped" in row["miss_reasons"]]
    assert missing
    assert all(row["outcomes"]["5"]["status"] == "no_fill_price" for row in missing)
    assert all(row["instruction_status"] == "recorded_intent" for row in missing)


def test_markouts_handle_split_bases_sell_sign_and_close_excursions(simulation):
    panel = simulation.panel
    raw, adjusted = panel.raw_closes.copy(), panel.adjusted_closes.copy()
    raw[510:515, 0] = [100, 45, 50, 55, 60]
    adjusted[510:515, 0] = [50, 45, 50, 55, 60]
    panel = replace(panel, raw_closes=raw, adjusted_closes=adjusted)
    buy = markouts(panel, 510, 0, 100, "buy", stop=515)["5"]
    sell = markouts(panel, 510, 0, 100, "sell", stop=515)["5"]
    assert buy["signed_markout_bps"] == pytest.approx(2000)
    assert buy["adverse_close_bps"] == pytest.approx(1000)
    assert buy["favourable_close_bps"] == pytest.approx(2000)
    assert sell["signed_markout_bps"] == pytest.approx(-2000)
    assert sell["adverse_close_bps"] == pytest.approx(2000)
    assert sell["favourable_close_bps"] == pytest.approx(1000)
    assert markouts(panel, 510, 0, 100, "buy", stop=514)["5"]["status"] == "pending"
    assert markouts(panel, 510, 0, None, "buy", stop=515)["5"]["status"] == "no_fill_price"


def test_forecast_errors_use_exact_model_label_and_maturity(simulation):
    candidate = simulation.program.baseline.model_copy(update={"entry_model": EntryModelSpec()})
    spec = candidate.entry_model
    shape = simulation.panel.adjusted_closes.shape
    predictions = np.full((*shape, 4), 100.0)
    target = np.full(shape, 0.03)
    forecast = SimpleNamespace(
        outcomes=SimpleNamespace(
            net_return=target, adverse_excursion=target, deferral_advantage=target
        )
    )
    fake = SimpleNamespace(
        panel=simulation.panel,
        entry_model=lambda _: SimpleNamespace(predictions=predictions),
        entry_forecasts={
            (Candidate(config=candidate.config).digest(), forecast_key(spec)): forecast
        },
    )
    stop = 550
    rows = _forecast_rows(fake, candidate, stop - spec.horizon - 1, 0, stop)
    assert rows[0]["status"] == "pending"
    rows = _forecast_rows(fake, candidate, stop - spec.horizon - 2, 0, stop)
    assert rows[0]["error_bps"] == pytest.approx(200)
    assert rows[0]["actual_bps"] == pytest.approx(300)
    assert "2bps each way" in rows[0]["basis"]


def test_baseline_opportunities_remain_visible_when_candidate_emits_no_order(simulation):
    candidate = simulation.program.baseline.model_copy(update={"entry_model": EntryModelSpec()})
    shape = simulation.panel.adjusted_closes.shape
    opportunities = np.zeros(shape, dtype=bool)
    opportunities[510, 0] = True
    values = np.full(shape, 0.01)
    forecast = SimpleNamespace(
        opportunities=opportunities,
        outcomes=SimpleNamespace(
            net_return=values, adverse_excursion=values, deferral_advantage=values
        ),
    )
    entry = SimpleNamespace(
        predictions=np.full((*shape, 4), 100.0),
        reasons=np.full(shape, "fixture_deferred"),
        increase_multipliers=np.zeros(shape),
        utility_bps=np.ones(shape),
    )
    fake = SimpleNamespace(
        panel=simulation.panel,
        program=simulation.program,
        diagnostic_trace=lambda _: simulation.diagnostic_trace(simulation.program.baseline),
        entry_model=lambda _: entry,
        entry_forecasts={
            (
                Candidate(config=candidate.config).digest(),
                forecast_key(candidate.entry_model),
            ): forecast,
        },
    )
    recorded = simulation.run(simulation.program.baseline)
    no_orders = replace(
        recorded,
        sessions=tuple(
            replace(row, instructions=(), fills=(), missed=()) for row in recorded.sessions
        ),
    )
    events = build_monitor(fake, candidate, no_orders, run_id="fixture")["events"]
    assert len(events) == 1
    assert events[0]["event_kind"] == "baseline_opportunity"
    assert events[0]["execution_status"] == "not_an_order"
    assert events[0]["filled_quantity"] is None
    assert events[0]["forecasts"][0]["status"] == "matured"


def test_earlier_asof_cannot_see_later_outcomes(simulation):
    full = simulation.run(simulation.program.baseline)
    early = replace(full, sessions=full.sessions[:-20])
    candidate = simulation.program.baseline
    current = build_monitor(simulation, candidate, full, run_id="primary")
    before = build_monitor(simulation, candidate, early, run_id="primary")
    by_id = {row["event_id"]: row for row in current["events"]}
    for row in before["events"]:
        later = by_id[row["event_id"]]
        for field in ("signal_score", "regime", "decision_session", "instruction_status"):
            assert row[field] == later[field]
        for horizon, outcome in row["outcomes"].items():
            if outcome["status"] == "matured":
                assert outcome == later["outcomes"][horizon]


def test_viewer_exports_escape_untrusted_evidence_and_reject_duplicate_identity(tmp_path):
    row = {
        "source": "paper",
        "run_id": "paper-test",
        "model_id": "test",
        "event_id": "id",
        "execution_session": "2026-09-01",
        "event_kind": "instruction",
        "symbol": "=HYPERLINK(1)",
        "side": "buy",
        "reason": "</script><script>alert(1)</script>",
        "regime": "unknown",
        "outcomes": {},
        "forecasts": [],
        "alerts": [],
    }
    payload = document([row])
    path = write_monitor(tmp_path, payload)
    assert row["reason"] not in path.read_text()
    assert "'" + row["symbol"] in (tmp_path / "trades.csv").read_text()
    assert payload["summary"]["cohorts"][0]["sample_status"] == "insufficient_sample"
    with pytest.raises(ValueError, match="duplicate"):
        combine([tmp_path / "trade-monitor.json"] * 2, tmp_path / "combined")


def test_cohorts_do_not_mix_sources_sides_or_counterfactuals():
    rows = []
    for index, (source, side, kind) in enumerate(
        [
            ("backtest", "buy", "instruction"),
            ("paper", "buy", "instruction"),
            ("backtest", "sell", "instruction"),
            ("backtest", "buy", "baseline_opportunity"),
        ]
    ):
        rows.append(
            {
                "source": source,
                "run_id": "run",
                "model_id": "model",
                "event_id": str(index),
                "execution_session": "2026-09-01",
                "side": side,
                "event_kind": kind,
                "outcomes": {},
                "forecasts": [],
                "alerts": [],
            }
        )
    cohorts = document(rows)["summary"]["cohorts"]
    assert len([c for c in cohorts if c["dimension"] == "all"]) == 4


def test_date_weighting_and_sample_flags_do_not_treat_simultaneous_trades_as_independent():
    rows = []
    for index in range(31):
        rows.append(
            {
                "source": "backtest",
                "run_id": "run",
                "model_id": "model",
                "event_id": str(index),
                "execution_session": "2026-09-01" if index < 30 else "2026-09-02",
                "side": "buy",
                "event_kind": "instruction",
                "alerts": [],
                "forecasts": [],
                "outcomes": {
                    "5": {"status": "matured", "signed_markout_bps": 100 if index < 30 else -100}
                },
            }
        )
    all_rows = next(c for c in document(rows)["summary"]["cohorts"] if c["dimension"] == "all")
    five = next(o for o in all_rows["outcomes"] if o["horizon_sessions"] == 5)
    assert five["date_weighted_mean_bps"] == pytest.approx(0)
    assert five["mean_signed_markout_bps"] == pytest.approx(2900 / 31)
    assert five["events"] == 31
    assert five["dates"] == 2
    assert five["sample_status"] == "insufficient_sample"
