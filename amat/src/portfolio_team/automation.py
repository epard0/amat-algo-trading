"""Bounded scheduled review cycle; explicit catalog only, never model-generated commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from amat.errors import ConfigurationError
from portfolio_team.contracts import Policy
from portfolio_team.coordinator import review_identity, run_review
from portfolio_team.evidence import BudgetDeferredError, EvidenceError, load_catalog, make_bundle
from portfolio_team.model_runtime import RemoteProvider
from portfolio_team.providers import Provider
from portfolio_team.reports import _write_once, export_review
from portfolio_team.research_queue import build_queue
from portfolio_team.store import Store

MAX_CYCLE_REVIEWS = 20


def run_cycle(
    store: Store,
    root: Path,
    catalog_path: Path,
    provider: Provider | RemoteProvider,
    *,
    max_reviews: int = 1,
) -> dict[str, Any]:
    if not 1 <= max_reviews <= MAX_CYCLE_REVIEWS:
        raise EvidenceError("max_reviews must be between 1 and 20")
    catalog = load_catalog(catalog_path)
    rows: list[dict[str, Any]] = []
    dispatched = 0
    for comparison in catalog.comparisons:
        try:
            bundle, blobs = make_bundle(catalog, comparison.id, root)
            store.import_bundle(bundle, blobs)
        except (ValueError, OSError, ConfigurationError) as exc:
            store.quarantine(comparison.id, type(exc).__name__)
            rows.append({"comparison": comparison.id, "status": "quarantined"})
            continue
        review_id, _ = review_identity(bundle, provider, Policy())
        prior = store.db.execute("SELECT status FROM reviews WHERE id=?", (review_id,)).fetchone()
        row = {"comparison": comparison.id, "review_id": review_id}
        if prior and prior["status"] == "failed":
            rows.append({**row, "status": "needs_operator_retry"})
            continue
        cached = store.decision(review_id)
        if cached is None and dispatched >= max_reviews:
            rows.append({**row, "status": "deferred_cycle_limit"})
            continue
        calls_before = len(store.model_calls(review_id))
        try:
            if cached is None:
                dispatched += 1
            decision = cached or run_review(store, bundle, provider)
            target = export_review(store, decision, root / "outputs/governance")
            # Proposed work only. Nothing consumes these as executable commands.
            chief = next(item for item in decision.assessments if item.role == "chief_pm")
            backlog = {
                "schema_version": "research-backlog/1",
                "review_id": decision.review_id,
                "evidence_sha256": decision.evidence_sha256,
                "status": "proposed_not_authorized",
                "execution_authority": "none",
                "requests_in_priority_order": [item.model_dump() for item in chief.requests],
            }
            _write_once(target / "research_backlog.json", json.dumps(backlog, indent=2).encode())
            queue = build_queue(bundle, decision)
            _write_once(target / "research_queue.v2.json", (queue.canonical() + "\n").encode())
            rows.append({**row, "status": "cached" if cached else "completed"})
        except BudgetDeferredError as exc:
            if cached is None and len(store.model_calls(review_id)) == calls_before:
                dispatched -= 1
            rows.append({**row, "status": "deferred_budget", "budget": exc.budget})
        except (ValueError, OSError) as exc:
            rows.append({**row, "status": "failed", "error": type(exc).__name__})
    result = {
        "dispatched_reviews": dispatched,
        "incomplete": any(row["status"] not in ("completed", "cached") for row in rows),
        "rows": rows,
        "execution_authority": "none",
    }
    with store.db:
        store.event("review_cycle", result)
    return result
