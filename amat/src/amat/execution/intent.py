"""The execution boundary: what leaves this repository, and what may not.

Why this module exists
----------------------
The backtest forms a decision on every session and schedules it into the next
session inside the panel. On the *final* session there is no next session, so
that decision is discarded -- and it is the only decision a live account cares
about. This module turns it into an artifact.

The artifact is deliberately inert. An :class:`ExecutionPlan` is a description
of intent: which symbol, which direction, how many shares, sized against which
mark, and the earliest session in which it may be acted on. It knows nothing
about brokers, accounts, credentials, venues, or networks, and nothing in this
package can acquire that knowledge -- ``tests/unit/test_read_only_data_access.py``
holds the line.

The division of responsibility
------------------------------
This repository owns the *decision* and its provenance. An executor owns the
*calendar*, the *account*, and the *submission*. That split is not stylistic:

* the trading calendar (holidays, half-days, halts) is an execution concern, so
  the plan names the session it may not execute *before* rather than pretending
  to know the session it will execute *in*;
* positions and equity are supplied by the caller and never fetched, so a plan
  can be rebuilt identically from a journal without contacting anything;
* the plan carries an expiry, because a signal formed from Tuesday's close is
  not a licence to trade on Friday.

Reconciliation is a first-class output, not an afterthought. A holding the
strategy does not recognise is recorded in
:attr:`ExecutionPlan.unrecognized_holdings` and never traded: this module will
not emit an instruction for an instrument whose target it cannot compute. That
failure mode -- an executor quietly liquidating something it did not understand
-- is worse than doing nothing.

What this module refuses to do
------------------------------
Fail closed, per guardrail 9. A missing mark for a symbol the strategy wants to
hold is an error, not a zero. A negative target weight is an error, not a short.
Gross exposure above the configured cap is an error, not a rounding tolerance.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from amat.config.models import StrategyConfig
from amat.domain.positions import Position
from amat.domain.types import Cash, Price, Quantity, SessionDate, Side, Symbol, parse_symbol
from amat.errors import ConfigurationError, DataIntegrityError
from amat.execution import build_instructions

__all__ = [
    "PLAN_SCHEMA_VERSION",
    "ExecutionPlan",
    "TradeIntent",
    "WithheldIntent",
    "build_execution_plan",
]

#: Schema version of the serialized plan. An executor must refuse a major
#: version it was not written against; silently tolerating an unknown shape is
#: how a renamed field becomes a wrong-sized position.
PLAN_SCHEMA_VERSION = "1.0.0"

#: Calendar days a plan stays valid. Four covers a Friday signal executing on
#: the Tuesday after a Monday holiday. It is a backstop only: the binding
#: control is that execution must occur in the *first* session after the signal
#: session, which the executor resolves from its own calendar.
_DEFAULT_VALIDITY_DAYS = 4


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """One instruction an executor may act on, with the arithmetic behind it.

    Attributes:
        symbol: Instrument to trade.
        side: ``buy`` increases a long; ``sell`` reduces or closes one. The
            strategy is long-or-cash, so a sell never crosses through zero.
        quantity: Positive share count. Direction lives in ``side`` alone.
        reference_price: The decision session's mark used to convert a weight
            into a share count. It is *not* an expected fill price -- the fill
            happens in a later session's opening window, and that gap is real
            execution risk that no plan can remove.
        notional: ``quantity * reference_price``, for sizing checks only.
        current_weight: Portfolio weight held when the plan was formed.
        target_weight: Portfolio weight sought.
        reason: Which contract rebalance rule emitted this.
    """

    symbol: str
    side: str
    quantity: Decimal
    reference_price: Decimal
    notional: Decimal
    current_weight: float
    target_weight: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready payload. Decimals are strings, never floats."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "reference_price": str(self.reference_price),
            "notional": str(self.notional),
            "current_weight": self.current_weight,
            "target_weight": self.target_weight,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class WithheldIntent:
    """A trade the rebalance rule formed and then declined to emit.

    Recorded rather than dropped: "the target was not reached because the trade
    was below the minimum notional" and "the target was reached" must never look
    the same to whoever reads the plan.
    """

    symbol: str
    side: str
    quantity: Decimal
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """A dated, provenance-carrying set of intents for one decision session."""

    schema_version: str
    generated_at: dt.datetime
    engine: str

    strategy_name: str
    strategy_version: str
    config_sha256: str
    snapshot_id: str
    appended_sessions: int
    code_revision: str

    as_of_session: SessionDate
    """The completed session whose close formed this decision."""

    earliest_execution_session: SessionDate
    """The earliest date execution may occur. The real session is the first
    exchange session on or after this date, which the executor resolves."""

    window_start_local: dt.time
    window_end_local: dt.time
    exchange_timezone: str
    expires_at: dt.datetime
    """UTC instant after which this plan must be discarded, not executed late."""

    account_equity: Cash
    target_weights: dict[str, float]
    current_positions: dict[str, Decimal]
    intents: tuple[TradeIntent, ...]
    withheld: tuple[WithheldIntent, ...]
    unrecognized_holdings: tuple[str, ...]
    """Symbols the account holds that the strategy universe does not contain.
    Never traded by this plan. Their presence means the account and the strategy
    disagree about what is held, which a human must resolve."""

    caveats: tuple[str, ...]

    @property
    def gross_target_exposure(self) -> float:
        """Sum of target weights. Long-or-cash, so this is also net exposure."""
        return float(sum(self.target_weights.values()))

    def is_expired(self, *, now: dt.datetime) -> bool:
        """Whether ``now`` is past the plan's staleness backstop."""
        if now.tzinfo is None:
            message = "expiry must be evaluated against a timezone-aware instant"
            raise ConfigurationError(message)
        return now > self.expires_at

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready payload, stable across runs for the same inputs."""
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.isoformat(),
            "engine": self.engine,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "config_sha256": self.config_sha256,
            "snapshot_id": self.snapshot_id,
            "appended_sessions": self.appended_sessions,
            "code_revision": self.code_revision,
            "as_of_session": self.as_of_session.isoformat(),
            "earliest_execution_session": self.earliest_execution_session.isoformat(),
            "window_start_local": self.window_start_local.isoformat(),
            "window_end_local": self.window_end_local.isoformat(),
            "exchange_timezone": self.exchange_timezone,
            "expires_at": self.expires_at.isoformat(),
            "account_equity": str(self.account_equity),
            "gross_target_exposure": self.gross_target_exposure,
            "target_weights": {
                symbol: self.target_weights[symbol] for symbol in sorted(self.target_weights)
            },
            "current_positions": {
                symbol: str(self.current_positions[symbol])
                for symbol in sorted(self.current_positions)
            },
            "intents": [intent.as_dict() for intent in self.intents],
            "withheld": [item.as_dict() for item in self.withheld],
            "unrecognized_holdings": list(self.unrecognized_holdings),
            "caveats": list(self.caveats),
        }


def build_execution_plan(
    strategy: StrategyConfig,
    *,
    engine: str,
    as_of_session: SessionDate,
    target_weights: Mapping[str, float],
    marks: Mapping[str, Decimal],
    current_positions: Mapping[str, Decimal],
    account_equity: Cash,
    config_sha256: str,
    snapshot_id: str,
    code_revision: str,
    appended_sessions: int = 0,
    generated_at: dt.datetime | None = None,
    validity_calendar_days: int = _DEFAULT_VALIDITY_DAYS,
    extra_caveats: tuple[str, ...] = (),
) -> ExecutionPlan:
    """Assemble an execution plan from a formed decision and an account state.

    This function performs no I/O and consults no clock other than
    ``generated_at``, so the same inputs always produce the same plan. That is
    what lets an executor's journal be replayed and re-checked later.

    Args:
        strategy: The validated configuration the decision was formed under.
        engine: Which engine formed it, for the audit trail.
        as_of_session: Completed session whose close produced the decision.
        target_weights: Post-constraint target weight per symbol. Long-or-cash,
            so every value must be non-negative.
        marks: Decision-session raw closes, used to convert weights to shares.
            Required for every symbol with a non-zero target and for every
            symbol currently held.
        current_positions: Share count per symbol in the real account, supplied
            by the caller. Never fetched here.
        account_equity: The real account's equity, supplied by the caller.
        config_sha256: Content digest of the configuration.
        snapshot_id: Immutable data snapshot the decision was formed from.
        code_revision: Revision of the code that formed it.
        appended_sessions: Sessions appended past the snapshot's frozen digest.
        generated_at: Timezone-aware creation instant. Defaults to now (UTC).
        validity_calendar_days: Staleness backstop, in calendar days.
        extra_caveats: Additional warnings to carry into the plan.

    Raises:
        ConfigurationError: on a negative target weight, non-positive equity,
            naive ``generated_at``, or a non-positive validity window.
        DataIntegrityError: if a mark is missing for a symbol that must be
            sized, or if gross target exposure exceeds the configured cap.
    """
    if account_equity <= 0:
        message = f"account equity must be positive; received {account_equity}"
        raise ConfigurationError(message)
    if validity_calendar_days < 1:
        message = f"validity_calendar_days must be at least 1; received {validity_calendar_days}"
        raise ConfigurationError(message)

    moment = generated_at or dt.datetime.now(tz=dt.UTC)
    if moment.tzinfo is None:
        message = "generated_at must be timezone-aware"
        raise ConfigurationError(message)

    universe_symbols = {str(symbol) for symbol in strategy.universe.symbols}

    negative = sorted(symbol for symbol, weight in target_weights.items() if weight < 0)
    if negative:
        message = (
            f"long-or-cash strategy produced negative target weight(s) for "
            f"{', '.join(negative)}; a short is not representable in this contract"
        )
        raise ConfigurationError(message)

    gross = float(sum(target_weights.values()))
    cap = strategy.constraints.max_gross_exposure
    # A float sum of many weights carries representation error; the tolerance is
    # derived from that, not chosen to make a breach pass.
    if gross > cap + 1e-9:
        message = (
            f"target gross exposure {gross:.6f} exceeds the configured cap {cap:.6f}; "
            "the allocator must clip before a plan is built, never the executor"
        )
        raise DataIntegrityError(message)

    held = {symbol: quantity for symbol, quantity in current_positions.items() if quantity != 0}
    unrecognized = tuple(sorted(symbol for symbol in held if symbol not in universe_symbols))

    # Every symbol that needs a share count needs a price. Missing one is an
    # error rather than an implicit zero target: silently skipping a held symbol
    # would leave the account holding something the plan claims to have flattened.
    needs_mark = {symbol for symbol, weight in target_weights.items() if weight > 0}
    needs_mark |= {symbol for symbol in held if symbol in universe_symbols}
    missing = sorted(symbol for symbol in needs_mark if symbol not in marks)
    if missing:
        message = (
            f"no decision-session mark for {', '.join(missing)}; a position cannot be "
            "sized without one and a missing price must not be read as a flat target"
        )
        raise DataIntegrityError(message)

    typed_targets: dict[Symbol, float] = {
        parse_symbol(symbol): float(weight)
        for symbol, weight in target_weights.items()
        if symbol in universe_symbols
    }
    typed_marks: dict[Symbol, Price] = {
        parse_symbol(symbol): Price(price)
        for symbol, price in marks.items()
        if symbol in universe_symbols
    }
    typed_positions: dict[Symbol, Position] = {
        parse_symbol(symbol): Position(
            symbol=parse_symbol(symbol),
            quantity=Quantity(quantity),
            mark=typed_marks[parse_symbol(symbol)],
            as_of_session=as_of_session,
        )
        for symbol, quantity in held.items()
        if symbol in universe_symbols
    }

    # The earliest date execution may occur. The contract's lag is expressed in
    # *sessions*, and this repository holds no trading calendar, so the plan
    # states a lower bound and names the resolution as the executor's job.
    earliest = as_of_session + dt.timedelta(
        days=strategy.execution.signal_to_execution_lag_sessions
    )

    instructions, below_minimum = build_instructions(
        typed_targets,
        typed_positions,
        typed_marks,
        account_equity,
        signal_session=as_of_session,
        execution_session=earliest,
        rebalance=strategy.rebalance,
        execution=strategy.execution,
    )

    intents: list[TradeIntent] = []
    for instruction in instructions:
        mark = typed_marks[instruction.symbol]
        held_quantity = held.get(str(instruction.symbol), Decimal(0))
        intents.append(
            TradeIntent(
                symbol=str(instruction.symbol),
                side="buy" if instruction.side == Side.BUY else "sell",
                quantity=instruction.quantity,
                reference_price=mark,
                notional=instruction.quantity * mark,
                current_weight=float(held_quantity * mark / account_equity),
                target_weight=float(typed_targets.get(instruction.symbol, 0.0)),
                reason=str(instruction.reason),
            )
        )
    intents.sort(key=lambda item: item.symbol)

    withheld = tuple(
        WithheldIntent(
            symbol=str(item.symbol),
            side="buy" if item.side == Side.BUY else "sell",
            quantity=item.requested_quantity,
            reason=str(item.reason),
        )
        for item in sorted(below_minimum, key=lambda item: str(item.symbol))
    )

    zone = ZoneInfo(strategy.execution.timezone)
    expires_at = dt.datetime.combine(
        as_of_session + dt.timedelta(days=validity_calendar_days),
        strategy.execution.window_end,
        tzinfo=zone,
    ).astimezone(dt.UTC)

    caveats = [
        "PLAN ONLY -- this artifact describes intent. It submits nothing and reaches no account.",
        "reference_price is the decision session's close, not an expected fill. "
        "The fill occurs in a later session's opening window.",
        "Share counts assume fractional quantities are permitted. An executor "
        "that cannot trade fractions must round down and record the residual.",
    ]
    if unrecognized:
        caveats.append(
            f"account holds {len(unrecognized)} symbol(s) outside the strategy universe "
            f"({', '.join(unrecognized)}); this plan does not trade them and the "
            "discrepancy needs a human decision."
        )
    if appended_sessions:
        caveats.append(
            f"{appended_sessions} session(s) were appended past the frozen digest for "
            f"{snapshot_id} and are not covered by its manifest."
        )
    caveats.extend(extra_caveats)

    return ExecutionPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        generated_at=moment,
        engine=engine,
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        config_sha256=config_sha256,
        snapshot_id=snapshot_id,
        appended_sessions=appended_sessions,
        code_revision=code_revision,
        as_of_session=as_of_session,
        earliest_execution_session=earliest,
        window_start_local=strategy.execution.window_start,
        window_end_local=strategy.execution.window_end,
        exchange_timezone=strategy.execution.timezone,
        expires_at=expires_at,
        account_equity=account_equity,
        target_weights={
            symbol: float(weight) for symbol, weight in target_weights.items() if weight > 0
        },
        current_positions=dict(held),
        intents=tuple(intents),
        withheld=withheld,
        unrecognized_holdings=unrecognized,
        caveats=tuple(caveats),
    )
