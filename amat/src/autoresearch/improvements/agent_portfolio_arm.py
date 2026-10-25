"""Inert, versioned portfolio-agent proposals; never produces execution authority.

No HTTP, broker, model, shell or code-evaluation interface lives here. A replay
may consume a *validated proposal* only through its own declared fill model.
Acceptance means structurally/economically admissible, never profitable or safe
to deploy. All prices and capacities must be known at the decision cutoff.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

SCHEMA = "research-agent-portfolio-action/1"
KINDS = {"hold", "stop_loss", "take_profit", "resize", "open", "close", "hedge"}
CLOCKS = {"next_session_open", "next_bar_open", "intraday_next_quote"}
ZERO = Decimal(0)
MAX_ACTIONS = 32
MAX_BPS = 1000
MAX_MODEL_ID = 200
MAX_REASON = 2000


class ContractError(ValueError):
    """Missing, unknown, stale or inadmissible proposal/evidence."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _number(value: Any, label: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, (str, Decimal)):
        raise ContractError(f"{label} must be an exact decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ContractError(f"invalid {label}") from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ContractError(f"invalid {label}")
    return number


def _aware(value: dt.datetime) -> None:
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("timezone-aware clock required")


@dataclass(frozen=True)
class ArmPolicy:
    """Trusted, preregistered limits, separate from the model's output."""

    max_gross: Decimal = Decimal("1")
    max_abs_net: Decimal = Decimal("1")
    max_symbol: Decimal = Decimal("0.45")
    cash_reserve_fraction: Decimal = Decimal("0.01")
    one_way_bps: Decimal = Decimal("10")
    short_collateral_fraction: Decimal = Decimal("1")
    max_evidence_age_seconds: int = 300
    max_actions: int = 16
    allow_direct_short: bool = False
    allow_inverse: bool = False
    allow_intraday: bool = False

    @property
    def identity(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True)
class MarketEvidence:
    symbol: str
    evidence_id: str
    available_at: dt.datetime
    mark: Decimal
    # Conservative *past-observed* proposal cap. Actual replay liquidity is an
    # additional future fill constraint, never supplied to the proposer early.
    entry_quantity_cap: Decimal
    instrument: str = "ordinary_etf"  # ordinary_etf, inverse_etf
    borrow_available: bool | None = None
    borrow_annual_rate: Decimal | None = None
    short_stress_validated: bool = False


@dataclass(frozen=True)
class AuthorizedIntent:
    """Read-only existing authority; a proposal cannot replace or enlarge it."""

    intent_id: str
    symbol: str
    side: str
    original_quantity: Decimal
    cumulative_fills: Decimal
    remaining_quantity: Decimal
    pending_cash: Decimal
    expires_at: dt.datetime
    account_id: str
    policy_id: str


@dataclass(frozen=True)
class ResearchContext:
    decision_at: dt.datetime
    account_observed_at: dt.datetime
    execution_at: dt.datetime
    execution_clock: str
    expires_at: dt.datetime
    account_id: str
    equity: Decimal
    confirmed_cash: Decimal
    restricted_cash: Decimal
    # Signed actual quantities, immutable tuple; never simulated inception state.
    positions: tuple[tuple[str, Decimal], ...]
    evidence: tuple[MarketEvidence, ...]
    authorized_intents: tuple[AuthorizedIntent, ...]
    policy: ArmPolicy

    @property
    def evidence_sha256(self) -> str:
        return _digest(asdict(self))


def hold_contract(context: ResearchContext, *, model_id: str, reason: str) -> dict[str, Any]:
    """Explicit abstention/template; callers still validate it before retention."""
    return {
        "schema": SCHEMA,
        "research_only": True,
        "execution_authority": "none",
        "provider": "openrouter",
        "model_id": model_id,
        "decision_at": context.decision_at.isoformat(),
        "execution_at": context.execution_at.isoformat(),
        "execution_clock": context.execution_clock,
        "expires_at": context.expires_at.isoformat(),
        "account_id": context.account_id,
        "policy_id": context.policy.identity,
        "evidence_sha256": context.evidence_sha256,
        "authorized_intents_sha256": _digest([asdict(i) for i in context.authorized_intents]),
        "limits": {
            "max_gross": str(context.policy.max_gross),
            "max_abs_net": str(context.policy.max_abs_net),
            "max_symbol": str(context.policy.max_symbol),
        },
        "actions": [{"kind": "hold", "reason": reason}],
    }


def _validate_context(context: ResearchContext) -> None:  # noqa: PLR0912, PLR0915 - ordered fail-closed checks
    policy = context.policy
    for clock in (
        context.decision_at,
        context.account_observed_at,
        context.execution_at,
        context.expires_at,
    ):
        _aware(clock)
    if not context.decision_at < context.execution_at < context.expires_at:
        raise ContractError("execution must follow decision and precede expiry")
    if context.execution_clock not in CLOCKS:
        raise ContractError("unsupported execution clock")
    if context.execution_clock != "next_session_open" and not policy.allow_intraday:
        raise ContractError("intraday capability is disabled")
    if type(policy.max_evidence_age_seconds) is not int or policy.max_evidence_age_seconds <= 0:
        raise ContractError("invalid freshness policy")
    if type(policy.max_actions) is not int or not 1 <= policy.max_actions <= MAX_ACTIONS:
        raise ContractError("invalid action budget")
    for flag in (policy.allow_direct_short, policy.allow_inverse, policy.allow_intraday):
        if type(flag) is not bool:
            raise ContractError("capabilities must be boolean")
    for name in ("max_gross", "max_abs_net", "max_symbol", "short_collateral_fraction"):
        _number(getattr(policy, name), name, positive=True)
    if any(getattr(policy, k) > 1 for k in ("max_gross", "max_abs_net", "max_symbol")):
        raise ContractError("v1 contract does not permit leverage")
    if policy.short_collateral_fraction < 1:
        raise ContractError("v1 requires full collateral for proposed shorts")
    _number(policy.cash_reserve_fraction, "cash reserve")
    if not 0 <= policy.cash_reserve_fraction < 1:
        raise ContractError("invalid cash reserve")
    _number(policy.one_way_bps, "cost")
    if policy.one_way_bps > MAX_BPS:
        raise ContractError("invalid cost policy")
    if not context.account_id:
        raise ContractError("account identity required")
    _number(context.equity, "equity", positive=True)
    _number(context.confirmed_cash, "confirmed cash")
    _number(context.restricted_cash, "restricted cash")
    if context.restricted_cash > context.confirmed_cash:
        raise ContractError("restricted cash exceeds confirmed cash")
    age = (context.decision_at - context.account_observed_at).total_seconds()
    if not 0 <= age <= policy.max_evidence_age_seconds:
        raise ContractError("stale or future account evidence")
    marks: dict[str, MarketEvidence] = {}
    for item in context.evidence:
        _aware(item.available_at)
        age = (context.decision_at - item.available_at).total_seconds()
        if not 0 <= age <= policy.max_evidence_age_seconds:
            raise ContractError("stale or future market evidence")
        if not item.symbol or not item.evidence_id or item.symbol in marks:
            raise ContractError("duplicate or unidentified market evidence")
        if item.instrument not in {"ordinary_etf", "inverse_etf"}:
            raise ContractError("unsupported instrument")
        _number(item.mark, "mark", positive=True)
        _number(item.entry_quantity_cap, "entry quantity cap")
        if item.borrow_annual_rate is not None:
            _number(item.borrow_annual_rate, "borrow cost")
        if item.borrow_available is not None and type(item.borrow_available) is not bool:
            raise ContractError("borrow observation must be boolean or unknown")
        if type(item.short_stress_validated) is not bool:
            raise ContractError("short stress capability must be boolean")
        marks[item.symbol] = item
    positions: dict[str, Decimal] = {}
    for symbol, quantity in context.positions:
        if symbol in positions or symbol not in marks or not isinstance(quantity, Decimal):
            raise ContractError("duplicate position or missing position evidence")
        if not quantity.is_finite():
            raise ContractError("invalid position quantity")
        positions[symbol] = quantity
    short_liability = sum((max(-q, ZERO) * marks[s].mark for s, q in positions.items()), ZERO)
    if context.restricted_cash < short_liability:
        raise ContractError("existing short proceeds are not fully restricted")
    # The account must reconcile to these marks. Signed stock accounts carry
    # short proceeds in cash; restricted cash is not subtracted from NAV again.
    marked = context.confirmed_cash + sum((q * marks[s].mark for s, q in positions.items()), ZERO)
    if abs(marked - context.equity) > Decimal("0.01"):
        raise ContractError("account equity and marked positions do not reconcile")
    seen: set[str] = set()
    for intent in context.authorized_intents:
        _aware(intent.expires_at)
        if (
            not intent.intent_id
            or intent.intent_id in seen
            or intent.symbol not in marks
            or intent.side not in {"buy", "sell"}
            or intent.account_id != context.account_id
            or intent.policy_id != policy.identity
        ):
            raise ContractError("invalid existing intent identity")
        seen.add(intent.intent_id)
        for name in ("original_quantity", "cumulative_fills", "remaining_quantity", "pending_cash"):
            _number(getattr(intent, name), name)
        if intent.original_quantity != intent.cumulative_fills + intent.remaining_quantity:
            raise ContractError("existing intent authorization does not reconcile")
        if intent.original_quantity == 0:
            raise ContractError("zero existing authorization")


def validate_action_contract(proposal: dict[str, Any], context: ResearchContext) -> dict[str, Any]:  # noqa: PLR0912, PLR0915 - ordered economic invariants
    """Validate an untrusted JSON object and return a sealed inert receipt.

    V1 permits one action per symbol, no implicit OCO/brackets, no side crossing,
    no increase funded by hypothetical sales, and whole-share risk increases.
    A replay must separately model triggered stops, gaps, fills and actual
    observed capacity; this validator is not that replay or an order adapter.
    """
    _validate_context(context)
    if not isinstance(proposal, dict):
        raise ContractError("proposal must be a JSON object")
    model = proposal.get("model_id", "")
    if not isinstance(model, str) or not model.endswith(":free") or len(model) > MAX_MODEL_ID:
        raise ContractError("explicit OpenRouter free model identifier required")
    template = hold_contract(context, model_id=model, reason="template")
    if set(proposal) != set(template):
        raise ContractError("unknown or missing contract fields; executable code is forbidden")
    for key in set(template) - {"actions", "model_id"}:
        if proposal[key] != template[key] or type(proposal[key]) is not type(template[key]):
            raise ContractError(f"untrusted contract differs from context: {key}")
    actions = proposal["actions"]
    if not isinstance(actions, list) or not 1 <= len(actions) <= context.policy.max_actions:
        raise ContractError("invalid action count")
    marks = {e.symbol: e for e in context.evidence}
    original = dict(context.positions)
    projected = dict(original)
    locked = {i.symbol for i in context.authorized_intents if i.remaining_quantity > 0}
    pending_cash = sum((i.pending_cash for i in context.authorized_intents), ZERO)
    available = (
        context.confirmed_cash
        - context.restricted_cash
        - pending_cash
        - context.equity * context.policy.cash_reserve_fraction
    )
    committed = ZERO
    fee_budget = ZERO
    # Keep every accepted limit, not just the last action's price. Otherwise a
    # later action silently re-marks earlier increases at their cheaper marks.
    risk_prices = {symbol: item.mark for symbol, item in marks.items()}
    seen: set[str] = set()
    if len(actions) > 1 and any(a.get("kind") == "hold" for a in actions if isinstance(a, dict)):
        raise ContractError("hold must be an exclusive abstention")
    for action in actions:
        if (
            not isinstance(action, dict)
            or not isinstance(action.get("kind"), str)
            or action["kind"] not in KINDS
        ):
            raise ContractError("unknown action")
        reason = action.get("reason")
        if not isinstance(reason, str) or not 1 <= len(reason) <= MAX_REASON:
            raise ContractError("bounded action rationale required")
        kind = action["kind"]
        if kind == "hold":
            if set(action) != {"kind", "reason"}:
                raise ContractError("hold cannot carry instructions")
            continue
        required = {"kind", "reason", "symbol", "side", "quantity", "limit_price", "evidence_id"}
        if kind in {"stop_loss", "take_profit"}:
            required.add("trigger_price")
        if set(action) != required:
            raise ContractError("unknown or missing action fields")
        symbol, side = action["symbol"], action["side"]
        if (
            not isinstance(symbol, str)
            or symbol not in marks
            or not isinstance(side, str)
            or side not in {"buy", "sell"}
        ):
            raise ContractError("unknown symbol or side")
        if symbol in locked or symbol in seen:
            raise ContractError("existing authority or multiple actions for symbol")
        seen.add(symbol)
        market = marks[symbol]
        if action["evidence_id"] != market.evidence_id:
            raise ContractError("action evidence identity differs")
        quantity = _number(action["quantity"], "quantity", positive=True)
        price = _number(action["limit_price"], "limit price", positive=True)
        risk_prices[symbol] = max(risk_prices[symbol], price)
        held = original.get(symbol, ZERO)
        delta = quantity if side == "buy" else -quantity
        target = held + delta
        reducing = (held > 0 and -held <= delta < 0) or (held < 0 and 0 < delta <= -held)
        if held * target < 0:
            raise ContractError("v1 requires separate settled close before direction reversal")
        if kind in {"stop_loss", "take_profit", "close"} and not reducing:
            raise ContractError("protective action must reduce an actual held position")
        if kind == "close" and target != 0:
            raise ContractError("close must close the actual holding")
        if kind in {"open", "hedge"} and held != 0:
            raise ContractError("open/hedge requires a flat instrument; use resize")
        if kind == "resize" and held == 0:
            raise ContractError("resize requires an actual holding")
        if kind in {"stop_loss", "take_profit"}:
            trigger = _number(action["trigger_price"], "trigger price", positive=True)
            below = trigger < market.mark
            if trigger == market.mark or below != ((held > 0) == (kind == "stop_loss")):
                raise ContractError("protective trigger is on the wrong side of the mark")
            if (side == "buy" and price < trigger) or (side == "sell" and price > trigger):
                raise ContractError("limit cannot support the declared trigger")
        if not reducing:
            if quantity != quantity.to_integral_value() or quantity > market.entry_quantity_cap:
                raise ContractError("risk increases require whole shares within observed capacity")
            if market.instrument == "inverse_etf" and not context.policy.allow_inverse:
                raise ContractError("inverse capability is disabled")
            if target < 0:
                if (
                    not context.policy.allow_direct_short
                    or market.borrow_available is not True
                    or market.borrow_annual_rate is None
                    or not market.short_stress_validated
                ):
                    raise ContractError("short borrow, collateral and stress capability unverified")
                if market.instrument == "inverse_etf":
                    raise ContractError("shorting inverse instruments is outside v1")
                # Borrow buffer is explicit over the entire proposal life.
                days = Decimal(
                    str((context.expires_at - context.decision_at).total_seconds())
                ) / Decimal(86400)
                committed += (
                    quantity
                    * max(price, market.mark)
                    * (
                        context.policy.short_collateral_fraction
                        + market.borrow_annual_rate * days / Decimal(365)
                        + context.policy.one_way_bps / Decimal(10000)
                    )
                )
        if side == "buy":
            committed += quantity * price * (1 + context.policy.one_way_bps / Decimal(10000))
        fee_budget += (
            quantity * max(price, market.mark) * context.policy.one_way_bps / Decimal(10000)
        )
        # No simulated sell proceeds or pending proceeds enlarge available cash.
        # An unfilled sell reserves no purchase cash. Its native fee is paid
        # from its own proceeds; an unmet cash reserve must not prevent an
        # otherwise valid reduction. Purchases still cannot use sale credit.
        if committed > 0 and committed > available:
            raise ContractError(
                "insufficient confirmed cash after restrictions, pending cash and reserves"
            )
        # Do not grant new risk credit for an exit that may not fill, including
        # a conditional stop/TP. Preserve the worst-case original holdings.
        if not reducing:
            # Intent cash alone does not bound its future position exposure.
            # AuthorizedIntent deliberately carries no executable price/risk
            # envelope, so an unresolved order in *another* symbol cannot be
            # safely combined with a new increase. Expiry does not prove that
            # an accepted order has stopped filling. Await fresh settled state;
            # never cancel, rewrite or release the original authorization here.
            if locked:
                raise ContractError("new risk blocked by outstanding intent risk")
            projected[symbol] = target
        if not reducing:
            # Check both independent and cumulative increases. Opposing trades
            # cannot make an oversized standalone proposal appear compliant.
            for book in ({**original, symbol: target}, projected):
                values = {s: q * risk_prices[s] for s, q in book.items()}
                equity = context.equity - committed
                if equity <= 0:
                    raise ContractError("cost/collateral budget leaves no equity")
                # Costs reduce equity; reserved purchase notional is an asset,
                # so use only actual conservative fee burden in NAV limits.
                nav_floor = context.equity - fee_budget
                if (
                    nav_floor <= 0
                    or sum(map(abs, values.values())) > nav_floor * context.policy.max_gross
                    or abs(sum(values.values(), ZERO)) > nav_floor * context.policy.max_abs_net
                    or any(abs(v) > nav_floor * context.policy.max_symbol for v in values.values())
                ):
                    raise ContractError("gross, net or symbol risk limit exceeded")
    return {
        "schema": "research-agent-admissibility/1",
        "research_only": True,
        "execution_authority": "none",
        "proposal_sha256": _digest(proposal),
        "evidence_sha256": context.evidence_sha256,
        "policy_id": context.policy.identity,
        "new_cash_commitment": str(committed),
        "existing_pending_cash": str(pending_cash),
        "status": "admissible_for_replay_only",
        "limitations": [
            "not a fill simulation",
            "not broker authority",
            "not a performance result",
        ],
    }
