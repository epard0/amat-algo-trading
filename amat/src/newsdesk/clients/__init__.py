"""Read-only vendor clients, and nothing else.

The whole package's network capability lives in these three modules, and
``tests/unit/test_newsdesk_isolation.py`` pins that: no other ``newsdesk``
module may import a transport library, no module anywhere in the package may
contain a state-changing HTTP verb literal, and ``urllib`` itself appears only
in ``base``.

Every client copies the discipline of ``amat.data.alpaca``: one pinned host, a
path-prefix allowlist checked before any request is built, ``GET`` as the only
method the code can express, credentials loaded from the environment by name
and scrubbed from every error message, fail-closed pagination, and a response
size cap so a hostile or broken vendor cannot balloon the raw-payload archive.
"""

from __future__ import annotations

from newsdesk.clients.adanos import AdanosClient, parse_adanos_mentions
from newsdesk.clients.alpaca import AlpacaNewsClient, parse_alpaca_news_page
from newsdesk.clients.base import ApiCredential, FetchedPage, ReadOnlyJsonClient
from newsdesk.clients.marketaux import MarketauxClient, parse_marketaux_page

__all__ = [
    "AdanosClient",
    "AlpacaNewsClient",
    "ApiCredential",
    "FetchedPage",
    "MarketauxClient",
    "ReadOnlyJsonClient",
    "parse_adanos_mentions",
    "parse_alpaca_news_page",
    "parse_marketaux_page",
]
