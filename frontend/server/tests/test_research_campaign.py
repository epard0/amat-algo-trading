import datetime as dt
import hashlib
import json

import pytest

from server.research_campaign import CAMPAIGN, describe


def evidence(root):
    store = root / "amat/outputs/research-campaigns" / CAMPAIGN
    store.mkdir(parents=True)
    protocol = {"start_utc": "2026-10-23T16:40:10Z", "deadline_utc": "2026-10-24T04:40:10Z"}
    (store / "protocol.json").write_text(json.dumps(protocol))
    (store / "protocol.sha256").write_text(
        hashlib.sha256(json.dumps(protocol, sort_keys=True, allow_nan=False).encode()).hexdigest()
    )
    state = dict(
        status="awaiting_next_tick",
        baseline={"version": "v14.3.0", "profile_sha256": "known"},
        completed_ticks=[{"tick": "initial"}],
        experiments_completed=0,
        experiments_promoted=0,
        promotion_enabled=False,
        next_scheduled_utc="2026-10-23T17:40:10Z",
        pending_foundation=["nativecontrolreproduction"],
        updated_utc="2026-10-23T16:51:40+00:00",
    )
    (store / "state.json").write_text(json.dumps(state))
    return store


def test_laptop_evidence_is_local_and_does_not_claim_remote_independence(tmp_path):
    evidence(tmp_path)
    result = describe(tmp_path, dt.datetime(2026, 10, 23, 17, 20, tzinfo=dt.UTC))
    assert result["transport"] == "local" and not result["collection_independent_of_desktop"]
    assert result["counts"] == dict(completed_experiments=0, completed_runs=1, promotions=0)
    assert result["campaign"]["promotion_enabled"] is False
    assert result["results"] == [] and not result["stale"]


def test_missed_local_tick_is_not_a_completed_run(tmp_path):
    evidence(tmp_path)
    result = describe(tmp_path, dt.datetime(2026, 10, 23, 18, 30, tzinfo=dt.UTC))
    assert result["stale"] and result["counts"]["completed_runs"] == 1


def test_changed_protocol_and_missing_evidence_are_unavailable(tmp_path):
    assert describe(tmp_path)["status"] == "unavailable"
    store = evidence(tmp_path)
    (store / "protocol.json").write_text("{}")
    assert describe(tmp_path)["status"] == "unavailable"


def test_unknown_counts_stay_unknown_and_expired_schedule_has_no_next_tick(tmp_path):
    store = evidence(tmp_path)
    (store / "state.json").write_text("{}")
    result = describe(tmp_path, dt.datetime(2026, 10, 24, 5, tzinfo=dt.UTC))
    assert result["counts"]["completed_experiments"] is None
    assert result["counts"]["completed_runs"] is None and result["next_run_at"] is None


def test_results_require_campaign_binding_and_own_seal(tmp_path):
    store = evidence(tmp_path)
    protocol_sha = (store / "protocol.sha256").read_text()
    result = {
        "schema": "incremental-research-dashboard/1",
        "protocol_sha256": protocol_sha,
        "results": [{"id": "one", "status": "rejected", "net_return": 0.02}],
    }
    path = store / "dashboard-results.json"
    path.write_text(json.dumps(result))
    path.with_suffix(".sha256").write_text(
        hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
    )
    assert describe(tmp_path)["results"][0]["status"] == "rejected"
    result["protocol_sha256"] = "different"
    path.write_text(json.dumps(result))
    assert describe(tmp_path)["results"] == [] and describe(tmp_path)["results_error"]


def test_verified_comparison_and_saved_summary_are_published_without_fake_experiments(tmp_path):
    store = evidence(tmp_path)
    state = json.loads((store / "state.json").read_text())
    state["last_summary"] = "Current default reproduced; no alpha result yet."
    (store / "state.json").write_text(json.dumps(state))
    result = {
        "schema": "incremental-research-dashboard/1",
        "protocol_sha256": (store / "protocol.sha256").read_text(),
        "results": [],
        "comparison": {
            "costs": {"6": {"series": []}},
            "limitations": ["Historical execution only"],
        },
    }
    path = store / "dashboard-results.json"
    path.write_text(json.dumps(result))
    path.with_suffix(".sha256").write_text(
        hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
    )
    read = describe(tmp_path)
    assert read["last_summary"] == state["last_summary"]
    assert read["comparison"] == result["comparison"]
    assert read["counts"]["completed_experiments"] == 0
    path.write_text("{}")
    read = describe(tmp_path)
    assert read["comparison"] is None and read["results"] == []


def test_full_campaign_capacity_is_bounded_and_preserves_old_benchmarks(tmp_path):
    store = evidence(tmp_path)
    result = {
        "schema": "incremental-research-dashboard/1",
        "protocol_sha256": (store / "protocol.sha256").read_text(),
        "results": [{"id": str(i), "status": "rejected"} for i in range(80)],
    }
    path = store / "dashboard-results.json"

    def publish():
        path.write_text(json.dumps(result))
        path.with_suffix(".sha256").write_text(
            hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
        )

    publish()
    assert len(describe(tmp_path)["results"]) == 80
    result["results"].append({"id": "overflow"})
    publish()
    assert describe(tmp_path)["results"] == []
    assert describe(tmp_path)["results_error"]


def generation(store, name="one", *, count=1, extra=None):
    result = {
        "schema": "incremental-research-dashboard/1",
        "protocol_sha256": (store / "protocol.sha256").read_text(),
        "results": [{"id": f"{name}-{i}", "status": "rejected"} for i in range(count)],
    }
    if extra:
        result.update(extra)
    sha = hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
    directory = store / "publications"
    directory.mkdir(exist_ok=True)
    path = directory / f"{sha}.json"
    path.write_text(json.dumps(result))
    return path, sha, result


def point_to(store, sha, **changes):
    pointer = {
        "schema": "incremental-publication-pointer/1",
        "protocol_sha256": (store / "protocol.sha256").read_text(),
        "publication_sha256": sha,
        **changes,
    }
    temporary = store / "publication-pointer.tmp"
    temporary.write_text(json.dumps(pointer))
    temporary.replace(store / "publication-pointer.json")


def legacy_publication(store):
    _, _, result = generation(store, "legacy")
    path = store / "dashboard-results.json"
    path.write_text(json.dumps(result))
    path.with_suffix(".sha256").write_text(
        hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
    )


def assert_rejected_pointer(root):
    result = describe(root)
    assert result["results"] == []
    assert result["comparison"] is None
    assert result["results_error"]


def test_pointer_keeps_old_verified_generation_through_legacy_pair_update(tmp_path):
    store = evidence(tmp_path)
    legacy_publication(store)
    first, sha, old = generation(store, "old")
    point_to(store, sha)
    # A generation needs no separately published seal: the atomic pointer binds it.
    assert not first.with_suffix(".sha256").exists()
    (store / "dashboard-results.json").write_text("new but incomplete legacy JSON")
    (store / "dashboard-results.sha256").write_text("old legacy seal")
    result = describe(tmp_path)
    assert result["results"] == old["results"]
    assert result["results_error"] is None
    assert result["publication_evidence"]["file"] == f"publications/{sha}.json"
    assert result["publication_evidence"]["sha256"] == sha
    _, new_sha, new = generation(store, "new")
    point_to(store, new_sha)
    updated = describe(tmp_path)
    assert updated["results"] == new["results"]
    assert updated["publication_evidence"]["file"] == f"publications/{new_sha}.json"


@pytest.mark.parametrize(
    "change",
    [
        {"publication_sha256": "../dashboard-results"},
        {"publication_sha256": "../../" + "a" * 64},
        {"publication_sha256": "a" * 63},
        {"publication_sha256": "x" * 64},
        {"publication_sha256": 123},
        {"protocol_sha256": "b" * 64},
        {"schema": "incremental-publication-pointer/9"},
        {"path": "/outside.json"},
    ],
)
def test_invalid_pointer_never_falls_back_to_valid_legacy(tmp_path, change):
    store = evidence(tmp_path)
    legacy_publication(store)
    _, sha, _ = generation(store)
    point_to(store, sha, **change)
    assert_rejected_pointer(tmp_path)


def test_pointer_missing_or_tampered_generation_fails_closed(tmp_path):
    store = evidence(tmp_path)
    legacy_publication(store)
    path, sha, _ = generation(store)
    point_to(store, sha)
    path.unlink()
    assert_rejected_pointer(tmp_path)
    path.write_text("{}")
    assert_rejected_pointer(tmp_path)


@pytest.mark.parametrize("symlink_kind", ["pointer", "dangling_pointer", "generation", "directory"])
def test_pointer_and_generation_symlinks_fail_closed(tmp_path, symlink_kind):
    store = evidence(tmp_path)
    legacy_publication(store)
    path, sha, _ = generation(store)
    point_to(store, sha)
    pointer = store / "publication-pointer.json"
    if symlink_kind in {"pointer", "dangling_pointer"}:
        external = tmp_path / "external-pointer.json"
        if symlink_kind == "pointer":
            external.write_bytes(pointer.read_bytes())
        pointer.unlink()
        pointer.symlink_to(external)
    elif symlink_kind == "generation":
        external = tmp_path / "external-generation.json"
        external.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(external)
    else:
        directory = store / "publications"
        external = tmp_path / "external-generations"
        directory.rename(external)
        directory.symlink_to(external, target_is_directory=True)
    assert_rejected_pointer(tmp_path)


@pytest.mark.parametrize("kind", ["oversized", "row_overflow", "non_mapping"])
def test_pointer_generation_keeps_size_shape_and_capacity_guards(tmp_path, kind):
    store = evidence(tmp_path)
    legacy_publication(store)
    if kind == "oversized":
        _, sha, _ = generation(store, extra={"padding": "x" * 1_048_576})
    elif kind == "row_overflow":
        _, sha, _ = generation(store, count=81)
    else:
        value = []
        sha = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        (store / "publications" / f"{sha}.json").write_text(json.dumps(value))
    point_to(store, sha)
    assert_rejected_pointer(tmp_path)
