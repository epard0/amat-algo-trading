"""Read-only published study separates audit visibility from active selection."""

from pathlib import Path

import pytest

from server.analysis import STUDIES, overview

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    not (ROOT / STUDIES["percentile_sizing"]).is_dir(),
    reason="Requires the local published percentile_sizing research archive; excluded from source releases",
)
def test_percentile_sizing_cost_stress_and_rejected_visibility():
    for cost in [2, 6, 10]:
        result = overview(
            ROOT, study_id="percentile_sizing", strategy="core_daily", period="full", cost=cost
        )
        assert result["status"] == "available"
        detail = result["efficiency"]
        assert detail["approved"] == ["core_daily"]
        assert detail["candidate_count"] == detail["rejected_count"] == 6
        assert len(detail["all_results"]) == 7
        assert len(detail["asset_activation"]) == 11
        assert [c["key"] for c in detail["asset_activation_columns"]] == ["p80", "p90", "p95"]
        assert all(r["p95"] <= r["p90"] <= r["p80"] for r in detail["asset_activation"])
        assert all(
            0 <= r[k] <= 1 for r in detail["asset_activation"] for k in ["p80", "p90", "p95"]
        )
        assert all(len(r["approaches"]) == 7 for r in detail["stress"])
        assert all(len(r["approaches"]) == 7 for r in detail["yearly"])
    for arm in [
        "p80_reduce",
        "p90_reduce",
        "p95_reduce",
        "p80_relative",
        "p90_relative",
        "p95_relative",
    ]:
        result = overview(ROOT, study_id="percentile_sizing", strategy=arm, period="full", cost=6)
        assert result["status"] == "unavailable"
