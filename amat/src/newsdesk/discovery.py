"""Point-in-time news themes translated into short-lived asset candidates.

This module deliberately does not use an LLM.  Headlines are hostile input and
an instruction hidden in one must never become executable trading logic.  A
checked-in taxonomy defines which phrases identify a theme and which liquid US
symbols represent it; bounded vendor sentiment (or a declared lexical fallback)
decides whether the event is positive enough to admit that theme temporarily.

The output is an inert JSON artifact.  ``amat`` validates that artifact and may
add its active symbols to a separately capped risk cluster.  News therefore
controls eligibility, not position size; the existing price trend, covariance,
symbol cap, cluster cap, and rebalance rules still control every order.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import tomllib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from newsdesk.errors import ConfigurationError
from newsdesk.store import NewsStore, StoredMention, to_utc_z

__all__ = [
    "DISCOVERY_SCHEMA_VERSION",
    "DiscoveryParameters",
    "Theme",
    "carry_retired_candidates",
    "discover_assets",
    "load_themes",
    "render_asset_artifact",
]

DISCOVERY_SCHEMA_VERSION = "1.0.0"
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.]{0,10}[A-Z0-9]|[A-Z]")
_MAX_EVIDENCE = 3
_MAX_TTL_HOURS = 96
_MAX_TRACKED_ASSETS = 64


@dataclass(frozen=True, slots=True)
class Theme:
    """A reviewed mapping from event language to tradable US companies."""

    name: str
    keywords: tuple[str, ...]
    symbols: tuple[str, ...]
    positive_terms: tuple[str, ...]
    negative_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryParameters:
    """Controls recorded in every candidate artifact."""

    lookback_hours: int = 72
    min_sentiment: float = 0.15
    min_articles: int = 1
    max_assets: int = 8
    ttl_hours: int = 30

    def validate(self) -> None:
        if self.lookback_hours < 1:
            raise ConfigurationError("lookback_hours must be positive")
        if not 0.0 < self.min_sentiment <= 1.0:
            raise ConfigurationError("min_sentiment must lie in (0, 1]")
        if self.min_articles < 1:
            raise ConfigurationError("min_articles must be positive")
        if self.max_assets < 1:
            raise ConfigurationError("max_assets must be positive")
        if not 1 <= self.ttl_hours <= _MAX_TTL_HOURS:
            raise ConfigurationError(f"ttl_hours must lie between 1 and {_MAX_TTL_HOURS}")


def _strings(raw: Any, *, name: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
        raise ConfigurationError(f"{name} must be a non-empty string array")
    cleaned = tuple(item.strip() for item in raw if item.strip())
    if not cleaned:
        raise ConfigurationError(f"{name} must contain a non-empty value")
    return cleaned


def load_themes(path: Path | str) -> tuple[Theme, ...]:
    """Load and strictly validate the reviewed TOML theme taxonomy."""
    source = Path(path)
    try:
        document = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot read news theme configuration {source}: {exc}") from exc
    unknown = set(document) - {"themes"}
    if unknown:
        raise ConfigurationError(f"unknown top-level news theme keys: {', '.join(sorted(unknown))}")
    rows = document.get("themes")
    if not isinstance(rows, list) or not rows:
        raise ConfigurationError("news theme configuration must contain [[themes]] entries")

    themes: list[Theme] = []
    seen_names: set[str] = set()
    seen_symbols: dict[str, str] = {}
    required = {"name", "keywords", "symbols", "positive_terms", "negative_terms"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ConfigurationError(f"themes[{index}] must be a table")
        keys = set(row)
        if keys != required:
            missing = required - keys
            extra = keys - required
            detail = []
            if missing:
                detail.append(f"missing {', '.join(sorted(missing))}")
            if extra:
                detail.append(f"unknown {', '.join(sorted(extra))}")
            raise ConfigurationError(f"themes[{index}]: {'; '.join(detail)}")
        name = str(row["name"]).strip()
        if not name or name in seen_names:
            raise ConfigurationError(f"theme name is empty or duplicated: {name!r}")
        keywords = tuple(value.casefold() for value in _strings(row["keywords"], name="keywords"))
        symbols = tuple(value.upper() for value in _strings(row["symbols"], name="symbols"))
        invalid = [symbol for symbol in symbols if not _SYMBOL.fullmatch(symbol)]
        if invalid:
            raise ConfigurationError(f"theme {name}: implausible symbols: {', '.join(invalid)}")
        duplicates = [symbol for symbol in symbols if symbol in seen_symbols]
        if duplicates:
            raise ConfigurationError(
                f"theme {name}: symbols already assigned to another theme: {', '.join(duplicates)}"
            )
        seen_names.add(name)
        seen_symbols.update(dict.fromkeys(symbols, name))
        themes.append(
            Theme(
                name=name,
                keywords=keywords,
                symbols=symbols,
                positive_terms=tuple(
                    value.casefold()
                    for value in _strings(row["positive_terms"], name="positive_terms")
                ),
                negative_terms=tuple(
                    value.casefold()
                    for value in _strings(row["negative_terms"], name="negative_terms")
                ),
            )
        )
    return tuple(themes)


def _lexical_sentiment(text: str, theme: Theme) -> float | None:
    positive = sum(1 for term in theme.positive_terms if term in text)
    negative = sum(1 for term in theme.negative_terms if term in text)
    total = positive + negative
    return None if total == 0 else (positive - negative) / total


def _group_mentions(rows: Sequence[StoredMention]) -> dict[int, list[StoredMention]]:
    grouped: dict[int, list[StoredMention]] = defaultdict(list)
    for row in rows:
        grouped[row.article_id].append(row)
    return grouped


def discover_assets(
    store: NewsStore,
    *,
    themes: Sequence[Theme],
    as_of: dt.datetime,
    parameters: DiscoveryParameters | None = None,
    excluded_symbols: Sequence[str] = (),
) -> dict[str, Any]:
    """Discover positive theme candidates from news knowable at ``as_of``.

    Multiple vendor entity sentiments on one article are averaged once; they do
    not turn one syndicated story into several votes.  When the vendor supplies
    no usable sentiment, the theme's reviewed positive/negative phrase lists
    provide a deterministic fallback.  An article with neither contributes no
    score and cannot admit an asset.
    """
    if as_of.tzinfo is None:
        raise ConfigurationError("as_of must carry a timezone")
    params = parameters or DiscoveryParameters()
    params.validate()
    as_of_utc = as_of.astimezone(dt.UTC)
    since = as_of_utc - dt.timedelta(hours=params.lookback_hours)
    rows = store.mentions_knowable_at(to_utc_z(as_of_utc), since=to_utc_z(since))
    excluded = {symbol.strip().upper() for symbol in excluded_symbols}

    scores: dict[str, list[float]] = defaultdict(list)
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article_rows in _group_mentions(rows).values():
        article = article_rows[0]
        text = f"{article.title}\n{article.snippet or ''}".casefold()
        vendor_values = [row.sentiment for row in article_rows if row.sentiment is not None]
        for theme in themes:
            matched = [keyword for keyword in theme.keywords if keyword in text]
            if not matched:
                continue
            sentiment = (
                sum(vendor_values) / len(vendor_values)
                if vendor_values
                else _lexical_sentiment(text, theme)
            )
            if (
                sentiment is None
                or not math.isfinite(sentiment)
                or sentiment < params.min_sentiment
            ):
                continue
            for symbol in theme.symbols:
                if symbol in excluded:
                    continue
                scores[symbol].append(float(sentiment))
                if len(evidence[symbol]) < _MAX_EVIDENCE:
                    evidence[symbol].append(
                        {
                            "article_id": article.article_id,
                            "published_at": article.published_at,
                            "title": article.title,
                            "url": article.url,
                            "matched_keywords": matched,
                        }
                    )

    theme_of = {symbol: theme.name for theme in themes for symbol in theme.symbols}
    ranked: list[dict[str, Any]] = [
        {
            "symbol": symbol,
            "theme": theme_of[symbol],
            "sentiment": sum(values) / len(values),
            "article_count": len(values),
            "evidence": evidence[symbol],
            "active": True,
        }
        for symbol, values in scores.items()
        if len(values) >= params.min_articles
    ]
    ranked.sort(
        key=lambda item: (
            -float(item["sentiment"]),
            -int(item["article_count"]),
            str(item["symbol"]),
        )
    )
    return render_asset_artifact(
        ranked[: params.max_assets], as_of=as_of_utc, parameters=params, themes=themes
    )


def carry_retired_candidates(
    artifact: dict[str, Any],
    previous: Mapping[str, Any],
    *,
    themes: Sequence[Theme],
    as_of: dt.datetime,
) -> dict[str, Any]:
    """Carry removed candidates as exit-only symbols on later artifacts.

    Without this lifecycle, the first artifact after a story leaves the
    lookback would make an existing news position "unrecognized" and the
    executor would correctly halt instead of guessing whether to liquidate it.
    A retired candidate remains recognized until it is deliberately removed
    from the reviewed taxonomy, so a dust position or withheld small sale can
    never age into an unknown holding. Only symbols still present in that
    taxonomy may be carried from the prior, untrusted JSON file.
    """
    current = artifact.get("candidates")
    prior = previous.get("candidates")
    if not isinstance(current, list) or not isinstance(prior, list):
        return artifact
    active_symbols = {
        str(row.get("symbol", "")).upper() for row in current if isinstance(row, dict)
    }
    theme_of = {symbol: theme.name for theme in themes for symbol in theme.symbols}
    now = as_of.astimezone(dt.UTC)
    retired: list[dict[str, Any]] = []
    for row in prior:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol", "")).upper()
        theme = str(row.get("theme", ""))
        if symbol in active_symbols or theme_of.get(symbol) != theme:
            continue
        retired_text = str(row.get("retired_at") or now.isoformat())
        try:
            retired_at = dt.datetime.fromisoformat(retired_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if retired_at.tzinfo is None:
            continue
        sentiment_raw = row.get("sentiment")
        article_count_raw = row.get("article_count")
        if sentiment_raw is None or article_count_raw is None:
            continue
        try:
            sentiment = float(sentiment_raw)
            article_count = int(article_count_raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(sentiment) or not 0.0 < sentiment <= 1.0 or article_count < 1:
            continue
        prior_evidence = row.get("evidence", [])
        if not isinstance(prior_evidence, list):
            prior_evidence = []
        retired.append(
            {
                "symbol": symbol,
                "theme": theme,
                "sentiment": sentiment,
                "article_count": article_count,
                "evidence": prior_evidence[:_MAX_EVIDENCE],
                "active": False,
                "retired_at": retired_at.astimezone(dt.UTC).isoformat(),
            }
        )
    artifact["candidates"] = [*current, *retired][:_MAX_TRACKED_ASSETS]
    return artifact


def render_asset_artifact(
    candidates: Sequence[Mapping[str, Any]],
    *,
    as_of: dt.datetime,
    parameters: DiscoveryParameters,
    themes: Sequence[Theme],
) -> dict[str, Any]:
    """Build the JSON-ready, content-addressed handoff artifact."""
    generated = as_of.astimezone(dt.UTC)
    taxonomy = json.dumps(
        [
            {
                "name": theme.name,
                "keywords": theme.keywords,
                "symbols": theme.symbols,
                "positive_terms": theme.positive_terms,
                "negative_terms": theme.negative_terms,
            }
            for theme in themes
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "as_of": generated.isoformat(),
        "expires_at": (generated + dt.timedelta(hours=parameters.ttl_hours)).isoformat(),
        "taxonomy_sha256": hashlib.sha256(taxonomy.encode("utf-8")).hexdigest(),
        "parameters": {
            "lookback_hours": parameters.lookback_hours,
            "min_sentiment": parameters.min_sentiment,
            "min_articles": parameters.min_articles,
            "max_assets": parameters.max_assets,
            "ttl_hours": parameters.ttl_hours,
        },
        "candidates": [dict(candidate) for candidate in candidates],
    }
