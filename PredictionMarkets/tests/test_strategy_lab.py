from datetime import datetime, timedelta, timezone
from decimal import Decimal

from execution.strategy_lab import LabObservation, STRATEGIES, StrategyLab


D = Decimal
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def observation(**overrides) -> LabObservation:
    values = dict(
        ticker="KXBTC15M-LAB",
        asset="BTC",
        observed_at=NOW,
        close_time=NOW + timedelta(minutes=3),
        target_price=D("100000"),
        spot=D("100300"),
        probability_yes=D("0.75"),
        distance_z=D("1.8"),
        momentum=D("0.002"),
        regime="UPTREND",
        regime_confidence=D("0.8"),
        yes_bid=D("0.68"),
        yes_ask=D("0.70"),
        yes_bid_size=D("100"),
        no_bid_size=D("100"),
        microprice=D("0.695"),
        imbalance=D("0.1"),
        toxicity=D("0.1"),
        fee_multiplier=D("1"),
        raw_probability_yes=D("0.78"),
    )
    values.update(overrides)
    return LabObservation(**values)


def test_each_strategy_has_an_independent_wallet_and_strict_schema(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("20"), stake=D("2"))
    wallets = lab.connection.execute("SELECT strategy FROM lab_wallets").fetchall()
    assert {row["strategy"] for row in wallets} == set(STRATEGIES)
    strict = {
        row["name"]: row["strict"]
        for row in lab.connection.execute("PRAGMA table_list")
    }
    assert strict["lab_wallets"] == 1
    assert strict["lab_positions"] == 1
    assert strict["lab_observations"] == 1
    assert strict["lab_pair_cycles"] == 1
    lab.close()


def test_same_observation_is_scored_by_every_wallet(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("20"), stake=D("2"))
    lab.observe(observation())
    count = lab.connection.execute("SELECT COUNT(*) FROM lab_observations").fetchone()[
        0
    ]
    assert count == len(STRATEGIES)
    entries = lab.connection.execute("SELECT COUNT(*) FROM lab_positions").fetchone()[0]
    assert entries >= 3
    balances = lab.connection.execute(
        "SELECT COUNT(DISTINCT cash_balance) FROM lab_wallets"
    ).fetchone()[0]
    assert balances > 1
    lab.close()


def test_early_consensus_wallet_requires_three_persistent_observations(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("100"), stake=D("10"))
    close = NOW + timedelta(seconds=300)
    for offset in (0, 5):
        lab.observe(observation(
            observed_at=NOW + timedelta(seconds=offset), close_time=close,
            probability_yes=D("0.80"),
        ))
    assert lab.connection.execute(
        "SELECT COUNT(*) FROM lab_positions WHERE strategy='early_consensus_300'"
    ).fetchone()[0] == 0
    lab.observe(observation(
        observed_at=NOW + timedelta(seconds=10), close_time=close,
        probability_yes=D("0.80"),
    ))
    assert lab.connection.execute(
        "SELECT COUNT(*) FROM lab_positions WHERE strategy='early_consensus_300'"
    ).fetchone()[0] == 1
    # The matched 180-second policy sees the same data but cannot enter early.
    assert lab.connection.execute(
        "SELECT COUNT(*) FROM lab_positions WHERE strategy='early_consensus_180'"
    ).fetchone()[0] == 0
    lab.close()


def test_pair_wallet_only_books_equal_quantity_sub_dollar_pairs(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("20"), stake=D("2"))
    # YES costs .55 and NO costs 1-.62=.38: a .93 pair before fees.
    lab.observe(observation(yes_bid=D("0.62"), yes_ask=D("0.55")))
    cycle = lab.connection.execute(
        "SELECT contracts,yes_price,no_price,pair_cost,guaranteed_payout,pnl "
        "FROM lab_pair_cycles"
    ).fetchone()
    wallet = lab.connection.execute(
        "SELECT trades,wins,realized_pnl FROM lab_wallets "
        "WHERE strategy='complementary_pair_arb'"
    ).fetchone()
    assert cycle is not None
    assert D(cycle["pair_cost"]) == D("0.93")
    assert cycle["guaranteed_payout"] == "1"
    assert D(cycle["pnl"]) > 0
    assert (wallet["trades"], wallet["wins"]) == (1, 1)
    lab.close()


def test_fixed_take_profit_wallet_exits_without_affecting_control(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("20"), stake=D("2"))
    lab.observe(observation())
    lab.observe(
        observation(
            observed_at=NOW + timedelta(seconds=10),
            yes_bid=D("0.82"),
            yes_ask=D("0.84"),
        )
    )
    fixed = lab.connection.execute(
        "SELECT status,pnl FROM lab_positions WHERE strategy='fixed_take_profit'"
    ).fetchone()
    control = lab.connection.execute(
        "SELECT status FROM lab_positions WHERE strategy='hold_to_settlement'"
    ).fetchone()
    assert fixed["status"] == "EXITED"
    assert D(fixed["pnl"]) > 0
    assert control["status"] == "OPEN"
    adaptive = lab.connection.execute(
        "SELECT status FROM lab_positions WHERE strategy='adaptive_take_profit'"
    ).fetchone()
    assert adaptive["status"] == "OPEN"
    lab.observe(
        observation(
            observed_at=NOW + timedelta(seconds=20),
            probability_yes=D("0.93"),
            yes_bid=D("0.95"),
            yes_ask=D("0.97"),
        )
    )
    adaptive = lab.connection.execute(
        "SELECT status,pnl FROM lab_positions WHERE strategy='adaptive_take_profit'"
    ).fetchone()
    assert adaptive["status"] == "EXITED"
    assert D(adaptive["pnl"]) > D(fixed["pnl"])
    lab.close()


def test_shrunk_tail_does_not_manufacture_a_longshot_edge(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("20"), stake=D("2"))
    # Shrunk P(NO)=.136 looks attractive at 1.8c, but the raw model assigns NO
    # effectively zero. The tail wallet must use the unshrunk probability.
    lab.observe(
        observation(
            probability_yes=D("0.864"),
            raw_probability_yes=D("0.999999"),
            yes_bid=D("0.982"),
            yes_ask=D("0.983"),
            distance_z=D("2.1"),
        )
    )
    count = lab.connection.execute(
        "SELECT COUNT(*) FROM lab_positions WHERE strategy='raw_value_longshot'"
    ).fetchone()[0]
    assert count == 0
    lab.close()


def test_consensus_tail_wallet_uses_raw_probability_with_distance(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("20"), stake=D("2"))
    lab.observe(
        observation(
            probability_yes=D("0.875"),
            raw_probability_yes=D("0.999"),
            yes_bid=D("0.94"),
            yes_ask=D("0.95"),
            distance_z=D("3.0"),
            regime="CONSOLIDATION",
        )
    )
    position = lab.connection.execute(
        "SELECT outcome,status FROM lab_positions WHERE strategy='consensus_tail_carry'"
    ).fetchone()
    assert tuple(position) == ("yes", "OPEN")
    lab.close()


def test_winner_hold_waits_for_persistence_while_adaptive_exits(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("100"), stake=D("10"))
    lab.observe(observation())
    winner = observation(
        observed_at=NOW + timedelta(seconds=10),
        probability_yes=D("0.80"),
        yes_bid=D("0.95"),
        yes_ask=D("0.97"),
    )
    lab.observe(winner)
    lab.observe(observation(**{**winner.__dict__, "observed_at": NOW + timedelta(seconds=20)}))
    states = {
        row["strategy"]: row["status"]
        for row in lab.connection.execute(
            "SELECT strategy,status FROM lab_positions WHERE strategy IN "
            "('adaptive_take_profit','winning_hold_take_profit')"
        )
    }
    assert states == {
        "adaptive_take_profit": "EXITED",
        "winning_hold_take_profit": "OPEN",
    }
    lab.close()


def test_winner_hold_applies_before_final_five_minutes(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("100"), stake=D("10"))
    close = NOW + timedelta(seconds=700)
    opening = observation(close_time=close)
    assert lab._open(
        "winning_hold_take_profit", opening, "yes", "existing position fixture"
    )
    winner = observation(
        observed_at=NOW + timedelta(seconds=10),
        close_time=close,
        probability_yes=D("0.80"),
        yes_bid=D("0.94"),
        yes_ask=D("0.96"),
    )
    lab.observe(winner)
    state = lab.connection.execute(
        "SELECT status,winner_streak FROM lab_positions "
        "WHERE strategy='winning_hold_take_profit'"
    ).fetchone()
    assert tuple(state) == ("OPEN", 1)
    lab.close()


def test_winner_hold_trusts_a_strong_book_when_model_lags(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("100"), stake=D("10"))
    close = NOW + timedelta(seconds=700)
    opening = observation(close_time=close)
    assert lab._open(
        "winning_hold_take_profit", opening, "yes", "existing position fixture"
    )
    lab.observe(
        observation(
            observed_at=NOW + timedelta(seconds=10),
            close_time=close,
            probability_yes=D("0.40"),
            yes_bid=D("0.96"),
            yes_ask=D("0.98"),
        )
    )
    state = lab.connection.execute(
        "SELECT status,winner_streak FROM lab_positions "
        "WHERE strategy='winning_hold_take_profit'"
    ).fetchone()
    assert tuple(state) == ("OPEN", 1)
    lab.close()


def test_confirmed_reversal_requires_two_three_signal_observations(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("100"), stake=D("10"))
    lab.observe(observation())
    reversal = dict(
        probability_yes=D("0.40"),
        yes_bid=D("0.40"),
        yes_ask=D("0.42"),
        distance_z=D("-0.5"),
        momentum=D("-0.002"),
    )
    lab.observe(observation(observed_at=NOW + timedelta(seconds=10), **reversal))
    first = lab.connection.execute(
        "SELECT status FROM lab_positions WHERE strategy='confirmed_reversal_exit'"
    ).fetchone()[0]
    lab.observe(observation(observed_at=NOW + timedelta(seconds=20), **reversal))
    second = lab.connection.execute(
        "SELECT status,close_reason FROM lab_positions "
        "WHERE strategy='confirmed_reversal_exit'"
    ).fetchone()
    assert first == "OPEN"
    assert tuple(second) == ("EXITED", "three-signal deterioration confirmed twice")
    lab.close()


def test_confirmed_reversal_requires_executable_book_to_flip(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("100"), stake=D("10"))
    lab.observe(observation())
    noisy_reversal = dict(
        probability_yes=D("0.40"),
        yes_bid=D("0.55"),
        yes_ask=D("0.57"),
        distance_z=D("-0.5"),
        momentum=D("-0.002"),
    )
    lab.observe(observation(observed_at=NOW + timedelta(seconds=10), **noisy_reversal))
    lab.observe(observation(observed_at=NOW + timedelta(seconds=20), **noisy_reversal))
    state = lab.connection.execute(
        "SELECT status,reversal_signal_count FROM lab_positions "
        "WHERE strategy='confirmed_reversal_exit'"
    ).fetchone()
    assert tuple(state) == ("OPEN", 0)
    lab.close()


async def test_principal_recovery_sells_only_enough_to_create_residual(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("100"), stake=D("10"))
    lab.observe(observation())
    lab.observe(
        observation(
            observed_at=NOW + timedelta(seconds=10),
            probability_yes=D("0.85"),
            yes_bid=D("0.96"),
            yes_ask=D("0.98"),
        )
    )
    position = lab.connection.execute(
        "SELECT * FROM lab_positions WHERE strategy='principal_recovery'"
    ).fetchone()
    assert position["status"] == "OPEN"
    assert position["principal_recovered"] == 1
    assert D(position["remaining_contracts"]) < D(position["contracts"])
    assert D(position["partial_proceeds"]) > 0

    class Result:
        result = "yes"

    async def resolve(_ticker):
        return Result()

    await lab.settle_expired(NOW + timedelta(minutes=4), resolve)
    settled = lab.connection.execute(
        "SELECT status,pnl FROM lab_positions WHERE strategy='principal_recovery'"
    ).fetchone()
    assert settled["status"] == "WON"
    assert D(settled["pnl"]) > 0
    lab.close()


def test_low_cash_wallet_never_rounds_fee_debit_below_zero(tmp_path) -> None:
    lab = StrategyLab(tmp_path / "lab.db", starting_balance=D("0.11"), stake=D("2"))
    # A 1c contract appears to fit eleven times if fees are added only after
    # sizing. Fee-inclusive sizing must either fit safely or abstain.
    cheap = observation(
        probability_yes=D("0.999"),
        raw_probability_yes=D("0.999"),
        yes_bid=D("0.005"),
        yes_ask=D("0.01"),
        distance_z=D("3"),
    )
    lab.observe(cheap)
    minimum = lab.connection.execute(
        "SELECT MIN(CAST(cash_balance AS REAL)) FROM lab_wallets"
    ).fetchone()[0]
    assert minimum >= 0
    lab.close()
