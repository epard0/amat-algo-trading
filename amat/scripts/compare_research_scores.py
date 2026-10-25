"""Re-rank recorded evidence under the incremental objective without changing a session.

Run with: uv run --locked python scripts/compare_research_scores.py SESSION OUTPUT
This is a retrospective ranking comparison, not a replay of adaptive search or promotion.
"""

from __future__ import annotations

import argparse
import csv
import json
from copy import deepcopy
from pathlib import Path

from autoresearch.contracts import Candidate, Program
from autoresearch.objective import INCREMENTAL_SCORE_VERSION, apply_incremental_score
from autoresearch.worker import complexity
from autoresearch.workspace import digest_file


def compare(root: Path, destination: Path) -> None:
    root, destination = root.resolve(), destination.resolve()
    if destination.is_relative_to(root):
        raise ValueError("Comparison output must be outside the historical session")
    if destination.exists():
        raise ValueError("Choose a new output directory to preserve prior comparisons")
    original = Program.model_validate_json((root / "program.json").read_text())
    if original.schema_version != "strategy-development/5":
        raise ValueError("This comparison requires a version 5 historical session")
    report = json.loads((root / "report.json").read_text())
    if original.digest() != report["program_sha256"]:
        raise ValueError("Report program reference mismatch")
    expected = {
        **original.frozen_files,
        "program.json": (root / "program.sha256").read_text(),
        "program.sha256": digest_file(root / "program.sha256"),
        "report.json": digest_file(root / "report.json"),
        "research.sqlite3": digest_file(root / "research.sqlite3"),
    }
    for row in report["experiments"]:
        result = json.loads(row["result"]) if row["result"] else {}
        expected.update(result.get("artifact_hashes", {}))

    def verify() -> None:
        for relative, digest in expected.items():
            path = root / relative
            if not path.resolve().is_relative_to(root) or digest_file(path) != digest:
                raise ValueError("Historical evidence changed: " + relative)

    verify()
    program = original.model_copy(update={"schema_version": "strategy-development/6"})
    rows = []
    for row in report["experiments"]:
        if not row["result"] or row["status"] not in ("baseline", "keep", "discard"):
            continue
        before = json.loads(row["result"])
        candidate_path = root / "candidates" / (row["candidate"] + ".json")
        candidate = Candidate.model_validate_json(candidate_path.read_text())
        if candidate.digest() != row["candidate"]:
            raise ValueError("Candidate digest mismatch")
        expected[str(candidate_path.relative_to(root))] = digest_file(candidate_path)
        result = deepcopy(before)
        apply_incremental_score(
            result,
            before.get("control_summary", {}),
            program,
            complexity(candidate),
            complexity(program.baseline),
        )
        parts = result["score_components"]
        rows.append(
            {
                "experiment_id": row["id"],
                "candidate_id": row["candidate"],
                "historical_decision": row["status"],
                "old_score": before["score"],
                "new_score": result["score"],
                "matched_sharpe_gain": parts.get("matched_sharpe_difference"),
                "complexity_penalty": parts["incremental_complexity_penalty"],
                "clears_initial_margin": result["score"] is not None
                and result["score"] > program.min_improvement,
                "historical_quality_gates_passed": before.get("eligible", False),
                "historical_failed_gates": ";".join(before.get("failed_gates", [])),
                "annual_return": before["train"]["annualized_return"],
                "sharpe": before["train"]["sharpe"],
                "exposure_error": parts.get("exposure_error"),
            }
        )
    candidates = [r for r in rows if r["historical_decision"] != "baseline"]
    old_order = sorted(candidates, key=lambda r: r["old_score"], reverse=True)
    new_order = sorted(
        candidates,
        key=lambda r: r["new_score"] if r["new_score"] is not None else float("-inf"),
        reverse=True,
    )
    for key, order in (("old_rank", old_order), ("new_rank", new_order)):
        for index, row in enumerate(order, 1):
            row[key] = index
    verify()
    destination.mkdir(parents=True)
    with (destination / "comparison.csv").open("w") as out:
        writer = csv.DictWriter(out, fieldnames=list(new_order[0]))
        writer.writeheader()
        writer.writerows(new_order)
    summary = {
        "source_session": str(root),
        "source_program_sha256": original.digest(),
        "new_score_version": INCREMENTAL_SCORE_VERSION,
        "score_implementation_sha256": digest_file(
            Path(apply_incremental_score.__code__.co_filename)
        ),
        "interpretation": "retrospective re-ranking only; historical decisions unchanged",
        "candidates": len(candidates),
        "verified_files": len(expected),
        "unavailable_scores": sum(r["new_score"] is None for r in candidates),
        "positive_scores": sum(
            r["new_score"] is not None and r["new_score"] > 0 for r in candidates
        ),
        "clear_initial_margin": sum(r["clears_initial_margin"] for r in candidates),
        "clear_initial_margin_and_historical_quality": sum(
            r["clears_initial_margin"] and r["historical_quality_gates_passed"] for r in candidates
        ),
        "old_top": old_order[0],
        "new_top": new_order[0],
        "rows": rows,
        "source_hashes": expected,
    }
    (destination / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "# Historical comparison of research scores",
        "",
        f"Re-ranked {len(candidates)} recorded strategies; "
        f"verified {len(expected):,} source files.",
        "Historical backtests, decisions and LLM reviews were not modified.",
        "",
        "This measures ranking alignment, not new strategy performance. Adaptive search would",
        "choose different later hypotheses; promotion uncertainty was not replayed.",
        "",
        f"Positive new scores: {summary['positive_scores']}. Candidates clearing the initial",
        f"0.05 margin: {summary['clear_initial_margin']}; also passing historical quality gates:",
        f"{summary['clear_initial_margin_and_historical_quality']}.",
        "",
        "| New rank | Experiment | Old rank | Old score | Matched Sharpe gain "
        "| Penalty | New score |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def number(value: float | None) -> str:
        return "unavailable" if value is None else f"{value:.6f}"

    for row in new_order[:15]:
        lines.append(
            f"| {row['new_rank']} | {row['experiment_id']} | {row['old_rank']} | "
            f"{row['old_score']:.6f} | {number(row['matched_sharpe_gain'])} | "
            f"{row['complexity_penalty']:.3f} | {number(row['new_score'])} |"
        )
    (destination / "report.md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in ("rows", "source_hashes")}, indent=2
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    compare(args.session, args.output)
