"""Campaign publication is bounded, sealed and separate from active selection."""

import hashlib
import json
from pathlib import Path

import pytest

from server import analysis
from server.analysis import CAMPAIGN_ROOT, _campaign_study, overview

ROOT = Path(__file__).resolve().parents[3]


def save_sealed(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.with_suffix(".sha256").write_text(
        hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
    )


def test_campaign_publication_cannot_escape_cycle_folder(tmp_path):
    save_sealed(
        tmp_path / CAMPAIGN_ROOT / "latest-study.json",
        {"study_relative_path": "elsewhere/study", "protocol_sha256": "x"},
    )
    with pytest.raises(ValueError, match="escapes"):
        _campaign_study(tmp_path)


@pytest.mark.skipif(
    not (ROOT / CAMPAIGN_ROOT / "cycles/001-six-positions/study").is_dir(),
    reason="Requires the local sealed campaign archive; excluded from source releases",
)
def test_first_campaign_cycle_is_audit_visible_but_not_approved(monkeypatch):
    study = ROOT / CAMPAIGN_ROOT / "cycles/001-six-positions/study"
    protocol = json.loads((study / "protocol.json").read_text())
    publication = {
        "cycle_id": "001-six-positions",
        "coverage_complete": False,
        "history_count": 182,
    }
    monkeypatch.setattr(
        analysis, "_campaign_study", lambda root: (study, protocol["labels"], publication)
    )
    for cost in [2, 6, 10]:
        data = overview(ROOT, study_id="campaign", strategy="core_daily", period="full", cost=cost)
        assert data["status"] == "available"
        detail = data["efficiency"]
        assert detail["approved"] == ["core_daily"]
        assert detail["candidate_count"] == detail["rejected_count"] == 1
        assert len(detail["all_results"]) == 2
        assert not detail["campaign"]["coverage_complete"]
        assert detail["campaign"]["history_count"] == 182
        assert detail["decisions"]["six_positions"]["status"] == "rejected"
        assert all(len(row["approaches"]) == 2 for row in detail["stress"])
        assert all(len(row["approaches"]) == 2 for row in detail["yearly"])
    assert (
        overview(ROOT, study_id="campaign", strategy="six_positions", period="full", cost=6)[
            "status"
        ]
        == "unavailable"
    )


@pytest.mark.skipif(
    not (ROOT / CAMPAIGN_ROOT / "cycles/006-whole-share-contract/study").is_dir(),
    reason="Requires the local sealed campaign archive; excluded from source releases",
)
def test_corrected_campaign_uses_whole_share_control_and_excludes_legacy_diagnostic(monkeypatch):
    study = ROOT / CAMPAIGN_ROOT / "cycles/006-whole-share-contract/study"
    protocol = json.loads((study / "protocol.json").read_text())
    publication = {"cycle_id": "006-whole-share-contract", "coverage_complete": False}
    monkeypatch.setattr(
        analysis, "_campaign_study", lambda root: (study, protocol["labels"], publication)
    )
    result = overview(ROOT, study_id="campaign", strategy="core_daily", period="full", cost=6)
    assert result["status"] == "available"
    assert abs(result["metrics"]["net_return"] - 2.5979726) < 1e-7
    assert "Whole-share" in result["quantity_scope"]
    assert result["efficiency"]["candidate_count"] == 1
    assert result["efficiency"]["rejected_count"] == 1
    rejected = overview(
        ROOT, study_id="campaign", strategy="fractional_reference", period="full", cost=6
    )
    assert rejected["status"] == "unavailable"


def test_campaign_hides_fractional_semantic_probes_from_whole_share_comparisons(tmp_path):
    study = tmp_path / CAMPAIGN_ROOT / "cycles/probe/study"
    protocol = {
        "labels": {"core_daily": "Default", "legacy": "Fractional semantic probe"},
        "whole_share_buys": True,
        "whole_share_buys_by_arm": {"legacy": False},
    }
    save_sealed(study / "protocol.json", protocol)
    publication = {
        "study_relative_path": str(study.relative_to(tmp_path)),
        "protocol_sha256": hashlib.sha256(
            json.dumps(protocol, sort_keys=True, allow_nan=False).encode()
        ).hexdigest(),
    }
    save_sealed(tmp_path / CAMPAIGN_ROOT / "latest-study.json", publication)
    _, labels, _ = _campaign_study(tmp_path)
    assert labels == {"core_daily": "Default"}
