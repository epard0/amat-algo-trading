"""A signed replacement proof cannot waive incomplete or mismatched historical evidence."""

from copy import deepcopy
from pathlib import Path

import pytest

from autoresearch.improvements.campaign_audit import receipt_supersession, sealed
from autoresearch.improvements.study import digest, write

C = Path("outputs/research-campaigns/2026-10-19-12h-performance").resolve()


def fixture(campaign):
    design = sealed(C / "receipt-supersession-design-015.json")
    proof = deepcopy(design)
    proof["schema"] = "receipt-supersession-proof/1"
    verified = {}
    invalid = []
    for spec in proof["receipts"]:
        old = sealed(Path(spec["old_receipt"]))
        dest = campaign / Path(spec["old_receipt"]).name
        write(dest, old)
        dest.with_suffix(".sha256").write_text(digest(old))
        spec["old_receipt"] = str(dest)
        invalid.append({"receipt": str(dest), "reason": "Missing comparable whole-share records"})
        for m in spec["mappings"]:
            replacement = m["replacement"]
            verified[(replacement["source_protocol"], replacement["source_arm"])] = replacement
    return proof, verified, invalid


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "missing_identity",
        "missing_replacement",
        "wrong_source",
        "wrong_cost",
        "wrong_gate",
        "receipt_hash",
        "cash_policy",
    ],
)
def test_supersession_requires_complete_current_identity_proof(tmp_path, mutation):
    proof, verified, invalid = fixture(tmp_path)
    m = proof["receipts"][0]["mappings"][0]
    if mutation == "missing_identity":
        proof["receipts"][0]["mappings"].pop()
    elif mutation == "missing_replacement":
        verified.pop((m["replacement"]["source_protocol"], m["replacement"]["source_arm"]))
    elif mutation == "wrong_source":
        m["original_identity"]["source_protocol_sha256"] = "0" * 64
    elif mutation == "wrong_cost":
        m["original_identity"]["records"]["10"]["sha256"] = "0" * 64
    elif mutation == "wrong_gate":
        m["replacement"]["switch_gate_sha256"] = "0" * 64
    elif mutation == "receipt_hash":
        m["replacement_receipt_sha256"] = "0" * 64
    if mutation == "cash_policy":
        replacement = m["replacement"]
        receipt = sealed(Path(replacement["receipt"]))
        protocol = sealed(Path(receipt["study"]) / "protocol.json")
        alias = next(
            x["replay_arm"]
            for x in receipt["mappings"]
            if x["source_protocol"] == replacement["source_protocol"]
            and x["source_arm"] == replacement["source_arm"]
        )
        protocol["execution_cash_policies"] = {alias: {"reserve": 0.1}}
        fake_study = tmp_path / "cash-study"
        fake_study.mkdir()
        write(fake_study / "protocol.json", protocol)
        (fake_study / "protocol.sha256").write_text(digest(protocol))
        receipt["study"] = str(fake_study)
        fake_receipt = tmp_path / "cash-replacement.json"
        write(fake_receipt, receipt)
        fake_receipt.with_suffix(".sha256").write_text(digest(receipt))
        replacement["receipt"] = str(fake_receipt)
        m["replacement_receipt_sha256"] = digest(receipt)
    path = tmp_path / "receipt-supersession-proof-test.json"
    write(path, proof)
    path.with_suffix(".sha256").write_text(digest(proof))
    remaining, retired = receipt_supersession(tmp_path, verified, invalid)
    if mutation == "none":
        assert not remaining
        assert len(retired) == 2
        assert sum(x["replacement_count"] for x in retired) == 13
        assert all(x["reason"] == "Missing comparable whole-share records" for x in retired)
    else:
        assert not retired  # atomic proof: a valid second receipt cannot hide a broken first.
        assert len(remaining) == 3
