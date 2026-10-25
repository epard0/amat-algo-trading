"""Training identities must exclude future prices but react to changed training observations."""

from dataclasses import replace
from pathlib import Path

import pytest

from amat.data.snapshot import load_panel
from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.training_inputs import training_identity

C = Path(__file__).resolve().parents[2] / "outputs/research-campaigns/2026-10-19-12h-performance"


def test_training_identity_is_future_invariant_and_training_sensitive() -> None:
    snapshot = Path(sealed(C / "protocol.json")["snapshot"])
    panel, _ = load_panel(snapshot, ["SPY", "QQQ"], with_context=True)
    cutoff = str(panel.sessions[500])
    expected = training_identity(panel, cutoff)
    future = panel.adjusted_closes.copy()
    future[501:] *= 2
    assert training_identity(replace(panel, adjusted_closes=future), cutoff) == expected
    past = panel.adjusted_closes.copy()
    past[499, 0] *= 1.001
    changed = training_identity(replace(panel, adjusted_closes=past), cutoff)
    assert changed["fit_panel_sha256"] != expected["fit_panel_sha256"]
    assert changed["dataset_sha256"] != expected["dataset_sha256"]
    assert expected["training_sessions"] == 501
    assert expected["fitting_performed"] is False
    assert expected["campaign_comparable"] is False


def test_empty_training_prefix_is_rejected() -> None:
    panel, _ = load_panel(Path(sealed(C / "protocol.json")["snapshot"]), ["SPY"], with_context=True)
    with pytest.raises(ValueError, match="no observed sessions"):
        training_identity(panel, "2000-01-01")
