"""Metric definitions, evidence-backed exports, and noninterfering run monitoring."""

import csv
import json
import math

import pytest

from autoresearch.ledger import Ledger
from autoresearch.telemetry import (
    analysis_lock,
    csv_value,
    daily_metrics,
    export_analysis,
    monitoring,
    read_telemetry,
)
from autoresearch.workspace import digest_file, write_json
from llmrouter.chain import Target
from llmrouter.contracts import ChatRequest, ChatResponse


def account():
    values = [100.0, 90.0, 99.0, 110.0]
    return [
        {
            "session": f"2020-01-0{i + 1}",
            "equity": value,
            "return": value / values[i - 1] - 1 if i else 0,
            "cash": "0",
            "gross_exposure": 1.0,
            "fills": [],
            "missed": [],
            "attribution": {},
        }
        for i, value in enumerate(values)
    ]


def test_downside_tail_and_turnover_metrics_exclude_anchor():
    daily = account()
    daily[1]["fills"] = [{"price": "10", "quantity": "2"}]
    daily[3]["fills"] = [{"price": "11", "quantity": "1"}]
    result = daily_metrics(daily)
    returns = [-0.1, 0.1, 110 / 99 - 1]
    assert result["return_observations"] == 3
    assert result["sortino_mar0"] == pytest.approx(
        sum(returns) / 3 * 252 / (math.sqrt(0.01 / 3) * math.sqrt(252))
    )
    assert result["calmar"] == pytest.approx((1.1**84 - 1) / 0.1)
    assert result["daily_var95_loss"] == pytest.approx(0.08)
    assert result["daily_expected_shortfall95_loss"] == pytest.approx(0.1)
    assert result["longest_underwater_sessions"] == 2
    assert result["annualized_one_way_turnover"] == pytest.approx((20 / 100 + 11 / 99) * 84)


def test_undefined_metrics_remain_null_and_invalid_history_fails():
    daily = account()
    for row in daily:
        row["equity"] = 100
        row["return"] = 0
    result = daily_metrics(daily)
    assert result["sortino_mar0"] is None
    assert result["calmar"] is None
    assert daily_metrics([])["available"] is False
    daily[2]["return"] = 0.1
    with pytest.raises(ValueError, match="disagree"):
        daily_metrics(daily)


def test_monitoring_does_not_interrupt_writer_and_records_provider_timing(tmp_path):
    with Ledger(tmp_path, 100000) as ledger:
        ledger.put("status", "running")
        ledger.phase("review", 1)
        call = ledger.reserve(
            Target(provider="openai", model="fixture"),
            ChatRequest(
                system="fixture",
                user="fixture",
                schema_json="{}",
                schema_name="review",
                max_output_tokens=256,
                timeout_seconds=1,
            ),
        )
        live = monitoring(read_telemetry(tmp_path), tmp_path)
        assert live["writer_active"] is True
        assert live["status"] == "running" and live["phase"] == "review"
        assert live["pending_calls"] == live["unknown_usage_calls"] == 1
        assert live["known_input_tokens"] == 0
        with pytest.raises(ValueError, match="automatically"), analysis_lock(tmp_path):
            pass
        ledger.settle(
            call,
            ChatResponse(
                provider="openai", model="fixture", text="{}", input_tokens=12, output_tokens=8
            ),
        )
        snapshot = read_telemetry(tmp_path)
        tracked = monitoring(snapshot, tmp_path)
        assert tracked["budget_charged_tokens"] == 20
        assert tracked["known_input_tokens"] == 12
        assert tracked["unknown_usage_calls"] == 0
        assert json.loads(snapshot["events"][-1]["payload"])["elapsed_seconds"] >= 0
        assert ledger.get("status") == "running"
    assert monitoring(read_telemetry(tmp_path), tmp_path)["status"] == "interrupted_or_inactive"
    with Ledger(tmp_path, 100000) as ledger:
        assert ledger.get("progress") == {}
        assert (
            ledger.db.execute(
                "select count(*) from events where kind='phase_interrupted'"
            ).fetchone()[0]
            == 1
        )


def test_exports_reconcile_rows_and_hashes_and_reject_changed_evidence(tmp_path):
    from amat.reporting.trade_monitor import document

    relative = "outputs/runs/candidate/primary/daily.json"
    write_json(tmp_path / relative, account())
    trade_path = "outputs/runs/candidate/primary/trade-monitor.json"
    write_json(
        tmp_path / trade_path,
        document(
            [
                {
                    "event_id": "trade-one",
                    "source": "backtest",
                    "run_id": "primary",
                    "model_id": "candidate",
                    "event_kind": "instruction",
                    "symbol": "SPY",
                    "side": "buy",
                    "execution_session": "2020-01-02",
                    "forecasts": [],
                    "alerts": [],
                    "outcomes": {},
                }
            ]
        ),
    )
    result = {
        "artifact_hashes": {
            relative: digest_file(tmp_path / relative),
            trade_path: digest_file(tmp_path / trade_path),
        },
        "train": {"sharpe": 0.5},
        "score": 0.25,
        "failed_gates": [],
        "robustness": {
            "scenarios": {
                "student_t_3": {
                    "kind": "hypothetical_synthetic",
                    "distribution": "student_t",
                    "coverage_complete": True,
                    "path_index": 0,
                    "candidate": {"return_distribution": {"loss_cvar_99": 0.09}},
                    "baseline": {"return_distribution": {"loss_cvar_99": 0.12}},
                },
            },
        },
    }
    report = {
        "program_sha256": "fixture",
        "experiments": [
            {
                "id": 1,
                "candidate": "candidate",
                "parent": "baseline",
                "status": "discard",
                "proposal": json.dumps({"hypothesis": "=FORMULA()"}),
                "result": json.dumps(result),
            }
        ],
    }
    snapshot = {"state": {}, "calls": [], "events": []}
    summary = export_analysis(tmp_path, report, snapshot)
    assert summary["rows"]["strategies"] == 1
    assert summary["rows"]["daily"] == 4
    assert summary["trade_monitor"] == {"recorded_runs": 1, "events": 1}
    assert (tmp_path / "analysis/trades/trade-monitor.html").is_file()
    with (tmp_path / "analysis/scenarios.csv").open() as stream:
        cases = list(csv.DictReader(stream))
    assert len(cases) == 2
    assert cases[0]["distribution"] == "student_t"
    assert cases[0]["coverage_complete"] == "True"
    assert [float(row["return_loss_cvar_99"]) for row in cases] == [0.09, 0.12]
    assert summary["strategies"][0]["stress_available"] is False
    with (tmp_path / "analysis/daily.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["is_anchor"] == "True"
    assert float(rows[0]["equity_index_100"]) == 100
    assert float(rows[2]["drawdown"]) == pytest.approx(-0.01)
    assert (tmp_path / "analysis/missed.csv").read_text().startswith("program_sha256")
    manifest = json.loads((tmp_path / "analysis/manifest.json").read_text())
    for name, expected in manifest["exports"].items():
        assert digest_file(tmp_path / "analysis" / name) == expected
    for name, expected in manifest["trade_exports"].items():
        assert digest_file(tmp_path / "analysis" / name) == expected
    (tmp_path / relative).write_text("[]")
    with pytest.raises(ValueError, match="evidence changed"):
        export_analysis(tmp_path, report, snapshot)
    write_json(tmp_path / relative, account())
    (tmp_path / trade_path).write_text("{}")
    with pytest.raises(ValueError, match=r"evidence changed.*trade-monitor"):
        export_analysis(tmp_path, report, snapshot)


def test_csv_preserves_numeric_precision_and_neutralizes_formulas():
    assert csv_value("-0.123456789012345678901") == "-0.123456789012345678901"
    assert csv_value("  =CMD()") == "'  =CMD()"
    assert csv_value("@untrusted") == "'@untrusted"
    assert csv_value(-1.5) == -1.5


def test_daily_recording_preserves_constraints_and_marks_nonfinite_forecasts_missing(tmp_path):
    from dataclasses import replace
    from pathlib import Path

    from amat.backtest.runner import run_backtest
    from amat.config.v6 import load_v6_config
    from amat.data.synthetic import generate_panel
    from autoresearch.contracts import Candidate
    from autoresearch.worker import persist_run

    source = Path(__file__).resolve().parents[2]
    candidate = Candidate(config=load_v6_config(source / "configs/strategy/amat_v6.toml").config)
    config = candidate.config.strategy
    panel = generate_panel(config.to_universe(), sessions=270)
    result = run_backtest(config, panel, config.to_universe())
    first = replace(
        result.sessions[0],
        forecast_volatility_pre=float("nan"),
        forecast_volatility_post=float("inf"),
        binding_constraints=("fixture_cap",),
    )
    result = replace(result, sessions=(first, *result.sessions[1:]))
    persist_run(tmp_path, tmp_path / "primary", result, candidate, "f" * 64)
    daily = json.loads((tmp_path / "primary/daily.json").read_text())
    assert daily[0]["forecast_volatility_pre"] is None
    assert daily[0]["forecast_volatility_post"] is None
    assert daily[0]["binding_constraints"] == ["fixture_cap"]


def test_expected_shortfall_does_not_dilute_losses_with_tied_zero_returns():
    import datetime as dt

    daily = [
        {
            "session": str(dt.date(2020, 1, 1) + dt.timedelta(days=i)),
            "equity": 100 if i == 0 else 90,
            "return": -0.1 if i == 1 else 0,
        }
        for i in range(101)
    ]
    metrics = daily_metrics(daily)
    assert metrics["daily_var95_loss"] == 0
    assert metrics["daily_expected_shortfall95_loss"] == pytest.approx(0.02)
