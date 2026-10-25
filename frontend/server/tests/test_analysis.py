"""Chart projections must agree with evidence and must fail closed on bad sources."""

import hashlib
import json

import polars as pl
import pytest

from server.analysis import ARMS, account_history, overview, saved_screen


def write_record(path, value):
    path.write_text(json.dumps(value))
    path.with_suffix(".sha256").write_text(
        hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
    )


@pytest.fixture
def evidence(tmp_path):
    study = tmp_path / "amat/outputs/analysis/2026-10-14-strategy-implementation/daily-study"
    study.mkdir(parents=True)
    snapshot = tmp_path / "amat/data/snapshots/alpaca-sip-core-2026-10-13-verified"
    snapshot.mkdir(parents=True)
    dates = ["2025-12-31", "2026-01-02", "2026-02-02"]
    pl.DataFrame(
        {
            "symbol": ["SPY"] * 3 + ["QQQ"] * 3,
            "session_date": dates * 2,
            "adjusted_close": [100.0, 105.0, 110.0, 100.0, 95.0, 90.0],
        }
    ).write_parquet(snapshot / "daily.parquet")
    digest = hashlib.sha256((snapshot / "daily.parquet").read_bytes()).hexdigest()
    protocol = {"partition_sha256": {"daily.parquet": digest}}
    (snapshot / "manifest.json").write_text(json.dumps(protocol))
    write_record(study / "protocol.json", protocol)
    identity = (study / "protocol.sha256").read_text()
    record = {
        "protocol_sha256": identity,
        "sessions": [{"session": d, "equity": v} for d, v in zip(dates, [100000, 110000, 99000])],
        "metrics": {"2026_ytd": {"net_return": -0.01}},
    }
    for arm in ARMS:
        write_record(study / f"{arm}-2bps.json", record)
    return tmp_path, study, snapshot


def test_curves_include_prior_close_and_compound_months(evidence):
    root, _, _ = evidence
    result = overview(root)
    assert result["status"] == "available"
    points = result["series"][0]["points"]
    assert points[0] == {"date": "2025-12-31", "growth": 0.0, "drawdown": 0.0}
    assert points[-1]["growth"] == pytest.approx(-1.0)
    assert points[-1]["drawdown"] == pytest.approx(-10.0)
    assert result["monthly"][0]["return"] == pytest.approx(10.0)
    assert result["monthly"][1]["return"] == pytest.approx(-10.0)
    assert len(result["benchmarks"]) == 2


def test_changed_record_or_benchmark_is_not_displayed(evidence):
    root, study, snapshot = evidence
    path = study / "B1-2bps.json"
    original = path.read_text()
    path.write_text(original.replace("110000", "120000"))
    assert overview(root)["status"] == "unavailable"
    path.write_text(original)
    (snapshot / "daily.parquet").write_bytes(b"changed")
    assert overview(root)["status"] == "unavailable"


def test_bad_inputs_are_rejected_before_reading(tmp_path):
    with pytest.raises(ValueError):
        overview(tmp_path, strategy="../elsewhere")
    with pytest.raises(ValueError):
        overview(tmp_path, cost=0)


def test_saved_screen_rejects_path_traversal(tmp_path):
    path = tmp_path / "amat/outputs/analysis/2026-10-18-codebase-review"
    path.mkdir(parents=True)
    (path / "diverse-five-day-screen.json").write_text(
        json.dumps({"schema": "strategy-condition-screen/1", "snapshot_id": "../../elsewhere"})
    )
    assert saved_screen(tmp_path)["status"] == "unavailable"


def test_account_history_does_not_invent_or_overwrite_observations(tmp_path):
    path = tmp_path / "equity_curve.ndjson"
    path.write_text('{"session":"2026-01-02","equity":"100"}\n')
    assert account_history(tmp_path)["status"] == "insufficient_history"
    with path.open("a") as out:
        out.write('{"session":"2026-01-06","equity":"101"}\n')
    assert [p["date"] for p in account_history(tmp_path)["points"]] == ["2026-01-02", "2026-01-06"]
    with path.open("a") as out:
        out.write('{"session":"2026-01-06","equity":"102"}\n')
    assert account_history(tmp_path)["status"] == "unavailable"


def test_adaptive_study_requires_complete_evidence_and_compares_both_references(evidence):
    from server.analysis import ADAPTIVE_ARMS, STUDIES

    root, original, _ = evidence
    target = root / STUDIES["adaptive"]
    target.mkdir(parents=True)
    protocol = json.loads((original / "protocol.json").read_text())
    write_record(target / "protocol.json", protocol)
    identity = (target / "protocol.sha256").read_text()
    record = json.loads((original / "baseline-2bps.json").read_text())
    record["protocol_sha256"] = identity
    for arm in ADAPTIVE_ARMS:
        for cost in [2, 6, 10]:
            write_record(target / f"{arm}-{cost}bps.json", record)
    assert overview(root, study_id="adaptive", strategy="combined")["status"] == "unavailable"
    (target / "report.md").write_text("Complete fixed-arm comparison")
    result = overview(root, study_id="adaptive", strategy="combined")
    assert result["status"] == "available"
    assert [s["id"] for s in result["series"]] == ["combined", "baseline", "B1"]
    assert len(result["comparison"]) == 10 and len(result["cost_sensitivity"]) == 10
    (target / "groups-10bps.json").write_text("{}")
    assert overview(root, study_id="adaptive", strategy="combined")["status"] == "unavailable"


def test_group_study_uses_its_own_snapshot_and_exposes_the_factorial(evidence):
    import shutil

    from server.analysis import GROUP_ARMS, STUDIES

    root, original, snapshot = evidence
    expanded = snapshot.parent / "expanded-verified"
    shutil.copytree(snapshot, expanded)
    target = root / STUDIES["groups"]
    target.mkdir(parents=True)
    protocol = json.loads((original / "protocol.json").read_text())
    protocol["snapshot_id"] = "expanded-verified"
    protocol["comparison_scope"] = "Same January 2024 cash start"
    write_record(target / "protocol.json", protocol)
    identity = (target / "protocol.sha256").read_text()
    record = json.loads((original / "baseline-2bps.json").read_text())
    record["protocol_sha256"] = identity
    for arm in GROUP_ARMS:
        for cost in [2, 6, 10]:
            write_record(target / f"{arm}-{cost}bps.json", record)
    (target / "report.md").write_text("Complete group comparison")
    # The former benchmark must not be read for this study.
    (snapshot / "daily.parquet").write_bytes(b"wrong old snapshot")
    result = overview(root, study_id="groups", strategy="wide_both")
    assert result["status"] == "available"
    assert [s["id"] for s in result["series"]] == ["wide_both", "groups", "baseline", "B1"]
    assert len(result["factorial"]) == 4 and len(result["comparison"]) == 12
    assert len(result["cost_sensitivity"]) == 12
    assert result["factorial"][0]["core"]["id"] == "groups"
    assert result["factorial"][0]["wide"]["id"] == "wide_groups"


def test_validation_exposes_months_stress_folds_and_seals_diagnostics(evidence):
    from server.analysis import STUDIES, VALIDATION_ARMS, VALIDATION_PERIODS

    root, original, _ = evidence
    target = root / STUDIES["validation"]
    target.mkdir(parents=True)
    protocol = json.loads((original / "protocol.json").read_text())
    protocol.update(
        recovery={"minimum_quota": 0.25},
        history_scope="Purged yearly tests",
        cost_scope="Observed VWAP; assumed costs",
    )
    write_record(target / "protocol.json", protocol)
    identity = (target / "protocol.sha256").read_text()
    record = json.loads((original / "baseline-2bps.json").read_text())
    record.update(
        protocol_sha256=identity,
        recovery_episodes=[],
        monthly=[
            {"month": "2025-12", "net_return": 0},
            {"month": "2026-01", "net_return": 0.1},
            {"month": "2026-02", "net_return": -0.1},
        ],
    )
    record["metrics"]["2026_ytd"]["max_drawdown"] = -0.1
    record["metrics"].update(
        {key: {"net_return": -0.01, "max_drawdown": -0.1} for key in [*VALIDATION_PERIODS, "full"]}
    )
    for arm in VALIDATION_ARMS:
        for cost in [2, 6, 10]:
            write_record(target / f"{arm}-{cost}bps.json", record)
    diagnostics = {
        "protocol_sha256": identity,
        "folds": [{"year": 2026, "brier": 0.2, "base_rate_brier": 0.1}],
        "predictions": [],
    }
    write_record(target / "diagnostics.json", diagnostics)
    (target / "report.md").write_text("Complete")
    result = overview(root, study_id="validation", strategy="core_daily", period="walk_forward")
    assert result["status"] == "available"
    assert len(result["comparison"]) == 9
    assert result["candidate_policy"]["incumbent"] == "core_daily"
    assert len(result["candidate_policy"]["rejected"]) == 6
    assert len(result["validation"]["yearly"]) == 7
    assert len(result["validation"]["stress"]) == 3
    assert result["validation"]["months"][0]["month"] == "2026-01"
    assert len(result["validation"]["months"][1]["approaches"]) == 9
    assert result["validation"]["folds"] == [{"year": 2026, "brier": 0.2, "base_rate_brier": 0.1}]
    # A correctly hashed diagnostic from another protocol must also be refused.
    write_record(target / "diagnostics.json", {**diagnostics, "protocol_sha256": "other"})
    assert (
        overview(root, study_id="validation", strategy="core_daily", period="walk_forward")[
            "status"
        ]
        == "unavailable"
    )
    with pytest.raises(ValueError):
        overview(root, study_id="groups", period="stress_covid", strategy="groups")


@pytest.mark.parametrize(
    "study_id", ["efficiency", "switching", "conditional", "indices", "interaction"]
)
def test_efficiency_study_hides_rejected_candidates_and_seals_decisions(evidence, study_id):
    from server.analysis import (
        CONDITIONAL_ARMS,
        EFFICIENCY_ARMS,
        INDEX_ARMS,
        INTERACTION_ARMS,
        STUDIES,
        SWITCHING_ARMS,
        VALIDATION_PERIODS,
    )

    arms = {
        "efficiency": EFFICIENCY_ARMS,
        "switching": SWITCHING_ARMS,
        "conditional": CONDITIONAL_ARMS,
        "indices": INDEX_ARMS,
        "interaction": INTERACTION_ARMS,
    }[study_id]

    root, original, _ = evidence
    target = root / STUDIES[study_id]
    target.mkdir(parents=True)
    protocol = json.loads((original / "protocol.json").read_text())
    protocol.update(
        retention="Match all cost/window returns and drawdown guard",
        allocation="segregated 80/20",
        rules="fixed index rules",
        cost_scope="borrow stress",
    )
    write_record(target / "protocol.json", protocol)
    identity = (target / "protocol.sha256").read_text()
    record = json.loads((original / "baseline-2bps.json").read_text())
    record["protocol_sha256"] = identity
    record["metrics"].update(
        {
            key: {"net_return": 0.1, "max_drawdown": -0.1, "annualized_turnover": 2.0}
            for key in [*VALIDATION_PERIODS, "full", "2026_ytd"]
        }
    )
    for arm in arms:
        for cost in [2, 6, 10]:
            write_record(target / f"{arm}-{cost}bps.json", record)
    if study_id == "indices":
        for arm in arms:
            if arm in {"core_daily", "conditional_reference"}:
                continue
            for suffix in ["", "-borrow6", "-no-borrow"]:
                write_record(
                    target / f"{arm}-6bps{suffix}.json",
                    {
                        **record,
                        "borrow_total": 0,
                        "sleeve_short_days": 0,
                        "sleeve_margin_breach_days": 0,
                    },
                )
    decisions = {
        "protocol_sha256": identity,
        "decisions": {
            arm: {
                "status": "incumbent"
                if arm == "core_daily"
                else "reference"
                if arm == "conditional_reference"
                or (study_id == "interaction" and arm == "all_groups")
                else "rejected"
            }
            for arm in arms
        },
    }
    expected = (
        ["core_daily", "all_groups", "conditional_reference"]
        if study_id == "interaction"
        else ["core_daily", "conditional_reference"]
        if study_id == "indices"
        else ["core_daily"]
    )
    write_record(target / "decisions.json", decisions)
    (target / "report.md").write_text("Complete")
    result = overview(root, study_id=study_id, strategy="core_daily", period="full")
    assert result["status"] == "available"
    assert [r["id"] for r in result["comparison"]] == expected
    assert result["efficiency"]["rejected_count"] == len(arms) - len(expected)
    assert len(result["efficiency"]["cost_grid"]) == len(arms)
    assert len(result["efficiency"]["yearly"]) == 7
    assert [r["id"] for r in result["cost_sensitivity"]] == expected
    rejected = next(a for a in arms if a not in expected)
    assert (
        overview(root, study_id=study_id, strategy=rejected, period="full")["status"]
        == "unavailable"
    )
    # A gate-qualified research candidate must remain selectable and comparable.
    decisions["decisions"][rejected]["status"] = "retained"
    write_record(target / "decisions.json", decisions)
    retained = overview(root, study_id=study_id, strategy=rejected, period="full")
    assert retained["status"] == "available"
    assert len(retained["comparison"]) == len(expected) + 1
    write_record(target / "decisions.json", {**decisions, "protocol_sha256": "wrong"})
    assert (
        overview(root, study_id=study_id, strategy="core_daily", period="full")["status"]
        == "unavailable"
    )


def test_native_recovery_deduplicates_and_binds_diagnosis(evidence):
    from server.analysis import RECOVERY_ARMS, STUDIES, VALIDATION_PERIODS

    root, original, _ = evidence
    study = root / STUDIES["recovery"]
    study.mkdir(parents=True)
    protocol = json.loads((original / "protocol.json").read_text())
    protocol["retention"] = "Full-history and exposed-year floors"
    write_record(study / "protocol.json", protocol)
    identity = (study / "protocol.sha256").read_text()
    record = json.loads((original / "baseline-2bps.json").read_text())
    record["protocol_sha256"] = identity
    record["metrics"] = {
        key: {"net_return": 0.1, "max_drawdown": -0.1, "annualized_turnover": 2.0}
        for key in [*VALIDATION_PERIODS, "full"]
    }
    for arm in RECOVERY_ARMS:
        for cost in [2, 6, 10]:
            write_record(study / f"{arm}-{cost}bps.json", record)
    write_record(
        study / "decisions.json",
        {
            "protocol_sha256": identity,
            "decisions": {
                arm: {
                    "status": "incumbent"
                    if arm == "core_daily"
                    else "retained"
                    if arm in {"all_groups", "all_groups_spy"}
                    else "rejected"
                }
                for arm in RECOVERY_ARMS
            },
        },
    )
    write_record(
        study / "selection.json",
        {"protocol_sha256": identity, "duplicates": {"all_groups_spy": "all_groups"}},
    )
    (study / "report.md").write_text("Complete")
    source_identity = hashlib.sha256(
        json.dumps(record["sessions"], sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    path = root / "amat/outputs/analysis/2026-10-19-2023-attribution/attribution.json"
    path.parent.mkdir(parents=True)
    write_record(path, {"baseline_sessions_sha256": source_identity})
    improved = (
        root / "amat/outputs/analysis/2026-10-19-2023-all-groups-attribution/attribution.json"
    )
    improved.parent.mkdir(parents=True)
    write_record(improved, {"candidate_sessions_sha256": source_identity})
    result = overview(root, study_id="recovery", strategy="all_groups", period="full")
    assert result["status"] == "available"
    assert result["efficiency"]["approved"] == ["all_groups", "core_daily"]
    assert result["efficiency"]["decisions"]["all_groups_spy"]["status"] == "duplicate"
    assert (
        overview(root, study_id="recovery", strategy="all_groups_spy", period="full")["status"]
        == "unavailable"
    )
    write_record(path, {"baseline_sessions_sha256": "changed"})
    assert (
        overview(root, study_id="recovery", strategy="all_groups", period="full")["status"]
        == "unavailable"
    )
