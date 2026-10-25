"""Signal parity diagnostics never supply or hide strategy return benchmarks."""

from copy import deepcopy
from pathlib import Path

import pytest

from autoresearch.improvements.campaign_audit import audit, sealed
from autoresearch.improvements.study import digest, write

C = Path(__file__).resolve().parents[2] / "outputs/research-campaigns/2026-10-19-12h-performance"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    write(path, value)
    path.with_suffix(".sha256").write_text(digest(value))


@pytest.mark.parametrize("mutation", ["none", "return_record", "configs", "protocol_identity"])
def test_signal_diagnostic_is_explicit_and_never_a_return_frontier(tmp_path, mutation):
    campaign = tmp_path / "campaign"
    save(campaign / "protocol.json", sealed(C / "protocol.json"))
    original = C / "cycles/018-cap-rebalance-band/semantic"
    protocol = deepcopy(sealed(original / "protocol.json"))
    result = deepcopy(sealed(original / "result.json"))
    folder = campaign / "cycles/signal"
    if mutation == "configs":
        protocol["configs"] = {"fake_candidate": {"name": "hidden"}}
    elif mutation == "return_record":
        save(folder / "candidate-6bps.json", {"metrics": {"full": {"net_return": 999}}})
    elif mutation == "protocol_identity":
        result["protocol_sha256"] = "0" * 64
    save(folder / "protocol.json", protocol)
    save(folder / "result.json", result)
    evidence = audit(tmp_path, campaign, campaign / "audit.json")
    assert evidence["unique_comparable_histories"] == 0
    assert not evidence["records"]
    assert all(x["net_return"] is None for v in evidence["frontier"].values() for x in v.values())
    if mutation == "none":
        assert evidence["studies"][0]["status"] == "non_return_signal_diagnostic"
        assert not evidence["pending_replay_or_review"]
    else:
        assert evidence["studies"][0]["status"] == "invalid_or_unverifiable"
        assert evidence["pending_replay_or_review"]
