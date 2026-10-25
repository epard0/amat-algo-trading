"""Publish sealed completed campaign measurements; never promotes or sends orders."""
# ruff: noqa: E501, PLR0912, PLR0915, PLR2004 -- explicit descriptive publication pipeline

from __future__ import annotations

import argparse
import collections
import datetime as dt
import fcntl
import json
from pathlib import Path
from typing import Any

from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.study import digest, write
from autoresearch.improvements.switching_study import paired_monthly_interval

LABELS = {
    "baseline": "Current default · v14.3.0",
    "qqq_native": "QQQ · native entry cap",
    "qqq_investor_uncapped": "QQQ · uncapped investor reference",
    "joint_downside75": "Conditional joint-loss risk reduction",
    "static75": "Always 75% risk · control",
}
COLORS = {
    "baseline": "#77ddcc",
    "qqq_native": "#e9b949",
    "qqq_investor_uncapped": "#e9b949",
    "joint_downside75": "#99baff",
    "static75": "#c8a2ee",
}


def publish(
    campaign: Path, foundation: Path, experiment: Path, total_return: Path | None = None
) -> None:
    protocol = sealed(campaign / "protocol.json")
    labels = dict(LABELS)
    if total_return is not None:
        labels["qqq_total_return_reference"] = "QQQ · idealized total return"
    records: dict[str, dict[int, dict[str, Any]]] = {}
    paths = {}
    for arm in labels:
        directory = (
            total_return
            if arm == "qqq_total_return_reference"
            else foundation
            if arm.startswith("qqq") or arm == "baseline"
            else experiment
        )
        records[arm] = {}
        for cost in (2, 6, 10):
            path = directory / f"{arm}-{cost}bps.json"
            record = sealed(path)
            if record["arm"] != arm or record["cost_bps"] != cost or not record["finalized"]:
                raise ValueError("Measurement identity is incomplete")
            p = sealed(directory / "protocol.json")
            if (
                record["protocol_sha256"] != digest(p)
                or digest(record["sessions"]) != record["sessions_sha256"]
            ):
                raise ValueError("Measurement source seal differs")
            records[arm][cost] = record
            paths[(arm, cost)] = {"path": str(path), "sha256": digest(record)}
    reproduction = []
    for cost in (2, 6, 10):
        first = records["baseline"][cost]
        replay = sealed(experiment / f"baseline-{cost}bps.json")
        old = sealed(
            Path(protocol["original_baseline"]["profile_path"]).parents[2]
            / "outputs/analysis/2026-10-20-cost-aware-annual-allocation/study"
            / f"core_daily-{cost}bps.json"
        )
        a = [(x["session"], x["equity"]) for x in first["sessions"]]
        for kind, value in [("experiment control", replay), ("archived incumbent", old)]:
            b = [
                (x["session"], x["equity"])
                for x in value["sessions"]
                if "2017-01-03" <= x["session"] <= "2026-10-12"
            ]
            reproduction.append({"cost_bps": cost, "kind": kind, "exact_date_equity_match": a == b})
            if a != b:
                raise ValueError("Current default does not reproduce exactly")
    decisions = {}
    for arm in ("joint_downside75", "static75"):
        failures = []
        cells = []
        for cost in (2, 6, 10):
            candidate = records[arm][cost]
            base = records["baseline"][cost]
            for period in ("full", "walk_forward"):
                left = candidate["metrics"][period]
                right = base["metrics"][period]
                delta = left["net_return"] - right["net_return"]
                cells.append({"cost_bps": cost, "period": period, "return_difference": delta})
                if delta < -1e-12:
                    failures.append(f"{cost}bps {period} return below baseline")
                if abs(left["max_drawdown"]) > abs(right["max_drawdown"]) + 1e-12:
                    failures.append(f"{cost}bps {period} drawdown worse than baseline")
            if (
                candidate["metrics"]["2023"]["net_return"] + 1e-12
                < base["metrics"]["2023"]["net_return"]
            ):
                failures.append(f"{cost}bps 2023 return below baseline")
        decisions[arm] = {
            "status": "rejected" if failures else "pending verification",
            "reason": "; ".join(failures)
            if failures
            else "Numerical screen passed; native planning/execution and independent robustness gates remain incomplete.",
            "failures": failures,
            "cells": cells,
            "monthly_log_return_difference_annualized_95pct_interval": {
                str(cost): paired_monthly_interval(records[arm][cost], records["baseline"][cost])
                for cost in (2, 6, 10)
            },
        }
    typed = sealed(experiment / "typed-evaluation.json")
    for arm in ("joint_downside75", "static75"):
        verified = typed["results"][arm]
        if any(
            verified["candidate_records_sha256"][str(cost)] != digest(records[arm][cost])
            for cost in (2, 6, 10)
        ):
            raise ValueError("Independent review refers to different measurements")
        if (verified["status"] == "rejected") != (decisions[arm]["status"] == "rejected"):
            raise ValueError("Descriptive publication disagrees with independent numerical review")
    rows = []
    comparison = {
        "costs": {},
        "limitations": [
            "The 2016 history seeds signals; accounts run January 3, 2017 to October 12, 2026. Every historical date has prior research exposure; annual slices are chronological continuing-account evaluations, not untouched holdouts.",
            "Costs are modeled at 2/6/10 basis points per side, not measured spreads or broker implementation shortfall. All cash earns zero.",
            "QQQ holds a 100% target outside AMAT diversification limits. Both references use whole shares, next-session observed VWAP, native rebalance bands and cash distributions. The observed entry cap does not bind this $100,000 QQQ account, so the capped and uncapped histories coincide.",
            "The separate idealized QQQ total-return curve uses observed initial VWAP and entry costs, then provider adjusted-close ratios with fractional reinvestment. It has no native cap/band and is not a promotable strategy. These cash-distribution QQQ references are not an official fractional dividend-reinvestment index. Dividend cash can be reinvested only when native trade thresholds permit. Ex-date cash timing differs from broker payable-date buying power.",
            "Per-asset P&L is reconstructed exactly from fills, splits, cash distributions and marks. Joint monthly losses motivate a test; they do not establish a cause.",
            "The conditional trigger reads target-weighted trailing returns, not realized past portfolio returns. Static 75% is a fixed severity ablation, not an exactly exposure-matched beta control.",
            "Historical VWAP/volume execution and broker staged limit fills are different. Local sizing/accounting parity is verified; actual broker fill equivalence remains unverified. The new research callback has no verified exported-plan adapter.",
            "Older valid and rejected candidates remain available in the existing research comparisons and sealed ledgers. No overall historical-best claim or new default is made by this publication.",
        ],
    }
    for cost in (2, 6, 10):
        series = []
        periods = []
        months = []
        for arm, arm_records in records.items():
            record = arm_records[cost]
            full = record["metrics"]["full"]
            forward = record["metrics"]["walk_forward"]
            status = (
                "current verified default"
                if arm == "baseline"
                else "reference only"
                if arm.startswith("qqq")
                else decisions[arm]["status"]
            )
            reason = (
                "Unchanged baseline reproduced exactly against archived and new controls."
                if arm == "baseline"
                else "Reference investment; not an AMAT promotion candidate."
                if arm.startswith("qqq")
                else decisions[arm]["reason"]
            )
            rows.append(
                {
                    "id": f"{arm}-{cost}",
                    "name": labels[arm],
                    "cost_bps": cost,
                    "status": status,
                    "net_return": full["net_return"],
                    "forward_return": forward["net_return"],
                    "max_drawdown": full["max_drawdown"],
                    "reason": reason,
                    "display_reason": (
                        "Full-history returns fell at lower costs; the highest-cost case also worsened drawdown and 2023 performance."
                        if arm == "joint_downside75" and decisions[arm]["status"] == "rejected"
                        else "Lower returns in every full-history and forward comparison, despite lower drawdown."
                        if arm == "static75" and decisions[arm]["status"] == "rejected"
                        else reason
                    ),
                    "evidence": paths[(arm, cost)],
                }
            )
            monthly_points = {}
            peak = 100000.0
            for row in record["sessions"]:
                peak = max(peak, row["equity"])
                monthly_points[row["session"][:7]] = {
                    "date": row["session"],
                    "growth": row["equity"] / 100000 - 1,
                    "drawdown": row["equity"] / peak - 1,
                }
            if arm != "qqq_investor_uncapped":
                points = [
                    {"date": "2017-01-03", "growth": 0, "drawdown": 0},
                    *monthly_points.values(),
                ]
                series.append(
                    {"name": labels[arm], "color": COLORS.get(arm, "#f6a568"), "points": points}
                )
                for key in (
                    "full",
                    "walk_forward",
                    "2020",
                    "2021",
                    "2022",
                    "2023",
                    "2024",
                    "2025",
                    "2026",
                    "stress_2018",
                    "stress_covid",
                    "stress_2022",
                ):
                    m = record["metrics"][key]
                    periods.append(
                        {
                            "period": key.replace("walk_forward", "2020 onward")
                            .replace("full", "Full history")
                            .replace("stress_", "Stress: "),
                            "name": labels[arm],
                            "net_return": m["net_return"],
                            "max_drawdown": m["max_drawdown"],
                            "fees": m["costs"],
                        }
                    )
                contributions = collections.defaultdict(lambda: collections.defaultdict(float))
                for day in record["sessions"]:
                    for asset, v in day.get("assets", {}).items():
                        contributions[day["session"][:7]][asset] += float(v["net_pnl"])
                for month in record["monthly"]:
                    losses = sorted(
                        ((a, v) for a, v in contributions[month["month"]].items() if v < 0),
                        key=lambda x: x[1],
                    )[:3]
                    months.append(
                        {
                            "month": month["month"],
                            "name": labels[arm],
                            "net_return": month["net_return"],
                            "contributors": ", ".join(f"{a}: ${v:,.0f}" for a, v in losses)
                            if month["net_return"] < 0
                            else "",
                        }
                    )
        comparison["costs"][str(cost)] = {
            "series": series,
            "periods": periods,
            "months": sorted(months, key=lambda x: (x["month"], x["name"])),
        }
    publication = {
        "schema": "incremental-research-dashboard/1",
        "protocol_sha256": digest(protocol),
        "published_utc": dt.datetime.now(dt.UTC).isoformat(),
        "results": rows,
        "comparison": comparison,
        "decisions": decisions,
        "reproduction": reproduction,
        "evidence": [
            str(experiment / "typed-evaluation.json"),
            str(campaign / "research-review/parity-foundation/typed-gates-foundation.json"),
            str(
                campaign.parents[1]
                / "research-review-2026-10-23/parity-foundation/parity-summary.json"
            ),
        ],
    }
    summary = {
        "schema": "incremental-experiment-comparison/1",
        "protocol_sha256": digest(protocol),
        "foundation_protocol_sha256": sealed(foundation / "protocol.json")
        and (foundation / "protocol.sha256").read_text().strip(),
        "experiment_protocol_sha256": (experiment / "protocol.sha256").read_text().strip(),
        "decisions": decisions,
        "reproduction": reproduction,
        "baseline_unchanged": True,
        "activation_authority": False,
        "source_publication_sha256": digest(publication),
    }
    with (campaign / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads((campaign / "state.json").read_text())
        if state.get("active_run"):
            raise ValueError("Research measurement still active")
        archive = campaign / "publications"
        archive.mkdir(exist_ok=True)
        old_path = campaign / "dashboard-results.json"
        if old_path.exists():
            old = sealed(old_path)
            old_archive = archive / f"{digest(old)}.json"
            if not old_archive.exists():
                write(old_archive, old)
                old_archive.with_suffix(".sha256").write_text(digest(old))
        current_archive = archive / f"{digest(publication)}.json"
        if not current_archive.exists():
            write(current_archive, publication)
            current_archive.with_suffix(".sha256").write_text(digest(publication))
        write(old_path, publication)
        old_path.with_suffix(".sha256").write_text(digest(publication))
        comparison_path = experiment / "comparison.json"
        if not comparison_path.exists():
            write(comparison_path, summary)
            comparison_path.with_suffix(".sha256").write_text(digest(summary))
        elif sealed(comparison_path)["decisions"] != decisions:
            raise ValueError("Immutable experiment decision changed")
        now = dt.datetime.fromisoformat(publication["published_utc"])
        next_run = now.replace(minute=40, second=10, microsecond=0)
        if next_run <= now:
            next_run += dt.timedelta(hours=1)
        state.update(
            experiments_completed=1,
            experiments_promoted=0,
            promotion_enabled=False,
            status="awaiting_next_tick",
            updated_utc=publication["published_utc"],
            pending_foundation=[
                "native_broker_fill_contract_parity",
                "candidate_exported_plan_parity",
                "typed_numeric_gate_evaluator_integration",
            ],
            last_summary="QQQ controls and exact current-default attribution complete. First joint-loss experiment measured at all three cost levels; see keep/reject decisions. Default remains v14.3.0.",
            next_scheduled_utc=next_run.isoformat().replace("+00:00", "Z"),
        )
        state["current_experiment"].update(
            status="completed", comparison=str(experiment / "comparison.json")
        )
        already_completed = any(
            t.get("tick") == "manual-user-request-foundation-and-e01"
            for t in state["completed_ticks"]
        )
        if not already_completed:
            state["completed_ticks"].append(
                {
                    "tick": "manual-user-request-foundation-and-e01",
                    "completed_utc": publication["published_utc"],
                    "measurement_receipts": [
                        "cycles/manual-qqq-foundation-1732/receipt.json",
                        "cycles/manual-e01-joint-downside/receipt.json",
                    ],
                    "work": "Nine foundation and nine experiment measurements; one actual experiment; no strategy promotion",
                }
            )
        write(campaign / "state.json", state)
        with (campaign / "events.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "event": "comparison_republished"
                        if already_completed
                        else "experiment_completed",
                        "utc": publication["published_utc"],
                        "experiment": "e01-joint-downside",
                        "decisions": {a: d["status"] for a, d in decisions.items()},
                        "baseline_unchanged": True,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    print(json.dumps(decisions, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--foundation", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--total-return", type=Path)
    args = parser.parse_args()
    publish(args.campaign, args.foundation, args.experiment, args.total_return)
