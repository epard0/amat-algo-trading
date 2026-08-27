"""Durable SQLite trade ledger for paper and live execution records."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any


CENT = Decimal("0.01")
CENTICENT = Decimal("0.0001")
KALSHI_QUADRATIC_TAKER_RATE = Decimal("0.07")
# v4 is an additive event/lifecycle contract. Existing v3 trade and periodic
# snapshot rows are deliberately not rewritten: money records retain the exact
# schema under which they were produced, while data_runs.contract_version tells
# analysis which additional normalized event tables are available.
SCHEMA_VERSION = 4
CORE_ROW_SCHEMA_VERSION = 3


def _utc_iso(value: datetime | None = None) -> str:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat()


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _ceil_to(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_CEILING)


@dataclass(frozen=True)
class PaperFill:
    contracts: Decimal
    price: Decimal
    entry_cost: Decimal
    fees_paid: Decimal
    total_debit: Decimal


def calculate_paper_fill(
    contracts: Decimal,
    price: Decimal,
    fee_multiplier: Decimal = Decimal("1"),
) -> PaperFill:
    """Approximate one aggregate Kalshi taker fill, including fee rounding.

    Kalshi's quadratic taker fee is ``0.07 * C * P * (1-P)`` times the
    series multiplier, rounded up to $0.0001. The resulting balance debit is
    then rounded up to the next cent. The difference is recorded as total fees.
    """
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    if not Decimal("0") < price < Decimal("1"):
        raise ValueError("price must be between 0 and 1")
    if fee_multiplier < 0:
        raise ValueError("fee_multiplier cannot be negative")

    entry_cost = contracts * price
    raw_trade_fee = (
        KALSHI_QUADRATIC_TAKER_RATE
        * fee_multiplier
        * contracts
        * price
        * (Decimal("1") - price)
    )
    trade_fee = _ceil_to(raw_trade_fee, CENTICENT)
    total_debit = _ceil_to(entry_cost + trade_fee, CENT)
    return PaperFill(
        contracts=contracts,
        price=price,
        entry_cost=entry_cost.quantize(CENTICENT),
        fees_paid=(total_debit - entry_cost).quantize(CENTICENT),
        total_debit=total_debit,
    )


@dataclass(frozen=True)
class TradeRecord:
    trade_id: str
    mode: str
    environment: str
    strategy: str
    asset: str
    ticker: str
    event_ticker: str
    order_id: str
    client_order_id: str
    outcome: str
    status: str
    exchange_status: str
    market_result: str | None
    placed_at: datetime
    market_open_time: datetime
    market_close_time: datetime
    status_check_time: datetime
    last_status_check_at: datetime | None
    settled_at: datetime | None
    contracts: Decimal
    remaining_contracts: Decimal
    entry_price: Decimal
    entry_cost: Decimal
    fees_paid: Decimal
    total_debit: Decimal
    gross_payout: Decimal | None
    pnl: Decimal | None
    exit_price: Decimal | None
    exit_contracts: Decimal | None
    exit_fees: Decimal | None
    exited_at: datetime | None
    target_price: Decimal
    model_probability: Decimal
    model_edge: Decimal
    metadata: dict[str, Any]
    entry_sequence: int = 1
    schema_version: int = CORE_ROW_SCHEMA_VERSION


@dataclass(frozen=True)
class LedgerSummary:
    starting_balance: Decimal
    current_balance: Decimal
    available_balance: Decimal
    locked_capital: Decimal
    total_trades: int
    open_trades: int
    wins: int
    losses: int
    voids: int
    win_rate: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    total_fees: Decimal
    net_pnl: Decimal
    exits: int = 0


class TradeLedger:
    """SQLite-backed trade ledger that survives process restarts."""

    # Book deltas are high-rate analytical telemetry, not money-bearing state.
    # Keep synchronous=FULL, but group these rows so the asyncio thread is not
    # stalled by one filesystem journal flush per websocket message.
    BOOK_EVENT_COMMIT_SECONDS = 1.0
    BOOK_EVENT_COMMIT_COUNT = 500

    def __init__(self, path: Path, paper_starting_balance: Decimal) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._book_event_rows: list[tuple[Any, ...]] = []
        self._public_trade_rows: list[tuple[Any, ...]] = []
        self._book_events_committed_at = time.monotonic()
        self._closed = False
        self._create_schema()
        self._set_initial_paper_balance(paper_starting_balance)

    def close(self) -> None:
        if self._closed:
            return
        # Flush the final sub-second telemetry batch during graceful shutdown.
        self._flush_stream_telemetry()
        self.connection.commit()
        self.connection.close()
        self._closed = True

    def _create_schema(self) -> None:
        # Migration runs first. ``_create_schema_only`` declares indexes over
        # columns that only exist in v2, so building it against a live v1 table
        # fails on ``no such column: strategy``.
        self._migrate_v1_to_v2()
        self._migrate_v2_to_v3()
        self._migrate_v3_to_v4()
        self._create_schema_only()
        self._ensure_partial_exit_schema()
        self._ensure_position_evaluation_schema()
        self._ensure_capture_provenance_schema()

    def _create_schema_only(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ledger_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL DEFAULT 3
                    CHECK(schema_version = 3),
                mode TEXT NOT NULL CHECK(mode IN ('paper', 'live')),
                environment TEXT NOT NULL,
                strategy TEXT NOT NULL DEFAULT 'predictor',
                entry_sequence INTEGER NOT NULL DEFAULT 1
                    CHECK(entry_sequence BETWEEN 1 AND 4),
                asset TEXT NOT NULL,
                ticker TEXT NOT NULL,
                event_ticker TEXT NOT NULL,
                order_id TEXT NOT NULL,
                client_order_id TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('yes', 'no')),
                status TEXT NOT NULL
                    CHECK(status IN ('OPEN', 'WON', 'LOST', 'VOID', 'EXITED')),
                exchange_status TEXT NOT NULL DEFAULT '',
                market_result TEXT,
                placed_at TEXT NOT NULL,
                market_open_time TEXT NOT NULL,
                market_close_time TEXT NOT NULL,
                status_check_time TEXT NOT NULL,
                last_status_check_at TEXT,
                settled_at TEXT,
                contracts TEXT NOT NULL,
                remaining_contracts TEXT,
                entry_price TEXT NOT NULL,
                entry_cost TEXT NOT NULL,
                fees_paid TEXT NOT NULL,
                total_debit TEXT NOT NULL,
                gross_payout TEXT,
                pnl TEXT,
                exit_price TEXT,
                exit_contracts TEXT,
                exit_fees TEXT,
                exited_at TEXT,
                target_price TEXT NOT NULL,
                model_probability TEXT NOT NULL,
                model_edge TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
                    CHECK(json_valid(metadata_json)),
                CHECK(CAST(contracts AS REAL) > 0),
                CHECK(CAST(entry_price AS REAL) > 0 AND CAST(entry_price AS REAL) < 1),
                CHECK(CAST(entry_cost AS REAL) >= 0),
                CHECK(CAST(fees_paid AS REAL) >= 0),
                CHECK(CAST(total_debit AS REAL) > 0),
                CHECK(CAST(model_probability AS REAL) >= 0
                    AND CAST(model_probability AS REAL) <= 1)
            ) STRICT;

            CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_one_open_per_strategy
            ON trades(mode, environment, ticker, strategy)
            WHERE status = 'OPEN';

            CREATE INDEX IF NOT EXISTS idx_trades_open
            ON trades(mode, environment, status, market_close_time);

            CREATE INDEX IF NOT EXISTS idx_trades_strategy_open
            ON trades(mode, environment, strategy, status);

            CREATE INDEX IF NOT EXISTS idx_trades_settled
            ON trades(mode, environment, settled_at);

            CREATE TABLE IF NOT EXISTS trade_exit_fills (
                exit_fill_id TEXT PRIMARY KEY,
                trade_id TEXT NOT NULL REFERENCES trades(trade_id),
                client_order_id TEXT NOT NULL UNIQUE,
                filled_at TEXT NOT NULL,
                contracts TEXT NOT NULL CHECK(CAST(contracts AS REAL) > 0),
                price TEXT NOT NULL CHECK(CAST(price AS REAL) >= 0
                    AND CAST(price AS REAL) <= 1),
                fee TEXT NOT NULL CHECK(CAST(fee AS REAL) >= 0),
                proceeds TEXT NOT NULL,
                exchange_status TEXT NOT NULL,
                remaining_contracts TEXT NOT NULL
                    CHECK(CAST(remaining_contracts AS REAL) >= 0)
            ) STRICT;

            CREATE INDEX IF NOT EXISTS idx_trade_exit_fills_trade
            ON trade_exit_fills(trade_id, filled_at);

            CREATE TABLE IF NOT EXISTS data_runs (
                run_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL CHECK(schema_version = 3),
                contract_version INTEGER NOT NULL DEFAULT 4
                    CHECK(contract_version IN (3, 4)),
                mode TEXT NOT NULL CHECK(mode IN ('paper', 'live')),
                environment TEXT NOT NULL,
                strategy TEXT NOT NULL,
                assets_json TEXT NOT NULL CHECK(json_valid(assets_json)),
                source_revision TEXT NOT NULL DEFAULT 'unknown',
                config_hash TEXT NOT NULL DEFAULT 'unknown',
                config_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(config_json)),
                model_hash TEXT NOT NULL DEFAULT 'unknown',
                fee_model TEXT NOT NULL DEFAULT 'kalshi_quadratic_v1',
                fill_model TEXT NOT NULL DEFAULT 'unknown',
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL CHECK(status IN ('RUNNING', 'COMPLETED', 'ABORTED'))
            ) STRICT;

            CREATE TABLE IF NOT EXISTS market_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES data_runs(run_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 3),
                cycle_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                mode TEXT NOT NULL CHECK(mode IN ('paper', 'live')),
                environment TEXT NOT NULL,
                asset TEXT NOT NULL,
                ticker TEXT NOT NULL,
                event_ticker TEXT NOT NULL,
                exchange_index INTEGER NOT NULL DEFAULT 0 CHECK(exchange_index >= 0),
                market_open_time TEXT NOT NULL,
                market_close_time TEXT NOT NULL,
                seconds_to_close REAL NOT NULL,
                target_price TEXT NOT NULL CHECK(CAST(target_price AS REAL) > 0),
                yes_bid TEXT NOT NULL CHECK(CAST(yes_bid AS REAL) BETWEEN 0 AND 1),
                yes_ask TEXT NOT NULL CHECK(CAST(yes_ask AS REAL) BETWEEN 0 AND 1),
                yes_bid_size TEXT NOT NULL CHECK(CAST(yes_bid_size AS REAL) >= 0),
                yes_ask_size TEXT NOT NULL CHECK(CAST(yes_ask_size AS REAL) >= 0),
                fee_multiplier TEXT NOT NULL CHECK(CAST(fee_multiplier AS REAL) >= 0),
                spread TEXT NOT NULL CHECK(CAST(spread AS REAL) >= 0),
                implied_probability_yes TEXT NOT NULL
                    CHECK(CAST(implied_probability_yes AS REAL) BETWEEN 0 AND 1),
                microprice TEXT,
                imbalance TEXT,
                toxicity TEXT,
                book_source TEXT NOT NULL DEFAULT 'discovery'
                    CHECK(book_source IN ('discovery', 'rest', 'websocket')),
                book_sequence INTEGER,
                book_age_ms REAL NOT NULL DEFAULT 0 CHECK(book_age_ms >= 0),
                levels_yes INTEGER NOT NULL DEFAULT 0 CHECK(levels_yes >= 0),
                levels_no INTEGER NOT NULL DEFAULT 0 CHECK(levels_no >= 0),
                UNIQUE(run_id, cycle_id, ticker)
            ) STRICT;

            CREATE INDEX IF NOT EXISTS idx_market_snapshots_ticker_time
            ON market_snapshots(ticker, observed_at);

            CREATE TABLE IF NOT EXISTS orderbook_levels (
                snapshot_id TEXT NOT NULL
                    REFERENCES market_snapshots(snapshot_id) ON DELETE CASCADE,
                outcome_side TEXT NOT NULL CHECK(outcome_side IN ('yes', 'no')),
                level_index INTEGER NOT NULL CHECK(level_index >= 0),
                price TEXT NOT NULL CHECK(CAST(price AS REAL) > 0
                    AND CAST(price AS REAL) < 1),
                quantity TEXT NOT NULL CHECK(CAST(quantity AS REAL) > 0),
                PRIMARY KEY(snapshot_id, outcome_side, level_index)
            ) STRICT;

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                portfolio_snapshot_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES data_runs(run_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 3),
                observed_at TEXT NOT NULL,
                mode TEXT NOT NULL CHECK(mode IN ('paper', 'live')),
                environment TEXT NOT NULL,
                current_balance TEXT NOT NULL CHECK(CAST(current_balance AS REAL) >= 0),
                available_balance TEXT NOT NULL CHECK(CAST(available_balance AS REAL) >= 0),
                locked_capital TEXT NOT NULL CHECK(CAST(locked_capital AS REAL) >= 0),
                realized_pnl TEXT NOT NULL,
                open_positions INTEGER NOT NULL CHECK(open_positions >= 0)
            ) STRICT;

            CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_run_time
            ON portfolio_snapshots(run_id, observed_at);

            CREATE TABLE IF NOT EXISTS pair_arb_observations (
                observation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES data_runs(run_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 3),
                cycle_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                asset TEXT NOT NULL,
                ticker TEXT NOT NULL,
                yes_ask TEXT NOT NULL CHECK(CAST(yes_ask AS REAL) BETWEEN 0 AND 1),
                no_ask TEXT NOT NULL CHECK(CAST(no_ask AS REAL) BETWEEN 0 AND 1),
                pair_cost TEXT NOT NULL CHECK(CAST(pair_cost AS REAL) >= 0),
                guaranteed_payout TEXT NOT NULL
                    CHECK(CAST(guaranteed_payout AS REAL) = 1),
                gross_edge TEXT NOT NULL,
                yes_fee TEXT NOT NULL CHECK(CAST(yes_fee AS REAL) >= 0),
                no_fee TEXT NOT NULL CHECK(CAST(no_fee AS REAL) >= 0),
                net_edge TEXT NOT NULL,
                yes_depth TEXT NOT NULL CHECK(CAST(yes_depth AS REAL) >= 0),
                no_depth TEXT NOT NULL CHECK(CAST(no_depth AS REAL) >= 0),
                equal_contracts TEXT NOT NULL CHECK(CAST(equal_contracts AS REAL) >= 0),
                approved INTEGER NOT NULL CHECK(approved IN (0, 1)),
                simulated INTEGER NOT NULL CHECK(simulated IN (0, 1)),
                gate TEXT NOT NULL,
                reason TEXT NOT NULL,
                modeled_profit TEXT NOT NULL,
                execution_assumption TEXT NOT NULL,
                UNIQUE(run_id, cycle_id, ticker)
            ) STRICT;

            CREATE INDEX IF NOT EXISTS idx_pair_arb_run_time
            ON pair_arb_observations(run_id, observed_at);

            CREATE TABLE IF NOT EXISTS book_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES data_runs(run_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 4),
                ticker TEXT NOT NULL,
                sequence INTEGER,
                event_type TEXT NOT NULL
                    CHECK(event_type IN ('snapshot', 'delta', 'recovery_snapshot')),
                outcome_side TEXT CHECK(outcome_side IN ('yes', 'no')),
                price TEXT CHECK(price IS NULL OR
                    (CAST(price AS REAL) > 0 AND CAST(price AS REAL) < 1)),
                quantity_delta TEXT,
                exchange_ts_ms INTEGER,
                received_at TEXT NOT NULL,
                received_monotonic_ns INTEGER NOT NULL CHECK(received_monotonic_ns >= 0),
                payload_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload_json)),
                CHECK((event_type = 'delta' AND outcome_side IS NOT NULL
                    AND price IS NOT NULL AND quantity_delta IS NOT NULL)
                    OR event_type IN ('snapshot', 'recovery_snapshot'))
            ) STRICT;

            CREATE INDEX IF NOT EXISTS idx_book_events_ticker_sequence
            ON book_events(run_id, ticker, sequence);

            CREATE TABLE IF NOT EXISTS public_trades (
                public_trade_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES data_runs(run_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 4),
                ticker TEXT NOT NULL,
                trade_id TEXT,
                yes_price TEXT NOT NULL CHECK(CAST(yes_price AS REAL) BETWEEN 0 AND 1),
                contracts TEXT NOT NULL CHECK(CAST(contracts AS REAL) > 0),
                taker_side TEXT CHECK(taker_side IN ('yes', 'no', 'unknown')),
                exchange_ts_ms INTEGER,
                received_at TEXT NOT NULL,
                UNIQUE(run_id, trade_id)
            ) STRICT;

            CREATE TABLE IF NOT EXISTS signal_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES data_runs(run_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 4),
                cycle_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                strategy TEXT NOT NULL,
                evaluated_at TEXT NOT NULL,
                model_probability TEXT NOT NULL
                    CHECK(CAST(model_probability AS REAL) BETWEEN 0 AND 1),
                market_probability TEXT NOT NULL
                    CHECK(CAST(market_probability AS REAL) BETWEEN 0 AND 1),
                executable_price TEXT,
                net_edge TEXT,
                outcome TEXT CHECK(outcome IN ('yes', 'no')),
                verdict TEXT NOT NULL CHECK(verdict IN ('approved', 'blocked', 'observed')),
                gate TEXT NOT NULL,
                features_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(features_json)),
                UNIQUE(run_id, cycle_id, ticker, strategy)
            ) STRICT;

            CREATE TABLE IF NOT EXISTS orders (
                order_record_id TEXT PRIMARY KEY,
                run_id TEXT REFERENCES data_runs(run_id),
                trade_id TEXT REFERENCES trades(trade_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 4),
                client_order_id TEXT NOT NULL,
                venue_order_id TEXT,
                ticker TEXT NOT NULL,
                strategy TEXT NOT NULL,
                intent TEXT NOT NULL CHECK(intent IN ('entry', 'exit', 'maker', 'cancel')),
                book_side TEXT NOT NULL CHECK(book_side IN ('bid', 'ask')),
                outcome TEXT NOT NULL CHECK(outcome IN ('yes', 'no')),
                order_type TEXT NOT NULL CHECK(order_type IN ('ioc', 'fok', 'gtc')),
                limit_price TEXT NOT NULL
                    CHECK(CAST(limit_price AS REAL) > 0 AND CAST(limit_price AS REAL) < 1),
                requested_contracts TEXT NOT NULL CHECK(CAST(requested_contracts AS REAL) > 0),
                reduce_only INTEGER NOT NULL CHECK(reduce_only IN (0, 1)),
                post_only INTEGER NOT NULL CHECK(post_only IN (0, 1)),
                submitted_at TEXT NOT NULL,
                submitted_monotonic_ns INTEGER NOT NULL CHECK(submitted_monotonic_ns >= 0),
                UNIQUE(client_order_id)
            ) STRICT;

            CREATE INDEX IF NOT EXISTS idx_orders_ticker_time
            ON orders(ticker, submitted_at);

            CREATE TABLE IF NOT EXISTS order_transitions (
                transition_id TEXT PRIMARY KEY,
                order_record_id TEXT NOT NULL REFERENCES orders(order_record_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 4),
                state TEXT NOT NULL CHECK(state IN (
                    'submitted', 'acknowledged', 'resting', 'partial', 'filled',
                    'canceled', 'expired', 'rejected', 'unknown'
                )),
                venue_order_id TEXT,
                fill_count TEXT NOT NULL DEFAULT '0' CHECK(CAST(fill_count AS REAL) >= 0),
                remaining_count TEXT NOT NULL CHECK(CAST(remaining_count AS REAL) >= 0),
                error_code TEXT,
                error_message TEXT,
                exchange_ts_ms INTEGER,
                observed_at TEXT NOT NULL,
                observed_monotonic_ns INTEGER NOT NULL CHECK(observed_monotonic_ns >= 0)
            ) STRICT;

            CREATE INDEX IF NOT EXISTS idx_order_transitions_order_time
            ON order_transitions(order_record_id, observed_at);

            CREATE TABLE IF NOT EXISTS fills (
                fill_record_id TEXT PRIMARY KEY,
                order_record_id TEXT NOT NULL REFERENCES orders(order_record_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 4),
                venue_fill_id TEXT,
                price TEXT NOT NULL CHECK(CAST(price AS REAL) BETWEEN 0 AND 1),
                contracts TEXT NOT NULL CHECK(CAST(contracts AS REAL) > 0),
                liquidity_role TEXT NOT NULL CHECK(liquidity_role IN ('maker', 'taker', 'unknown')),
                fee TEXT NOT NULL CHECK(CAST(fee AS REAL) >= 0),
                exchange_ts_ms INTEGER,
                received_at TEXT NOT NULL,
                UNIQUE(order_record_id, venue_fill_id)
            ) STRICT;

            CREATE TABLE IF NOT EXISTS position_evaluations (
                position_evaluation_id TEXT PRIMARY KEY,
                run_id TEXT REFERENCES data_runs(run_id),
                trade_id TEXT NOT NULL REFERENCES trades(trade_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 4),
                evaluated_at TEXT NOT NULL,
                trigger TEXT NOT NULL CHECK(trigger IN ('book', 'trade', 'timer', 'recovery')),
                executable_exit_price TEXT,
                executable_depth TEXT NOT NULL DEFAULT '0'
                    CHECK(CAST(executable_depth AS REAL) >= 0),
                estimated_exit_fee TEXT NOT NULL DEFAULT '0'
                    CHECK(CAST(estimated_exit_fee AS REAL) >= 0),
                net_executable_pnl TEXT,
                model_probability TEXT,
                required_gain_per_contract TEXT,
                maximum_settlement_gain_per_contract TEXT,
                spread TEXT,
                seconds_to_close REAL NOT NULL,
                verdict TEXT NOT NULL CHECK(verdict IN ('hold', 'exit', 'blocked')),
                gate TEXT NOT NULL
            ) STRICT;

            CREATE TABLE IF NOT EXISTS exit_counterfactuals (
                trade_id TEXT PRIMARY KEY REFERENCES trades(trade_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 4),
                market_result TEXT NOT NULL CHECK(market_result IN ('yes','no','void')),
                settled_at TEXT NOT NULL,
                realized_exit_pnl TEXT NOT NULL,
                hold_payout TEXT NOT NULL CHECK(CAST(hold_payout AS REAL) >= 0),
                hold_pnl TEXT NOT NULL,
                delta_hold_minus_exit TEXT NOT NULL
            ) STRICT;

            CREATE TABLE IF NOT EXISTS probability_calibrations (
                trade_id TEXT PRIMARY KEY REFERENCES trades(trade_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 4),
                asset TEXT NOT NULL,
                ticker TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('yes','no')),
                time_bucket TEXT NOT NULL CHECK(time_bucket IN (
                    '0_30','31_60','61_120','121_180','181_plus'
                )),
                seconds_to_close REAL NOT NULL CHECK(seconds_to_close >= 0),
                model_probability_yes TEXT NOT NULL
                    CHECK(CAST(model_probability_yes AS REAL) BETWEEN 0 AND 1),
                raw_probability_yes TEXT,
                market_probability_yes TEXT,
                distance_z TEXT,
                regime TEXT,
                actual_yes INTEGER CHECK(actual_yes IN (0,1)),
                brier_model REAL,
                brier_raw REAL,
                brier_market REAL,
                labeled_at TEXT NOT NULL
            ) STRICT;

            CREATE INDEX IF NOT EXISTS idx_probability_calibration_bucket
            ON probability_calibrations(asset, time_bucket);

            CREATE TABLE IF NOT EXISTS data_quality_events (
                quality_event_id TEXT PRIMARY KEY,
                run_id TEXT REFERENCES data_runs(run_id),
                schema_version INTEGER NOT NULL CHECK(schema_version = 4),
                ticker TEXT,
                event_type TEXT NOT NULL CHECK(event_type IN (
                    'sequence_gap', 'stale_feed', 'reconnect', 'rest_mismatch',
                    'http_error', 'decode_error', 'clock_skew'
                )),
                severity TEXT NOT NULL CHECK(severity IN ('info', 'warning', 'critical')),
                expected_sequence INTEGER,
                actual_sequence INTEGER,
                observed_at TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(details_json))
            ) STRICT;

            CREATE TABLE IF NOT EXISTS persistent_risk_state (
                mode TEXT NOT NULL CHECK(mode IN ('paper', 'live')),
                environment TEXT NOT NULL,
                risk_date TEXT NOT NULL,
                schema_version INTEGER NOT NULL CHECK(schema_version = 4),
                starting_balance TEXT NOT NULL CHECK(CAST(starting_balance AS REAL) >= 0),
                peak_balance TEXT NOT NULL CHECK(CAST(peak_balance AS REAL) >= 0),
                current_balance TEXT NOT NULL CHECK(CAST(current_balance AS REAL) >= 0),
                realized_pnl TEXT NOT NULL,
                consecutive_losses INTEGER NOT NULL CHECK(consecutive_losses >= 0),
                halted INTEGER NOT NULL CHECK(halted IN (0, 1)),
                halt_reason TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(mode, environment, risk_date)
            ) STRICT;
            """
        )
        self.connection.execute(
            """
            INSERT INTO ledger_metadata(key, value) VALUES ('schema_version', '4')
            ON CONFLICT(key) DO UPDATE SET value = '4'
            """
        )
        self.connection.commit()

    def _ensure_partial_exit_schema(self) -> None:
        """Add durable residual-position accounting to existing v4 ledgers."""
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(trades)").fetchall()
        }
        if columns and "remaining_contracts" not in columns:
            with self.connection:
                self.connection.execute(
                    "ALTER TABLE trades ADD COLUMN remaining_contracts TEXT"
                )
                self.connection.execute(
                    "UPDATE trades SET remaining_contracts = contracts "
                    "WHERE remaining_contracts IS NULL"
                )
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS trade_exit_fills (
                    exit_fill_id TEXT PRIMARY KEY,
                    trade_id TEXT NOT NULL REFERENCES trades(trade_id),
                    client_order_id TEXT NOT NULL UNIQUE,
                    filled_at TEXT NOT NULL,
                    contracts TEXT NOT NULL CHECK(CAST(contracts AS REAL) > 0),
                    price TEXT NOT NULL CHECK(CAST(price AS REAL) >= 0
                        AND CAST(price AS REAL) <= 1),
                    fee TEXT NOT NULL CHECK(CAST(fee AS REAL) >= 0),
                    proceeds TEXT NOT NULL,
                    exchange_status TEXT NOT NULL,
                    remaining_contracts TEXT NOT NULL
                        CHECK(CAST(remaining_contracts AS REAL) >= 0)
                ) STRICT;
                CREATE INDEX IF NOT EXISTS idx_trade_exit_fills_trade
                ON trade_exit_fills(trade_id, filled_at);
                """
            )

    def _ensure_position_evaluation_schema(self) -> None:
        """Add the inputs behind the adaptive exit verdict to existing v4 ledgers."""
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(position_evaluations)"
            ).fetchall()
        }
        additions = {
            "model_probability": "TEXT",
            "required_gain_per_contract": "TEXT",
            "maximum_settlement_gain_per_contract": "TEXT",
        }
        with self.connection:
            for name, definition in additions.items():
                if columns and name not in columns:
                    self.connection.execute(
                        f"ALTER TABLE position_evaluations ADD COLUMN {name} {definition}"
                    )

    def _ensure_capture_provenance_schema(self) -> None:
        """Add attributable run and canonical-book fields to existing ledgers."""
        run_columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(data_runs)"
            ).fetchall()
        }
        snapshot_columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(market_snapshots)"
            ).fetchall()
        }
        with self.connection:
            if run_columns and "config_json" not in run_columns:
                self.connection.execute(
                    "ALTER TABLE data_runs ADD COLUMN config_json TEXT "
                    "NOT NULL DEFAULT '{}' CHECK(json_valid(config_json))"
                )
            additions = {
                "recorded_at": "TEXT",
                "book_source": (
                    "TEXT NOT NULL DEFAULT 'discovery' CHECK(book_source IN "
                    "('discovery', 'rest', 'websocket'))"
                ),
                "book_sequence": "INTEGER",
                "book_age_ms": "REAL NOT NULL DEFAULT 0 CHECK(book_age_ms >= 0)",
                "exchange_index": (
                    "INTEGER NOT NULL DEFAULT 0 CHECK(exchange_index >= 0)"
                ),
            }
            for name, definition in additions.items():
                if snapshot_columns and name not in snapshot_columns:
                    self.connection.execute(
                        f"ALTER TABLE market_snapshots ADD COLUMN {name} {definition}"
                    )

    def _migrate_v3_to_v4(self) -> None:
        """Add the v4 run provenance columns without rewriting v3 money rows."""
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(data_runs)"
            ).fetchall()
        }
        if not columns or "contract_version" in columns:
            return
        additions = (
            "contract_version INTEGER NOT NULL DEFAULT 3 CHECK(contract_version IN (3, 4))",
            "source_revision TEXT NOT NULL DEFAULT 'legacy'",
            "config_hash TEXT NOT NULL DEFAULT 'legacy'",
            "model_hash TEXT NOT NULL DEFAULT 'legacy'",
            "fee_model TEXT NOT NULL DEFAULT 'kalshi_quadratic_v1'",
            "fill_model TEXT NOT NULL DEFAULT 'legacy_snapshot_v3'",
        )
        with self.connection:
            for definition in additions:
                self.connection.execute(
                    f"ALTER TABLE data_runs ADD COLUMN {definition}"
                )

    def _migrate_v2_to_v3(self) -> None:
        """Permit bounded sequential legs and add strict row constraints."""
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(trades)").fetchall()
        }
        if not columns or "entry_sequence" in columns:
            return
        legacy = ", ".join(
            name
            for name in (
                "trade_id",
                "mode",
                "environment",
                "strategy",
                "asset",
                "ticker",
                "event_ticker",
                "order_id",
                "client_order_id",
                "outcome",
                "status",
                "exchange_status",
                "market_result",
                "placed_at",
                "market_open_time",
                "market_close_time",
                "status_check_time",
                "last_status_check_at",
                "settled_at",
                "contracts",
                "entry_price",
                "entry_cost",
                "fees_paid",
                "total_debit",
                "gross_payout",
                "pnl",
                "exit_price",
                "exit_contracts",
                "exit_fees",
                "exited_at",
                "target_price",
                "model_probability",
                "model_edge",
                "metadata_json",
            )
            if name in columns
        )
        with self.connection:
            self.connection.execute("ALTER TABLE trades RENAME TO trades_v2")
            for index in (
                "idx_trades_open",
                "idx_trades_strategy_open",
                "idx_trades_settled",
                "idx_trades_one_open",
                "idx_trades_one_open_per_strategy",
            ):
                self.connection.execute(f"DROP INDEX IF EXISTS {index}")
            self._create_schema_only()
            self.connection.execute(
                f"INSERT INTO trades ({legacy}) SELECT {legacy} FROM trades_v2"
            )
            self.connection.execute("DROP TABLE trades_v2")
        integrity = self.connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(
                f"Ledger integrity check failed after v3 migration: {integrity}"
            )

    def _migrate_v1_to_v2(self) -> None:
        """Rebuild a v1 ``trades`` table in place, preserving every row.

        SQLite cannot drop a constraint, so widening ``status``, adding the
        ``strategy`` and exit columns, replacing ``UNIQUE(mode, environment,
        ticker)`` and removing ``idx_trades_one_open`` all require a table
        rebuild. It runs inside one transaction: a failure leaves v1 intact
        rather than a half-migrated ledger.

        There is deliberately no ``except`` here. A migration that quietly
        swallowed an error would report success over money records it had just
        mangled, and the caller's ability to see the failure is the only thing
        separating a migrated ledger from a lost one.
        """
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(trades)").fetchall()
        }
        if not columns or "strategy" in columns:
            return

        legacy = ", ".join(
            name
            for name in (
                "trade_id",
                "mode",
                "environment",
                "asset",
                "ticker",
                "event_ticker",
                "order_id",
                "client_order_id",
                "outcome",
                "status",
                "exchange_status",
                "market_result",
                "placed_at",
                "market_open_time",
                "market_close_time",
                "status_check_time",
                "last_status_check_at",
                "settled_at",
                "contracts",
                "entry_price",
                "entry_cost",
                "fees_paid",
                "total_debit",
                "gross_payout",
                "pnl",
                "target_price",
                "model_probability",
                "model_edge",
                "metadata_json",
            )
        )
        with self.connection:
            self.connection.execute("ALTER TABLE trades RENAME TO trades_v1")
            self.connection.execute("DROP INDEX IF EXISTS idx_trades_one_open")
            self.connection.execute("DROP INDEX IF EXISTS idx_trades_open")
            self.connection.execute("DROP INDEX IF EXISTS idx_trades_settled")
            self._create_schema_only()
            self.connection.execute(
                f"INSERT INTO trades ({legacy}) SELECT {legacy} FROM trades_v1"
            )
            self.connection.execute("DROP TABLE trades_v1")
            self.connection.execute(
                """
                INSERT INTO ledger_metadata(key, value) VALUES ('schema_version', '2')
                ON CONFLICT(key) DO UPDATE SET value = '2'
                """
            )
        integrity = self.connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(
                f"Ledger integrity check failed after migration: {integrity}"
            )

    def _set_initial_paper_balance(self, balance: Decimal) -> None:
        if balance <= 0:
            raise ValueError("paper starting balance must be positive")
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO ledger_metadata(key, value)
                VALUES ('paper_starting_balance', ?)
                """,
                (str(balance),),
            )

    def start_data_run(
        self,
        *,
        mode: str,
        environment: str,
        strategy: str,
        assets: tuple[str, ...],
        started_at: datetime | None = None,
        source_revision: str = "unknown",
        config_hash: str = "unknown",
        config_json: str = "{}",
        model_hash: str = "unknown",
        fee_model: str = "kalshi_quadratic_v1",
        fill_model: str = "rest_snapshot_taker_v2",
    ) -> str:
        """Open one normalized collection run and return its stable id."""
        run_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO data_runs(
                    run_id, schema_version, contract_version, mode, environment,
                    strategy, assets_json, source_revision, config_hash,
                    config_json, model_hash, fee_model, fill_model, started_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING')
                """,
                (
                    run_id,
                    CORE_ROW_SCHEMA_VERSION,
                    SCHEMA_VERSION,
                    mode,
                    environment,
                    strategy,
                    json.dumps(list(assets), separators=(",", ":")),
                    source_revision,
                    config_hash,
                    config_json,
                    model_hash,
                    fee_model,
                    fill_model,
                    _utc_iso(started_at),
                ),
            )
        return run_id

    def finish_data_run(
        self,
        run_id: str,
        *,
        completed: bool,
        ended_at: datetime | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE data_runs SET ended_at = ?, status = ?
                WHERE run_id = ? AND status = 'RUNNING'
                """,
                (_utc_iso(ended_at), "COMPLETED" if completed else "ABORTED", run_id),
            )

    def record_market_snapshot(
        self,
        *,
        run_id: str,
        cycle_id: str,
        mode: str,
        environment: str,
        market: Any,
        observed_at: datetime,
        recorded_at: datetime | None = None,
        yes_levels: list[tuple[Decimal, Decimal]] | None = None,
        no_levels: list[tuple[Decimal, Decimal]] | None = None,
        analysis: Any = None,
        book_source: str = "discovery",
        book_sequence: int | None = None,
        book_age_ms: float = 0.0,
        _commit: bool = True,
    ) -> str:
        """Record one strict market row and its optional normalized depth."""
        if _commit:
            with self.connection:
                return self.record_market_snapshot(
                    run_id=run_id,
                    cycle_id=cycle_id,
                    mode=mode,
                    environment=environment,
                    market=market,
                    observed_at=observed_at,
                    recorded_at=recorded_at,
                    yes_levels=yes_levels,
                    no_levels=no_levels,
                    analysis=analysis,
                    book_source=book_source,
                    book_sequence=book_sequence,
                    book_age_ms=book_age_ms,
                    _commit=False,
                )
        snapshot_id = str(uuid.uuid4())
        seconds_to_close = (market.close_time - observed_at).total_seconds()
        spread = max(Decimal("0"), market.yes_ask - market.yes_bid)
        implied = (market.yes_bid + market.yes_ask) / Decimal("2")
        yes_levels = yes_levels or []
        no_levels = no_levels or []
        self.connection.execute(
            """
                INSERT INTO market_snapshots(
                    snapshot_id, run_id, schema_version, cycle_id, observed_at,
                    recorded_at, mode, environment, asset, ticker, event_ticker,
                    exchange_index, market_open_time, market_close_time, seconds_to_close,
                    target_price, yes_bid, yes_ask, yes_bid_size, yes_ask_size,
                    fee_multiplier, spread, implied_probability_yes,
                    microprice, imbalance, toxicity, book_source, book_sequence,
                    book_age_ms, levels_yes, levels_no
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
            (
                snapshot_id,
                run_id,
                CORE_ROW_SCHEMA_VERSION,
                cycle_id,
                _utc_iso(observed_at),
                _utc_iso(recorded_at),
                mode,
                environment,
                market.asset,
                market.ticker,
                market.event_ticker,
                int(getattr(market, "exchange_index", 0) or 0),
                _utc_iso(market.open_time),
                _utc_iso(market.close_time),
                seconds_to_close,
                str(market.target_price),
                str(market.yes_bid),
                str(market.yes_ask),
                str(market.yes_bid_size),
                str(market.yes_ask_size),
                str(market.fee_multiplier),
                str(spread),
                str(implied),
                str(analysis.microprice) if analysis is not None else None,
                str(analysis.imbalance) if analysis is not None else None,
                str(analysis.toxicity) if analysis is not None else None,
                book_source,
                book_sequence,
                max(0.0, float(book_age_ms)),
                len(yes_levels),
                len(no_levels),
            ),
        )
        for side, levels in (("yes", yes_levels), ("no", no_levels)):
            self.connection.executemany(
                """
                    INSERT INTO orderbook_levels(
                        snapshot_id, outcome_side, level_index, price, quantity
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                [
                    (snapshot_id, side, index, str(price), str(quantity))
                    for index, (price, quantity) in enumerate(levels)
                ],
            )
        return snapshot_id

    def record_market_snapshots(self, records: list[dict[str, Any]]) -> list[str]:
        """Persist one scan's normalized books in a single durable commit."""
        with self.connection:
            return [
                self.record_market_snapshot(**record, _commit=False)
                for record in records
            ]

    def record_portfolio_snapshot(
        self,
        *,
        run_id: str,
        mode: str,
        environment: str,
        current_balance: Decimal,
        available_balance: Decimal,
        locked_capital: Decimal,
        realized_pnl: Decimal,
        open_positions: int,
        observed_at: datetime | None = None,
    ) -> str:
        snapshot_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO portfolio_snapshots(
                    portfolio_snapshot_id, run_id, schema_version, observed_at,
                    mode, environment, current_balance, available_balance,
                    locked_capital, realized_pnl, open_positions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    run_id,
                    CORE_ROW_SCHEMA_VERSION,
                    _utc_iso(observed_at),
                    mode,
                    environment,
                    str(current_balance),
                    str(available_balance),
                    str(locked_capital),
                    str(realized_pnl),
                    open_positions,
                ),
            )
        return snapshot_id

    def record_pair_arb_observation(
        self,
        *,
        run_id: str,
        cycle_id: str,
        market: Any,
        observed_at: datetime,
        assessment: Any,
        simulated: bool,
    ) -> str:
        """Record one strict, equal-quantity Kalshi pair-arb verdict."""
        observation_id = str(uuid.uuid4())
        modeled_profit = assessment.modeled_profit if simulated else Decimal("0")
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO pair_arb_observations(
                    observation_id, run_id, schema_version, cycle_id, observed_at,
                    asset, ticker, yes_ask, no_ask, pair_cost,
                    guaranteed_payout, gross_edge, yes_fee, no_fee, net_edge,
                    yes_depth, no_depth, equal_contracts, approved, simulated,
                    gate, reason, modeled_profit, execution_assumption
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                """,
                (
                    observation_id,
                    run_id,
                    CORE_ROW_SCHEMA_VERSION,
                    cycle_id,
                    _utc_iso(observed_at),
                    market.asset,
                    market.ticker,
                    str(assessment.yes_ask),
                    str(assessment.no_ask),
                    str(assessment.pair_cost),
                    str(assessment.guaranteed_payout),
                    str(assessment.gross_edge),
                    str(assessment.yes_fee),
                    str(assessment.no_fee),
                    str(assessment.net_edge),
                    str(assessment.yes_depth),
                    str(assessment.no_depth),
                    str(assessment.contracts),
                    int(assessment.approved),
                    int(simulated),
                    assessment.gate,
                    assessment.reason,
                    str(modeled_profit),
                    "same_snapshot_equal_depth_non_atomic_shadow",
                ),
            )
        return observation_id

    # ----------------------------------------------------- v4 event contract --

    def record_book_event(
        self,
        *,
        run_id: str,
        ticker: str,
        event_type: str,
        received_at: datetime | None = None,
        received_monotonic_ns: int | None = None,
        sequence: int | None = None,
        outcome_side: str | None = None,
        price: Decimal | None = None,
        quantity_delta: Decimal | None = None,
        exchange_ts_ms: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        self._book_event_rows.append(
            (
                event_id,
                run_id,
                ticker,
                sequence,
                event_type,
                outcome_side,
                str(price) if price is not None else None,
                str(quantity_delta) if quantity_delta is not None else None,
                exchange_ts_ms,
                _utc_iso(received_at),
                received_monotonic_ns
                if received_monotonic_ns is not None
                else time.monotonic_ns(),
                json.dumps(payload or {}, separators=(",", ":"), default=str),
            )
        )
        now = time.monotonic()
        if (
            len(self._book_event_rows) + len(self._public_trade_rows)
            >= self.BOOK_EVENT_COMMIT_COUNT
            or now - self._book_events_committed_at >= self.BOOK_EVENT_COMMIT_SECONDS
        ):
            self._flush_stream_telemetry(now=now)
        return event_id

    def _flush_book_events(self, *, now: float | None = None) -> None:
        """Compatibility wrapper for callers/tests that flush book telemetry."""
        self._flush_stream_telemetry(now=now)

    def _flush_stream_telemetry(self, *, now: float | None = None) -> None:
        """Commit websocket analytics in one batch instead of per message.

        Public trades used to open and durably commit their own transaction for
        every print. BTC alone produced tens of writes per second; combined
        with raw deltas this stalled the event loop long enough for authenticated
        order headers to expire. Both optional streams now share one batch.
        """
        if not self._book_event_rows and not self._public_trade_rows:
            return
        book_rows = self._book_event_rows
        trade_rows = self._public_trade_rows
        self._book_event_rows = []
        self._public_trade_rows = []
        try:
            with self.connection:
                if book_rows:
                    self.connection.executemany(
                        """
                        INSERT INTO book_events(
                            event_id, run_id, schema_version, ticker, sequence,
                            event_type, outcome_side, price, quantity_delta,
                            exchange_ts_ms, received_at, received_monotonic_ns,
                            payload_json
                        ) VALUES (?, ?, 4, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        book_rows,
                    )
                if trade_rows:
                    self.connection.executemany(
                        """
                        INSERT OR IGNORE INTO public_trades(
                            public_trade_id, run_id, schema_version, ticker,
                            trade_id, yes_price, contracts, taker_side,
                            exchange_ts_ms, received_at
                        ) VALUES (?, ?, 4, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        trade_rows,
                    )
        except Exception:
            self._book_event_rows = book_rows + self._book_event_rows
            self._public_trade_rows = trade_rows + self._public_trade_rows
            raise
        self._book_events_committed_at = now or time.monotonic()

    def record_public_trade(
        self,
        *,
        run_id: str,
        ticker: str,
        yes_price: Decimal,
        contracts: Decimal,
        trade_id: str | None = None,
        taker_side: str = "unknown",
        exchange_ts_ms: int | None = None,
        received_at: datetime | None = None,
    ) -> str:
        record_id = str(uuid.uuid4())
        self._public_trade_rows.append(
            (
                record_id,
                run_id,
                ticker,
                trade_id,
                str(yes_price),
                str(contracts),
                taker_side,
                exchange_ts_ms,
                _utc_iso(received_at),
            )
        )
        now = time.monotonic()
        if (
            len(self._book_event_rows) + len(self._public_trade_rows)
            >= self.BOOK_EVENT_COMMIT_COUNT
            or now - self._book_events_committed_at >= self.BOOK_EVENT_COMMIT_SECONDS
        ):
            self._flush_stream_telemetry(now=now)
        return record_id

    def record_signal_evaluation(
        self,
        *,
        run_id: str,
        cycle_id: str,
        ticker: str,
        strategy: str,
        model_probability: Decimal,
        market_probability: Decimal,
        verdict: str,
        gate: str,
        outcome: str | None = None,
        executable_price: Decimal | None = None,
        net_edge: Decimal | None = None,
        features: dict[str, Any] | None = None,
        evaluated_at: datetime | None = None,
    ) -> str:
        evaluation_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO signal_evaluations(
                    evaluation_id, run_id, schema_version, cycle_id, ticker,
                    strategy, evaluated_at, model_probability,
                    market_probability, executable_price, net_edge, outcome,
                    verdict, gate, features_json
                ) VALUES (?, ?, 4, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation_id,
                    run_id,
                    cycle_id,
                    ticker,
                    strategy,
                    _utc_iso(evaluated_at),
                    str(model_probability),
                    str(market_probability),
                    str(executable_price) if executable_price is not None else None,
                    str(net_edge) if net_edge is not None else None,
                    outcome,
                    verdict,
                    gate,
                    json.dumps(features or {}, separators=(",", ":"), default=str),
                ),
            )
        return evaluation_id

    def record_order(
        self,
        *,
        client_order_id: str,
        ticker: str,
        strategy: str,
        intent: str,
        book_side: str,
        outcome: str,
        order_type: str,
        limit_price: Decimal,
        requested_contracts: Decimal,
        reduce_only: bool,
        post_only: bool,
        run_id: str | None = None,
        trade_id: str | None = None,
        venue_order_id: str | None = None,
        submitted_at: datetime | None = None,
        submitted_monotonic_ns: int | None = None,
    ) -> str:
        # Preserve causal replay: every buffered book change the order could
        # have observed is durable before the order-intent row is committed.
        self._flush_book_events()
        record_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO orders(
                    order_record_id, run_id, trade_id, schema_version,
                    client_order_id, venue_order_id, ticker, strategy, intent,
                    book_side, outcome, order_type, limit_price,
                    requested_contracts, reduce_only, post_only, submitted_at,
                    submitted_monotonic_ns
                ) VALUES (?, ?, ?, 4, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    run_id,
                    trade_id,
                    client_order_id,
                    venue_order_id,
                    ticker,
                    strategy,
                    intent,
                    book_side,
                    outcome,
                    order_type,
                    str(limit_price),
                    str(requested_contracts),
                    int(reduce_only),
                    int(post_only),
                    _utc_iso(submitted_at),
                    submitted_monotonic_ns
                    if submitted_monotonic_ns is not None
                    else time.monotonic_ns(),
                ),
            )
        return record_id

    def record_order_transition(
        self,
        *,
        order_record_id: str,
        state: str,
        remaining_count: Decimal,
        fill_count: Decimal = Decimal("0"),
        venue_order_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        exchange_ts_ms: int | None = None,
        observed_at: datetime | None = None,
        observed_monotonic_ns: int | None = None,
    ) -> str:
        transition_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO order_transitions(
                    transition_id, order_record_id, schema_version, state,
                    venue_order_id, fill_count, remaining_count, error_code,
                    error_message, exchange_ts_ms, observed_at,
                    observed_monotonic_ns
                ) VALUES (?, ?, 4, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transition_id,
                    order_record_id,
                    state,
                    venue_order_id,
                    str(fill_count),
                    str(remaining_count),
                    error_code,
                    error_message,
                    exchange_ts_ms,
                    _utc_iso(observed_at),
                    observed_monotonic_ns
                    if observed_monotonic_ns is not None
                    else time.monotonic_ns(),
                ),
            )
        return transition_id

    def record_fill(
        self,
        *,
        order_record_id: str,
        price: Decimal,
        contracts: Decimal,
        fee: Decimal,
        liquidity_role: str = "unknown",
        venue_fill_id: str | None = None,
        exchange_ts_ms: int | None = None,
        received_at: datetime | None = None,
    ) -> str:
        fill_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO fills(
                    fill_record_id, order_record_id, schema_version,
                    venue_fill_id, price, contracts, liquidity_role, fee,
                    exchange_ts_ms, received_at
                ) VALUES (?, ?, 4, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill_id,
                    order_record_id,
                    venue_fill_id,
                    str(price),
                    str(contracts),
                    liquidity_role,
                    str(fee),
                    exchange_ts_ms,
                    _utc_iso(received_at),
                ),
            )
        return fill_id

    def record_position_evaluation(
        self,
        *,
        trade_id: str,
        trigger: str,
        executable_depth: Decimal,
        estimated_exit_fee: Decimal,
        seconds_to_close: float,
        verdict: str,
        gate: str,
        run_id: str | None = None,
        executable_exit_price: Decimal | None = None,
        net_executable_pnl: Decimal | None = None,
        model_probability: Decimal | None = None,
        required_gain_per_contract: Decimal | None = None,
        maximum_settlement_gain_per_contract: Decimal | None = None,
        spread: Decimal | None = None,
        evaluated_at: datetime | None = None,
    ) -> str:
        record_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO position_evaluations(
                    position_evaluation_id, run_id, trade_id, schema_version,
                    evaluated_at, trigger, executable_exit_price,
                    executable_depth, estimated_exit_fee, net_executable_pnl,
                    model_probability, required_gain_per_contract,
                    maximum_settlement_gain_per_contract, spread,
                    seconds_to_close, verdict, gate
                ) VALUES (?, ?, ?, 4, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    run_id,
                    trade_id,
                    _utc_iso(evaluated_at),
                    trigger,
                    str(executable_exit_price)
                    if executable_exit_price is not None
                    else None,
                    str(executable_depth),
                    str(estimated_exit_fee),
                    str(net_executable_pnl) if net_executable_pnl is not None else None,
                    str(model_probability) if model_probability is not None else None,
                    str(required_gain_per_contract)
                    if required_gain_per_contract is not None
                    else None,
                    str(maximum_settlement_gain_per_contract)
                    if maximum_settlement_gain_per_contract is not None
                    else None,
                    str(spread) if spread is not None else None,
                    seconds_to_close,
                    verdict,
                    gate,
                ),
            )
        return record_id

    @staticmethod
    def _calibration_bucket(seconds_to_close: float) -> str:
        if seconds_to_close <= 30:
            return "0_30"
        if seconds_to_close <= 60:
            return "31_60"
        if seconds_to_close <= 120:
            return "61_120"
        if seconds_to_close <= 180:
            return "121_180"
        return "181_plus"

    def record_probability_calibration(
        self,
        trade: TradeRecord,
        market_result: str,
        labeled_at: datetime | None = None,
    ) -> None:
        """Persist a settlement label for time/asset probability calibration."""
        result = market_result.lower()
        if result == "void":
            return
        if result not in {"yes", "no"}:
            raise ValueError("market_result must be yes, no, or void")
        model_yes = (
            trade.model_probability
            if trade.outcome == "yes"
            else Decimal("1") - trade.model_probability
        )
        prediction = trade.metadata.get("prediction", {})
        raw_value = prediction.get("raw_probability_yes")
        raw_yes = Decimal(str(raw_value)) if raw_value is not None else None
        outcome_market_value = trade.metadata.get("top_of_book_price")
        market_yes = None
        if outcome_market_value is not None:
            outcome_market = Decimal(str(outcome_market_value))
            market_yes = (
                outcome_market
                if trade.outcome == "yes"
                else Decimal("1") - outcome_market
            )
        distance_value = prediction.get("distance_z")
        seconds = max(
            0.0,
            (trade.market_close_time - trade.placed_at).total_seconds(),
        )
        actual_yes = int(result == "yes")

        def brier(probability: Decimal | None) -> float | None:
            if probability is None:
                return None
            return float((probability - Decimal(actual_yes)) ** 2)

        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO probability_calibrations(
                    trade_id,schema_version,asset,ticker,outcome,time_bucket,
                    seconds_to_close,model_probability_yes,raw_probability_yes,
                    market_probability_yes,distance_z,regime,actual_yes,
                    brier_model,brier_raw,brier_market,labeled_at
                ) VALUES (?,4,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade.trade_id,
                    trade.asset,
                    trade.ticker,
                    trade.outcome,
                    self._calibration_bucket(seconds),
                    seconds,
                    str(model_yes),
                    str(raw_yes) if raw_yes is not None else None,
                    str(market_yes) if market_yes is not None else None,
                    str(distance_value) if distance_value is not None else None,
                    str(trade.metadata.get("regime", "")),
                    actual_yes,
                    brier(model_yes),
                    brier(raw_yes),
                    brier(market_yes),
                    _utc_iso(labeled_at),
                ),
            )

    def record_exit_counterfactual(
        self,
        trade_id: str,
        market_result: str,
        settled_at: datetime | None = None,
    ) -> None:
        """Label a completed exit with what full settlement would have paid."""
        result = market_result.lower()
        if result not in {"yes", "no", "void"}:
            raise ValueError("market_result must be yes, no, or void")
        trade = self.get_trade(trade_id)
        if trade is None:
            raise KeyError(f"Unknown trade: {trade_id}")
        if trade.status != "EXITED":
            raise ValueError("exit counterfactuals require an EXITED trade")
        hold_payout = (
            trade.total_debit
            if result == "void"
            else (trade.contracts if result == trade.outcome else Decimal("0"))
        )
        hold_pnl = Decimal("0") if result == "void" else hold_payout - trade.total_debit
        realized = trade.pnl or Decimal("0")
        with self.connection:
            self.connection.execute(
                """INSERT OR IGNORE INTO exit_counterfactuals(
                    trade_id,schema_version,market_result,settled_at,
                    realized_exit_pnl,hold_payout,hold_pnl,delta_hold_minus_exit
                ) VALUES (?,4,?,?,?,?,?,?)""",
                (
                    trade_id,
                    result,
                    _utc_iso(settled_at),
                    str(realized),
                    str(hold_payout),
                    str(hold_pnl),
                    str(hold_pnl - realized),
                ),
            )
        self.record_probability_calibration(trade, result, settled_at)

    def record_data_quality_event(
        self,
        *,
        event_type: str,
        severity: str,
        run_id: str | None = None,
        ticker: str | None = None,
        expected_sequence: int | None = None,
        actual_sequence: int | None = None,
        details: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
    ) -> str:
        record_id = str(uuid.uuid4())
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO data_quality_events(
                    quality_event_id, run_id, schema_version, ticker,
                    event_type, severity, expected_sequence, actual_sequence,
                    observed_at, details_json
                ) VALUES (?, ?, 4, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    run_id,
                    ticker,
                    event_type,
                    severity,
                    expected_sequence,
                    actual_sequence,
                    _utc_iso(observed_at),
                    json.dumps(details or {}, separators=(",", ":"), default=str),
                ),
            )
        return record_id

    def upsert_risk_state(
        self,
        *,
        mode: str,
        environment: str,
        risk_date: str,
        starting_balance: Decimal,
        peak_balance: Decimal,
        current_balance: Decimal,
        realized_pnl: Decimal,
        consecutive_losses: int,
        halted: bool,
        halt_reason: str = "",
        updated_at: datetime | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO persistent_risk_state(
                    mode, environment, risk_date, schema_version,
                    starting_balance, peak_balance, current_balance,
                    realized_pnl, consecutive_losses, halted, halt_reason,
                    updated_at
                ) VALUES (?, ?, ?, 4, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mode, environment, risk_date) DO UPDATE SET
                    peak_balance = MAX(
                        CAST(persistent_risk_state.peak_balance AS REAL),
                        CAST(excluded.peak_balance AS REAL)
                    ),
                    current_balance = excluded.current_balance,
                    realized_pnl = excluded.realized_pnl,
                    consecutive_losses = excluded.consecutive_losses,
                    halted = excluded.halted,
                    halt_reason = excluded.halt_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    mode,
                    environment,
                    risk_date,
                    str(starting_balance),
                    str(peak_balance),
                    str(current_balance),
                    str(realized_pnl),
                    consecutive_losses,
                    int(halted),
                    halt_reason,
                    _utc_iso(updated_at),
                ),
            )

    def get_risk_state(
        self, *, mode: str, environment: str, risk_date: str
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT * FROM persistent_risk_state
            WHERE mode = ? AND environment = ? AND risk_date = ?
            """,
            (mode, environment, risk_date),
        ).fetchone()

    def record_open_trade(
        self,
        *,
        mode: str,
        environment: str,
        asset: str,
        ticker: str,
        event_ticker: str,
        order_id: str,
        client_order_id: str,
        outcome: str,
        placed_at: datetime,
        market_open_time: datetime,
        market_close_time: datetime,
        status_check_time: datetime,
        contracts: Decimal,
        entry_price: Decimal,
        entry_cost: Decimal,
        fees_paid: Decimal,
        total_debit: Decimal,
        target_price: Decimal,
        model_probability: Decimal,
        model_edge: Decimal,
        strategy: str = "predictor",
        metadata: dict[str, Any] | None = None,
    ) -> TradeRecord:
        trade_id = str(uuid.uuid4())
        sequence_row = self.connection.execute(
            """
            SELECT COUNT(*) AS n FROM trades
            WHERE mode = ? AND environment = ? AND ticker = ? AND strategy = ?
            """,
            (mode, environment, ticker, strategy),
        ).fetchone()
        entry_sequence = int(sequence_row["n"]) + 1
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO trades (
                    trade_id, mode, environment, strategy, entry_sequence, asset, ticker,
                    event_ticker,
                    order_id, client_order_id, outcome, status, exchange_status,
                    placed_at, market_open_time, market_close_time,
                    status_check_time, contracts, entry_price, entry_cost,
                    remaining_contracts, fees_paid, total_debit, target_price, model_probability,
                    model_edge, metadata_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', '', ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    trade_id,
                    mode,
                    environment,
                    strategy,
                    entry_sequence,
                    asset,
                    ticker,
                    event_ticker,
                    order_id,
                    client_order_id,
                    outcome,
                    _utc_iso(placed_at),
                    _utc_iso(market_open_time),
                    _utc_iso(market_close_time),
                    _utc_iso(status_check_time),
                    str(contracts),
                    str(entry_price),
                    str(entry_cost),
                    str(contracts),
                    str(fees_paid),
                    str(total_debit),
                    str(target_price),
                    str(model_probability),
                    str(model_edge),
                    json.dumps(metadata or {}, separators=(",", ":")),
                ),
            )
        trade = self.get_trade(trade_id)
        if trade is None:  # pragma: no cover - SQLite invariant
            raise RuntimeError("Trade insert succeeded but record could not be read")
        return trade

    def get_trade(self, trade_id: str) -> TradeRecord | None:
        row = self.connection.execute(
            "SELECT * FROM trades WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
        return self._row_to_trade(row) if row else None

    def get_open_trade(self, mode: str, environment: str) -> TradeRecord | None:
        row = self.connection.execute(
            """
            SELECT * FROM trades
            WHERE mode = ? AND environment = ? AND status = 'OPEN'
            ORDER BY placed_at ASC
            LIMIT 1
            """,
            (mode, environment),
        ).fetchone()
        return self._row_to_trade(row) if row else None

    def get_open_trades(
        self,
        mode: str,
        environment: str,
        strategy: str | None = None,
    ) -> list[TradeRecord]:
        """Every open position, oldest first, optionally for one strategy."""
        clauses = ["mode = ?", "environment = ?", "status = 'OPEN'"]
        parameters: list[Any] = [mode, environment]
        if strategy is not None:
            clauses.append("strategy = ?")
            parameters.append(strategy)
        rows = self.connection.execute(
            f"""
            SELECT * FROM trades
            WHERE {" AND ".join(clauses)}
            ORDER BY placed_at ASC
            """,
            parameters,
        ).fetchall()
        return [self._row_to_trade(row) for row in rows]

    def get_unlabeled_exited_trades(
        self,
        mode: str,
        environment: str,
        closed_by: datetime | None = None,
    ) -> list[TradeRecord]:
        """Exited trades whose eventual settlement counterfactual is still absent."""
        moment = _utc_iso(closed_by)
        rows = self.connection.execute(
            """SELECT t.* FROM trades t
               LEFT JOIN exit_counterfactuals c ON c.trade_id=t.trade_id
               WHERE t.mode=? AND t.environment=? AND t.status='EXITED'
                 AND t.market_close_time<=? AND c.trade_id IS NULL
               ORDER BY t.market_close_time,t.placed_at""",
            (mode, environment, moment),
        ).fetchall()
        return [self._row_to_trade(row) for row in rows]

    def count_open_trades(
        self,
        mode: str,
        environment: str,
        strategy: str | None = None,
    ) -> int:
        clauses = ["mode = ?", "environment = ?", "status = 'OPEN'"]
        parameters: list[Any] = [mode, environment]
        if strategy is not None:
            clauses.append("strategy = ?")
            parameters.append(strategy)
        row = self.connection.execute(
            f"SELECT COUNT(*) AS n FROM trades WHERE {' AND '.join(clauses)}",
            parameters,
        ).fetchone()
        return int(row["n"])

    def has_traded_ticker(
        self,
        mode: str,
        environment: str,
        ticker: str,
        strategy: str | None = None,
    ) -> bool:
        """Duplicate protection.

        Scoped to a strategy when one is named: two strategies reaching the
        same conclusion about the same market is a coincidence, not a double
        entry, and the ledger's uniqueness constraint is scoped the same way.
        Called without a strategy it keeps the original whole-ledger meaning.
        """
        clauses = ["mode = ?", "environment = ?", "ticker = ?"]
        parameters: list[Any] = [mode, environment, ticker]
        if strategy is not None:
            clauses.append("strategy = ?")
            parameters.append(strategy)
        row = self.connection.execute(
            f"SELECT 1 FROM trades WHERE {' AND '.join(clauses)} LIMIT 1",
            parameters,
        ).fetchone()
        return row is not None

    def latest_trade_for_ticker(
        self,
        mode: str,
        environment: str,
        ticker: str,
        strategy: str,
    ) -> TradeRecord | None:
        row = self.connection.execute(
            """
            SELECT * FROM trades
            WHERE mode = ? AND environment = ? AND ticker = ? AND strategy = ?
            ORDER BY entry_sequence DESC, placed_at DESC
            LIMIT 1
            """,
            (mode, environment, ticker, strategy),
        ).fetchone()
        return self._row_to_trade(row) if row else None

    def count_ticker_entries(
        self,
        mode: str,
        environment: str,
        ticker: str,
        strategy: str,
    ) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS n FROM trades
            WHERE mode = ? AND environment = ? AND ticker = ? AND strategy = ?
            """,
            (mode, environment, ticker, strategy),
        ).fetchone()
        return int(row["n"])

    def can_enter_ticker(
        self,
        *,
        mode: str,
        environment: str,
        ticker: str,
        strategy: str,
        outcome: str | None,
        maximum_entries: int,
    ) -> tuple[bool, str]:
        """Bounded re-entry policy shared by predictor and scalper."""
        prior = self.latest_trade_for_ticker(mode, environment, ticker, strategy)
        if prior is None:
            return True, "first entry"
        if prior.status == "OPEN":
            return False, "an entry for this strategy and ticker is still open"
        if prior.entry_sequence >= maximum_entries:
            return False, f"the {maximum_entries}-entry market limit is exhausted"
        if prior.status != "EXITED" or prior.exchange_status != "take_profit":
            return False, "only a completed take-profit exit permits re-entry"
        if prior.pnl is None or prior.pnl <= 0:
            return False, "the prior exit did not realize a profit"
        if outcome is None:
            return True, "opposite outcome must still be confirmed"
        if outcome == prior.outcome:
            return (
                False,
                "same-side re-entry is churn; only an opposite side is allowed",
            )
        return True, f"opposite-side entry after profitable leg {prior.entry_sequence}"

    def record_exit(
        self,
        trade_id: str,
        *,
        exit_price: Decimal,
        exit_contracts: Decimal,
        exit_fees: Decimal,
        exited_at: datetime | None = None,
        exchange_status: str = "exited",
    ) -> TradeRecord:
        """Compatibility wrapper for callers that require a complete exit."""
        current = self.get_trade(trade_id)
        if current is None:
            raise KeyError(f"Unknown trade: {trade_id}")
        if current.status != "OPEN":
            return current
        if exit_contracts <= 0:
            raise ValueError("exit_contracts must be positive")
        if exit_contracts != current.remaining_contracts:
            raise ValueError(
                "exit_contracts must equal the complete open position; "
                "use record_exit_fill for IOC partial fills"
            )
        return self.record_exit_fill(
            trade_id,
            client_order_id=f"legacy-exit-{uuid.uuid4().hex}",
            exit_price=exit_price,
            exit_contracts=exit_contracts,
            exit_fees=exit_fees,
            exited_at=exited_at,
            exchange_status=exchange_status,
        )

    def record_exit_fill(
        self,
        trade_id: str,
        *,
        client_order_id: str,
        exit_price: Decimal,
        exit_contracts: Decimal,
        exit_fees: Decimal,
        exited_at: datetime | None = None,
        exchange_status: str = "exited",
    ) -> TradeRecord:
        """Apply one idempotent IOC fill and retain any unsold remainder."""
        if not client_order_id:
            raise ValueError("client_order_id is required for idempotent exits")
        current = self.get_trade(trade_id)
        if current is None:
            raise KeyError(f"Unknown trade: {trade_id}")
        existing = self.connection.execute(
            "SELECT trade_id FROM trade_exit_fills WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
        if existing is not None:
            if existing["trade_id"] != trade_id:
                raise ValueError("client_order_id already belongs to another trade")
            return current
        if current.status != "OPEN":
            return current
        if exit_contracts <= 0:
            raise ValueError("exit_contracts must be positive")
        if exit_contracts > current.remaining_contracts:
            raise ValueError("exit fill exceeds the remaining position")
        if exit_fees < 0:
            raise ValueError("exit_fees cannot be negative")
        if not Decimal("0") <= exit_price <= Decimal("1"):
            raise ValueError("exit_price must be between 0 and 1")

        moment = _utc_iso(exited_at)
        proceeds = (exit_contracts * exit_price) - exit_fees
        remaining = current.remaining_contracts - exit_contracts
        prior_contracts = current.exit_contracts or Decimal("0")
        prior_fees = current.exit_fees or Decimal("0")
        prior_proceeds = current.gross_payout or Decimal("0")
        cumulative_contracts = prior_contracts + exit_contracts
        cumulative_fees = prior_fees + exit_fees
        cumulative_proceeds = prior_proceeds + proceeds
        weighted_price = (
            (current.exit_price or Decimal("0")) * prior_contracts
            + exit_price * exit_contracts
        ) / cumulative_contracts
        completed = remaining == 0
        pnl = cumulative_proceeds - current.total_debit if completed else None
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO trade_exit_fills (
                    exit_fill_id, trade_id, client_order_id, filled_at,
                    contracts, price, fee, proceeds, exchange_status,
                    remaining_contracts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    trade_id,
                    client_order_id,
                    moment,
                    str(exit_contracts),
                    str(exit_price),
                    str(exit_fees),
                    str(proceeds),
                    exchange_status,
                    str(remaining),
                ),
            )
            self.connection.execute(
                """
                UPDATE trades
                SET status = ?, exchange_status = ?, settled_at = ?,
                    last_status_check_at = ?, exited_at = ?, exit_price = ?,
                    exit_contracts = ?, exit_fees = ?, gross_payout = ?, pnl = ?,
                    remaining_contracts = ?
                WHERE trade_id = ? AND status = 'OPEN'
                """,
                (
                    "EXITED" if completed else "OPEN",
                    exchange_status if completed else f"{exchange_status}_partial",
                    moment if completed else None,
                    moment,
                    moment if completed else None,
                    str(weighted_price),
                    str(cumulative_contracts),
                    str(cumulative_fees),
                    str(cumulative_proceeds),
                    str(pnl) if pnl is not None else None,
                    str(remaining),
                    trade_id,
                ),
            )
        refreshed = self.get_trade(trade_id)
        if refreshed is None:  # pragma: no cover - SQLite invariant
            raise RuntimeError("Trade disappeared while recording an exit fill")
        return refreshed

    def record_status_check(
        self,
        trade_id: str,
        exchange_status: str,
        checked_at: datetime | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE trades
                SET exchange_status = ?, last_status_check_at = ?
                WHERE trade_id = ? AND status = 'OPEN'
                """,
                (exchange_status, _utc_iso(checked_at), trade_id),
            )

    def settle_trade(
        self,
        trade_id: str,
        market_result: str,
        exchange_status: str,
        settled_at: datetime | None = None,
    ) -> TradeRecord:
        normalized_result = market_result.lower()
        if normalized_result not in {"yes", "no", "void"}:
            raise ValueError("market_result must be yes, no, or void")

        current = self.get_trade(trade_id)
        if current is None:
            raise KeyError(f"Unknown trade: {trade_id}")
        if current.status != "OPEN":
            return current

        remaining = current.remaining_contracts
        prior_proceeds = current.gross_payout or Decimal("0")
        if normalized_result == "void":
            status = "VOID"
            # A void returns the cost basis of the still-open residual. Any
            # previously sold portion already has real proceeds in the ledger.
            residual_basis = (
                current.total_debit * remaining / current.contracts
                if current.contracts > 0
                else Decimal("0")
            )
            gross_payout = prior_proceeds + residual_basis
            pnl = Decimal("0")
        else:
            won = normalized_result == current.outcome
            status = "WON" if won else "LOST"
            gross_payout = prior_proceeds + (remaining if won else Decimal("0"))
            pnl = gross_payout - current.total_debit

        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE trades
                SET status = ?, exchange_status = ?, market_result = ?,
                    settled_at = ?, last_status_check_at = ?,
                    gross_payout = ?, pnl = ?, remaining_contracts = '0'
                WHERE trade_id = ? AND status = 'OPEN'
                """,
                (
                    status,
                    exchange_status,
                    normalized_result,
                    _utc_iso(settled_at),
                    _utc_iso(settled_at),
                    str(gross_payout),
                    str(pnl),
                    trade_id,
                ),
            )
        if cursor.rowcount != 1:
            refreshed = self.get_trade(trade_id)
            if refreshed is None:  # pragma: no cover - SQLite invariant
                raise RuntimeError("Trade disappeared while settling")
            return refreshed

        settled = self.get_trade(trade_id)
        if settled is None:  # pragma: no cover - SQLite invariant
            raise RuntimeError("Settled trade could not be read")
        return settled

    def list_trades(
        self,
        mode: str = "paper",
        environment: str | None = None,
        limit: int = 100,
    ) -> list[TradeRecord]:
        clauses = ["mode = ?"]
        parameters: list[Any] = [mode]
        if environment is not None:
            clauses.append("environment = ?")
            parameters.append(environment)
        parameters.append(max(1, limit))
        rows = self.connection.execute(
            f"""
            SELECT * FROM trades
            WHERE {" AND ".join(clauses)}
            ORDER BY placed_at DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [self._row_to_trade(row) for row in rows]

    def paper_summary(
        self,
        environment: str | None = None,
        strategy: str | None = None,
    ) -> LedgerSummary:
        starting_balance_row = self.connection.execute(
            """
            SELECT value FROM ledger_metadata
            WHERE key = 'paper_starting_balance'
            """
        ).fetchone()
        if starting_balance_row is None:  # pragma: no cover - schema invariant
            raise RuntimeError("Paper starting balance is missing")
        starting_balance = _decimal(starting_balance_row["value"])

        where = "mode = 'paper'"
        parameters_list: list[Any] = []
        if environment is not None:
            where += " AND environment = ?"
            parameters_list.append(environment)
        if strategy is not None:
            where += " AND strategy = ?"
            parameters_list.append(strategy)
        parameters: tuple[Any, ...] = tuple(parameters_list)
        rows = self.connection.execute(
            f"""
            SELECT status, contracts, remaining_contracts, total_debit,
                   gross_payout, fees_paid, pnl, exit_fees
            FROM trades
            WHERE {where}
            """,
            parameters,
        ).fetchall()

        open_trades = sum(row["status"] == "OPEN" for row in rows)
        voids = sum(row["status"] == "VOID" for row in rows)
        exits = sum(row["status"] == "EXITED" for row in rows)
        # An early exit is a realised outcome, so it counts toward the win rate
        # on the sign of its P&L. Booking every exit as neither a win nor a loss
        # would let a strategy that exits all its losers report a perfect record.
        wins = sum(
            row["status"] == "WON"
            or (row["status"] == "EXITED" and _decimal(row["pnl"] or 0) > 0)
            for row in rows
        )
        losses = sum(
            row["status"] == "LOST"
            or (row["status"] == "EXITED" and _decimal(row["pnl"] or 0) <= 0)
            for row in rows
        )
        locked_capital = sum(
            (
                _decimal(row["total_debit"])
                * _decimal(row["remaining_contracts"] or row["contracts"])
                / _decimal(row["contracts"])
                for row in rows
                if row["status"] == "OPEN"
            ),
            Decimal("0"),
        )
        realized_pnls = [
            _decimal(row["pnl"])
            for row in rows
            if row["pnl"] is not None and row["status"] != "OPEN"
        ]
        partial_realized = sum(
            (
                _decimal(row["gross_payout"] or 0)
                - _decimal(row["total_debit"])
                * (
                    _decimal(row["contracts"])
                    - _decimal(row["remaining_contracts"] or row["contracts"])
                )
                / _decimal(row["contracts"])
                for row in rows
                if row["status"] == "OPEN" and row["gross_payout"] is not None
            ),
            Decimal("0"),
        )
        net_pnl = sum(realized_pnls, Decimal("0")) + partial_realized
        gross_profit = sum((pnl for pnl in realized_pnls if pnl > 0), Decimal("0"))
        gross_loss = -sum((pnl for pnl in realized_pnls if pnl < 0), Decimal("0"))
        total_fees = sum(
            (
                _decimal(row["fees_paid"]) + _decimal(row["exit_fees"] or 0)
                for row in rows
            ),
            Decimal("0"),
        )
        decided = wins + losses
        win_rate = Decimal(wins) / Decimal(decided) if decided else Decimal("0")
        current_balance = starting_balance + net_pnl
        available_balance = current_balance - locked_capital
        return LedgerSummary(
            starting_balance=starting_balance,
            current_balance=current_balance,
            available_balance=available_balance,
            locked_capital=locked_capital,
            total_trades=len(rows),
            open_trades=open_trades,
            wins=wins,
            losses=losses,
            voids=voids,
            win_rate=win_rate,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            total_fees=total_fees,
            net_pnl=net_pnl,
            exits=exits,
        )

    @staticmethod
    def _row_to_trade(row: sqlite3.Row) -> TradeRecord:
        def parse_time(name: str) -> datetime | None:
            value = row[name]
            return datetime.fromisoformat(value) if value else None

        return TradeRecord(
            trade_id=row["trade_id"],
            mode=row["mode"],
            environment=row["environment"],
            strategy=row["strategy"],
            asset=row["asset"],
            ticker=row["ticker"],
            event_ticker=row["event_ticker"],
            order_id=row["order_id"],
            client_order_id=row["client_order_id"],
            outcome=row["outcome"],
            status=row["status"],
            exchange_status=row["exchange_status"],
            market_result=row["market_result"],
            placed_at=parse_time("placed_at"),  # type: ignore[arg-type]
            market_open_time=parse_time("market_open_time"),  # type: ignore[arg-type]
            market_close_time=parse_time("market_close_time"),  # type: ignore[arg-type]
            status_check_time=parse_time("status_check_time"),  # type: ignore[arg-type]
            last_status_check_at=parse_time("last_status_check_at"),
            settled_at=parse_time("settled_at"),
            contracts=_decimal(row["contracts"]),
            remaining_contracts=_decimal(
                row["remaining_contracts"]
                if row["remaining_contracts"] is not None
                else row["contracts"]
            ),
            entry_price=_decimal(row["entry_price"]),
            entry_cost=_decimal(row["entry_cost"]),
            fees_paid=_decimal(row["fees_paid"]),
            total_debit=_decimal(row["total_debit"]),
            gross_payout=(
                _decimal(row["gross_payout"])
                if row["gross_payout"] is not None
                else None
            ),
            pnl=_decimal(row["pnl"]) if row["pnl"] is not None else None,
            exit_price=(
                _decimal(row["exit_price"]) if row["exit_price"] is not None else None
            ),
            exit_contracts=(
                _decimal(row["exit_contracts"])
                if row["exit_contracts"] is not None
                else None
            ),
            exit_fees=(
                _decimal(row["exit_fees"]) if row["exit_fees"] is not None else None
            ),
            exited_at=parse_time("exited_at"),
            target_price=_decimal(row["target_price"]),
            model_probability=_decimal(row["model_probability"]),
            model_edge=_decimal(row["model_edge"]),
            metadata=json.loads(row["metadata_json"]),
            entry_sequence=int(row["entry_sequence"]),
            schema_version=int(row["schema_version"]),
        )
