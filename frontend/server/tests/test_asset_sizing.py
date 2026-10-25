"""Read-only published study separates audit visibility from active selection."""

from pathlib import Path

import pytest

from server.analysis import STUDIES, overview

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    not (ROOT / STUDIES["asset_sizing"]).is_dir(),
    reason="Requires the local published asset_sizing research archive; excluded from source releases",
)
def test_asset_sizing_cost_stress_and_rejected_visibility():
    for cost in [2, 6, 10]:
        result = overview(
            ROOT, study_id="asset_sizing", strategy="core_daily", period="full", cost=cost
        )
        assert result["status"] == "available"
        detail = result["efficiency"]
        assert detail["approved"] == ["core_daily"]
        assert detail["candidate_count"] == detail["rejected_count"] == 6
        assert len(detail["all_results"]) == 7
        assert len(detail["asset_activation"]) == 11
        assert all(
            0 <= r[k] <= 1 for r in detail["asset_activation"] for k in ["weak", "vol", "either"]
        )
        assert all(len(r["approaches"]) == 7 for r in detail["stress"])
        assert all(len(r["approaches"]) == 7 for r in detail["yearly"])
    for arm in [
        "weak_reduce",
        "vol_reduce",
        "either_reduce",
        "weak_relative",
        "vol_relative",
        "either_relative",
    ]:
        result = overview(ROOT, study_id="asset_sizing", strategy=arm, period="full", cost=6)
        assert result["status"] == "unavailable"
