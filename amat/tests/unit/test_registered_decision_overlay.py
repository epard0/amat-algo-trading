"""Research overlays must be registered, bounded and aligned before any measurement."""

from copy import deepcopy

import numpy as np
import pytest

from amat.backtest.runner import DecisionOverlay
from autoresearch.improvements.switching_study import (
    decision_overlay_digest,
    verify_registered_decision_overlays,
)


def test_overlay_registration_binds_risk_and_entry_permissions():
    risk = np.ones((12, 2))
    risk[4:8, 0] = 0.5
    allow = np.ones_like(risk, dtype=bool)
    overlay = DecisionOverlay(risk, allow)
    protocol = {
        "labels": {"arm": "Arm"},
        "registered_decision_overlay_sha256": {"arm": decision_overlay_digest(overlay)},
    }
    verify_registered_decision_overlays(protocol, {"arm": overlay}, risk.shape)
    for field in ["risk", "allow"]:
        changed = DecisionOverlay(risk.copy(), allow.copy())
        if field == "risk":
            changed.risk_multipliers[7, 0] = 1
        else:
            changed.allow_increase[7, 0] = False
        with pytest.raises(ValueError, match="bytes differ"):
            verify_registered_decision_overlays(protocol, {"arm": changed}, risk.shape)
    with pytest.raises(ValueError, match="identities differ"):
        verify_registered_decision_overlays(protocol, {}, risk.shape)
    bare = deepcopy(protocol)
    del bare["registered_decision_overlay_sha256"]
    with pytest.raises(ValueError, match="identities differ"):
        verify_registered_decision_overlays(bare, {"arm": overlay}, risk.shape)
    verify_registered_decision_overlays({"labels": {"arm": "Arm"}}, {}, risk.shape)


@pytest.mark.parametrize("bad", ["shape", "nan", "leverage", "negative", "float_allow"])
def test_overlay_validation_rejects_invalid_arrays_even_with_a_matching_hash(bad):
    risk = np.ones((12, 2))
    allow = np.ones_like(risk, dtype=bool)
    if bad == "shape":
        allow = allow[:-1]
    elif bad == "nan":
        risk[0, 0] = np.nan
    elif bad == "leverage":
        risk[0, 0] = 1.1
    elif bad == "negative":
        risk[0, 0] = -0.1
    else:
        allow = allow.astype(float)
    overlay = DecisionOverlay(risk, allow)
    protocol = {
        "labels": {"arm": "Arm"},
        "registered_decision_overlay_sha256": {
            "arm": "not-evaluated" if bad == "nan" else decision_overlay_digest(overlay)
        },
    }
    with pytest.raises(ValueError, match=r"aligned finite \[0,1\]"):
        verify_registered_decision_overlays(protocol, {"arm": overlay}, (12, 2))
