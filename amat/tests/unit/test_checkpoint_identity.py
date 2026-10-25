import copy

import pytest

from autoresearch.improvements.checkpoint_identity import verify_registry_transition
from autoresearch.improvements.fit_evidence import compact


def seal(value):
    value.pop("checkpoint_hash", None)
    value["checkpoint_hash"] = compact(value)
    return value


def pair():
    old = seal(
        {
            "ledger": [{"fitness": 1.0, "diagnostics": {"evaluation": {"registry_sha256": "old"}}}],
            "context_hash": "same",
        }
    )
    new = copy.deepcopy(old)
    new["ledger"][0]["diagnostics"]["evaluation"]["registry_sha256"] = "new"
    return old, seal(new)


def test_exact_predicted_identity_transition():
    old, new = pair()
    result = verify_registry_transition(old, new, "old", "new")
    assert result["predicted_transition_exact"]
    assert not result["raw_identity_equal"]
    assert not result["archived_strict_gate_overridden"]
    assert old["ledger"][0]["diagnostics"]["evaluation"]["registry_sha256"] == "old"


@pytest.mark.parametrize("field,value", [("fitness", 2.0), ("genome_hash", "changed")])
def test_rejects_economic_or_genome_change(field, value):
    old, new = pair()
    new["ledger"][0][field] = value
    with pytest.raises(ValueError, match="beyond"):
        verify_registry_transition(old, seal(new), "old", "new")


def test_rejects_bad_checksum():
    old, new = pair()
    new["checkpoint_hash"] = "bad"
    with pytest.raises(ValueError, match="checksum"):
        verify_registry_transition(old, new, "old", "new")


def test_rejects_unexpected_registry():
    old, new = pair()
    with pytest.raises(ValueError, match="Unexpected"):
        verify_registry_transition(old, new, "other", "new")


def test_rejects_context_change():
    old, new = pair()
    new["context_hash"] = "changed"
    with pytest.raises(ValueError, match="beyond"):
        verify_registry_transition(old, seal(new), "old", "new")
