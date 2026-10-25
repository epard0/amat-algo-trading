"""Native paired account-path mechanics diagnostic, not a learned strategy trial."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from run_qqq_foundation import END, START, explain

from amat.backtest.runner import run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from amat.domain.types import Side
from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.nested_inventory import file_hash
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, source_hashes, write

YEARS = (2019, 2022, 2025)
ZERO_DECISIONS = 5
TERMINAL_OFFSET = 6
SPEC = {  # fixed diagnostic semantics, not a search grid
    "cutoffs": "last observed completed close in each of 2019, 2022, 2025",
    "selection": (
        "lexicographically first strictly positive held symbol at cutoff; no outcome selection"
    ),
    "intervention": "selected asset target zero for cutoff..cutoff+4 decision closes only",
    "restoration": "unchanged baseline allocator resumes at cutoff+5; no guaranteed re-entry",
    "terminal": "cutoff+6 close after lag1 restoration opportunity, marked holdings plus cash",
    "control": "continue baseline policy, including its natural exits; not literal hold",
    "estimand": (
        "coupled whole-account terminal NAV difference / shared cutoff NAV; "
        "not isolated asset alpha"
    ),
    "fit": "none; three deterministic mechanics examples, not a training sample or validation set",
    "authority": "foundation diagnostic only; no strategy promotion or broker actions",
}


def ledger_state(ledger: Any) -> dict[str, Any]:
    return {
        "session": str(ledger.session_date),
        "cash": str(ledger.cash),
        "equity": str(ledger.equity),
        "positions": {
            str(s): {"quantity": str(p.quantity), "mark": str(p.mark)}
            for s, p in sorted(ledger.positions.items(), key=lambda item: str(item[0]))
            if p.quantity > 0
        },
    }


def embargo_policy(sessions: tuple, cutoff: dt.date) -> tuple[Any, list[dict[str, Any]]]:
    """Use only current settled holdings; later prices cannot choose the asset."""
    index = {date: i for i, date in enumerate(sessions)}
    first = index[cutoff]
    if first + TERMINAL_OFFSET >= len(sessions):
        raise ValueError("Complete terminal settlement session required")
    observations: list[dict[str, Any]] = []
    chosen = None

    def policy(targets: dict, ledger: Any, session: dt.date, _covariance: Any) -> dict:
        nonlocal chosen
        t = index[session]
        result = dict(targets)
        if t == first:
            held = sorted((s for s, p in ledger.positions.items() if p.quantity > 0), key=str)
            if not held:
                raise ValueError("Fixed cutoff has no actual positive holding; no replacement date")
            chosen = held[0]
        if first <= t < first + ZERO_DECISIONS:
            if chosen is None or chosen not in result:
                raise ValueError("Missing fixed held asset or policy target")
            result[chosen] = 0.0
        if first <= t <= first + TERMINAL_OFFSET:
            observations.append(
                {
                    "session": str(session),
                    "selected_symbol": str(chosen),
                    "phase": "zero_target" if t < first + ZERO_DECISIONS else "baseline_restored",
                    "baseline_targets": {str(s): v for s, v in targets.items()},
                    "result_targets": {str(s): v for s, v in result.items()},
                    "settled_state": ledger_state(ledger),
                }
            )
        return result

    return policy, observations


def prefix_panel(panel: Any, last: int) -> Any:
    """Retain all signal history and observed fields; no account-state restart."""
    if not 0 <= last < len(panel):
        raise ValueError("Observed prefix endpoint required")
    arrays = (
        "adjusted_closes",
        "raw_closes",
        "window_vwaps",
        "window_volumes",
        "adjusted_highs",
        "adjusted_lows",
        "session_volumes",
        "adjusted_opens",
    )
    return replace(
        panel,
        sessions=panel.sessions[: last + 1],
        **{
            name: getattr(panel, name)[: last + 1] if getattr(panel, name) is not None else None
            for name in arrays
        },
    )


def measure(  # noqa: PLR0912, PLR0915 - sequential evidence contracts
    protocol_path: Path, cost: int, output: Path, run_id: str
) -> None:
    p = sealed(protocol_path)
    if (
        cost not in (2, 6, 10)
        or not run_id
        or output.exists()
        or output.with_suffix(".sha256").exists()
    ):
        raise ValueError("Unregistered cost/run or immutable output exists")
    if p["spec"] != SPEC or p["driver_sha256"] != file_hash(Path(__file__)):
        raise ValueError("Frozen diagnostic implementation changed")
    root = Path(p["root"])
    review = sealed(Path(p["review_path"]))
    if (
        review.get("implementation_approved") is not True
        or review["driver_sha256"] != p["driver_sha256"]
    ):
        raise ValueError("Exact-source independent approval required")
    if digest(review) != p["review_sha256"] or review.get("outcomes_seen") is not False:
        raise ValueError("Pre-outcome review identity differs")
    if source_hashes(root) != p["source_sha256"]:
        raise ValueError("Native sources changed")
    attest_loaded_sources(root / "src", p["source_sha256"], dict(sys.modules))
    if dt.datetime.now(dt.UTC) >= dt.datetime.fromisoformat(p["hard_stop_utc"]):
        raise ValueError("Diagnostic tick hard stop reached")
    strategy = StrategyConfig.model_validate(p["config"])
    resolved = load_strategy_config(Path(p["config_path"]))
    if (
        resolved.content_sha256 != p["baseline_config_sha256"]
        or resolved.strategy.model_dump(mode="json") != p["config"]
    ):
        raise ValueError("Actual baseline profile/configuration changed")
    if strategy.execution.signal_to_execution_lag_sessions != 1:
        raise ValueError("Settled lag1 contract required")
    if strategy.cash.constant_annualized_rate != 0:
        raise ValueError("Zero cash yield required")
    strategy = strategy.model_copy(
        update={
            "adaptive_rotation": strategy.adaptive_rotation.model_copy(
                update={"switch_cost_bps": cost}
            )
        }
    )
    snapshot = Path(p["snapshot"])
    if load_manifest(snapshot).partition_sha256 != p["partition_sha256"]:
        raise ValueError("Frozen data changed")
    panel, _ = load_panel(
        snapshot, list(map(str, strategy.universe.symbols)), end=END, with_context=True
    )
    if panel.sessions[0].year != 2016 or panel.sessions[-1] != END:  # noqa: PLR2004
        raise ValueError("Full2016 signal history and fixed endpoint required")
    actions = load_corporate_actions(snapshot, panel.symbols, sessions=panel.sessions)
    kwargs = dict(  # noqa: C408 - explicit native keyword contract
        execution_start=START,
        one_way_bps=cost,
        actions_by_session=actions,
        max_entry_window_participation=0.01,
        whole_share_buys=True,
    )
    control = run_backtest(strategy, panel, strategy.to_universe(), **kwargs)
    control_rows, control_accounting = explain(control, panel, actions)
    prior = sealed(Path(p["archived_controls"][str(cost)]["path"]))
    if (
        file_hash(Path(p["archived_controls"][str(cost)]["path"]))
        != p["archived_controls"][str(cost)]["sha256"]
    ):
        raise ValueError("Archived control identity changed")
    if digest(control_rows) != prior["sessions_sha256"]:
        raise ValueError("All2457 archived control rows must reproduce exactly")
    pairs = []
    for year in YEARS:
        if dt.datetime.now(dt.UTC) >= dt.datetime.fromisoformat(p["hard_stop_utc"]):
            raise ValueError("No new native replay after tick hard stop")
        cutoff = max(s for s in panel.sessions if s.year == year)
        first = panel.sessions.index(cutoff)
        terminal = panel.sessions[first + TERMINAL_OFFSET]
        small = prefix_panel(panel, first + TERMINAL_OFFSET)
        policy, observations = embargo_policy(small.sessions, cutoff)
        branch = run_backtest(
            strategy, small, strategy.to_universe(), decision_policy=policy, **kwargs
        )
        rows, accounting = explain(branch, small, actions)
        before = [r for r in control_rows if r["session"] <= str(cutoff)]
        prefix = [r for r in rows if r["session"] <= str(cutoff)]
        if digest(before) != digest(prefix) or not observations:
            raise ValueError("Candidate and control actual pre-intervention states differ")
        anchor = before[-1]
        observed = observations[0]["settled_state"]
        expected_positions = {
            s: v["quantity"] for s, v in anchor["assets"].items() if float(v["quantity"]) > 0
        }
        if (
            observed["cash"] != anchor["cash"]
            or observed["equity"] != anchor["equity_decimal"]
            or {s: v["quantity"] for s, v in observed["positions"].items()} != expected_positions
        ):
            raise ValueError("Actual callback ledger does not equal reconstructed control")
        base_end = next(r for r in control_rows if r["session"] == str(terminal))
        end = rows[-1]

        delta = Decimal(end["equity_decimal"]) - Decimal(base_end["equity_decimal"])
        window = [r for r in branch.sessions if cutoff < r.session <= terminal]
        control_window = [r for r in control.sessions if cutoff < r.session <= terminal]
        if any(
            f.side == Side.BUY and f.quantity != f.quantity.to_integral_value()
            for r in window
            for f in r.fills
        ):
            raise ValueError("Whole-share buy contract violated")
        pairs.append(
            {
                "cutoff": str(cutoff),
                "terminal": str(terminal),
                "selected_symbol": observations[0]["selected_symbol"],
                "shared_prefix_sessions_sha256": digest(prefix),
                "shared_prefix_rows": len(prefix),
                "actual_cutoff_state": observed,
                "control_terminal": base_end,
                "intervention_terminal": end,
                "terminal_nav_delta": str(delta),
                "terminal_delta_bps_of_shared_cutoff_nav": str(
                    delta / Decimal(anchor["equity_decimal"]) * 10000
                ),
                "intervention_window_fills": [asdict(f) for r in window for f in r.fills],
                "intervention_window_missed": [asdict(m) for r in window for m in r.missed],
                "intervention_window_instructions": [
                    asdict(o) for r in window for o in r.instructions
                ],
                "control_window_fills": [asdict(f) for r in control_window for f in r.fills],
                "control_window_missed": [asdict(m) for r in control_window for m in r.missed],
                "control_window_instructions": [
                    asdict(o) for r in control_window for o in r.instructions
                ],
                "intervention_window_rows": [
                    r for r in rows if str(cutoff) <= r["session"] <= str(terminal)
                ],
                "control_window_rows": [
                    r for r in control_rows if str(cutoff) <= r["session"] <= str(terminal)
                ],
                "policy_observations": observations,
                "accounting": accounting,
                "reentry_guaranteed": False,
                "isolated_asset_effect": False,
            }
        )
    if source_hashes(root) != p["source_sha256"]:
        raise ValueError("Native source changed during diagnostic")
    attest_loaded_sources(root / "src", p["source_sha256"], dict(sys.modules))
    record = {
        "schema": "native-exit-pair-foundation-result/1",
        "protocol_sha256": digest(p),
        "run_id": run_id,
        "cost_bps": cost,
        "profile_sha256": p["baseline_config_sha256"],
        "control_sessions_sha256": digest(control_rows),
        "control_rows": len(control_rows),
        "control_accounting": control_accounting,
        "pairs": pairs,
        "learned_model": False,
        "new_strategy_experiment": False,
        "promotion_authority": False,
    }
    # Native dataclasses contain dates, Decimal and enums; preserve their exact text representation.

    record = json.loads(json.dumps(record, default=str, allow_nan=False))
    output.parent.mkdir(parents=True, exist_ok=True)
    write(output, record)
    output.with_suffix(".sha256").write_text(digest(record))
    print(
        f"Native paired mechanics: {cost}bps, {len(pairs)} fixed cutoffs; no strategy claim",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--cost", type=int, choices=(2, 6, 10), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    measure(args.protocol, args.cost, args.output, args.run_id)
