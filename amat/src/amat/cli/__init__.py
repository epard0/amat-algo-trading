"""Command-line interface.

Offline and read-only by construction. No subcommand fetches data, mutates
evidence, or contacts an external service.
"""

from __future__ import annotations

from amat.cli.main import build_parser, main

__all__ = ["build_parser", "main"]
