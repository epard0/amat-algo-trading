"""Annual research is read-only, sealed and separate from active profiles."""

import hashlib
import json
from pathlib import Path

import pytest

from server.native_annual import RESEARCH_ROOT, published_comparison


def test_missing_annual_publication_is_optional(tmp_path):
    assert published_comparison(tmp_path) is None


def test_annual_publication_rejects_tampering_and_execution_authority(tmp_path):
    path = tmp_path / RESEARCH_ROOT / "publication.json"
    path.parent.mkdir(parents=True)
    for authority in ["none", "submit"]:
        value = {"schema": "native-annual-dashboard/1", "execution_authority": authority}
        path.write_text(json.dumps(value))
        path.with_suffix(".sha256").write_text(
            hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
        )
        result = published_comparison(tmp_path)
        if authority == "none":
            assert result == value
        else:
            assert result["status"] == "unavailable"
    path.write_text("{}")
    assert published_comparison(tmp_path)["status"] == "unavailable"


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[3] / RESEARCH_ROOT).is_dir(),
    reason="Requires the local sealed annual research archive; excluded from source releases",
)
def test_published_annual_comparison_has_no_approved_candidates():
    root = Path(__file__).resolve().parents[3]
    value = published_comparison(root)
    assert value and value["status"] == "available"
    assert value["promoted_count"] == 0
    assert value["candidate_count"] == 16
    assert value["control_replays"] == 15
    assert value["source_parity_replays"] == 78
    for cost in ["2", "6", "10"]:
        rows = value["scenarios"][cost]["rows"]
        assert len(rows) == 20
        assert all(r["status"] == "Rejected" for r in rows if r["parent"] != "reference")
        control = next(r for r in rows if r["id"] == "core_daily")
        assert control["status"] == "Current default"
        assert 2 < control["full_return"] < 3


def test_benchmark_summary_is_sealed_and_cannot_authorize_execution(tmp_path):
    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.with_suffix(".sha256").write_text(
            hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
        )

    folder = tmp_path / RESEARCH_ROOT
    publication = {
        "schema": "native-annual-dashboard/1",
        "status": "available",
        "execution_authority": "none",
    }
    save(folder / "publication.json", publication)
    review = {
        "schema": "native-annual-benchmark-summary/1",
        "status": "available",
        "execution_authority": "none",
        "message": "Reviewed; no promotion.",
    }
    save(folder / "benchmark-summary.json", review)
    assert published_comparison(tmp_path)["benchmark_details"] == review
    review["execution_authority"] = "submit"
    save(folder / "benchmark-summary.json", review)
    value = published_comparison(tmp_path)
    assert value["status"] == "available"
    assert value["benchmark_details"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "folder", ["2026-10-20-quality-annual-allocation", "2026-10-20-cost-aware-annual-allocation"]
)
def test_followup_requires_seal_and_inert_authority(tmp_path, folder):
    field = "quality_comparison" if "cost-aware" not in folder else "cost_aware_comparison"

    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.with_suffix(".sha256").write_text(
            hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
        )

    save(
        tmp_path / RESEARCH_ROOT / "publication.json",
        {"schema": "native-annual-dashboard/1", "execution_authority": "none"},
    )
    quality = tmp_path / "amat/outputs/analysis" / folder / "publication.json"
    value = {
        "schema": "native-annual-dashboard/1",
        "execution_authority": "none",
        "candidate_count": 2,
    }
    save(quality, value)
    assert published_comparison(tmp_path)[field] == value
    value["execution_authority"] = "submit"
    save(quality, value)
    assert published_comparison(tmp_path)[field]["status"] == "unavailable"
    quality.write_text("{}")
    assert published_comparison(tmp_path)[field]["status"] == "unavailable"
