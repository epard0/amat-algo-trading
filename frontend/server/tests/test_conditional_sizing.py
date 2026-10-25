"""Read-only published study separates audit visibility from active selection."""

from pathlib import Path

import pytest

from server.analysis import STUDIES, overview

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    not (ROOT / STUDIES["conditional_sizing"]).is_dir(),
    reason="Requires the local published conditional_sizing research archive; excluded from source releases",
)
def test_conditional_sizing_cost_stress_and_rejected_visibility():
    for cost in [2, 6, 10]:
        result = overview(
            ROOT, study_id="conditional_sizing", strategy="core_daily", period="full", cost=cost
        )
        assert result["status"] == "available"
        detail = result["efficiency"]
        assert detail["approved"] == ["core_daily"]
        assert detail["candidate_count"] == detail["rejected_count"] == 3
        assert len(detail["all_results"]) == 4
        assert len(detail["condition_summary"]) == 3
        assert all(0 < r["fraction"] < 1 for r in detail["condition_summary"])
        assert all(len(r["approaches"]) == 4 for r in detail["stress"])
        assert all(len(r["approaches"]) == 4 for r in detail["yearly"])
    for arm in ["weak_trend", "volatility", "either"]:
        result = overview(ROOT, study_id="conditional_sizing", strategy=arm, period="full", cost=6)
        assert result["status"] == "unavailable"
