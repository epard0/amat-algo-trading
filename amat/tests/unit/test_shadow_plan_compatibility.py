"""Local synthetic mechanics only; no production accounts or strategy outcomes."""

import importlib
import importlib.util
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from amat.config.loading import load_strategy_config
from autoresearch.improvements.agent_portfolio_arm import (
    AuthorizedIntent,
    ContractError,
    validate_action_contract,
)
from autoresearch.improvements.study import digest, source_hashes

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/run_shadow_plan_compatibility.py"
CONFIG = SCRIPT.parents[1] / "configs/strategy/amat_group_selection_volatility.toml"


@pytest.fixture
def mod():
    spec = importlib.util.spec_from_file_location("shadow_plan_diag_test", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize("cost", [2, 6, 10])
def test_fixed_counterexamples_all_costs_are_inert(mod, cost):
    result = mod.diagnose(CONFIG, cost)
    hold, resize, stop = result["cases"]
    assert hold["native_intent_delta"] == {}
    assert resize["requested_action_delta"] == {"QQQ": "1"}
    assert resize["NAIVE_target_projection"] == {"QQQ": 0.11}
    assert resize["native_intent_delta"] == {}
    assert Decimal(stop["native_intent_delta"]["QQQ"]) == -10
    assert stop["same_emitted_quantities"] is True
    assert stop["conditionality_lost"] is True
    assert stop["original_proposal"]["actions"][0]["limit_price"] == "89"
    assert stop["original_proposal"]["actions"][0]["trigger_price"] == "90"
    assert stop["NAIVE_native_plan"]["intents"][0]["reference_price"] == "100"
    assert stop["expiry_preserved"] is False
    assert not result["build_plan_accepts_target_weights"]
    for row in result["cases"]:
        assert row["execution_authority"] == "none"
        assert not row["performance_evidence"] and not row["promotion_authority"]
        assert not row["native_compatible_export_verified"]
        assert len(row["limitations"]) >= 5
        assert {"pending_cash", "account_id", "limit_price", "trigger_price"} <= set(
            row["context_or_action_parameters_absent_from_builder"]
        )


def test_whole_original_proposals_and_context_remain_unchanged(mod):
    context = mod.fixture(6)
    before = context.evidence_sha256
    for case in mod.CASES:
        mod.diagnose_case(load_strategy_config(CONFIG).strategy, mod.sha(CONFIG), context, case)
    assert context.evidence_sha256 == before
    assert dict(context.positions) == {"QQQ": Decimal("10")}


def test_unchanged_other_holding_is_preserved_not_zeroed(mod):
    context = replace(
        mod.fixture(6),
        positions=(("QQQ", Decimal("10")), ("SPY", Decimal("3"))),
        confirmed_cash=Decimal("8700"),
    )
    proposal = mod.proposal_for(context, "resize_buy")
    validate_action_contract(proposal, context)
    targets = mod.naive_targets(context, proposal)
    assert targets == {"QQQ": 0.11, "SPY": 0.03}


def test_original_contract_validated_before_native_builder(mod, monkeypatch):
    context = replace(mod.fixture(6), confirmed_cash=Decimal("1"))
    called = []
    monkeypatch.setattr(mod, "build_execution_plan", lambda *_args, **_kwargs: called.append(True))
    with pytest.raises(ContractError):
        mod.diagnose_case(
            load_strategy_config(CONFIG).strategy, mod.sha(CONFIG), context, "resize_buy"
        )
    assert not called


def test_pending_cash_cannot_fund_shadow_resize(mod):
    context = mod.fixture(6)
    intent = AuthorizedIntent(
        "synthetic-pending",
        "SPY",
        "buy",
        Decimal("100"),
        Decimal("0"),
        Decimal("100"),
        Decimal("8900"),
        mod.EXPIRY,
        context.account_id,
        context.policy.identity,
    )
    context = replace(context, authorized_intents=(intent,))
    with pytest.raises(ContractError, match="cash"):
        validate_action_contract(mod.proposal_for(context, "resize_buy"), context)


@pytest.mark.parametrize("cost", [0, 3, True, 2.0])
def test_unregistered_cost_refused(mod, cost):
    with pytest.raises(ValueError):
        mod.fixture(cost)


def test_unknown_case_refused(mod):
    with pytest.raises(ValueError):
        mod.proposal_for(mod.fixture(6), "live_open")


def seal(path, value):
    path.write_text(json.dumps(value, sort_keys=True))
    path.with_suffix(".sha256").write_text(digest(value))


@pytest.fixture
def managed(mod, tmp_path, monkeypatch):
    cycle = "synthetic-native-plan"
    output = tmp_path / "cycles" / cycle / "cost-6.json"
    output.parent.mkdir(parents=True)
    campaign_protocol = {"deadline_utc": "2026-10-24T04:40:10Z"}
    seal(tmp_path / "protocol.json", campaign_protocol)
    sources = {
        str(SCRIPT.parents[1] / raw): sha for raw, sha in source_hashes(SCRIPT.parents[1]).items()
    }
    sources[str(SCRIPT)] = mod.sha(SCRIPT)
    protocol = {
        "schema": "shadow-plan-compatibility/1",
        "campaign_root": str(tmp_path),
        "campaign_protocol_sha256": digest(campaign_protocol),
        "config": {"path": str(CONFIG), "sha256": mod.sha(CONFIG)},
        "source_files": sources,
        "hard_stop_utc": "2026-10-24T04:30:00Z",
        "campaign_deadline_utc": campaign_protocol["deadline_utc"],
        "costs_bps": [2, 6, 10],
        "cases": list(mod.CASES),
    }
    protocol_path = tmp_path / "diagnostic.json"
    seal(protocol_path, protocol)
    pins = {**sources, str(CONFIG): mod.sha(CONFIG), str(protocol_path): mod.sha(protocol_path)}
    manifest = {
        "campaign_root": str(tmp_path),
        "inputs": [{"path": path, "sha256": sha} for path, sha in pins.items()],
    }
    manifest_path = tmp_path / "manifest.json"
    seal(manifest_path, manifest)
    active = {
        "run_id": "fixed-test-token",
        "manifest_path": str(manifest_path),
        "manifest_sha256": digest(manifest),
        "protocol_sha256": digest(campaign_protocol),
        "cycle_id": cycle,
        "output": str(output.parent),
    }
    (tmp_path / "state.json").write_text(json.dumps({"active_run": active}))
    monkeypatch.setenv("AMAT_RESEARCH_RUN_ID", "fixed-test-token")
    monkeypatch.setattr(mod, "assert_time", lambda _protocol: None)
    return protocol_path, output, protocol, active


def test_managed_source_and_config_bindings(mod, managed):
    # Reproduce the full-suite import state instead of depending on test order.
    importlib.import_module("amat.risk.active")
    path, output, protocol, active = managed
    actual, context = mod.verify_context(path, output, 6)
    assert actual == protocol and context == active


def test_unmanaged_identity_refused(mod, managed, monkeypatch):
    path, output, *_ = managed
    monkeypatch.setenv("AMAT_RESEARCH_RUN_ID", "different")
    with pytest.raises(ValueError, match="identity"):
        mod.verify_context(path, output, 6)


def test_config_source_pin_mismatch_refused(mod, managed):
    path, output, protocol, _ = managed
    protocol["config"]["sha256"] = "0" * 64
    seal(path, protocol)
    with pytest.raises(ValueError, match="source/config"):
        mod.verify_context(path, output, 6)


def test_output_escape_and_existing_result_refused(mod, managed):
    path, output, *_ = managed
    with pytest.raises(ValueError, match="outside"):
        mod.verify_context(path, output.parent.parent / "escape.json", 6)
    output.write_text("preserve existing")
    with pytest.raises(ValueError, match="already present"):
        mod.verify_context(path, output, 6)


def test_deadline_refused(mod):
    with pytest.raises(ValueError, match="deadline"):
        mod.assert_time(
            {
                "hard_stop_utc": "2000-01-01T00:00:00Z",
                "campaign_deadline_utc": "2000-01-02T00:00:00Z",
            }
        )


def test_exclusive_publication_and_seal(mod, tmp_path):
    output = tmp_path / "result.json"
    mod.publish(output, {"execution_authority": "none"})
    assert output.with_suffix(".sha256").read_text().strip() == digest(
        {"execution_authority": "none"}
    )
    with pytest.raises(ValueError):
        mod.publish(output, {"replace": True})


def test_dangling_seal_refused_without_output(mod, tmp_path):
    output = tmp_path / "result.json"
    output.with_suffix(".sha256").symlink_to(tmp_path / "nonexistent")
    with pytest.raises(ValueError):
        mod.publish(output, {})
    assert not output.exists()
