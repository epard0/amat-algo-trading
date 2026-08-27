"""Authenticated Kalshi order-book stream with deterministic reconstruction.

The stream is an acceleration and recording path, never a trading dependency:
REST polling remains authoritative when the socket is unavailable.  Every
snapshot and delta is persisted before its derived analysis is emitted.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import aiohttp
from loguru import logger

from core.strategy_brain.strategies.orderbook import analyse
from execution.kalshi_client import OrderBookSnapshot

try:
    from kalshi_python_async import KalshiAuth
except ImportError as exc:  # pragma: no cover - bad installation
    raise RuntimeError(
        "The official async Kalshi SDK is missing. Run: uv sync"
    ) from exc


AnalysisCallback = Callable[[str, Any], Awaitable[None] | None]


class SequenceGap(RuntimeError):
    """Raised internally so a fresh snapshot replaces an incomplete book."""


def _levels(raw: Any) -> dict[Decimal, Decimal]:
    levels: dict[Decimal, Decimal] = {}
    for level in raw or ():
        if isinstance(level, dict):
            price = level.get("price_dollars", level.get("price"))
            quantity = level.get("count_fp", level.get("quantity"))
        else:
            price, quantity = level[0], level[1]
        size = Decimal(str(quantity))
        if size > 0:
            levels[Decimal(str(price))] = size
    return levels


class KalshiBookStream:
    """Record and reconstruct selected Kalshi books from sequenced events."""

    def __init__(
        self,
        *,
        settings: Any,
        ledger: Any,
        run_id_provider: Callable[[], str],
        on_analysis: AnalysisCallback | None = None,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.run_id_provider = run_id_provider
        self.on_analysis = on_analysis
        self._desired: frozenset[str] = frozenset()
        self._books: dict[str, dict[str, dict[Decimal, Decimal]]] = {}
        self._sequences: dict[str, int] = {}
        self._channel_sequences: dict[str, int] = {}
        self._observed_at: dict[str, datetime] = {}
        self._task: asyncio.Task[None] | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._changed = asyncio.Event()
        self._closed = False
        self._last_stale_report = 0.0

    @property
    def enabled(self) -> bool:
        return bool(
            getattr(self.settings, "kalshi_websocket_enabled", True)
            and self.settings.api_key_id
            and self.settings.private_key_path.is_file()
        )

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task is not None and not self._task.done():
            return
        if self._task is not None:
            try:
                failure = self._task.exception()
            except (asyncio.CancelledError, asyncio.InvalidStateError):
                failure = None
            if failure is not None:
                logger.warning("Restarting failed Kalshi book stream: {}", failure)
            self._task = None
        self._closed = False
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._run(), name="kalshi-book-stream")

    async def close(self) -> None:
        self._closed = True
        self._changed.set()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def update_markets(self, tickers: Iterable[str]) -> None:
        # This method is called every polling cycle, including when the desired
        # ticker set is unchanged. It therefore doubles as the stream
        # supervisor: a task that terminated unexpectedly is restarted instead
        # of remaining silently dead for the lifetime of the bot.
        await self.start()
        desired = frozenset(tickers)
        if desired == self._desired:
            await self._check_staleness(desired)
            return
        self._desired = desired
        self._changed.set()
        # Reconnect deliberately. A new subscription starts with an atomic
        # snapshot, which is safer than merging a rolled market into old state.
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

    async def _check_staleness(self, desired: frozenset[str]) -> None:
        if self._ws is None or self._ws.closed or not desired:
            return
        maximum_age = float(
            getattr(self.settings, "kalshi_websocket_stale_seconds", 10.0)
        )
        now = datetime.now(timezone.utc)
        stale = [
            ticker
            for ticker in sorted(desired)
            if ticker not in self._observed_at
            or (now - self._observed_at[ticker]).total_seconds() > maximum_age
        ]
        if not stale:
            return
        now_mono = time.monotonic()
        if now_mono - self._last_stale_report >= maximum_age:
            self._quality(
                "stale_feed",
                "warning",
                ticker=stale[0] if len(stale) == 1 else None,
                details={"stale_tickers": stale, "maximum_age_seconds": maximum_age},
            )
            self._last_stale_report = now_mono
        if len(stale) == len(desired):
            # An entirely stale stream is not an acceleration path. Reconnect
            # for an atomic snapshot while REST discovery continues normally.
            await self._ws.close()

    def analysis(self, ticker: str) -> Any:
        book = self._books.get(ticker)
        if not book:
            return None
        return analyse(tuple(book["yes"].items()), tuple(book["no"].items()))

    def snapshot(
        self,
        ticker: str,
        *,
        max_age_seconds: float | None = None,
    ) -> OrderBookSnapshot | None:
        """Return one internally consistent reconstructed book when fresh.

        Consumers receive the exact levels used to derive ``analysis``. This
        prevents discovery touch fields and stream depth from being combined
        into a contradictory training row or trading decision.
        """
        book = self._books.get(ticker)
        observed_at = self._observed_at.get(ticker)
        if not book or observed_at is None:
            return None
        age = (datetime.now(timezone.utc) - observed_at).total_seconds()
        if max_age_seconds is not None and age > max_age_seconds:
            return None
        yes_levels = tuple(sorted(book["yes"].items()))
        no_levels = tuple(sorted(book["no"].items()))
        analysis = analyse(yes_levels, no_levels)
        if analysis is None:
            return None
        return OrderBookSnapshot(
            ticker=ticker,
            observed_at=observed_at,
            yes_levels=yes_levels,
            no_levels=no_levels,
            analysis=analysis,
            source="websocket",
            sequence=self._sequences.get(ticker),
        )

    def health(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        newest = max(self._observed_at.values(), default=None)
        return {
            "enabled": self.enabled,
            "running": self._task is not None and not self._task.done(),
            "connected": self._ws is not None and not self._ws.closed,
            "books": len(self._books),
            "last_event_age_seconds": (
                (now - newest).total_seconds() if newest is not None else None
            ),
        }

    async def _run(self) -> None:
        backoff = 1.0
        while not self._closed:
            if not self._desired:
                self._changed.clear()
                await self._changed.wait()
                continue
            try:
                await self._connect_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - stream must self-heal
                self._quality("websocket_error", "warning", details={"error": str(exc)})
                logger.warning(
                    "Kalshi book stream disconnected: {}; REST remains active", exc
                )
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                self._changed.clear()
                backoff = min(backoff * 2, 30.0)

    async def _connect_once(self) -> None:
        assert self._session is not None
        signer = KalshiAuth(
            self.settings.api_key_id,
            self.settings.private_key_path.read_text(),
        )
        headers = signer.create_auth_headers("GET", "/trade-api/ws/v2")
        try:
            ws = await self._session.ws_connect(
                self.settings.websocket_url,
                headers=headers,
                heartbeat=20,
                receive_timeout=45,
            )
        except aiohttp.WSServerHandshakeError as exc:
            self._quality(
                "websocket_http_error",
                "error",
                details={"status": exc.status, "message": exc.message},
            )
            raise
        self._ws = ws
        self._books.clear()
        self._sequences.clear()
        self._channel_sequences.clear()
        self._observed_at.clear()
        tickers = sorted(self._desired)
        await ws.send_json(
            {
                "id": 1,
                "cmd": "subscribe",
                "params": {
                    "channels": ["orderbook_delta", "trade"],
                    "market_tickers": tickers,
                },
            }
        )
        self._quality("websocket_connected", "info", details={"tickers": tickers})
        try:
            async for frame in ws:
                if frame.type == aiohttp.WSMsgType.TEXT:
                    try:
                        await self.handle_message(json.loads(frame.data))
                    except SequenceGap:
                        await ws.close()
                        return
                    except (ValueError, TypeError, KeyError) as exc:
                        self._quality(
                            "websocket_decode_error",
                            "warning",
                            details={"error": str(exc), "frame": frame.data[:1000]},
                        )
                elif frame.type in {
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.ERROR,
                }:
                    break
        finally:
            self._ws = None

    async def handle_message(self, envelope: dict[str, Any]) -> None:
        """Apply one decoded venue message; public for deterministic tests."""
        event_type = str(envelope.get("type", ""))
        message = envelope.get("msg") or {}
        if event_type == "orderbook_snapshot":
            await self._snapshot(envelope, message)
        elif event_type == "orderbook_delta":
            await self._delta(envelope, message)
        elif event_type == "trade":
            self._trade(envelope, message)
        elif event_type == "error":
            code = message.get("code", envelope.get("code"))
            detail = message.get("msg", message.get("message", message))
            self._quality(
                "websocket_venue_error",
                "error",
                details={"code": code, "message": detail},
            )
            logger.warning("Kalshi websocket rejected a request: {} {}", code, detail)

    async def _snapshot(
        self, envelope: dict[str, Any], message: dict[str, Any]
    ) -> None:
        ticker = str(message["market_ticker"])
        sequence = self._advance_sequence(envelope, message, ticker=ticker)
        yes = _levels(message.get("yes_dollars_fp", message.get("yes")))
        no = _levels(message.get("no_dollars_fp", message.get("no")))
        self._books[ticker] = {"yes": yes, "no": no}
        self._observed_at[ticker] = datetime.now(timezone.utc)
        if sequence is not None:
            self._sequences[ticker] = sequence
        if getattr(self.settings, "raw_book_event_recording_enabled", False):
            self.ledger.record_book_event(
                run_id=self.run_id_provider(),
                ticker=ticker,
                event_type="snapshot",
                sequence=sequence,
                exchange_ts_ms=self._timestamp_ms(message),
                payload={
                    "yes": [[str(p), str(q)] for p, q in sorted(yes.items())],
                    "no": [[str(p), str(q)] for p, q in sorted(no.items())],
                },
            )
        await self._emit(ticker)

    async def _delta(self, envelope: dict[str, Any], message: dict[str, Any]) -> None:
        ticker = str(message["market_ticker"])
        if ticker not in self._books:
            sequence = self._sequence(envelope, message)
            self._quality(
                "delta_before_snapshot",
                "warning",
                ticker=ticker,
                actual_sequence=sequence,
            )
            raise SequenceGap(f"delta before snapshot for {ticker}")
        sequence = self._advance_sequence(envelope, message, ticker=ticker)

        side = str(message["side"]).lower()
        if side not in {"yes", "no"}:
            raise ValueError(f"unknown order-book side: {side}")
        price = Decimal(str(message.get("price_dollars", message.get("price"))))
        delta = Decimal(str(message.get("delta_fp", message.get("delta"))))
        levels = self._books[ticker][side]
        new_size = levels.get(price, Decimal("0")) + delta
        if new_size <= 0:
            levels.pop(price, None)
        else:
            levels[price] = new_size
        if sequence is not None:
            self._sequences[ticker] = sequence
        self._observed_at[ticker] = datetime.now(timezone.utc)
        if getattr(self.settings, "raw_book_event_recording_enabled", False):
            self.ledger.record_book_event(
                run_id=self.run_id_provider(),
                ticker=ticker,
                event_type="delta",
                sequence=sequence,
                outcome_side=side,
                price=price,
                quantity_delta=delta,
                exchange_ts_ms=self._timestamp_ms(message),
                received_monotonic_ns=time.monotonic_ns(),
                payload={},
            )
        await self._emit(ticker)

    def _trade(self, envelope: dict[str, Any], message: dict[str, Any]) -> None:
        ticker = str(message["market_ticker"])
        sequence = self._advance_sequence(envelope, message, ticker=ticker)
        yes_price = message.get("yes_price_dollars", message.get("yes_price"))
        contracts = message.get("count_fp", message.get("count"))
        if yes_price is None or contracts is None:
            raise ValueError("public trade omitted yes price or contract count")
        if getattr(self.settings, "public_trade_recording_enabled", True):
            self.ledger.record_public_trade(
                run_id=self.run_id_provider(),
                ticker=ticker,
                yes_price=Decimal(str(yes_price)),
                contracts=Decimal(str(contracts)),
                trade_id=message.get("trade_id"),
                taker_side=str(message.get("taker_side", "unknown")).lower(),
                exchange_ts_ms=self._timestamp_ms(message),
            )
        if sequence is not None:
            self._sequences[ticker] = sequence

    async def _emit(self, ticker: str) -> None:
        analysis = self.analysis(ticker)
        if analysis is None or self.on_analysis is None:
            return
        result = self.on_analysis(ticker, analysis)
        if result is not None:
            await result

    @staticmethod
    def _sequence(envelope: dict[str, Any], message: dict[str, Any]) -> int | None:
        raw = envelope.get("seq", message.get("seq"))
        return int(raw) if raw is not None else None

    def _advance_sequence(
        self,
        envelope: dict[str, Any],
        message: dict[str, Any],
        *,
        ticker: str,
    ) -> int | None:
        """Advance the sequence belonging to one server subscription.

        A single order-book subscription emits initial snapshots for every
        ticker at consecutive sequence numbers, then deltas continue that same
        sequence. Tracking per ticker falsely reports a gap on the first delta.
        Separate channel subscriptions (for example public trades) carry their
        own ``sid`` and therefore their own counter.
        """
        sequence = self._sequence(envelope, message)
        if sequence is None:
            return None
        channel = str(envelope.get("sid", message.get("sid", "default")))
        prior = self._channel_sequences.get(channel)
        if prior is not None and sequence != prior + 1:
            self._quality(
                "sequence_gap",
                "error",
                ticker=ticker,
                expected_sequence=prior + 1,
                actual_sequence=sequence,
                details={"subscription_id": channel},
            )
            raise SequenceGap(
                f"subscription {channel}: expected {prior + 1}, got {sequence}"
            )
        self._channel_sequences[channel] = sequence
        return sequence

    @staticmethod
    def _timestamp_ms(message: dict[str, Any]) -> int | None:
        raw = message.get("ts_ms", message.get("ts"))
        return int(raw) if raw is not None else None

    def _quality(
        self,
        event_type: str,
        severity: str,
        *,
        ticker: str | None = None,
        expected_sequence: int | None = None,
        actual_sequence: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        # Keep transport vocabulary out of the strict persistence contract.
        # Connection lifecycle is a reconnect event, malformed frames are
        # decode errors, venue/handshake failures are HTTP errors, and a delta
        # without its snapshot is a sequence gap. The original event name is
        # retained in details for diagnosis.
        normalized_type = {
            "websocket_connected": "reconnect",
            "websocket_error": "reconnect",
            "websocket_http_error": "http_error",
            "websocket_decode_error": "decode_error",
            "websocket_venue_error": "http_error",
            "delta_before_snapshot": "sequence_gap",
        }.get(event_type, event_type)
        normalized_severity = {
            "error": "warning",
            "fatal": "critical",
        }.get(severity, severity)
        normalized_details = dict(details or {})
        normalized_details.setdefault("stream_event", event_type)
        try:
            self.ledger.record_data_quality_event(
                run_id=self.run_id_provider(),
                ticker=ticker,
                event_type=normalized_type,
                severity=normalized_severity,
                expected_sequence=expected_sequence,
                actual_sequence=actual_sequence,
                observed_at=datetime.now(timezone.utc),
                details=normalized_details,
            )
        except Exception as exc:  # noqa: BLE001 - telemetry cannot kill recovery
            logger.warning(
                "Could not persist Kalshi stream health event {}: {}",
                event_type,
                exc,
            )
