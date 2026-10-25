"""A broader native study must use each ETF's observed panel and reject misalignment."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from amat.data.snapshot import load_manifest
from autoresearch.improvements import switching_study as shared
from autoresearch.improvements.campaign_audit import sealed

ROOT = Path(__file__).resolve().parents[2]
C = ROOT / "outputs/research-campaigns/2026-10-19-12h-performance"


def test_real_broader_panel_uses_its_own_observed_prices_actions_and_calendar():
    snapshot = Path(sealed(C / "protocol.json")["snapshot"])
    core = ["SPY", "QQQ", "IWM", "EFA", "EEM", "IEF", "GLD", "SLV", "DBC", "USO", "UUP"]
    narrow, _ = shared.load_study_panel(snapshot, core)
    symbols = list(load_manifest(snapshot).symbols)
    broad, actions = shared.load_study_panel(snapshot, symbols, narrow.sessions)
    vti, _ = shared.load_study_panel(snapshot, ["VTI"], narrow.sessions)
    assert broad.adjusted_closes.shape == (2709, 30)
    assert narrow.adjusted_closes.shape == (2709, 11)
    index = symbols.index("VTI")
    np.testing.assert_array_equal(broad.window_vwaps[:, index], vti.window_vwaps[:, 0])
    np.testing.assert_array_equal(broad.adjusted_closes[:, index], vti.adjusted_closes[:, 0])
    assert actions
    with pytest.raises(ValueError, match="calendars"):
        shared.load_study_panel(snapshot, symbols, narrow.sessions[1:])


@pytest.mark.parametrize(
    "mutation", ["order", "missing_volume", "nonfinite_volume", "missing_price"]
)
def test_universe_loader_refuses_bad_symbol_or_observation_alignment(monkeypatch, mutation):
    snapshot = Path(sealed(C / "protocol.json")["snapshot"])
    panel, _ = shared.load_study_panel(snapshot, ["SPY", "QQQ"])
    if mutation == "order":
        panel = replace(panel, symbols=tuple(reversed(panel.symbols)))
    elif mutation == "missing_price":
        values = panel.window_vwaps.copy()
        values[260, 1] = 0
        panel = replace(panel, window_vwaps=values)
    else:
        values = panel.window_volumes.copy()
        values[260, 1] = np.nan if mutation == "nonfinite_volume" else 0
        panel = replace(panel, window_volumes=values)
    monkeypatch.setattr(shared, "load_panel", lambda *_args, **_kwargs: (panel, None))
    with pytest.raises(ValueError, match=r"symbol order|execution-window"):
        shared.load_study_panel(snapshot, ["SPY", "QQQ"])


@pytest.mark.parametrize("external", ["mask", "overlay"])
def test_shared_external_arrays_cannot_cross_universes_before_any_measurement(
    monkeypatch, tmp_path, external
):
    proposal = sealed(C / "proposal-020.json")
    protocol = {"configs": proposal["configs"], "snapshot": "unused", "execution_cash_policies": {}}
    monkeypatch.setattr(shared, "verify", lambda _path: protocol)

    def forbid_load(*_args, **_kwargs):
        raise AssertionError("Mixed external arrays must fail before data or return measurement")

    monkeypatch.setattr(shared, "load_study_panel", forbid_load)
    kwargs = (
        {"mask_builder": lambda *_args: {}}
        if external == "mask"
        else {"overlay_builder": lambda *_args: {}}
    )
    with pytest.raises(ValueError, match="arm-specific external overlay alignment"):
        shared.run(tmp_path, labels=proposal["labels"], **kwargs)
