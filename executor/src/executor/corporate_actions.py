"""Strict share effects for Alpaca's documented executed activity-stream legs.

Legacy REST SSP/SSO quantities are not this schema and remain unsupported.
Reference: https://docs.alpaca.markets/us/docs/activity-sse
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def number(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid_corporate_quantity") from exc


def share_effect(row: dict[str, Any]) -> tuple[str, Decimal, Decimal]:
    pair = (row.get("activity_type"), row.get("activity_subtype"))
    if pair not in {("SPLIT", "FSPLIT"), ("SPLIT", "RSPLIT"), ("SPLIT", "USPLIT"), ("DIV", "SDIV")}:
        raise ValueError("unsupported_activity_type")
    if row.get("status") != "executed" or row.get("currency") != "USD" or row.get("previous_id"):
        raise ValueError("unsupported_or_corrected_corporate_activity")
    details = row.get("details")
    if not isinstance(details, dict) or not details.get("ca_id") or not row.get("ref_id"):
        raise ValueError("missing_corporate_action_evidence")
    symbol = details.get("symbol")
    shares, cash = number(row.get("qty")), number(row.get("net_amount"))
    if not symbol or not isinstance(symbol, str) or not shares.is_finite() or not cash.is_finite():
        raise ValueError("invalid_corporate_action_leg")
    if pair[0] == "SPLIT":
        old, new = number(details.get("old_qty")), number(details.get("new_qty"))
        if (
            not old.is_finite()
            or not new.is_finite()
            or old <= 0
            or new <= 0
            or shares not in {-old, new}
        ):
            raise ValueError("invalid_split_removal_or_addition")
        if cash != 0:
            raise ValueError("split_cash_requires_separate_cash_activity")
    elif shares <= 0:
        raise ValueError("invalid_stock_dividend")
    return symbol, shares, cash
