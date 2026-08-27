"""Authenticated client construction.

``ApiClient.set_kalshi_auth`` is broken in kalshi-python-async 3.26.0 (it
references ``KalshiAuth`` without importing it, and hands it a file path where
PEM text is expected), so the client builds the signer itself. This test is the
guard: it fails the moment that wiring regresses, instead of the failure showing
up as ``Startup failed: name 'KalshiAuth' is not defined`` on a live run.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from execution.kalshi_client import AsyncKalshiTradingClient
from tests.test_kalshi_order_plans import settings


def write_private_key(path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


async def test_live_client_signs_requests(tmp_path: Path) -> None:
    write_private_key(tmp_path / "private.pem")
    config = replace(settings(tmp_path), api_key_id="test-key-id")

    client = AsyncKalshiTradingClient(config, authenticated=True)
    try:
        assert client.authenticated is True
        headers = client._client.kalshi_auth.create_auth_headers(
            "GET", "/trade-api/v2/portfolio/balance"
        )
        assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"
        assert headers["KALSHI-ACCESS-SIGNATURE"]
        assert headers["KALSHI-ACCESS-TIMESTAMP"]
    finally:
        await client.close()


async def test_missing_key_file_is_reported_before_any_request(tmp_path: Path) -> None:
    config = replace(settings(tmp_path), api_key_id="test-key-id")

    with pytest.raises(RuntimeError, match="private key not found"):
        AsyncKalshiTradingClient(config, authenticated=True)


async def test_missing_api_key_id_is_reported(tmp_path: Path) -> None:
    write_private_key(tmp_path / "private.pem")

    with pytest.raises(RuntimeError, match="KALSHI_API_KEY"):
        AsyncKalshiTradingClient(settings(tmp_path), authenticated=True)
