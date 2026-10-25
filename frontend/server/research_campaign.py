"""Read the laptop campaign's sealed protocol and actual local state. No SSH or execution."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

CAMPAIGN = "2026-10-23-adaptive-12h"
MAX_RESULTS = 80  # Bounded capacity for ten three-arm studies and fixed references.


def read_json(path: Path):
    if path.is_symlink() or path.stat().st_size > 1_048_576:
        raise ValueError("Invalid local campaign evidence")
    return json.loads(path.read_text())


def read_publication(store: Path, protocol_sha256: str):
    """A present pointer is authoritative; never downgrade to a legacy pair."""
    pointer_path = store / "publication-pointer.json"
    if pointer_path.exists() or pointer_path.is_symlink():
        pointer = read_json(pointer_path)
        if (
            not isinstance(pointer, dict)
            or set(pointer) != {"schema", "protocol_sha256", "publication_sha256"}
            or pointer.get("schema") != "incremental-publication-pointer/1"
            or pointer.get("protocol_sha256") != protocol_sha256
            or not isinstance(pointer.get("publication_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", pointer["publication_sha256"]) is None
        ):
            raise ValueError("Invalid campaign publication pointer")
        expected_sha256 = pointer["publication_sha256"]
        directory = store / "publications"
        if directory.is_symlink():
            raise ValueError("Publication generation directory must not be a symlink")
        path = directory / f"{expected_sha256}.json"
    else:
        path = store / "dashboard-results.json"
        expected_sha256 = path.with_suffix(".sha256").read_text().strip()
    reported = read_json(path)
    actual_sha256 = hashlib.sha256(
        json.dumps(reported, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    if (
        not isinstance(reported, dict)
        or actual_sha256 != expected_sha256
        or reported.get("schema") != "incremental-research-dashboard/1"
        or reported.get("protocol_sha256") != protocol_sha256
        or not isinstance(reported.get("results"), list)
        or len(reported["results"]) > MAX_RESULTS
    ):
        raise ValueError("Unbound experiment display evidence")
    return reported, str(path.relative_to(store)), actual_sha256


def describe(root: Path, now: dt.datetime | None = None):
    now = now or dt.datetime.now(dt.UTC)
    store = Path(root) / "amat/outputs/research-campaigns" / CAMPAIGN
    try:
        protocol = read_json(store / "protocol.json")
        seal = (store / "protocol.sha256").read_text().strip()
        actual = hashlib.sha256(
            json.dumps(protocol, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        if seal != actual:
            raise ValueError("Campaign protocol seal differs")
        state = read_json(store / "state.json")
        if not isinstance(state, dict):
            raise ValueError("Invalid campaign state")
        baseline = state.get("baseline", {})
        ticks = state.get("completed_ticks")
        next_run = state.get("next_scheduled_utc")
        deadline = dt.datetime.fromisoformat(protocol["deadline_utc"].replace("Z", "+00:00"))
        next_at = dt.datetime.fromisoformat(next_run.replace("Z", "+00:00")) if next_run else None
        active = state.get("active_run")
        stale = bool(
            now < deadline and next_at and now > next_at + dt.timedelta(minutes=45) and not active
        )
        results = []
        comparison = None
        comparison_evidence = []
        results_error = None
        publication_file = "protocol.json"
        publication_sha = seal
        result_path = store / "dashboard-results.json"
        pointer_path = store / "publication-pointer.json"
        if result_path.exists() or pointer_path.exists() or pointer_path.is_symlink():
            try:
                reported, publication_file, publication_sha = read_publication(store, seal)
                results = reported["results"]
                comparison = reported.get("comparison")
                comparison_evidence = reported.get("evidence", [])
            except (OSError, ValueError, TypeError):
                results_error = "Strategy comparison publication failed its source seal; no unverified results are displayed."
        return dict(
            schema="local-research-campaign/1",
            campaign_id=CAMPAIGN,
            execution_authority="none",
            collection_location="this laptop",
            collection_independent_of_desktop=False,
            transport="local",
            status=state.get("status", "unknown"),
            lifecycle=state.get("status", "unknown"),
            stale=stale,
            started_at=protocol["start_utc"],
            deadline=protocol["deadline_utc"],
            next_run_at=next_run if now < deadline else None,
            observed_at=state.get("updated_utc") or state.get("initial_tick_opened_utc"),
            read_at=now.isoformat(),
            campaign=state,
            current_default=dict(
                label=baseline.get("version"),
                id=baseline.get("profile_path"),
                sha256=baseline.get("profile_sha256"),
            ),
            counts=dict(
                completed_experiments=state.get("experiments_completed"),
                completed_runs=len(ticks) if isinstance(ticks, list) else None,
                promotions=state.get("experiments_promoted"),
            ),
            pending_foundations=state.get("pending_foundation", []),
            results=results,
            comparison=comparison,
            evidence=comparison_evidence,
            results_error=results_error,
            current_run=active,
            last_summary=state.get("last_summary")
            or (
                f"Foundation validation records {state.get('engineering_tests_passed', 'an unavailable number of')} engineering tests. No completed strategy experiment or promotion is recorded."
                if state.get("experiments_completed") == 0
                else state.get("last_summary", "See the sealed experiment and promotion evidence.")
            ),
            publication_evidence=dict(
                file=publication_file, sha256=publication_sha, protocol_sha256=seal
            ),
            scheduling_note="Codex runs this finite hourly campaign on this laptop. It must remain awake with Codex available. The VPS paper-session monitor is separate.",
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return dict(
            schema="local-research-campaign/1",
            status="unavailable",
            transport="local",
            stale=True,
            collection_location="this laptop",
            execution_authority="none",
            message="Local campaign evidence is unavailable or failed verification.",
            error_type=type(exc).__name__,
        )
