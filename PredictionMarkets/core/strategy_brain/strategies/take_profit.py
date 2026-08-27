"""Pure executable take-profit rule for binary contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.strategy_brain.strategies.salvage import OutcomeBook


ZERO = Decimal("0")


@dataclass(frozen=True)
class TakeProfitDecision:
    should_exit: bool
    gate: str
    reason: str
    bid: Decimal = ZERO
    spread: Decimal = ZERO
    cost_basis: Decimal = ZERO
    exit_fee: Decimal = ZERO
    gain_per_contract: Decimal = ZERO
    required_gain_per_contract: Decimal = ZERO
    maximum_settlement_gain_per_contract: Decimal = ZERO
    model_probability: Decimal | None = None


def is_active_winner(
    *,
    book_bid: Decimal,
    model_probability: Decimal | None,
    seconds_to_close: Decimal,
    minimum_bid: Decimal,
    minimum_probability: Decimal,
    strong_book_bid: Decimal,
    maximum_seconds_to_close: Decimal,
) -> bool:
    """Return whether routine profit-taking must be vetoed.

    A zero ``maximum_seconds_to_close`` means the veto applies throughout the
    position's lifetime.  The executable book can confirm a winner by itself
    at ``strong_book_bid``; below that threshold the book and independent model
    must agree.  This prevents a lagging commodity model from clipping a
    contract that the venue can already sell for nearly its full payout.
    """
    inside_window = (
        maximum_seconds_to_close <= ZERO
        or seconds_to_close <= maximum_seconds_to_close
    )
    strong_book = book_bid >= strong_book_bid
    corroborated = (
        book_bid >= minimum_bid
        and model_probability is not None
        and model_probability >= minimum_probability
    )
    return inside_window and (strong_book or corroborated)


def decide_take_profit(
    *,
    book: OutcomeBook | None,
    contracts: Decimal,
    position_contracts: Decimal | None = None,
    total_debit: Decimal,
    exit_fee_per_contract: Decimal,
    minimum_gain_per_contract: Decimal,
    maximum_spread: Decimal,
    seconds_to_close: Decimal,
    minimum_seconds_to_close: Decimal,
    minimum_capture_fraction: Decimal = ZERO,
    model_probability: Decimal | None = None,
    confidence_margin: Decimal = ZERO,
    winning_hold_enabled: bool = False,
    winning_hold_min_bid: Decimal = Decimal("1"),
    winning_hold_min_probability: Decimal = Decimal("1"),
    winning_hold_strong_book_bid: Decimal = Decimal("1"),
    winning_hold_max_seconds_to_close: Decimal = ZERO,
    winning_hold_confirmation_count: int = 1,
    winning_hold_required_confirmations: int = 1,
    confirmed_reversal: bool = False,
    confirmed_reversal_reason: str = "",
) -> TakeProfitDecision:
    """Exit only after capturing enough upside without risking principal.

    ``confirmed_reversal`` is deliberately advisory.  It used to return before
    the profit checks, which allowed two noisy observations to liquidate a
    position below its all-in cost.  A binary contract is not a guaranteed
    loser until the venue determines the result; at that point it is settled,
    not sold.  Consequently no model or book reversal may bypass the executable
    break-even floor here.
    """
    if book is None:
        return TakeProfitDecision(False, "book", "no usable two-sided book")
    original_contracts = position_contracts or contracts
    cost_basis = (
        total_debit / original_contracts if original_contracts > ZERO else ZERO
    )
    common = {
        "bid": book.bid,
        "spread": book.spread,
        "cost_basis": cost_basis,
        "exit_fee": exit_fee_per_contract,
    }
    if contracts <= ZERO or total_debit <= ZERO:
        return TakeProfitDecision(
            False, "position", "position has no positive size", **common
        )
    if seconds_to_close < minimum_seconds_to_close:
        return TakeProfitDecision(
            False,
            "time",
            f"only {seconds_to_close}s remain; an exit may not complete",
            **common,
        )
    if book.spread > maximum_spread:
        return TakeProfitDecision(
            False,
            "spread",
            f"spread {book.spread:.2f} exceeds {maximum_spread:.2f}",
            **common,
        )
    if book.depth <= ZERO:
        return TakeProfitDecision(
            False,
            "depth",
            "no executable bid depth",
            **common,
        )
    gain = book.bid - exit_fee_per_contract - cost_basis
    maximum_gain = max(ZERO, Decimal("1") - cost_basis)
    required_gain = max(
        minimum_gain_per_contract,
        maximum_gain * minimum_capture_fraction,
    )
    decision_values = {
        "gain_per_contract": gain,
        "required_gain_per_contract": required_gain,
        "maximum_settlement_gain_per_contract": maximum_gain,
        "model_probability": model_probability,
        **common,
    }
    # Hard safety invariant: an automatic exit may never lock in a loss.  This
    # must precede every signal-based override.  The estimate includes the exit
    # fee and ``cost_basis`` includes the entry fee through ``total_debit``.
    if gain < ZERO:
        reversal_note = (
            f"; reversal observed ({confirmed_reversal_reason})"
            if confirmed_reversal and confirmed_reversal_reason
            else ""
        )
        return TakeProfitDecision(
            False,
            "principal_protection",
            f"net executable gain {gain:.4f}/contract is below break-even; "
            f"hold for authoritative settlement{reversal_note}",
            **decision_values,
        )
    # A high executable bid is useful market evidence, not merely an exit price.
    # Protect an active winner throughout its lifetime by default.  A very
    # strong book is independently sufficient because commodity models update
    # less reliably than the venue; otherwise require book/model agreement.
    if (
        winning_hold_enabled
        and is_active_winner(
            book_bid=book.bid,
            model_probability=model_probability,
            seconds_to_close=seconds_to_close,
            minimum_bid=winning_hold_min_bid,
            minimum_probability=winning_hold_min_probability,
            strong_book_bid=winning_hold_strong_book_bid,
            maximum_seconds_to_close=winning_hold_max_seconds_to_close,
        )
    ):
        confirmed = (
            winning_hold_confirmation_count >= winning_hold_required_confirmations
        )
        evidence = (
            f"strong executable bid {book.bid:.4f} >= "
            f"{winning_hold_strong_book_bid:.4f}"
            if book.bid >= winning_hold_strong_book_bid
            else (
                f"bid {book.bid:.4f} >= {winning_hold_min_bid:.4f} and model "
                f"{model_probability:.4f} >= {winning_hold_min_probability:.4f}"
            )
        )
        window = (
            "throughout the contract window"
            if winning_hold_max_seconds_to_close <= ZERO
            else f"with {seconds_to_close}s remaining"
        )
        return TakeProfitDecision(
            False,
            "winning_hold" if confirmed else "winning_confirmation",
            (
                f"active winner: {evidence}; protected {window}; confirmed "
                f"{winning_hold_confirmation_count}/"
                f"{winning_hold_required_confirmations} times"
            ),
            **decision_values,
        )
    if gain < required_gain:
        reversal_note = (
            "; reversal is advisory and cannot bypass the profit target"
            if confirmed_reversal
            else ""
        )
        return TakeProfitDecision(
            False,
            "capture",
            f"net gain {gain:.4f}/contract is below adaptive target "
            f"{required_gain:.4f}{reversal_note}",
            **decision_values,
        )
    if (
        model_probability is not None
        and model_probability - book.bid >= confidence_margin
    ):
        return TakeProfitDecision(
            False,
            "confidence",
            f"model probability {model_probability:.4f} remains above bid {book.bid:.4f}",
            **decision_values,
        )
    return TakeProfitDecision(
        True,
        "take_profit",
        f"executable net gain {gain:.4f}/contract clears adaptive target {required_gain:.4f}",
        **decision_values,
    )
