"""Broker adapters with explicit paper/live selection."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from executor.config import ExecutorSettings

from executor.broker.alpaca_paper import (
    Account,
    AlpacaPaperClient,
    CalendarSession,
    Clock,
    Order,
    Position,
)

__all__ = [
    "Account",
    "AlpacaPaperClient",
    "CalendarSession",
    "Clock",
    "Order",
    "Position",
]


def create_client(settings: ExecutorSettings) -> AlpacaPaperClient:
    """Construct only the adapter explicitly selected by validated settings."""
    from executor.errors import ConfigurationError  # noqa: PLC0415
    from executor.safety import bind_trading_mode  # noqa: PLC0415

    bind_trading_mode(settings.state_root, settings.trading_mode)
    if settings.trading_mode == "paper":
        return AlpacaPaperClient(settings.trading_key_id, settings.trading_secret_key)
    if settings.trading_mode != "live":
        raise ConfigurationError("Unknown trading mode")
    from executor.broker.alpaca_live import AlpacaLiveClient  # noqa: PLC0415

    return AlpacaLiveClient(
        settings.trading_key_id,
        settings.trading_secret_key,
        account_number=settings.live_account_number,
        enabled=settings.live_trading_enabled
        and not settings.dry_run
        and not settings.limits.disabled,
    )
