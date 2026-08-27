"""Parallel paper-only strategy laboratory with one wallet per policy.

Every policy sees the same observation and pays the same conservative taker
fee.  It cannot place live orders: the only side effect is its own strict
SQLite database.  This makes a comparison causal enough to be useful without
letting experimental logic touch the production paper/live ledger.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
from pathlib import Path
from typing import Any, Awaitable, Callable

from core.strategy_brain.strategies.take_profit import is_active_winner


D = Decimal
ZERO = D("0")
ONE = D("1")
CENT = D("0.01")
CENTICENT = D("0.0001")

STRATEGIES = (
    "complementary_pair_arb",
    "raw_value_longshot",
    "consensus_tail_carry",
    "early_consensus_180",
    "early_consensus_240",
    "early_consensus_300",
    "early_consensus_360",
    "hold_to_settlement",
    "fixed_take_profit",
    "adaptive_take_profit",
    "winning_hold_take_profit",
    "confirmed_reversal_exit",
    "principal_recovery",
    "trend_trailing",
    "strike_distance",
    "change_point_flip",
    "probability_acceleration",
    "displaced_shelf",
    "microprice_flow",
    "regime_ensemble",
)


def _iso(moment: datetime | None = None) -> str:
    value = moment or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _fee(contracts: Decimal, price: Decimal, multiplier: Decimal) -> Decimal:
    raw = D("0.07") * multiplier * contracts * price * (ONE - price)
    return raw.quantize(CENTICENT, rounding=ROUND_CEILING)


@dataclass(frozen=True)
class LabObservation:
    ticker: str
    asset: str
    observed_at: datetime
    close_time: datetime
    target_price: Decimal
    spot: Decimal
    probability_yes: Decimal
    distance_z: Decimal
    momentum: Decimal
    regime: str
    regime_confidence: Decimal
    yes_bid: Decimal
    yes_ask: Decimal
    yes_bid_size: Decimal
    no_bid_size: Decimal
    microprice: Decimal
    imbalance: Decimal
    toxicity: Decimal
    fee_multiplier: Decimal
    raw_probability_yes: Decimal = D("0.5")
    directional_probability_yes: Decimal = D("0.5")

    @property
    def seconds_to_close(self) -> Decimal:
        return D(str((self.close_time - self.observed_at).total_seconds()))

    def probability(self, outcome: str) -> Decimal:
        return self.probability_yes if outcome == "yes" else ONE - self.probability_yes

    def ask(self, outcome: str) -> Decimal:
        return self.yes_ask if outcome == "yes" else ONE - self.yes_bid

    def bid(self, outcome: str) -> Decimal:
        return self.yes_bid if outcome == "yes" else ONE - self.yes_ask

    def depth(self, outcome: str) -> Decimal:
        return self.no_bid_size if outcome == "yes" else self.yes_bid_size


class StrategyLab:
    """Evaluate independent policies against one shared stream of observations."""

    def __init__(
        self, path: Path, *, starting_balance: Decimal, stake: Decimal
    ) -> None:
        if starting_balance <= 0 or stake <= 0:
            raise ValueError("strategy lab balances and stake must be positive")
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.starting_balance = starting_balance
        self.stake = stake
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self._create_schema()
        self._last_probability: dict[tuple[str, str], list[Decimal]] = {}
        self._last_side: dict[tuple[str, str], list[str]] = {}
        self._pair_active: set[str] = set()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS lab_wallets (
                strategy TEXT PRIMARY KEY,
                starting_balance TEXT NOT NULL CHECK(CAST(starting_balance AS REAL) > 0),
                cash_balance TEXT NOT NULL CHECK(CAST(cash_balance AS REAL) >= 0),
                realized_pnl TEXT NOT NULL DEFAULT '0',
                trades INTEGER NOT NULL DEFAULT 0 CHECK(trades >= 0),
                wins INTEGER NOT NULL DEFAULT 0 CHECK(wins >= 0),
                losses INTEGER NOT NULL DEFAULT 0 CHECK(losses >= 0),
                updated_at TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS lab_positions (
                position_id TEXT PRIMARY KEY,
                strategy TEXT NOT NULL REFERENCES lab_wallets(strategy),
                ticker TEXT NOT NULL,
                asset TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('yes','no')),
                status TEXT NOT NULL CHECK(status IN ('OPEN','EXITED','WON','LOST','VOID')),
                contracts TEXT NOT NULL CHECK(CAST(contracts AS REAL) > 0),
                remaining_contracts TEXT,
                entry_price TEXT NOT NULL CHECK(CAST(entry_price AS REAL) > 0 AND CAST(entry_price AS REAL) < 1),
                entry_fee TEXT NOT NULL CHECK(CAST(entry_fee AS REAL) >= 0),
                opened_at TEXT NOT NULL,
                close_time TEXT NOT NULL,
                target_price TEXT NOT NULL,
                entry_probability TEXT NOT NULL,
                last_probability TEXT NOT NULL,
                entry_distance_z TEXT NOT NULL,
                peak_bid TEXT NOT NULL,
                reversal_count INTEGER NOT NULL DEFAULT 0 CHECK(reversal_count >= 0),
                winner_streak INTEGER NOT NULL DEFAULT 0 CHECK(winner_streak >= 0),
                reversal_signal_count INTEGER NOT NULL DEFAULT 0
                    CHECK(reversal_signal_count >= 0),
                partial_proceeds TEXT NOT NULL DEFAULT '0',
                partial_exit_fee TEXT NOT NULL DEFAULT '0',
                principal_recovered INTEGER NOT NULL DEFAULT 0
                    CHECK(principal_recovered IN (0, 1)),
                exit_price TEXT,
                exit_fee TEXT,
                closed_at TEXT,
                pnl TEXT,
                close_reason TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json))
            ) STRICT;
            CREATE INDEX IF NOT EXISTS idx_lab_positions_open
            ON lab_positions(strategy, status, close_time);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_lab_one_open_per_market
            ON lab_positions(strategy, ticker) WHERE status='OPEN';
            CREATE TABLE IF NOT EXISTS lab_observations (
                observation_id TEXT PRIMARY KEY,
                strategy TEXT NOT NULL REFERENCES lab_wallets(strategy),
                ticker TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                probability_yes TEXT NOT NULL,
                distance_z TEXT NOT NULL,
                regime TEXT NOT NULL,
                yes_bid TEXT NOT NULL,
                yes_ask TEXT NOT NULL,
                microprice TEXT NOT NULL,
                imbalance TEXT NOT NULL,
                toxicity TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL
            ) STRICT;
            CREATE INDEX IF NOT EXISTS idx_lab_observations_time
            ON lab_observations(strategy, observed_at);
            CREATE TABLE IF NOT EXISTS lab_pair_cycles (
                cycle_id TEXT PRIMARY KEY,
                strategy TEXT NOT NULL REFERENCES lab_wallets(strategy),
                ticker TEXT NOT NULL,
                asset TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                contracts TEXT NOT NULL CHECK(CAST(contracts AS REAL) > 0),
                yes_price TEXT NOT NULL CHECK(CAST(yes_price AS REAL) > 0 AND CAST(yes_price AS REAL) < 1),
                no_price TEXT NOT NULL CHECK(CAST(no_price AS REAL) > 0 AND CAST(no_price AS REAL) < 1),
                pair_cost TEXT NOT NULL CHECK(CAST(pair_cost AS REAL) < 1),
                yes_fee TEXT NOT NULL CHECK(CAST(yes_fee AS REAL) >= 0),
                no_fee TEXT NOT NULL CHECK(CAST(no_fee AS REAL) >= 0),
                guaranteed_payout TEXT NOT NULL CHECK(guaranteed_payout = '1'),
                pnl TEXT NOT NULL CHECK(CAST(pnl AS REAL) > 0),
                CHECK(contracts = contracts)
            ) STRICT;
            CREATE TABLE IF NOT EXISTS lab_wallet_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                strategy TEXT NOT NULL REFERENCES lab_wallets(strategy),
                observed_at TEXT NOT NULL,
                cash_balance TEXT NOT NULL,
                locked_capital TEXT NOT NULL,
                equity TEXT NOT NULL,
                realized_pnl TEXT NOT NULL,
                open_positions INTEGER NOT NULL CHECK(open_positions >= 0)
            ) STRICT;
            """
        )
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(lab_positions)"
            ).fetchall()
        }
        additions = {
            "remaining_contracts": "TEXT",
            "winner_streak": "INTEGER NOT NULL DEFAULT 0",
            "reversal_signal_count": "INTEGER NOT NULL DEFAULT 0",
            "partial_proceeds": "TEXT NOT NULL DEFAULT '0'",
            "partial_exit_fee": "TEXT NOT NULL DEFAULT '0'",
            "principal_recovered": "INTEGER NOT NULL DEFAULT 0",
        }
        with self.connection:
            for name, definition in additions.items():
                if columns and name not in columns:
                    self.connection.execute(
                        f"ALTER TABLE lab_positions ADD COLUMN {name} {definition}"
                    )
            self.connection.execute(
                "UPDATE lab_positions SET remaining_contracts=contracts "
                "WHERE remaining_contracts IS NULL"
            )
        now = _iso()
        with self.connection:
            for strategy in STRATEGIES:
                self.connection.execute(
                    """INSERT OR IGNORE INTO lab_wallets
                       (strategy, starting_balance, cash_balance, updated_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        strategy,
                        str(self.starting_balance),
                        str(self.starting_balance),
                        now,
                    ),
                )

    def close(self) -> None:
        self.connection.close()

    def _position(self, strategy: str, ticker: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM lab_positions WHERE strategy=? AND ticker=? AND status='OPEN'",
            (strategy, ticker),
        ).fetchone()

    def _wallet_cash(self, strategy: str) -> Decimal:
        row = self.connection.execute(
            "SELECT cash_balance FROM lab_wallets WHERE strategy=?", (strategy,)
        ).fetchone()
        return D(row["cash_balance"])

    @staticmethod
    def _favorite(observation: LabObservation) -> str:
        return "yes" if observation.probability_yes >= D("0.5") else "no"

    def _entry_signal(self, strategy: str, o: LabObservation) -> tuple[str | None, str]:
        side = self._favorite(o)
        probability = o.probability(side)
        ask = o.ask(side)
        edge = probability - ask - D("0.015")
        early_windows = {
            "early_consensus_180": D("180"),
            "early_consensus_240": D("240"),
            "early_consensus_300": D("300"),
            "early_consensus_360": D("360"),
        }
        window = early_windows.get(strategy, D("240"))
        if not D("20") <= o.seconds_to_close <= window:
            return None, f"outside 20-{window}s entry window"
        if strategy == "raw_value_longshot":
            yes_edge = o.raw_probability_yes - o.yes_ask - D("0.015")
            raw_no = ONE - o.raw_probability_yes
            no_ask = ONE - o.yes_bid
            no_edge = raw_no - no_ask - D("0.015")
            raw_side, raw_probability, raw_ask, raw_edge = (
                ("yes", o.raw_probability_yes, o.yes_ask, yes_edge)
                if yes_edge >= no_edge
                else ("no", raw_no, no_ask, no_edge)
            )
            if (
                D("0.08") <= raw_probability <= D("0.38")
                and raw_ask <= D("0.20")
                and raw_edge >= D("0.06")
                and o.toxicity <= D("0.75")
            ):
                return (
                    raw_side,
                    "unshrunk tail probability clears price, edge, and flow",
                )
            return None, "no unshrunk, fee-buffered longshot edge"
        if strategy == "consensus_tail_carry":
            raw_side = "yes" if o.raw_probability_yes >= D("0.5") else "no"
            raw_probability = (
                o.raw_probability_yes
                if raw_side == "yes"
                else ONE - o.raw_probability_yes
            )
            raw_ask = o.ask(raw_side)
            aligned_z = o.distance_z if raw_side == "yes" else -o.distance_z
            directional_probability = (
                o.directional_probability_yes
                if raw_side == "yes"
                else ONE - o.directional_probability_yes
            )
            if (
                raw_probability >= D("0.97")
                and raw_ask >= D("0.85")
                and raw_probability - raw_ask - D("0.015") >= D("0.02")
                and aligned_z >= D("2.0")
                and directional_probability >= D("0.50")
                and "VOLATILE_CHOP" not in o.regime.upper()
            ):
                return (
                    raw_side,
                    "raw tail, market consensus, distance, and regime agree",
                )
            return None, "tail consensus gate not met"
        if strategy in early_windows:
            raw_side = "yes" if o.raw_probability_yes >= D("0.5") else "no"
            raw_probability = (
                o.raw_probability_yes
                if side == "yes"
                else ONE - o.raw_probability_yes
            )
            directional_probability = (
                o.directional_probability_yes
                if side == "yes"
                else ONE - o.directional_probability_yes
            )
            signed_z = o.distance_z if side == "yes" else -o.distance_z
            sides = self._last_side.get((strategy, o.ticker), [])
            probabilities = self._last_probability.get((strategy, o.ticker), [])
            spread = o.yes_ask - o.yes_bid
            fee_one = _fee(ONE, ask, o.fee_multiplier)
            full_budget = min(self.stake, self._wallet_cash(strategy))
            exit_depth_value = o.depth(side) * max(ZERO, ask + fee_one)
            checks = (
                probability >= D("0.70"),
                edge >= D("0.04"),
                raw_side == side and raw_probability >= D("0.70"),
                directional_probability >= D("0.50"),
                signed_z >= D("1.0"),
                ask >= D("0.55"),
                spread <= D("0.02"),
                o.toxicity <= D("0.75"),
                "VOLATILE_CHOP" not in o.regime.upper(),
                len(sides) >= 3 and len(set(sides[-3:])) == 1,
                len(probabilities) >= 3 and min(probabilities[-3:]) >= D("0.68"),
                exit_depth_value >= full_budget,
            )
            if all(checks):
                return side, f"persistent multi-source consensus inside {window}s"
            return None, "early consensus persistence/liquidity gate not met"
        if probability < D("0.62") or edge < D("0.025"):
            return None, "base probability/edge gate"

        history = self._last_probability.get((strategy, o.ticker), [])
        signed_z = o.distance_z if side == "yes" else -o.distance_z
        signed_momentum = o.momentum if side == "yes" else -o.momentum
        regime = o.regime.upper()
        if strategy in {
            "hold_to_settlement",
            "fixed_take_profit",
            "adaptive_take_profit",
            "winning_hold_take_profit",
            "confirmed_reversal_exit",
            "principal_recovery",
        }:
            return side, "control admission"
        if strategy == "trend_trailing":
            if "TREND" in regime and signed_momentum >= 0:
                return side, "regime-confirmed continuation"
            return None, "no aligned trend"
        if strategy == "strike_distance":
            return (
                (side, "strike distance carry")
                if signed_z >= D("1.25")
                else (None, "distance below 1.25 sigma")
            )
        if strategy == "change_point_flip":
            if (
                len(history) >= 2
                and probability >= D("0.68")
                and probability - history[-2] >= D("0.03")
            ):
                return side, "persistent probability change point"
            return None, "no probability change point"
        if strategy == "probability_acceleration":
            if len(history) >= 3:
                velocity = history[-1] - history[-2]
                prior_velocity = history[-2] - history[-3]
                if velocity > 0 and velocity - prior_velocity >= D("0.01"):
                    return side, "positive probability acceleration"
            return None, "no positive probability acceleration"
        if strategy == "displaced_shelf":
            if (
                "CONSOLIDATION" in regime
                and signed_z >= D("1.50")
                and abs(o.momentum) <= D("0.003")
            ):
                return side, "large displacement holding in consolidation"
            return None, "not a displaced shelf"
        if strategy == "microprice_flow":
            book_side = (
                "yes" if o.microprice >= (o.yes_bid + o.yes_ask) / D("2") else "no"
            )
            if book_side == side and o.toxicity <= D("0.75"):
                return side, "microprice confirms model with acceptable toxicity"
            return None, "book does not safely confirm"
        if strategy == "regime_ensemble":
            if "VOLATILE_CHOP" in regime:
                return None, "ensemble vetoes volatile chop"
            if (
                signed_z >= D("1.0")
                or ("TREND" in regime and signed_momentum >= 0)
                or (o.toxicity <= D("0.55") and edge >= D("0.05"))
            ):
                return side, "ensemble consensus"
            return None, "insufficient ensemble agreement"
        return None, "unknown strategy"

    def _exit_signal(
        self, strategy: str, p: sqlite3.Row, o: LabObservation
    ) -> tuple[bool, str]:
        outcome = p["outcome"]
        probability = o.probability(outcome)
        bid = o.bid(outcome)
        entry_cost = D(p["entry_price"]) + D(p["entry_fee"]) / D(p["contracts"])
        gain = bid - _fee(D("1"), bid, o.fee_multiplier) - entry_cost
        peak = max(D(p["peak_bid"]), bid)
        opposite = ONE - probability
        if strategy == "hold_to_settlement" or strategy.startswith("early_consensus_"):
            return False, "control holds to settlement"
        if strategy in {"raw_value_longshot", "consensus_tail_carry"}:
            return gain >= D("0.05"), "tail policy takes a fee-adjusted 5c gain"
        if strategy == "fixed_take_profit":
            return gain >= D("0.05"), "fixed 5c net take-profit"
        if strategy == "adaptive_take_profit":
            maximum_gain = max(ZERO, ONE - entry_cost)
            required_gain = max(D("0.05"), maximum_gain * D("0.75"))
            confidence_cleared = probability - bid < D("0.02")
            return (
                gain >= required_gain and confidence_cleared,
                "75% upside captured and executable bid clears model confidence",
            )
        if strategy in {"winning_hold_take_profit", "confirmed_reversal_exit"}:
            if int(p["winner_streak"]) > 0:
                return (
                    False,
                    "active winner confirmed twice; hold toward settlement"
                    if int(p["winner_streak"]) >= 2
                    else "active winner awaiting second confirmation",
                )
            if (
                strategy == "confirmed_reversal_exit"
                and int(p["reversal_signal_count"]) >= 2
            ):
                return True, "three-signal deterioration confirmed twice"
            maximum_gain = max(ZERO, ONE - entry_cost)
            required_gain = max(D("0.05"), maximum_gain * D("0.75"))
            confidence_cleared = probability - bid < D("0.02")
            return (
                gain >= required_gain and confidence_cleared,
                "adaptive exit after winner/reversal persistence checks",
            )
        if strategy == "principal_recovery":
            return False, "recover principal once, then carry the residual"
        if strategy == "trend_trailing":
            armed = peak - entry_cost >= D("0.05")
            if armed and peak - bid >= D("0.04"):
                return True, "4c trailing drawdown after profit armed"
            if opposite >= D("0.70"):
                return True, "confirmed directional reversal"
            return False, "trend trail intact"
        if strategy == "strike_distance":
            aligned_z = o.distance_z if outcome == "yes" else -o.distance_z
            return aligned_z < D("0.70"), "strike distance contracted below 0.70 sigma"
        if strategy == "change_point_flip":
            count = int(p["reversal_count"])
            return count >= 3 and opposite >= D(
                "0.68"
            ), "three-observation opposite change point"
        if strategy == "probability_acceleration":
            history = self._last_probability.get((strategy, o.ticker), [])
            if len(history) >= 3 and history[-1] < history[-2] < history[-3]:
                return True, "probability decelerated for two observations"
            return False, "probability path intact"
        if strategy == "displaced_shelf":
            entry_z = abs(D(p["entry_distance_z"]))
            aligned_z = o.distance_z if outcome == "yes" else -o.distance_z
            return aligned_z < max(
                D("0.75"), entry_z * D("0.70")
            ), "shelf broke toward strike"
        if strategy == "microprice_flow":
            midpoint = (o.yes_bid + o.yes_ask) / D("2")
            confirms_yes = o.microprice >= midpoint
            return confirms_yes != (outcome == "yes") or o.toxicity > D(
                "0.90"
            ), "microprice flipped or flow became toxic"
        if strategy == "regime_ensemble":
            if "VOLATILE_CHOP" in o.regime.upper() or opposite >= D("0.67"):
                return True, "ensemble regime/probability exit"
            if peak - entry_cost >= D("0.06") and peak - bid >= D("0.035"):
                return True, "ensemble adaptive trail"
            return False, "ensemble hold"
        return False, "hold"

    def _open(
        self, strategy: str, o: LabObservation, outcome: str, reason: str
    ) -> bool:
        ask = o.ask(outcome)
        depth = o.depth(outcome)
        cash = self._wallet_cash(strategy)
        budget = min(self.stake, cash)
        fee_per_contract = _fee(ONE, ask, o.fee_multiplier)
        contracts = min(
            depth.to_integral_value(rounding=ROUND_DOWN),
            (budget / (ask + fee_per_contract)).to_integral_value(rounding=ROUND_DOWN),
        )
        if contracts < 1:
            return False
        fee = _fee(contracts, ask, o.fee_multiplier)
        debit = contracts * ask + fee
        # The aggregate fee rounds only once and is no larger than the sum of
        # individually rounded per-contract fees used for sizing above. Keep a
        # defensive loop for Decimal/tick changes without ever allowing a
        # negative strict wallet balance.
        while debit > cash and contracts >= 1:
            contracts -= 1
            fee = _fee(contracts, ask, o.fee_multiplier) if contracts else ZERO
            debit = contracts * ask + fee
        if contracts < 1:
            return False
        with self.connection:
            self.connection.execute(
                "UPDATE lab_wallets SET cash_balance=?, trades=trades+1, updated_at=? WHERE strategy=?",
                (str(cash - debit), _iso(o.observed_at), strategy),
            )
            self.connection.execute(
                """INSERT INTO lab_positions (
                    position_id,strategy,ticker,asset,outcome,status,contracts,
                    remaining_contracts,
                    entry_price,entry_fee,opened_at,close_time,target_price,
                    entry_probability,last_probability,entry_distance_z,peak_bid,
                    metadata_json
                ) VALUES (?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    strategy,
                    o.ticker,
                    o.asset,
                    outcome,
                    str(contracts),
                    str(contracts),
                    str(ask),
                    str(fee),
                    _iso(o.observed_at),
                    _iso(o.close_time),
                    str(o.target_price),
                    str(o.probability(outcome)),
                    str(o.probability(outcome)),
                    str(o.distance_z),
                    str(o.bid(outcome)),
                    json.dumps({"entry_reason": reason}, separators=(",", ":")),
                ),
            )
        return True

    def _recover_principal(self, p: sqlite3.Row, o: LabObservation) -> bool:
        """Sell only enough high-priced contracts to recover the original debit."""
        if int(p["principal_recovered"]):
            return False
        price = o.bid(p["outcome"])
        if price < D("0.95"):
            return False
        remaining = D(p["remaining_contracts"] or p["contracts"])
        fee_one = _fee(ONE, price, o.fee_multiplier)
        net_one = price - fee_one
        if net_one <= ZERO:
            return False
        debit = D(p["contracts"]) * D(p["entry_price"]) + D(p["entry_fee"])
        sell = (debit / net_one).to_integral_value(rounding=ROUND_CEILING)
        # A recovery that liquidates everything is just a full take-profit and
        # belongs to the fixed/adaptive controls, not this partial-exit wallet.
        if sell <= ZERO or sell >= remaining or o.depth(p["outcome"]) < sell:
            return False
        fee = _fee(sell, price, o.fee_multiplier)
        proceeds = sell * price - fee
        cash = self._wallet_cash(p["strategy"]) + proceeds
        with self.connection:
            self.connection.execute(
                """UPDATE lab_positions SET remaining_contracts=?,partial_proceeds=?,
                   partial_exit_fee=?,principal_recovered=1 WHERE position_id=?""",
                (str(remaining - sell), str(proceeds), str(fee), p["position_id"]),
            )
            self.connection.execute(
                "UPDATE lab_wallets SET cash_balance=?,updated_at=? WHERE strategy=?",
                (str(cash), _iso(o.observed_at), p["strategy"]),
            )
        return True

    def _close(self, p: sqlite3.Row, o: LabObservation, reason: str) -> None:
        contracts = D(p["contracts"])
        price = o.bid(p["outcome"])
        fee = _fee(contracts, price, o.fee_multiplier)
        proceeds = contracts * price - fee
        debit = contracts * D(p["entry_price"]) + D(p["entry_fee"])
        pnl = proceeds - debit
        cash = self._wallet_cash(p["strategy"]) + proceeds
        with self.connection:
            self.connection.execute(
                """UPDATE lab_positions SET status='EXITED',exit_price=?,exit_fee=?,
                   closed_at=?,pnl=?,close_reason=? WHERE position_id=?""",
                (
                    str(price),
                    str(fee),
                    _iso(o.observed_at),
                    str(pnl),
                    reason,
                    p["position_id"],
                ),
            )
            self.connection.execute(
                """UPDATE lab_wallets SET cash_balance=?,realized_pnl=CAST(realized_pnl AS REAL)+?,
                   wins=wins+?,losses=losses+?,updated_at=? WHERE strategy=?""",
                (
                    str(cash),
                    float(pnl),
                    int(pnl > 0),
                    int(pnl <= 0),
                    _iso(o.observed_at),
                    p["strategy"],
                ),
            )

    def _record(
        self, strategy: str, o: LabObservation, action: str, reason: str
    ) -> None:
        self.connection.execute(
            """INSERT INTO lab_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                strategy,
                o.ticker,
                _iso(o.observed_at),
                str(o.probability_yes),
                str(o.distance_z),
                o.regime,
                str(o.yes_bid),
                str(o.yes_ask),
                str(o.microprice),
                str(o.imbalance),
                str(o.toxicity),
                action,
                reason,
            ),
        )

    def _observe_pair_arb(self, o: LabObservation) -> None:
        """Instantly recycle an equal-quantity complementary pair in shadow.

        Kalshi exposes one net binary book. Buying YES crosses the NO bid and
        buying NO crosses the YES bid, so the executable prices are YES ask and
        ``1 - YES bid``. The simulated cycle is admitted only when both fees
        still leave a positive two-cent edge. Both legs always use exactly the
        same contract count and the strict row fixes their payout at $1.
        """
        strategy = "complementary_pair_arb"
        yes_price = o.yes_ask
        no_price = ONE - o.yes_bid
        pair_cost = yes_price + no_price
        yes_fee_one = _fee(ONE, yes_price, o.fee_multiplier)
        no_fee_one = _fee(ONE, no_price, o.fee_multiplier)
        net_edge = ONE - pair_cost - yes_fee_one - no_fee_one
        depth = min(o.yes_bid_size, o.no_bid_size).to_integral_value(
            rounding=ROUND_DOWN
        )
        cash = self._wallet_cash(strategy)
        all_in = pair_cost + yes_fee_one + no_fee_one
        affordable = (
            (min(self.stake, cash) / all_in).to_integral_value(rounding=ROUND_DOWN)
            if all_in > 0
            else ZERO
        )
        contracts = min(depth, affordable)
        if net_edge < D("0.02") or contracts < ONE:
            self._pair_active.discard(o.ticker)
            self._record(
                strategy,
                o,
                "skip",
                f"equal-leg net edge {net_edge:.4f} below 0.0200 or no common depth",
            )
            return
        if o.ticker in self._pair_active:
            self._record(
                strategy,
                o,
                "hold",
                "the same crossed-book episode was already consumed",
            )
            return
        self._pair_active.add(o.ticker)
        yes_fee = yes_fee_one * contracts
        no_fee = no_fee_one * contracts
        pnl = contracts * net_edge
        with self.connection:
            self.connection.execute(
                """INSERT INTO lab_pair_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    strategy,
                    o.ticker,
                    o.asset,
                    _iso(o.observed_at),
                    str(contracts),
                    str(yes_price),
                    str(no_price),
                    str(pair_cost),
                    str(yes_fee),
                    str(no_fee),
                    "1",
                    str(pnl),
                ),
            )
            self.connection.execute(
                """UPDATE lab_wallets SET cash_balance=?,realized_pnl=CAST(realized_pnl AS REAL)+?,
                   trades=trades+1,wins=wins+1,updated_at=? WHERE strategy=?""",
                (str(cash + pnl), float(pnl), _iso(o.observed_at), strategy),
            )
        self._record(
            strategy,
            o,
            "pair_cycle",
            f"equal YES+NO contracts recycled at net edge {net_edge:.4f}",
        )

    def observe(self, o: LabObservation) -> None:
        self._observe_pair_arb(o)
        for strategy in STRATEGIES:
            if strategy == "complementary_pair_arb":
                continue
            side = self._favorite(o)
            side_probability = o.probability(side)
            history = self._last_probability.setdefault((strategy, o.ticker), [])
            history.append(side_probability)
            del history[:-6]
            sides = self._last_side.setdefault((strategy, o.ticker), [])
            sides.append(side)
            del sides[:-6]
            position = self._position(strategy, o.ticker)
            action = "hold"
            reason = "no position"
            if position is not None:
                held_probability = o.probability(position["outcome"])
                opposite = ONE - held_probability
                reversal_count = int(position["reversal_count"])
                reversal_count = reversal_count + 1 if opposite >= D("0.68") else 0
                held_bid = o.bid(position["outcome"])
                peak_bid = max(D(position["peak_bid"]), held_bid)
                aligned_z = (
                    o.distance_z if position["outcome"] == "yes" else -o.distance_z
                )
                signed_momentum = (
                    o.momentum if position["outcome"] == "yes" else -o.momentum
                )
                active_winner = is_active_winner(
                    book_bid=held_bid,
                    model_probability=held_probability,
                    seconds_to_close=o.seconds_to_close,
                    minimum_bid=D("0.85"),
                    minimum_probability=D("0.65"),
                    strong_book_bid=D("0.95"),
                    maximum_seconds_to_close=ZERO,
                )
                winner_streak = (
                    int(position["winner_streak"]) + 1 if active_winner else 0
                )
                deterioration_signals = sum(
                    (
                        held_probability <= D("0.48"),
                        peak_bid - held_bid >= D("0.15"),
                        aligned_z <= ZERO,
                        signed_momentum < ZERO,
                        held_bid < D("0.50"),
                    )
                )
                reversal_signal_count = (
                    int(position["reversal_signal_count"]) + 1
                    if (
                        held_probability <= D("0.48")
                        and held_bid < D("0.50")
                        and deterioration_signals >= 3
                    )
                    else 0
                )
                self.connection.execute(
                    """UPDATE lab_positions SET last_probability=?,peak_bid=?,reversal_count=?,
                       winner_streak=?,reversal_signal_count=?
                       WHERE position_id=?""",
                    (
                        str(held_probability),
                        str(peak_bid),
                        reversal_count,
                        winner_streak,
                        reversal_signal_count,
                        position["position_id"],
                    ),
                )
                position = self._position(strategy, o.ticker)
                if strategy == "principal_recovery" and self._recover_principal(
                    position, o
                ):
                    self._record(strategy, o, "reduce", "original debit recovered")
                    continue
                should_exit, reason = self._exit_signal(strategy, position, o)
                if should_exit and o.depth(position["outcome"]) >= D(
                    position["contracts"]
                ):
                    old_outcome = position["outcome"]
                    self._close(position, o, reason)
                    action = "exit"
                    # A flip is two independently costed decisions. It is only
                    # permitted after the first position is durably flat.
                    if (
                        strategy == "change_point_flip"
                        and side != old_outcome
                        and side_probability >= D("0.68")
                    ):
                        if self._open(strategy, o, side, "post-change-point flip"):
                            action = "flip"
                else:
                    action = "hold"
            else:
                outcome, reason = self._entry_signal(strategy, o)
                if outcome is not None and self._open(strategy, o, outcome, reason):
                    action = "entry"
                else:
                    action = "skip"
            self._record(strategy, o, action, reason)
        self.connection.commit()

    async def settle_expired(
        self,
        now: datetime,
        resolver: Callable[[str], Awaitable[Any]],
    ) -> None:
        rows = self.connection.execute(
            "SELECT * FROM lab_positions WHERE status='OPEN' AND close_time <= ?",
            (_iso(now),),
        ).fetchall()
        by_ticker: dict[str, Any] = {}
        for p in rows:
            if p["ticker"] not in by_ticker:
                by_ticker[p["ticker"]] = await resolver(p["ticker"])
            result = getattr(by_ticker[p["ticker"]], "result", None)
            if result not in {"yes", "no", "void"}:
                continue
            contracts = D(p["remaining_contracts"] or p["contracts"])
            original_debit = D(p["contracts"]) * D(p["entry_price"]) + D(
                p["entry_fee"]
            )
            partial_proceeds = D(p["partial_proceeds"])
            residual_basis = (
                original_debit * contracts / D(p["contracts"])
                if D(p["contracts"]) > ZERO
                else ZERO
            )
            payout = (
                residual_basis
                if result == "void"
                else (contracts if result == p["outcome"] else ZERO)
            )
            pnl = (
                ZERO
                if result == "void" and partial_proceeds == ZERO
                else partial_proceeds + payout - original_debit
            )
            status = "VOID" if result == "void" else ("WON" if pnl > 0 else "LOST")
            cash = self._wallet_cash(p["strategy"]) + payout
            with self.connection:
                self.connection.execute(
                    """UPDATE lab_positions SET status=?,closed_at=?,pnl=?,close_reason='settlement'
                       WHERE position_id=?""",
                    (status, _iso(now), str(pnl), p["position_id"]),
                )
                self.connection.execute(
                    """UPDATE lab_wallets SET cash_balance=?,realized_pnl=CAST(realized_pnl AS REAL)+?,
                       wins=wins+?,losses=losses+?,updated_at=? WHERE strategy=?""",
                    (
                        str(cash),
                        float(pnl),
                        int(pnl > 0),
                        int(pnl < 0),
                        _iso(now),
                        p["strategy"],
                    ),
                )

    def snapshot(self, observed_at: datetime | None = None) -> None:
        now = observed_at or datetime.now(timezone.utc)
        with self.connection:
            for strategy in STRATEGIES:
                cash = self._wallet_cash(strategy)
                positions = self.connection.execute(
                    """SELECT contracts,entry_price,entry_fee,partial_proceeds
                       FROM lab_positions WHERE strategy=? AND status='OPEN'""",
                    (strategy,),
                ).fetchall()
                locked = sum(
                    (
                        max(
                            ZERO,
                            D(p["contracts"]) * D(p["entry_price"])
                            + D(p["entry_fee"])
                            - D(p["partial_proceeds"]),
                        )
                        for p in positions
                    ),
                    ZERO,
                )
                wallet = self.connection.execute(
                    "SELECT realized_pnl FROM lab_wallets WHERE strategy=?", (strategy,)
                ).fetchone()
                self.connection.execute(
                    "INSERT INTO lab_wallet_snapshots VALUES (?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        strategy,
                        _iso(now),
                        str(cash),
                        str(locked),
                        str(cash + locked),
                        wallet["realized_pnl"],
                        len(positions),
                    ),
                )

    def summary(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM lab_wallets ORDER BY strategy"
        ).fetchall()
        result = []
        for row in rows:
            open_count = self.connection.execute(
                "SELECT COUNT(*) FROM lab_positions WHERE strategy=? AND status='OPEN'",
                (row["strategy"],),
            ).fetchone()[0]
            decided = row["wins"] + row["losses"]
            result.append(
                {
                    "strategy": row["strategy"],
                    "cash_balance": row["cash_balance"],
                    "realized_pnl": row["realized_pnl"],
                    "trades": row["trades"],
                    "wins": row["wins"],
                    "losses": row["losses"],
                    "win_rate": (row["wins"] / decided if decided else 0.0),
                    "open_positions": open_count,
                }
            )
        return result
