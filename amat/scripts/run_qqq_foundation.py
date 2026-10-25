"""Frozen current-default and executable QQQ reference measurements; no broker I/O.

QQQ is a reference portfolio, not an eligible AMAT profile. Its 100% target is
deliberately outside AMAT's diversification caps. Both references retain native
order construction, whole-share sizing, cash rejection, actions and accounting.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from amat.backtest.accounting import SessionAccountant
from amat.backtest.panel import MarketPanel
from amat.backtest.runner import BacktestResult, SessionRecord, run_backtest
from amat.config.loading import load_strategy_config
from amat.config.models import StrategyConfig
from amat.data.snapshot import load_corporate_actions, load_manifest, load_panel
from amat.domain.actions import ActionKind, CorporateAction
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.types import Side
from amat.execution import SimulatedExecutionModel, build_instructions
from autoresearch.improvements.campaign_audit import sealed
from autoresearch.improvements.nested_inventory import file_hash
from autoresearch.improvements.source_attestation import attest_loaded_sources
from autoresearch.improvements.study import digest, source_hashes, write
from autoresearch.improvements.validation_study import (
    monthly_metrics,
    recovery_episodes,
    sliced_metrics,
)

ARMS = ("baseline", "qqq_native", "qqq_investor_uncapped")
START = dt.date(2017, 1, 3)
END = dt.date(2026, 10, 12)
INITIAL = Decimal("100000")
WARMUP_YEAR = 2016
Actions = Mapping[dt.date, Sequence[CorporateAction]]


def register(root: Path, snapshot: Path, config: Path, study: Path) -> dict[str, Any]:
    """Seal fixed economic conventions before observing benchmark outcomes."""
    if study.exists():
        raise ValueError("Use a new immutable study directory")
    resolved = load_strategy_config(config)
    manifest = load_manifest(snapshot)
    if manifest.snapshot_id != "alpaca-sip-core-2026-10-13-verified":
        raise ValueError("The foundation contract requires the verified core snapshot")
    panel, _ = load_panel(snapshot, list(map(str, resolved.strategy.universe.symbols)), end=END)
    protocol = {
        "schema": "qqq-native-foundation/1",
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "root": str(root.resolve()),
        "snapshot": str(snapshot.resolve()),
        "config_path": str(config.resolve()),
        "baseline_config_sha256": resolved.content_sha256,
        "config": resolved.strategy.model_dump(mode="json"),
        "configs": {"baseline": resolved.strategy.model_dump(mode="json")},
        "benchmark_configs": {
            name: resolved.strategy.model_dump(mode="json") for name in ARMS if name != "baseline"
        },
        "driver_sha256": file_hash(Path(__file__)),
        "source_sha256": source_hashes(root),
        "partition_sha256": manifest.partition_sha256,
        "costs_bps": [2, 6, 10],
        "execution_start": START.isoformat(),
        "execution_end": END.isoformat(),
        "initial_cash": str(INITIAL),
        "whole_share_buys": True,
        "native_entry_participation": 0.01,
        "participation_cap": 0.01,
        "study_id": study.name,
        "session_calendar_sha256": digest([str(s) for s in panel.sessions if START <= s <= END]),
        "cash_yield": 0,
        "arms": {
            "baseline": "Unmodified current default native backtest, flat initial account",
            "qqq_native": "100% QQQ target, native 1% observed opening-volume buy cap",
            "qqq_investor_uncapped": (
                "100% QQQ target, no entry-volume cap; investor reference only"
            ),
        },
        "qqq_conventions": {
            "signals": "Constant 100% QQQ, no technical signal or tactical exits",
            "decisions": (
                "Prior completed close; original native rebalance band and minimum notional"
            ),
            "execution": (
                "Next-session observed raw 09:30-09:35 VWAP, whole-share buys; unfunded buys miss"
            ),
            "distributions": (
                "Cash entitlement on ex-date, once only; cash may be reinvested at later "
                "native rebalance"
            ),
            "splits": "Native share and pending-intent rescaling before fills",
            "ending_value": "Marked holdings plus cash; no artificial final liquidation",
            "not_a_drp": "No fractional same-day dividend reinvestment or fabricated payment dates",
            "caps": (
                "100% QQQ reference intentionally does not share AMAT diversification/risk caps"
            ),
        },
        "forward_contract": (
            "Continuous account annual 2020-2026 slices; not independent annual resets "
            "or untouched holdout"
        ),
        "promotion_authority": False,
        "limitations": [
            "Ex-date dividend cash is native convention, not broker payable-date buying power",
            "Native VWAP simulation is not proof of staged limit-order broker fill parity",
            "History has prior research exposure",
        ],
    }
    study.mkdir(parents=True)
    write(study / "protocol.json", protocol)
    (study / "protocol.sha256").write_text(digest(protocol))
    return protocol


def qqq_reference(
    strategy: StrategyConfig,
    panel: MarketPanel,
    actions: Actions,
    *,
    cost: int,
    participation: float | None,
) -> BacktestResult:
    """Same native mechanics with an explicitly declared passive target policy."""
    if strategy.execution.signal_to_execution_lag_sessions != 1:
        raise ValueError("QQQ reference v1 requires exactly one session of execution lag")
    first = panel.sessions.index(START)
    if first < 1:
        raise ValueError("Prior completed session is required")
    accountant = SessionAccountant()
    ledger = LedgerSnapshot(panel.sessions[first - 1], INITIAL, {})
    execution = SimulatedExecutionModel(
        strategy.execution,
        strategy.costs,
        one_way_bps=cost,
        max_entry_window_participation=participation,
        whole_share_buys=True,
    )
    records = []
    warnings = []
    qqq = next(s for s in panel.symbols if str(s) == "QQQ")
    for index in range(first, len(panel)):
        session = panel.sessions[index]
        # The decision uses only the previous session's close and settled cash.
        instructions, below = build_instructions(
            {s: 1.0 if s == qqq else 0.0 for s in panel.symbols},
            ledger.positions,
            panel.marks_at(index - 1),
            ledger.equity,
            signal_session=panel.sessions[index - 1],
            execution_session=session,
            rebalance=strategy.rebalance,
            execution=strategy.execution,
        )
        # Match the runner's pending-share split convention without importing a
        # private helper: only today's declared split may restate prior intent.
        for action in actions.get(session, ()):
            if action.kind == ActionKind.SPLIT:
                instructions = [
                    replace(o, quantity=o.quantity * action.ratio)
                    if o.symbol == action.symbol
                    else o
                    for o in instructions
                ]
                below = [
                    replace(o, requested_quantity=o.requested_quantity * action.ratio)
                    if o.symbol == action.symbol
                    else o
                    for o in below
                ]
        # Match native settlement: newly credited ex-date cash cannot fund this
        # opening's orders. It enters the accountant after execution.
        fills, missed = execution.execute(
            instructions, panel.windows_at(index), available_cash=ledger.cash
        )
        ledger, attribution = accountant.advance(
            ledger,
            session,
            fills,
            panel.marks_at(index),
            actions=actions.get(session, ()),
            cash_rate_annualized=0,
        )
        if ledger.cash < 0:
            raise ValueError("QQQ reference overdrawn")
        records.append(
            SessionRecord(
                session=session,
                equity=ledger.equity,
                cash=ledger.cash,
                gross_exposure=ledger.gross_exposure,
                forecast_volatility_pre=0,
                forecast_volatility_post=0,
                fills=tuple(fills),
                missed=(*below, *missed),
                attribution=attribution,
                binding_constraints=(),
                instructions=tuple(instructions),
            )
        )
    return BacktestResult(tuple(records), INITIAL, cost, False, ("QQQ",), tuple(warnings))


def explain(
    result: BacktestResult,
    panel: MarketPanel,
    actions: Actions,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reconstruct exact account state and additive per-asset P&L from receipts."""
    indexes = {d: i for i, d in enumerate(panel.sessions)}
    first_index = indexes[result.sessions[0].session]
    previous_date = panel.sessions[max(0, first_index - 1)]
    ledger = LedgerSnapshot(previous_date, INITIAL, {})
    accountant = SessionAccountant()
    rows = []
    maximum_residual = Decimal(0)
    for record in result.sessions:
        index = indexes[record.session]
        old = ledger
        ledger, attributed = accountant.advance(
            old,
            record.session,
            record.fills,
            panel.marks_at(index),
            actions=actions.get(record.session, ()),
            cash_rate_annualized=0,
        )
        if ledger.equity != record.equity or ledger.cash != record.cash:
            raise ValueError(f"Native ledger reconstruction differs at {record.session}")
        if attributed != record.attribution:
            raise ValueError(f"Native attribution reconstruction differs at {record.session}")
        by_asset = {}
        for symbol in panel.symbols:
            position = old.positions.get(symbol)
            quantity = position.quantity if position else Decimal(0)
            old_mark = position.mark if position else Decimal(0)
            dividends = Decimal(0)
            for action in actions.get(record.session, ()):
                if action.symbol == symbol:
                    if action.kind == ActionKind.CASH_DIVIDEND:
                        dividends += action.cash_credit(quantity)
                    else:
                        quantity = action.adjust_quantity(quantity)
                        old_mark = action.adjust_mark(old_mark)
            signed_notional = sum(
                (
                    f.gross_notional if f.side == Side.BUY else -f.gross_notional
                    for f in record.fills
                    if f.symbol == symbol
                ),
                Decimal(0),
            )
            costs = sum((f.cost_cash for f in record.fills if f.symbol == symbol), Decimal(0))
            close_position = ledger.positions.get(symbol)
            close_value = close_position.market_value if close_position else Decimal(0)
            market = close_value - quantity * old_mark - signed_notional
            if market or dividends or costs or close_position:
                by_asset[str(symbol)] = {
                    "market_pnl": str(market),
                    "distributions": str(dividends),
                    "fees": str(costs),
                    "net_pnl": str(market + dividends - costs),
                    "quantity": str(close_position.quantity if close_position else 0),
                    "market_value": str(close_value),
                }
        residual = (
            ledger.equity
            - old.equity
            - sum((Decimal(v["net_pnl"]) for v in by_asset.values()), Decimal(0))
        )
        maximum_residual = max(maximum_residual, abs(residual))
        if abs(residual) > Decimal("0.000001"):
            raise ValueError("Per-asset P&L does not explain NAV")
        if record.session < START:
            continue
        notional = sum((f.gross_notional for f in record.fills), Decimal(0))
        rows.append(
            {
                "session": str(record.session),
                "equity": float(ledger.equity),
                "equity_decimal": str(ledger.equity),
                "cash": str(ledger.cash),
                "fees": float(attributed.fees),
                "exposure": record.gross_exposure,
                "fills": len(record.fills),
                "missed": len(record.missed),
                "missed_reasons": dict(Counter(str(m.reason) for m in record.missed)),
                "traded_notional": float(notional),
                "turnover": float(notional / old.equity),
                "attribution": {k: str(v) for k, v in asdict(attributed).items()},
                "assets": by_asset,
            }
        )
    return rows, {"ledger_reproduced": True, "maximum_asset_pnl_residual": str(maximum_residual)}


def measure(study: Path, arm: str, cost: int, destination: Path, run_id: str) -> dict[str, Any]:
    protocol = sealed(study / "protocol.json")
    if arm not in protocol["arms"] or cost not in protocol["costs_bps"]:
        raise ValueError("Unregistered arm or cost")
    if destination.exists() or destination.with_suffix(".sha256").exists():
        raise ValueError("Immutable result already exists")
    if file_hash(Path(__file__)) != protocol["driver_sha256"]:
        raise ValueError("Driver changed after registration")
    root = Path(protocol["root"])
    attest_loaded_sources(root / "src", protocol["source_sha256"], dict(sys.modules))
    snapshot = Path(protocol["snapshot"])
    manifest = load_manifest(snapshot)
    if manifest.partition_sha256 != protocol["partition_sha256"]:
        raise ValueError("Snapshot differs from registration")
    strategy = StrategyConfig.model_validate(protocol["config"])
    if strategy.adaptive_rotation.switch_condition == "volatility":
        strategy = strategy.model_copy(
            update={
                "adaptive_rotation": strategy.adaptive_rotation.model_copy(
                    update={"switch_cost_bps": cost}
                )
            }
        )
    if strategy.cash.constant_annualized_rate != 0:
        raise ValueError("Foundation requires zero cash yield")
    panel, _ = load_panel(
        snapshot, list(map(str, strategy.universe.symbols)), end=END, with_context=True
    )
    if panel.sessions[-1] != END or panel.sessions[0].year != WARMUP_YEAR:
        raise ValueError("Full 2016 warm-up and fixed terminal session required")
    actions = load_corporate_actions(snapshot, panel.symbols, sessions=panel.sessions)
    if arm == "baseline":
        result = run_backtest(
            strategy,
            panel,
            strategy.to_universe(),
            actions_by_session=actions,
            execution_start=START,
            one_way_bps=cost,
            max_entry_window_participation=0.01,
            whole_share_buys=True,
        )
    else:
        result = qqq_reference(
            strategy,
            panel,
            actions,
            cost=cost,
            participation=0.01 if arm == "qqq_native" else None,
        )
    rows, accounting = explain(result, panel, actions)
    if digest([r["session"] for r in rows]) != protocol["session_calendar_sha256"]:
        raise ValueError("Result calendar differs from registered execution sessions")
    fractional = sum(
        f.side == Side.BUY and f.quantity != f.quantity.to_integral_value()
        for record in result.sessions
        for f in record.fills
    )
    if fractional:
        raise ValueError("Fractional purchase violates foundation contract")
    record = {
        "schema": "qqq-native-foundation-result/1",
        "arm": arm,
        "cost_bps": cost,
        "finalized": True,
        "run_id": run_id,
        "study_id": protocol["study_id"],
        "profile_sha256": protocol["baseline_config_sha256"],
        "effective_config_sha256": digest(strategy.model_dump(mode="json")),
        "sessions_sha256": digest(rows),
        "protocol_sha256": digest(protocol),
        "sessions": rows,
        "metrics": sliced_metrics(rows),
        "monthly": monthly_metrics(rows),
        "recovery_episodes": recovery_episodes(rows),
        "accounting": accounting,
        "fractional_buy_count": fractional,
        "warnings": list(result.warnings),
        "limitations": protocol["limitations"],
        "promotion_authority": False,
    }
    attest_loaded_sources(root / "src", protocol["source_sha256"], dict(sys.modules))
    destination.parent.mkdir(parents=True, exist_ok=True)
    write(destination, record)
    destination.with_suffix(".sha256").write_text(digest(record))
    print(f"{arm} {cost} bps: {record['metrics']['full']['net_return']:.4%}", flush=True)
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    for name in ("root", "snapshot", "config", "study"):
        reg.add_argument(f"--{name}", type=Path, required=True)
    run = sub.add_parser("measure")
    run.add_argument("--study", type=Path, required=True)
    run.add_argument("--arm", choices=ARMS, required=True)
    run.add_argument("--cost", type=int, choices=(2, 6, 10), required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.command == "register":
        register(args.root, args.snapshot, args.config, args.study)
    else:
        measure(args.study, args.arm, args.cost, args.output, args.run_id)


if __name__ == "__main__":
    main()
