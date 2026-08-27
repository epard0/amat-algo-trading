"""Performance and diagnostic metrics computed from the durable record.

Pure functions over already-loaded records: no clock, no network, no database.
That keeps every number here testable against a fixed list of trades, and it is
why the readers live in ``monitoring.sources`` instead.

The metrics are chosen to answer three questions an operator actually has:

  1. Is the bot alive and seeing markets?      -> health(), gate_funnel()
  2. Is it making or losing money, and why?    -> summarize(), equity_curve()
  3. Is the model's probability trustworthy?   -> calibration(), brier_score()

(3) is the one that matters most and is the easiest to get wrong. A model can
win most of its trades and still lose money if it is systematically overpricing
its edge, because a 60c contract has to win 60% of the time just to break even.
Win rate alone cannot see that; calibration and realised-vs-claimed edge can.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Sequence

from monitoring.sources import LedgerTrade

ZERO = Decimal("0")

# Buckets for the calibration curve. Narrow near the middle, where the bot
# actually trades, and wide at the tails where it almost never does.
CALIBRATION_EDGES: tuple[Decimal, ...] = (
    Decimal("0.0"),
    Decimal("0.5"),
    Decimal("0.6"),
    Decimal("0.65"),
    Decimal("0.7"),
    Decimal("0.75"),
    Decimal("0.8"),
    Decimal("0.9"),
    Decimal("1.0"),
)


def _sum(values: Iterable[Decimal | None]) -> Decimal:
    return sum((v for v in values if v is not None), ZERO)


@dataclass(frozen=True)
class Summary:
    """Headline P&L and trade counts for one slice of the ledger."""

    trades: int = 0
    open_trades: int = 0
    settled: int = 0
    wins: int = 0
    losses: int = 0
    voids: int = 0
    net_pnl: Decimal = ZERO
    gross_payout: Decimal = ZERO
    total_staked: Decimal = ZERO
    fees_paid: Decimal = ZERO
    contracts: Decimal = ZERO

    @property
    def win_rate(self) -> Decimal:
        decided = self.wins + self.losses
        return Decimal(self.wins) / Decimal(decided) if decided else ZERO

    @property
    def roi(self) -> Decimal:
        """Net P&L per dollar staked. The honest return figure."""
        return self.net_pnl / self.total_staked if self.total_staked else ZERO

    @property
    def average_pnl(self) -> Decimal:
        return self.net_pnl / Decimal(self.settled) if self.settled else ZERO

    @property
    def fee_drag(self) -> Decimal:
        """Fees as a fraction of everything staked.

        On 15-minute markets this is rarely negligible: the quadratic taker fee
        peaks at a 50c price, which is exactly where the model finds most of
        its edge.
        """
        return self.fees_paid / self.total_staked if self.total_staked else ZERO


def summarize(trades: Sequence[LedgerTrade]) -> Summary:
    settled = [t for t in trades if t.is_settled]
    return Summary(
        trades=len(trades),
        open_trades=sum(1 for t in trades if t.status == "OPEN"),
        settled=len(settled),
        wins=sum(1 for t in settled if t.status == "WON"),
        losses=sum(1 for t in settled if t.status == "LOST"),
        voids=sum(1 for t in settled if t.status == "VOID"),
        net_pnl=_sum(t.pnl for t in settled),
        gross_payout=_sum(t.gross_payout for t in settled),
        total_staked=_sum(t.total_debit for t in trades),
        fees_paid=_sum(t.fees_paid for t in trades),
        contracts=_sum(t.contracts for t in trades),
    )


@dataclass(frozen=True)
class EquityPoint:
    timestamp: datetime
    pnl: Decimal
    cumulative: Decimal
    peak: Decimal
    drawdown: Decimal
    trade_id: str
    asset: str
    status: str


def equity_curve(trades: Sequence[LedgerTrade]) -> list[EquityPoint]:
    """Cumulative realised P&L in settlement order, with running drawdown.

    Ordered by settlement rather than placement: a position that settles later
    moves the balance later, and drawdown is a statement about the balance.
    """
    settled = sorted(
        (t for t in trades if t.is_settled and t.pnl is not None),
        key=lambda t: t.settled_at or t.market_close_time or datetime.min.replace(tzinfo=timezone.utc),
    )
    points: list[EquityPoint] = []
    cumulative = ZERO
    peak = ZERO
    for trade in settled:
        pnl = trade.pnl or ZERO
        cumulative += pnl
        peak = max(peak, cumulative)
        stamp = trade.settled_at or trade.market_close_time
        if stamp is None:
            continue
        points.append(
            EquityPoint(
                timestamp=stamp,
                pnl=pnl,
                cumulative=cumulative,
                peak=peak,
                drawdown=cumulative - peak,
                trade_id=trade.trade_id,
                asset=trade.asset,
                status=trade.status,
            )
        )
    return points


def max_drawdown(points: Sequence[EquityPoint]) -> Decimal:
    """Deepest peak-to-trough decline, as a negative number (0 if never down)."""
    return min((p.drawdown for p in points), default=ZERO)


def current_streak(trades: Sequence[LedgerTrade]) -> tuple[str, int]:
    """(kind, length) of the current run, e.g. ("LOST", 3).

    Voids break nothing and count for nothing — they return stake, so they are
    skipped rather than treated as a win or a loss.
    """
    settled = sorted(
        (t for t in trades if t.is_settled and t.status in {"WON", "LOST"}),
        key=lambda t: t.settled_at or datetime.min.replace(tzinfo=timezone.utc),
    )
    if not settled:
        return ("NONE", 0)
    kind = settled[-1].status
    length = 0
    for trade in reversed(settled):
        if trade.status != kind:
            break
        length += 1
    return (kind, length)


def longest_losing_streak(trades: Sequence[LedgerTrade]) -> int:
    settled = sorted(
        (t for t in trades if t.status in {"WON", "LOST"}),
        key=lambda t: t.settled_at or datetime.min.replace(tzinfo=timezone.utc),
    )
    longest = run = 0
    for trade in settled:
        run = run + 1 if trade.status == "LOST" else 0
        longest = max(longest, run)
    return longest


@dataclass(frozen=True)
class CalibrationBucket:
    """One slice of the reliability diagram."""

    low: Decimal
    high: Decimal
    count: int
    predicted_mean: Decimal
    actual_rate: Decimal
    net_pnl: Decimal

    @property
    def label(self) -> str:
        return f"{float(self.low):.0%}-{float(self.high):.0%}"

    @property
    def gap(self) -> Decimal:
        """actual - predicted. Negative means the model is overconfident."""
        return self.actual_rate - self.predicted_mean


def calibration(
    trades: Sequence[LedgerTrade],
    edges: Sequence[Decimal] = CALIBRATION_EDGES,
) -> list[CalibrationBucket]:
    """Bucket predicted probability against the outcome that actually happened.

    Voids are excluded: the market returned stake, so there is no outcome to
    score the prediction against.
    """
    scored = [
        t
        for t in trades
        if t.status in {"WON", "LOST"} and t.model_probability is not None
    ]
    buckets: list[CalibrationBucket] = []
    for low, high in zip(edges, edges[1:]):
        # Upper edge inclusive only in the final bucket, so 1.0 is not dropped.
        is_last = high == edges[-1]
        members = [
            t
            for t in scored
            if low <= (t.model_probability or ZERO) < high
            or (is_last and (t.model_probability or ZERO) == high)
        ]
        if not members:
            continue
        wins = sum(1 for t in members if t.status == "WON")
        buckets.append(
            CalibrationBucket(
                low=low,
                high=high,
                count=len(members),
                predicted_mean=(
                    _sum(t.model_probability for t in members) / Decimal(len(members))
                ),
                actual_rate=Decimal(wins) / Decimal(len(members)),
                net_pnl=_sum(t.pnl for t in members),
            )
        )
    return buckets


def brier_score(trades: Sequence[LedgerTrade]) -> Decimal | None:
    """Mean squared error of the probability forecast. Lower is better.

    0.25 is what you get by always saying 50%. Above that, the model is worse
    than a coin flip and its edge is noise.
    """
    scored = [
        t
        for t in trades
        if t.status in {"WON", "LOST"} and t.model_probability is not None
    ]
    if not scored:
        return None
    total = ZERO
    for trade in scored:
        actual = Decimal(1) if trade.status == "WON" else ZERO
        error = (trade.model_probability or ZERO) - actual
        total += error * error
    return total / Decimal(len(scored))


def edge_realisation(trades: Sequence[LedgerTrade]) -> dict[str, Decimal | None]:
    """Claimed edge vs. what the trades actually returned per dollar staked.

    The model asserts an edge at order time; ROI is what the book paid. A large
    positive claimed edge next to a negative realised one is the signature of a
    model priced off the wrong reference — exactly the failure the divergence
    gate exists to catch.
    """
    settled = [t for t in trades if t.is_settled]
    if not settled:
        return {"claimed_mean_edge": None, "realised_roi": None, "shortfall": None}
    claimed = _sum(t.model_edge for t in settled) / Decimal(len(settled))
    staked = _sum(t.total_debit for t in settled)
    realised = (_sum(t.pnl for t in settled) / staked) if staked else ZERO
    return {
        "claimed_mean_edge": claimed,
        "realised_roi": realised,
        "shortfall": realised - claimed,
    }


def group_summaries(
    trades: Sequence[LedgerTrade],
    key: str,
) -> dict[str, Summary]:
    """Summaries grouped by a trade attribute ("asset", "regime", "outcome")."""
    groups: dict[str, list[LedgerTrade]] = defaultdict(list)
    for trade in trades:
        groups[str(getattr(trade, key, "UNKNOWN"))].append(trade)
    return {name: summarize(members) for name, members in sorted(groups.items())}


@dataclass(frozen=True)
class GateFunnel:
    """Where evaluations die, most common first.

    This is the single most actionable diagnostic in the system: it converts
    "the bot isn't trading" into "the bot evaluated 19,033 markets and 16,275
    of them were outside the entry window", which is a schedule problem, not a
    model problem.
    """

    evaluations: int
    traded: int
    errors: int
    blocked: list[tuple[str, int]] = field(default_factory=list)

    @property
    def trade_rate(self) -> Decimal:
        return Decimal(self.traded) / Decimal(self.evaluations) if self.evaluations else ZERO


def gate_funnel(decisions: Iterable[dict[str, Any]]) -> GateFunnel:
    evaluations = traded = errors = 0
    blocked: Counter[str] = Counter()
    for record in decisions:
        if record.get("record_type") != "decision":
            continue
        # A record stands for one evaluation unless it is the summary of a run
        # of identical verdicts, in which case it says how many it folded. The
        # funnel counts evaluations, so it has to read that - otherwise the
        # collapsing in execution/decision_log.py would silently deflate every
        # blocked-gate count on the dashboard.
        weight = record.get("evaluations")
        weight = int(weight) if isinstance(weight, int) and weight > 0 else 1
        evaluations += weight
        decision = record.get("decision")
        if decision == "traded":
            traded += weight
        elif decision == "error":
            errors += weight
        reason = record.get("blocked_by")
        if reason:
            blocked[str(reason)] += weight
    return GateFunnel(
        evaluations=evaluations,
        traded=traded,
        errors=errors,
        blocked=blocked.most_common(),
    )


def regime_distribution(decisions: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in decisions:
        prediction = record.get("prediction") or {}
        regime = prediction.get("regime")
        if regime:
            counts[str(regime)] += 1
    return dict(counts.most_common())


@dataclass(frozen=True)
class Health:
    """Liveness, derived from evidence rather than from the process existing.

    ``--silent`` means a healthy bot and a blind one look identical in the
    journal, so staleness of the decision log is the real signal: every
    evaluation writes a record, whether or not it trades.
    """

    ok: bool
    last_decision_at: datetime | None
    seconds_since_decision: float | None
    last_trade_at: datetime | None
    open_trades: int
    consecutive_empty_discovery: int
    recent_errors: int
    issues: list[str] = field(default_factory=list)


def health(
    trades: Sequence[LedgerTrade],
    decisions: Sequence[dict[str, Any]],
    *,
    now: datetime,
    stale_after_seconds: float = 300.0,
) -> Health:
    issues: list[str] = []

    last_decision_at: datetime | None = None
    for record in reversed(decisions):
        stamp = record.get("timestamp")
        if stamp:
            try:
                parsed = datetime.fromisoformat(str(stamp))
            except ValueError:
                continue
            last_decision_at = (
                parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            )
            break

    age = (now - last_decision_at).total_seconds() if last_decision_at else None
    if age is None:
        issues.append("no decision records — the bot has never evaluated a market")
    elif age > stale_after_seconds:
        issues.append(
            f"decision log is stale ({age / 60:.1f} min); the bot may be down"
        )

    # Trailing window only: a discovery outage that recovered hours ago is
    # history, not a current fault.
    empty = 0
    for record in reversed(decisions[-500:]):
        if record.get("record_type") == "discovery_recovered":
            break
        if record.get("record_type") == "discovery_empty":
            empty = max(empty, int(record.get("consecutive_empty_cycles") or 0))
            break
    if empty:
        issues.append(f"market discovery empty for {empty} consecutive cycle(s)")

    errors = sum(
        1
        for record in decisions[-500:]
        if record.get("record_type") == "decision" and record.get("decision") == "error"
    )
    if errors:
        issues.append(f"{errors} evaluation error(s) in the last 500 decisions")

    open_trades = [t for t in trades if t.status == "OPEN"]
    for trade in open_trades:
        close = trade.market_close_time
        if close and now - close > timedelta(minutes=30):
            issues.append(
                f"{trade.ticker} has been OPEN for "
                f"{(now - close).total_seconds() / 60:.0f} min past close"
            )

    last_trade_at = max(
        (t.placed_at for t in trades if t.placed_at), default=None
    )

    return Health(
        ok=not issues,
        last_decision_at=last_decision_at,
        seconds_since_decision=age,
        last_trade_at=last_trade_at,
        open_trades=len(open_trades),
        consecutive_empty_discovery=empty,
        recent_errors=errors,
        issues=issues,
    )
