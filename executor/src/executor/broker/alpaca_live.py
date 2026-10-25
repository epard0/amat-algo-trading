"""Explicit live adapter, sharing the durable paper executor's broker protocol."""

from __future__ import annotations

from typing import Any

from executor.broker.alpaca_paper import Account, AlpacaPaperClient
from executor.errors import ConfigurationError

LIVE_TRADING_HOST = "api.alpaca.markets"


class AlpacaLiveClient(AlpacaPaperClient):
    """Pinned live endpoint with account verification before every order POST."""

    _host = LIVE_TRADING_HOST

    def __init__(
        self, key_id: str, secret_key: str, *, account_number: str, enabled: bool = False
    ) -> None:
        super().__init__(key_id, secret_key)
        if not account_number.strip():
            raise ConfigurationError("Live account binding is required")
        self._account_number = account_number
        self._enabled = enabled

    def get_account(self) -> Account:
        account = super().get_account()
        if account.account_number != self._account_number:
            raise ConfigurationError("Live account does not match configured account")
        return account

    def _post_once(self, path: str, body: dict[str, Any]) -> Any:
        if not self._enabled:
            raise ConfigurationError("LIVE_TRADING_ENABLED is false")
        if not self.get_account().is_tradable:
            raise ConfigurationError("Live account is not tradable")
        return super()._post_once(path, body)
