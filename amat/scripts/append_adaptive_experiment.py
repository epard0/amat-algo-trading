"""Append independently evaluated native outcomes to the local sealed display.

This publisher has no baseline, execution or deployment authority.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import fcntl
import json
import os
from pathlib import Path

from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.study import digest
from autoresearch.improvements.validation_study import monthly_metrics, sliced_metrics

MAX_RESULTS = 80  # Ten experiments * two candidates * three costs, plus reference rows.
MAX_PUBLICATION_BYTES = 1_048_576
MAX_CANDIDATES = 2
SHA256_LENGTH = 64


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with temporary.open("w") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def save(path: Path, value: dict) -> None:
    atomic_text(path, json.dumps(value, sort_keys=True, allow_nan=False) + "\n")
    atomic_text(path.with_suffix(".sha256"), digest(value))


def publication_pointer(campaign: Path, publication: dict) -> None:
    """Commit one verified immutable generation with a single atomic pointer."""
    pointer = {
        "schema": "incremental-publication-pointer/1",
        "protocol_sha256": publication["protocol_sha256"],
        "publication_sha256": digest(publication),
    }
    atomic_text(campaign / "publication-pointer.json", json.dumps(pointer, sort_keys=True))


def current_publication(campaign: Path, protocol_sha: str) -> dict:
    pointer_path = campaign / "publication-pointer.json"
    if pointer_path.is_symlink():
        raise ValueError("Invalid publication pointer symlink")
    if not pointer_path.exists():
        legacy = campaign / "dashboard-results.json"
        if legacy.is_symlink() or legacy.stat().st_size > MAX_PUBLICATION_BYTES:
            raise ValueError("Invalid compatibility publication file")
        publication = sealed(legacy)
    else:
        if pointer_path.is_symlink() or pointer_path.stat().st_size > MAX_PUBLICATION_BYTES:
            raise ValueError("Invalid publication pointer file")
        pointer = json.loads(pointer_path.read_text())
        generation = pointer.get("publication_sha256") if isinstance(pointer, dict) else None
        if (
            not isinstance(pointer, dict)
            or set(pointer) != {"schema", "protocol_sha256", "publication_sha256"}
            or pointer.get("schema") != "incremental-publication-pointer/1"
            or pointer.get("protocol_sha256") != protocol_sha
            or not isinstance(generation, str)
            or len(generation) != SHA256_LENGTH
            or any(c not in "0123456789abcdef" for c in generation)
        ):
            raise ValueError("Invalid publication pointer identity")
        path = campaign / "publications" / f"{generation}.json"
        if (
            path.parent.is_symlink()
            or path.is_symlink()
            or path.stat().st_size > MAX_PUBLICATION_BYTES
        ):
            raise ValueError("Invalid immutable publication file")
        publication = sealed(path)
        if digest(publication) != generation:
            raise ValueError("Immutable generation differs from pointer")
    if (
        not isinstance(publication, dict)
        or publication.get("schema") != "incremental-research-dashboard/1"
        or publication.get("protocol_sha256") != protocol_sha
        or not isinstance(publication.get("results"), list)
        or len(publication["results"]) > MAX_RESULTS
    ):
        raise ValueError("Invalid existing campaign publication")
    return publication


def display_decision(decision: dict) -> str:
    failed = [check for check in decision["checks"] if not check["passes"]]
    if not failed:
        return decision["reason"]
    messages = []
    for name, description in (
        ("incremental_return", "Lower full-history or forward returns"),
        ("preserve_2023", "Lower 2023 return"),
        ("drawdown", "Larger drawdown"),
    ):
        costs = sorted({check["cost_bps"] for check in failed if check["name"] == name})
        if costs:
            messages.append(f"{description} at {', '.join(map(str, costs))} bps.")
    return " ".join(messages) or decision["reason"]


def append(  # noqa: PLR0912, PLR0915 -- explicit validation and publication under one lock
    campaign: Path, study: Path, labels: dict[str, str]
) -> None:
    protocol = sealed(study / "protocol.json")
    review = sealed(study / "typed-evaluation.json")
    if review["protocol_sha256"] != digest(protocol):
        raise ValueError("Independent review is bound to a different protocol")
    if (
        study.resolve().parent != (campaign / "studies").resolve()
        or not 0 < len(labels) <= MAX_CANDIDATES
        or set(labels) != set(protocol["arms"]) - {"baseline"}
    ):
        raise ValueError("Study location or exact candidate family differs")
    additions = []
    for arm, label in labels.items():
        decision = review["results"][arm]
        if decision["status"] not in {"rejected", "pending_validation"}:
            raise ValueError("This display-only publisher accepts no promotion decision")
        for cost in (2, 6, 10):
            path = study / f"{arm}-{cost}bps.json"
            record = sealed(path)
            if (
                record["arm"] != arm
                or record["cost_bps"] != cost
                or not record["finalized"]
                or record["study_id"] != study.name
                or record["profile_sha256"] != protocol["baseline_config_sha256"]
                or not record["run_id"]
                or record["protocol_sha256"] != digest(protocol)
                or digest(record["sessions"]) != record["sessions_sha256"]
                or decision["candidate_records_sha256"][str(cost)] != digest(record)
            ):
                raise ValueError("Candidate identity differs from independent review")
            if sliced_metrics(record["sessions"]) != record["metrics"]:
                raise ValueError("Published metrics differ from native equity")
            if monthly_metrics(record["sessions"]) != record["monthly"]:
                raise ValueError("Published months differ from native equity")
            additions.append((arm, label, cost, path, record, decision))
    with (campaign / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads((campaign / "state.json").read_text())
        if state.get("active_run"):
            raise ValueError("Measurement writer remains active")
        if (
            state["current_experiment"]["protocol_sha256"] != digest(protocol)
            or state["current_experiment"]["id"] != study.name
            or state["baseline"]["profile_sha256"] != protocol["baseline_config_sha256"]
            or any(
                record["run_id"] != state["current_experiment"]["measurement_run_id"]
                for _, _, _, _, record, _ in additions
            )
        ):
            raise ValueError("Study is not this campaign's registered baseline experiment")
        publication = current_publication(campaign, digest(sealed(campaign / "protocol.json")))
        archive = campaign / "publications"
        archive.mkdir(exist_ok=True)
        old = archive / f"{digest(publication)}.json"
        if not old.exists():
            save(old, publication)
        elif sealed(old) != publication:
            raise ValueError("Existing immutable publication differs")
        # Seed the old committed generation before touching compatibility files.
        # A crash while writing JSON and its sidecar then leaves the API on the
        # previous verified generation rather than an unavailable half-pair.
        if not (campaign / "publication-pointer.json").exists():
            publication_pointer(campaign, publication)
        # Keep every prior numerical outcome visible; focus detailed plots on
        # the latest experiment. Prior monthly/annual details remain sealed in
        # the archived publication and original native measurements.
        prior_candidate_names = {
            row["name"]
            for row in publication["results"]
            if row["status"] not in {"current verified default", "reference only"}
        }
        for selected in publication["comparison"]["costs"].values():
            for field in ("series", "periods", "months"):
                selected[field] = [
                    row for row in selected[field] if row["name"] not in prior_candidate_names
                ]
        for arm, label, cost, path, record, decision in additions:
            identifier = f"{study.name}-{arm}-{cost}"
            if any(row["id"] == identifier for row in publication["results"]):
                raise ValueError("Experiment already published; refusing duplicate rows")
            publication["results"].append(
                {
                    "id": identifier,
                    "name": label,
                    "cost_bps": cost,
                    "status": decision["status"],
                    "net_return": record["metrics"]["full"]["net_return"],
                    "forward_return": record["metrics"]["walk_forward"]["net_return"],
                    "max_drawdown": record["metrics"]["full"]["max_drawdown"],
                    "reason": decision["reason"],
                    "display_reason": (
                        "Lower returns in all six full-history and forward comparisons. "
                        "Lower drawdown and a better 2023 did not offset the lost growth."
                        if all(
                            not check["passes"]
                            for check in decision["checks"]
                            if check["name"] == "incremental_return"
                        )
                        and all(
                            check["passes"]
                            for check in decision["checks"]
                            if check["name"] in {"drawdown", "preserve_2023"}
                        )
                        else display_decision(decision)
                    ),
                    "evidence": {"path": str(path), "sha256": digest(record)},
                }
            )
            selected = publication["comparison"]["costs"][str(cost)]
            points = {}
            peak = 100000.0
            contributions = collections.defaultdict(lambda: collections.defaultdict(float))
            for row in record["sessions"]:
                peak = max(peak, row["equity"])
                points[row["session"][:7]] = {
                    "date": row["session"],
                    "growth": row["equity"] / 100000 - 1,
                    "drawdown": row["equity"] / peak - 1,
                }
                for asset, values in row.get("assets", {}).items():
                    contributions[row["session"][:7]][asset] += float(values["net_pnl"])
            selected["series"].append(
                {
                    "name": label,
                    "color": "#88b6ff" if arm == next(iter(labels)) else "#ef91ac",
                    "points": [
                        {"date": "2017-01-03", "growth": 0, "drawdown": 0},
                        *points.values(),
                    ],
                }
            )
            for key in (
                "full",
                "walk_forward",
                *map(str, range(2020, 2027)),
                "stress_2018",
                "stress_covid",
                "stress_2022",
            ):
                metric = record["metrics"][key]
                selected["periods"].append(
                    {
                        "period": key.replace("walk_forward", "2020 onward")
                        .replace("full", "Full history")
                        .replace("stress_", "Stress: "),
                        "name": label,
                        "net_return": metric["net_return"],
                        "max_drawdown": metric["max_drawdown"],
                        "fees": metric["costs"],
                    }
                )
            for month in record["monthly"]:
                losses = sorted(
                    ((a, v) for a, v in contributions[month["month"]].items() if v < 0),
                    key=lambda x: x[1],
                )[:3]
                selected["months"].append(
                    {
                        "month": month["month"],
                        "name": label,
                        "net_return": month["net_return"],
                        "contributors": ", ".join(f"{a}: ${v:,.0f}" for a, v in losses)
                        if month["net_return"] < 0
                        else "",
                    }
                )
            selected["months"].sort(key=lambda x: (x["month"], x["name"]))
        publication["decisions"][study.name] = review["results"]
        publication["detailed_comparison_experiment"] = study.name
        publication["evidence"].append(str(study / "typed-evaluation.json"))
        publication["comparison"]["limitations"].append(protocol["display_limitation"])
        publication["published_utc"] = dt.datetime.now(dt.UTC).isoformat()
        if (
            len(publication["results"]) > MAX_RESULTS
            or len(json.dumps(publication).encode()) > MAX_PUBLICATION_BYTES
        ):
            raise ValueError("Publication exceeds the bounded dashboard contract")
        save(archive / f"{digest(publication)}.json", publication)
        save(campaign / "dashboard-results.json", publication)
        publication_pointer(campaign, publication)
    print(
        json.dumps(
            {
                "publication": str(campaign / "dashboard-results.json"),
                "rows": len(publication["results"]),
                "sha256": digest(publication),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--labels", type=json.loads, required=True)
    args = parser.parse_args()
    append(args.campaign, args.study, args.labels)
