"""Data stage: ingestion, manifests, normalization, calendars, corporate actions.

Implements :class:`amat.boundaries.DataBoundary`.

Contract for this package:

* raw vendor snapshots are append-only and checksum-addressed under
  ``data/raw``; this package reads them and never rewrites them;
* every normalized table records provider, feed, adjustment mode, ingestion
  time, as-of time, request parameters, pagination state, and coverage;
* event timestamp, ingestion timestamp, and exchange session date are distinct
  fields, never conflated;
* partial pagination, impossible OHLC relationships, duplicate or non-session
  dates, stale bars, and unexplained split-like jumps fail closed.

The one live-network component is :mod:`amat.data.alpaca`, which is read-only and
reaches a single historical market-data host. Everything downstream of ingestion
reads immutable snapshots from disk and needs no network at all, which is what
keeps the test suite offline.
"""

from __future__ import annotations

from amat.data.alpaca import AlpacaCredentials, AlpacaMarketData
from amat.data.ingest import IngestionPlan, ingest_snapshot
from amat.data.snapshot import (
    SnapshotManifest,
    load_corporate_actions,
    load_manifest,
    load_panel,
    write_snapshot,
)

__all__ = [
    "AlpacaCredentials",
    "AlpacaMarketData",
    "IngestionPlan",
    "SnapshotManifest",
    "ingest_snapshot",
    "load_corporate_actions",
    "load_manifest",
    "load_panel",
    "write_snapshot",
]
