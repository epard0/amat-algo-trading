"""PDF performance reports for the KX15 bot.

Like ``monitoring``, this package only observes: it opens the ledger read-only
and is never imported by ``bot.py``'s trading path, so a layout bug cannot
reach a position. Unlike ``monitoring`` it writes nothing at all beyond the PDF
it is asked for.

    from reporting.report import build_report
    build_report(ledger_path=settings.ledger_path, output_dir=settings.log_dir)
"""

from __future__ import annotations

__all__ = ["report", "pdf", "theme"]
