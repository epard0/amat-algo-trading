"""Prior candidates remain a return floor; missing coverage can never earn promotion."""


from copy import deepcopy
from pathlib import Path

import pytest

from amat.config.loading import load_strategy_config
from autoresearch.improvements.campaign_audit import economic_digest
from autoresearch.improvements.campaign_cycle_study import retain
from autoresearch.improvements.study import digest


def fixture():
    def record(value):
        return {
            "sessions": [],
            "metrics": {
                "full": {"net_return": value, "max_drawdown": -0.1},
                "walk_forward": {"net_return": value, "max_drawdown": -0.1},
                "2023": {"net_return": 0.1, "max_drawdown": -0.1},
            },
            "monthly": [{"month": f"2017-{m:02d}", "net_return": 0.01} for m in range(1, 13)],
        }

    records = {
        a: {c: record(0.25 if a == "core_daily" else 0.31) for c in [2, 6, 10]}
        for a in ["core_daily", "candidate"]
    }
    protocol = {
        "labels": {"core_daily": "Control", "candidate": "Candidate"},
        "parent_sessions_sha256": {str(c): digest([]) for c in [2, 6, 10]},
        "frontier": {
            str(c): {p: {"net_return": 0.30} for p in ["full", "walk_forward"]} for c in [2, 6, 10]
        },
        "coverage_complete": True,
    }
    return records, protocol


def test_beating_incumbent_is_insufficient_when_prior_candidate_is_stronger():
    records, protocol = fixture()
    records["candidate"][10]["metrics"]["walk_forward"]["net_return"] = 0.26
    decision = retain(records, protocol)["candidate"]
    assert decision["status"] == "rejected"
    assert not decision["return_checks"][-1]["passes"]


def test_incomplete_replay_coverage_blocks_even_numerical_winner():
    records, protocol = fixture()
    protocol["coverage_complete"] = False
    assert retain(records, protocol)["candidate"]["status"] == "pending"
    protocol["coverage_complete"] = True
    assert retain(records, protocol)["candidate"]["status"] == "retained"
    worse = deepcopy(records)
    worse["candidate"][6]["metrics"]["full"]["max_drawdown"] = -0.11
    assert retain(worse, protocol)["candidate"]["status"] == "rejected"
    worse = deepcopy(records)
    worse["candidate"][2]["metrics"]["2023"]["net_return"] = 0.09
    assert retain(worse, protocol)["candidate"]["status"] == "rejected"


def test_economic_dedup_ignores_names_but_preserves_actual_position_limit():
    config = load_strategy_config(
        Path("configs/strategy/amat_group_selection_volatility.toml")
    ).strategy.model_dump(mode="json")
    other = deepcopy(config)
    other["name"] = "a-new-label"
    other["version"] = "999.0.0"
    assert economic_digest(config) == economic_digest(other)
    other["cross_sectional"]["max_positions"] = 6
    assert economic_digest(config) != economic_digest(other)


def test_execution_contract_identity_distinguishes_fractional_and_whole_shares():
    from autoresearch.improvements.campaign_audit import execution_identity
    from autoresearch.improvements.switching_study import CASH_POLICY_SPEC, research_cash_policy

    config = load_strategy_config(
        Path("configs/strategy/amat_group_selection_volatility.toml")
    ).strategy.model_dump(mode="json")
    assert execution_identity(config) != execution_identity(config, whole_share_buys=True)
    assert execution_identity(
        config, CASH_POLICY_SPEC, whole_share_buys=True
    ) != execution_identity(config, whole_share_buys=True)
    assert research_cash_policy(None) is None
    assert research_cash_policy(CASH_POLICY_SPEC).reserve_fraction == __import__("decimal").Decimal(
        "0.01"
    )

    with pytest.raises(ValueError, match="Unregistered"):
        research_cash_policy({**CASH_POLICY_SPEC, "reserve_fraction": "0"})
