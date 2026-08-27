# Live statistics archive

- Created: `2026-08-24T16:00:41Z`
- Reason: operator-requested live statistics reset
- Source ledger: `trade_ledger.db`
- SQLite integrity before reset: `ok`
- Live rows: `77` (`0` open)
- Live realized P&L: `-$27.0524`
- Paper rows preserved in the active ledger: `5`
- Ledger SHA-256: `9665323f3942125a1e08d7fb34e96c7687650f0a947b7a225c9c19b4ce0e590e`

`trade_ledger.db` is a consistent SQLite online backup taken before any row was
removed. `project-logs/` contains the root decision/trade audit streams and
runtime-mode record available at reset time. `performance-logs/` contains the
human-readable run logs, ledger audit, archived decision log, and PDF reports.

The reset removed only live-mode records and their linked run, order, fill,
market-data, position-evaluation, data-quality, and persistent-risk rows. Paper
trade rows were not removed.
