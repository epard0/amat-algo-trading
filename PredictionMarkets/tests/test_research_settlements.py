import sqlite3

from research.settlements import tickers_from_ledger


def test_ledger_tickers_include_refused_signal_evaluations(tmp_path) -> None:
    path = tmp_path / "ledger.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE trades (ticker TEXT NOT NULL);
        CREATE TABLE signal_evaluations (ticker TEXT NOT NULL);
        INSERT INTO trades VALUES ('TRADED');
        INSERT INTO signal_evaluations VALUES ('TRADED'), ('REFUSED');
        """
    )
    connection.close()

    assert tickers_from_ledger(path) == {"TRADED", "REFUSED"}
