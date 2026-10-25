"""Single-window research adapter using native fills/accounting, never broker authority.

Opening quotes alone trigger protective proposals. Completed window ranges only
bound limit eligibility retrospectively. Neither is a strategy signal. Source
coverage packets are declarations to audit, not proof of vendor completeness.
"""

from __future__ import annotations

# Runtime evidence guards also validate objects arriving from untyped JSON callers.
# mypy: disable-error-code="redundant-expr"
import datetime as dt
import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from amat.backtest.accounting import SessionAccountant
from amat.config.models import CostConfig, ExecutionConfig
from amat.domain.bars import ExecutionWindowBar
from amat.domain.ledgers import LedgerSnapshot
from amat.domain.orders import SimulatedOrder, TradeReason
from amat.domain.positions import Position
from amat.domain.types import Side, parse_symbol
from amat.execution import SimulatedExecutionModel
from autoresearch.improvements.agent_portfolio_arm import ResearchContext, validate_action_contract

ZONE = ZoneInfo("America/New_York")
ZERO = Decimal(0)
POLICY = "research-native-opening-attempt/1"
LIMITATIONS = (
    "Research only; no broker, native executable export, default change or promotion authority.",
    (
        "Single opening-window attempt; no automatic retry, intrawindow trigger "
        "or tick-path inference."
    ),
    "Observed ranges bound limit eligibility retrospectively; only opening quotes trigger stops.",
    "Coverage is an explicit source declaration, not proof of vendor completeness.",
    (
        "No corporate-action unit changes, direct shorts, inverse instruments "
        "or intraday policy support."
    ),
    "Window-end VWAP valuation is not daily-close or full-strategy performance evidence.",
    "Durable receipt ownership is the caller's responsibility; this pure module submits nothing.",
)


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
        ).encode()
    ).hexdigest()


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, allow_nan=False))


@dataclass(frozen=True)
class AdapterPlan:
    proposal_json: str
    context: ResearchContext
    plan_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": POLICY,
            "plan_id": self.plan_id,
            "proposal": json.loads(self.proposal_json),
            "context": _json(asdict(self.context)),
            "context_sha256": self.context.evidence_sha256,
            "execution_authority": "none",
            "promotion_authority": False,
            "limitations": list(LIMITATIONS),
        }


@dataclass(frozen=True)
class ObservedWindowEvidence:
    bar: ExecutionWindowBar
    observed_at: dt.datetime
    low: Decimal | None
    high: Decimal | None
    source_id: str
    opening_quote: Decimal | None = None
    opening_quote_observed_at: dt.datetime | None = None
    range_complete: bool = False
    price_basis: str = "raw"


def compile_adapter(proposal: dict[str, Any], context: ResearchContext) -> AdapterPlan:
    validate_action_contract(proposal, context)
    context = replace(
        context,
        positions=tuple((symbol, quantity) for symbol, quantity in context.positions),
        evidence=tuple(context.evidence),
        authorized_intents=tuple(context.authorized_intents),
    )
    if context.execution_clock != "next_session_open" or any(
        (
            context.policy.allow_direct_short,
            context.policy.allow_inverse,
            context.policy.allow_intraday,
        )
    ):
        raise ValueError(
            "Research adapter permits only disabled-advanced-capability next-session policy"
        )
    if any(q < 0 for _, q in context.positions) or any(
        row.instrument != "ordinary_etf" for row in context.evidence
    ):
        raise ValueError("Only long ordinary ETF contexts supported")
    if context.execution_at.astimezone(ZONE).time().replace(tzinfo=None) != dt.time(9, 30):
        raise ValueError("Only native 09:30 opening attempt supported")
    if context.execution_at.astimezone(ZONE).date() <= context.decision_at.astimezone(ZONE).date():
        raise ValueError("Native attempt must follow the completed decision session")
    payload = json.dumps(proposal, sort_keys=True, separators=(",", ":"), allow_nan=False)
    identity = canonical_sha(
        {
            "policy": POLICY,
            "proposal": json.loads(payload),
            "context_sha256": context.evidence_sha256,
        }
    )
    return AdapterPlan(payload, context, identity)


def _ledger(context: ResearchContext, supplied: LedgerSnapshot | None) -> LedgerSnapshot:
    date = context.decision_at.astimezone(ZONE).date()
    marks = {row.symbol: row.mark for row in context.evidence}
    expected = LedgerSnapshot(
        date,
        context.confirmed_cash,
        {
            parse_symbol(symbol): Position(parse_symbol(symbol), quantity, marks[symbol], date)
            for symbol, quantity in context.positions
            if quantity
        },
    )
    if supplied is not None and (supplied != expected or supplied.equity != context.equity):
        raise ValueError("Previous ledger differs from exact context positions/cash/marks/session")
    if expected.equity != context.equity:
        raise ValueError("Context NAV differs from exact marked ledger")
    return expected if supplied is None else supplied


def _coverage(value: dict[str, Any] | None, context: ResearchContext) -> dict[str, Any]:
    fields = {"from_session", "to_session", "source_id", "actions", "actions_sha256"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Explicit bounded corporate-action source declaration required")
    start = dt.date.fromisoformat(value["from_session"])
    end = dt.date.fromisoformat(value["to_session"])
    if (
        start > context.decision_at.astimezone(ZONE).date()
        or end < context.execution_at.astimezone(ZONE).date()
        or not isinstance(value["source_id"], str)
        or not value["source_id"]
        or not isinstance(value["actions"], list)
        or value["actions_sha256"] != canonical_sha(value["actions"])
    ):
        raise ValueError("Corporate-action coverage bounds/source/hash differ")
    if value["actions"]:
        raise ValueError("Corporate actions require a separately validated quantity-unit policy")
    return dict(_json(value))


def _evidence_reason(  # noqa: PLR0911 -- explicit evidence failures
    symbol: str, row: ObservedWindowEvidence, context: ResearchContext
) -> str | None:
    bar = row.bar
    if not bar.is_tradable:
        return "zero_volume_unproved_window_valuation"
    if (
        bar.symbol != symbol
        or bar.session_date != context.execution_at.astimezone(ZONE).date()
        or bar.window_start != context.execution_at
    ):
        return "window_identity"
    if (
        bar.window_start.astimezone(ZONE).time().replace(tzinfo=None) != dt.time(9, 30)
        or bar.window_end.astimezone(ZONE).time().replace(tzinfo=None) != dt.time(9, 35)
        or bar.window_end.date() != bar.window_start.date()
        or bar.window_end > context.expires_at
    ):
        return "window_clock_or_expiry"
    if (
        not isinstance(row.observed_at, dt.datetime)
        or row.observed_at.tzinfo is None
        or row.observed_at.utcoffset() != dt.timedelta(0)
        or row.observed_at < bar.window_end
    ):
        return "incomplete_window_observation"
    if (
        not isinstance(row.source_id, str)
        or not row.source_id
        or row.source_id != bar.source
        or not bar.source
        or row.price_basis != "raw"
        or row.range_complete is not True
    ):
        return "missing_raw_range_declaration"
    if (
        not isinstance(row.low, Decimal)
        or not isinstance(row.high, Decimal)
        or not row.low.is_finite()
        or not row.high.is_finite()
        or not ZERO < row.low <= bar.vwap <= row.high
    ):
        return "invalid_range_or_vwap"
    return None


def _action_reason(action: dict[str, Any], row: ObservedWindowEvidence) -> str | None:
    if row.low is None or row.high is None:
        return "missing_range"
    if action["kind"] in {"stop_loss", "take_profit"}:
        quote = row.opening_quote
        if (
            not isinstance(quote, Decimal)
            or not quote.is_finite()
            or quote <= 0
            or row.opening_quote_observed_at != row.bar.window_start
        ):
            return "opening_trigger_unproved"
        if not row.low <= quote <= row.high:
            return "opening_quote_range_conflict"
        trigger = Decimal(action["trigger_price"])
        triggered = quote <= trigger if action["kind"] == "stop_loss" else quote >= trigger
        if not triggered:
            return "opening_trigger_not_met"
    limit = Decimal(action["limit_price"])
    if (action["side"] == "buy" and row.high > limit) or (
        action["side"] == "sell" and row.low < limit
    ):
        return "limit_not_proven_over_complete_window"
    return None


def _ledger_json(ledger: LedgerSnapshot) -> dict[str, Any]:
    return {
        "session_date": str(ledger.session_date),
        "cash": str(ledger.cash),
        "equity": str(ledger.equity),
        "positions": {
            str(symbol): {"quantity": str(pos.quantity), "mark": str(pos.mark)}
            for symbol, pos in ledger.positions.items()
        },
    }


def execute_adapter(  # noqa: PLR0912, PLR0915 -- ordered one-attempt scientific boundaries
    plan: AdapterPlan,
    evidence: Mapping[str, ObservedWindowEvidence],
    priorledger: LedgerSnapshot | None = None,
    previous_receipt: dict[str, Any] | None = None,
    *,
    action_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return native simulated evidence once; caller must durably seal before any reuse."""
    if previous_receipt is not None:
        raise ValueError("A prior receipt consumes this single attempt; no automatic retry")
    proposal = json.loads(plan.proposal_json)
    rebuilt = compile_adapter(proposal, plan.context)
    if rebuilt != plan:
        raise ValueError("Adapter plan identity differs from original context/proposal")
    context = plan.context
    known_symbols = {row.symbol for row in context.evidence}
    if not isinstance(evidence, Mapping) or any(
        symbol not in known_symbols
        or not isinstance(row, ObservedWindowEvidence)
        or not isinstance(row.bar, ExecutionWindowBar)
        for symbol, row in evidence.items()
    ):
        raise ValueError("Only typed evidence for declared context symbols is supported")
    coverage = _coverage(action_coverage, context)
    previous = _ledger(context, priorledger)
    declaration = {str(symbol): _json(asdict(row)) for symbol, row in evidence.items()}
    result = {
        "schema": "research-native-adapter-receipt/1",
        "plan_id": plan.plan_id,
        "attempt_id": canonical_sha(
            {"plan_id": plan.plan_id, "evidence": declaration, "coverage": coverage}
        ),
        "original_plan": plan.as_dict(),
        "original_authorized_intents": _json([asdict(i) for i in context.authorized_intents]),
        "original_pending_cash": str(
            sum((i.pending_cash for i in context.authorized_intents), ZERO)
        ),
        "corporate_action_coverage_declaration": coverage,
        "evidence": declaration,
        "fills": [],
        "missed": [],
        "blocked": [],
        "actions": [],
        "ledger": _ledger_json(previous),
        "accounting": None,
        "valuation_advanced": False,
        "execution_authority": "none",
        "promotion_authority": False,
        "performance_evidence": False,
        "single_attempt_consumed": True,
        "authorization_expires_at": proposal["expires_at"],
        "retrospective_observation": True,
        "observation_after_expiry": any(
            isinstance(row.observed_at, dt.datetime)
            and row.observed_at.tzinfo is not None
            and row.observed_at > context.expires_at
            for row in evidence.values()
        ),
        "expiry_releases_existing_commitments": False,
        "limitations": list(LIMITATIONS),
    }
    reasons = {symbol: _evidence_reason(symbol, row, context) for symbol, row in evidence.items()}
    required_marks = {symbol for symbol, quantity in context.positions if quantity} | {
        a["symbol"] for a in proposal["actions"] if a["kind"] != "hold"
    }
    missing_valuation = any(
        symbol not in evidence or reasons.get(symbol) for symbol in required_marks
    )
    orders = []
    for index, action in enumerate(proposal["actions"]):
        if action["kind"] == "hold":
            result["actions"].append(
                {
                    "action_index": index,
                    "original_quantity": "0",
                    "cumulative_fills": "0",
                    "remaining_quantity": "0",
                    "status": "hold",
                    "pending_cash_for_unfilled_action": "0",
                    "expires_at": proposal["expires_at"],
                }
            )
            continue
        row = evidence.get(action["symbol"])
        reason = (
            "missing_or_unproved_valuation_window"
            if missing_valuation
            else reasons.get(action["symbol"])
        )
        if not reason:
            reason = "missing_window" if row is None else _action_reason(action, row)
        if reason:
            result["blocked"].append({"action_index": index, "reason": reason})
        else:
            orders.append(
                SimulatedOrder(
                    action["symbol"],
                    context.decision_at.astimezone(ZONE).date(),
                    context.execution_at.astimezone(ZONE).date(),
                    Side.BUY if action["side"] == "buy" else Side.SELL,
                    Decimal(action["quantity"]),
                    (
                        TradeReason.POSITION_TRANSITION
                        if action["side"] == "sell"
                        and Decimal(action["quantity"])
                        == dict(context.positions).get(action["symbol"], ZERO)
                        else TradeReason.WEIGHT_DELTA
                    ),
                )
            )
        result["actions"].append(
            {
                "action_index": index,
                "original_quantity": action["quantity"],
                "cumulative_fills": "0",
                "remaining_quantity": action["quantity"],
                "status": "blocked" if reason else "attempted",
                "pending_cash_for_unfilled_action": "0",
                "expires_at": proposal["expires_at"],
            }
        )
    pending = sum((i.pending_cash for i in context.authorized_intents), ZERO)
    available = max(
        ZERO,
        context.confirmed_cash
        - context.restricted_cash
        - pending
        - context.equity * context.policy.cash_reserve_fraction,
    )
    model = SimulatedExecutionModel(
        ExecutionConfig(),
        CostConfig(),
        one_way_bps=float(context.policy.one_way_bps),
        max_entry_window_participation=0.01,
        whole_share_buys=True,
    )
    windows = {
        parse_symbol(symbol): row.bar for symbol, row in evidence.items() if not reasons.get(symbol)
    }
    sells, sell_missed = model.execute(
        [order for order in orders if order.side == Side.SELL],
        windows,
        available_cash=available,
        account_equity=context.equity,
    )
    buys, buy_missed = model.execute(
        [order for order in orders if order.side == Side.BUY],
        windows,
        available_cash=available,
        account_equity=context.equity,
    )
    fills = sells + buys
    result["fills"] = _json([asdict(fill) for fill in fills])
    result["missed"] = _json([asdict(miss) for miss in sell_missed + buy_missed])
    if not missing_valuation:
        ledger, accounting = SessionAccountant().advance(
            previous,
            context.execution_at.astimezone(ZONE).date(),
            fills,
            {parse_symbol(symbol): row.bar.vwap for symbol, row in evidence.items()},
            actions=(),
            cash_rate_annualized=0.0,
        )
        result.update(
            ledger=_ledger_json(ledger),
            accounting=_json(asdict(accounting)),
            valuation_advanced=True,
        )
    for row in result["actions"]:
        action = proposal["actions"][row["action_index"]]
        if action["kind"] == "hold":
            continue
        filled = sum(
            (
                fill.quantity
                for fill in fills
                if fill.symbol == action["symbol"] and str(fill.side) == action["side"]
            ),
            ZERO,
        )
        remaining = Decimal(action["quantity"]) - filled
        row.update(cumulative_fills=str(filled), remaining_quantity=str(remaining))
        if row["status"] != "blocked":
            row["status"] = "filled" if remaining == 0 else "partial_or_missed"
        if action["side"] == "buy":
            row["pending_cash_for_unfilled_action"] = str(
                remaining
                * Decimal(action["limit_price"])
                * (1 + context.policy.one_way_bps / Decimal(10000))
            )
        row["expires_at"] = proposal["expires_at"]
        row["expiry_releases_existing_commitments"] = False
    result["receipt_sha256"] = canonical_sha(result)
    return result
